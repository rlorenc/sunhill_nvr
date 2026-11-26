"""Continuous RTSP recorder supervisor and completed-segment publishing."""
from __future__ import annotations
import datetime as dt
import contextlib
import fcntl
import logging
import os
import re
import secrets
import subprocess
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo
from .media import probe,MediaError
from .recorder import SLUG,prepare_days,publish_closed_segment,recording_command,validate_staging_parts
from . import fd_mount,staging_cleanup

def same_recording_mount(staging: Path, archive: Path) -> bool:
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    first=os.open(staging,flags)
    try:
        second=os.open(archive,flags)
        try:return fd_mount.identity(first)==fd_mount.identity(second)
        finally:os.close(second)
    finally:os.close(first)

@dataclass
class ProcessState:
    process: object
    session: str
    started_at: float
    audio: str = 'copy'
    stopping_at: float | None = None
    last_segment_at: float | None = None
    segment_seconds: int = 300

class Supervisor:
    def __init__(self,staging: Path,archive: Path,*,timezone: str='Europe/Warsaw',
                 active: Callable[[Path],bool],spawn=None,inspect=None,archive_lock: Path | None = None,monotonic=None,
                 segment_watchdog_seconds: int = 1200):
        if staging.is_symlink() or archive.is_symlink() or not staging.is_dir() or not archive.is_dir():
            raise ValueError('recording roots must be available real directories')
        if not same_recording_mount(staging,archive):
            raise ValueError('staging and archive must be on same filesystem')
        self.staging=staging;self.archive=archive;self.zone=ZoneInfo(timezone);self.active=active
        self.spawn=spawn or (lambda command:subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,env={'PATH':'/usr/bin:/bin','TZ':timezone,'LANG':'C.UTF-8'}))
        self.inspect=inspect or probe
        self.archive_lock=archive_lock
        self.monotonic=monotonic or time.monotonic
        if type(segment_watchdog_seconds) is not int or segment_watchdog_seconds < 30:
            raise ValueError('invalid segment watchdog interval')
        self.segment_watchdog_seconds=segment_watchdog_seconds
        self.next_staging_cleanup=self.monotonic()+3600
        self.probe_failures=OrderedDict()
        self.publish_failures=OrderedDict()
        self.corrupt_cameras=set()
        self.blocked_cameras=set()
        self.preparation_failures=set()
        self.process_failures=set()
        self.audio_observations={}
        self.running={};self.next_attempt={};self.failures={};self.audio_modes={}
        self.last_tick_wall=None

    def tick(self,cameras: set[str],now: float, *, configured_cameras: set[str] | None=None, segment_seconds: int=300, on_published=None) -> None:
        from .recording_settings import SEGMENT_SECONDS
        if type(segment_seconds) is not int or segment_seconds not in SEGMENT_SECONDS:raise ValueError('invalid segment duration')
        if any(not isinstance(s,str) or not SLUG.fullmatch(s) for s in cameras):raise ValueError('invalid camera')
        configured_cameras=cameras if configured_cameras is None else configured_cameras
        if not isinstance(configured_cameras,set) or not cameras.issubset(configured_cameras) or any(not isinstance(s,str) or not SLUG.fullmatch(s) for s in configured_cameras):raise ValueError('invalid configured cameras')
        if (self.staging.is_symlink() or self.archive.is_symlink() or not self.staging.is_dir() or
            not self.archive.is_dir() or not same_recording_mount(self.staging,self.archive)):
            raise RuntimeError('recording storage changed')
        # A backward NTP correction must not postpone process restart or kill
        # deadlines by the size of the clock adjustment. Calendar paths still
        # use the corrected wall clock.
        if self.last_tick_wall is not None and now<self.last_tick_wall:
            shift=now-self.last_tick_wall
            for slug in self.next_attempt:self.next_attempt[slug]+=shift
            for state in self.running.values():
                state.started_at+=shift
                if state.stopping_at is not None:state.stopping_at+=shift
        self.last_tick_wall=now
        today=dt.datetime.fromtimestamp(now,self.zone).date()
        self.preparation_failures.intersection_update(cameras)
        self.process_failures.intersection_update(configured_cameras)
        for slug,state in list(self.running.items()):
            if (slug not in cameras or state.segment_seconds!=segment_seconds) and state.process.poll() is None and state.stopping_at is None:
                try:state.process.terminate()
                except ProcessLookupError:pass
                state.stopping_at=now
            if state.stopping_at is not None and state.process.poll() is None and now-state.stopping_at>=10:
                try:state.process.kill()
                except ProcessLookupError:pass
            code=state.process.poll()
            if code is not None:
                del self.running[slug]
                if slug in cameras and state.stopping_at is None:self.process_failures.add(slug)
                # Some cameras send audio codecs that MP4 cannot mux directly.
                # Alternate strategies after an early failure; video always remains
                # stream-copied and a successful long-running strategy is retained.
                if slug in cameras and state.stopping_at is None and code!=0 and now-state.started_at<60:
                    self.audio_modes[slug]='aac' if state.audio=='copy' else 'copy'
                self.failures[slug]=min(self.failures.get(slug,0)+1,8)
                self.next_attempt[slug]=now+min(300,2**self.failures[slug])
            elif state.stopping_at is None:
                try:
                    prepare_days(self.staging,slug,today,days=3,session=state.session)
                    self.preparation_failures.discard(slug)
                except (OSError,ValueError,fd_mount.MountBoundary):
                    # A broken camera directory must not stop other recorders.
                    # Stop this process before it encounters an unsafe path.
                    self.preparation_failures.add(slug)
                    try:state.process.terminate()
                    except ProcessLookupError:pass
                    state.stopping_at=now
        for slug in cameras:
            if slug in self.running or now<self.next_attempt.get(slug,0):continue
            session=secrets.token_hex(16)
            try:
                prepare_days(self.staging,slug,today,days=3,session=session)
            except (OSError,ValueError,fd_mount.MountBoundary):
                self.preparation_failures.add(slug)
                self.failures[slug]=min(self.failures.get(slug,0)+1,8)
                self.next_attempt[slug]=now+min(300,2**self.failures[slug])
                continue
            self.preparation_failures.discard(slug)
            audio=self.audio_modes.get(slug,'copy')
            command=recording_command(slug,self.staging,session=session,audio=audio,segment_seconds=segment_seconds)
            try:
                process=self.spawn(command)
            except OSError:
                self.process_failures.add(slug)
                self.failures[slug]=min(self.failures.get(slug,0)+1,8)
                self.next_attempt[slug]=now+min(300,2**self.failures[slug])
                continue
            self.running[slug]=ProcessState(process,session,now,audio,last_segment_at=self.monotonic(),segment_seconds=segment_seconds)
        # Only configured camera staging trees are visited. Stale sessions survive
        # service crashes and are processed after the file-activity check succeeds.
        staged={p.name for p in self.staging.iterdir() if p.is_dir() and not p.is_symlink() and SLUG.fullmatch(p.name)}
        unseen=set(self.probe_failures)|set(self.publish_failures)
        corrupt=set();blocked=set()
        for slug in staged:
            base=self.staging/slug
            if base.is_symlink() or not base.is_dir():continue
            for candidate in base.rglob('*.mp4'):
                if candidate.is_symlink() or not candidate.is_file():continue
                unseen.discard(candidate)
                try:
                    # Unrelated files are left untouched and never sent to the
                    # privileged helper, whose rejection is otherwise a hard failure.
                    validate_staging_parts(candidate.relative_to(self.staging).parts)
                    info=candidate.stat();signature=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
                    previous=self.probe_failures.get(candidate)
                    publish_failure=self.publish_failures.get(candidate)
                    if publish_failure and publish_failure[0]==signature and self.monotonic()<publish_failure[2]:
                        blocked.add(slug);continue
                    if previous and previous[0]==signature and self.monotonic()<previous[2]:
                        corrupt.add(slug);continue
                    if self.active(candidate):
                        if previous:corrupt.add(slug)
                        if publish_failure:blocked.add(slug)
                        continue
                    try:inspection=self.inspect(candidate)
                    except (OSError,ValueError,MediaError):
                        corrupt.add(slug)
                        failures=min((previous[1] if previous and previous[0]==signature else 0)+1,8)
                        self.probe_failures[candidate]=(signature,failures,self.monotonic()+min(900,15*2**(failures-1)))
                        self.probe_failures.move_to_end(candidate)
                        if len(self.probe_failures)>4096:self.probe_failures.popitem(last=False)
                        continue
                    with self.shared_archive_lock():
                        if self.active(candidate):continue
                        current=candidate.stat()
                        if (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)!=signature:
                            self.probe_failures.pop(candidate,None)
                            continue
                        try:published=publish_closed_segment(self.staging,self.archive,slug,candidate,expected=signature)
                        except FileNotFoundError:continue
                        except (OSError,ValueError):
                            try:
                                current=candidate.stat()
                                if (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)!=signature:continue
                            except OSError:continue
                            blocked.add(slug)
                            failures=min((publish_failure[1] if publish_failure and publish_failure[0]==signature else 0)+1,8)
                            self.publish_failures[candidate]=(signature,failures,self.monotonic()+min(900,15*2**(failures-1)))
                            self.publish_failures.move_to_end(candidate)
                            if len(self.publish_failures)>4096:self.publish_failures.popitem(last=False)
                            continue
                        if on_published is not None:
                            try:on_published(published,signature,inspection)
                            except Exception as error:
                                # Publication already succeeded. The normal indexer
                                # will reconcile this file; do not stop recording.
                                logging.error('Immediate RTSP index failed: %s',type(error).__name__)
                    self.probe_failures.pop(candidate,None)
                    self.publish_failures.pop(candidate,None)
                    # An old staged segment does not prove that the restarted
                    # process is working. Clear only after its own live session
                    # has produced and published a verified segment.
                    current_process=self.running.get(slug)
                    parts=candidate.relative_to(self.staging).parts
                    if current_process and len(parts)==6 and parts[1]==current_process.session and current_process.process.poll() is None:
                        current_process.last_segment_at=self.monotonic()
                        self.process_failures.discard(slug)
                    streams=inspection.get('streams') if isinstance(inspection,dict) else None
                    if slug in cameras and isinstance(streams,list) and all(isinstance(item,dict) for item in streams):
                        has_audio=any(item.get('codec_type')=='audio' for item in streams)
                        prior=self.audio_observations.get(slug)
                        if prior is None or info.st_mtime_ns>=prior[0]:self.audio_observations[slug]=(info.st_mtime_ns,has_audio)
                    self.failures[slug]=0
                except (OSError,ValueError,MediaError):
                    # Invalid/incomplete files are retained for diagnosis/recovery.
                    continue
        for candidate in unseen:
            self.probe_failures.pop(candidate,None);self.publish_failures.pop(candidate,None)
        # RTSP read timeouts do not catch streams that remain connected but
        # never deliver a usable video segment (for example audio-only traffic).
        # Use monotonic time and restart only the affected recorder. A known
        # storage/publication failure must not trigger pointless recorder restarts.
        for slug,state in self.running.items():
            if (slug not in cameras or slug in blocked or state.stopping_at is not None
                or state.process.poll() is not None or state.last_segment_at is None):
                continue
            if self.monotonic()-state.last_segment_at >= max(self.segment_watchdog_seconds, state.segment_seconds+120 if state.segment_seconds>300 else 0):
                self.process_failures.add(slug)
                try:state.process.terminate()
                except ProcessLookupError:pass
                state.stopping_at=now
        self.corrupt_cameras=corrupt
        self.blocked_cameras=blocked
        if self.monotonic()>=self.next_staging_cleanup:
            self.next_staging_cleanup=self.monotonic()+3600
            try:
                staging_cleanup.prune(self.staging,{(slug,state.session) for slug,state in self.running.items()},now=now)
            except (OSError,fd_mount.MountUnavailable):
                # Cleanup is optional and must not interrupt ongoing recording.
                pass

    def stop_all(self) -> None:
        """Terminate recorders together and use a shared bounded deadline."""
        states=list(self.running.values())
        for state in states:
            if state.process.poll() is None:
                try:state.process.terminate()
                except ProcessLookupError:pass
        deadline=time.monotonic()+10
        for state in states:
            if state.process.poll() is not None:continue
            try:state.process.wait(timeout=max(0,deadline-time.monotonic()))
            except subprocess.TimeoutExpired:break
            except ProcessLookupError:pass
        # A blocked process must not extend the grace period for every camera.
        for state in states:
            if state.process.poll() is None:
                try:state.process.kill()
                except ProcessLookupError:pass
        deadline=time.monotonic()+5
        for state in states:
            try:state.process.wait(timeout=max(0,deadline-time.monotonic()))
            except ProcessLookupError:pass
            except subprocess.TimeoutExpired as exc:
                # Keep references and let systemd terminate the service group.
                raise RuntimeError('recording process did not stop') from exc
        self.running.clear()

    @contextlib.contextmanager
    def shared_archive_lock(self):
        if self.archive_lock is None:
            yield
            return
        fd=os.open(self.archive_lock,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_SH)
            yield
        finally:os.close(fd)
