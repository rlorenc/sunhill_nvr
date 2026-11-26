"""Safe media probing and exact single-file export.

The caller must authenticate and resolve each clip against its own allow-list.
FFmpeg is always invoked as an argument vector, with no shell.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import time
import sys
import threading
from fractions import Fraction
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

@dataclass(frozen=True)
class Cut:
    source: Optional[Path | int]  # A file descriptor or None for a black segment
    start: float
    duration: float

class MediaError(RuntimeError):
    pass

def _source_argument(source: Path | int) -> str:
    if isinstance(source,int):
        os.fstat(source)
        return f'/proc/self/fd/{source}' if sys.platform.startswith('linux') else f'/dev/fd/{source}'
    return str(source)

def probe(path: Path | int, *, ffprobe: str = 'ffprobe') -> dict:
    pass_fds=(path,) if isinstance(path,int) else ()
    try:
        p = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-show_format',
                            '-of', 'json', '-protocol_whitelist', 'file,pipe', '-i', _source_argument(path)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=True, pass_fds=pass_fds)
        data = json.loads(p.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise MediaError('Cannot inspect recording') from exc
    if not any(s.get('codec_type') == 'video' for s in data.get('streams', [])):
        raise MediaError('Recording has no video stream')
    return data

def export(cuts: Sequence[Cut], output: Path, *, ffmpeg: str = 'ffmpeg',
           ffprobe: str = 'ffprobe', timeout: int | None = None, max_cuts: int = 128,
           keep_running: Callable[[float], bool] | None = None) -> None:
    """Create exactly one MP4 with sound. Do not replace an existing file.

    All cuts are transcoded for compatibility and frame-accurate boundaries.
    Audio-less segments get silence. Black segments get silent black video.
    Intermediate failures leave no output file. Output must be in a private export directory.
    """
    if not cuts or len(cuts) > max_cuts:
        raise ValueError('invalid number of cuts')
    if timeout is not None and timeout <= 0:
        raise ValueError('invalid timeout')
    # Indexed camera files are bounded separately. Intermediate files from a
    # multi-batch export can legitimately span more than a day; probe below
    # verifies every requested slice against its actual source duration.
    if any(not math.isfinite(c.start) or not math.isfinite(c.duration) or c.start < 0 or c.duration <= 0 for c in cuts):
        raise ValueError('invalid cut')
    total_seconds = math.fsum(c.duration for c in cuts)
    if not math.isfinite(total_seconds):
        raise ValueError('invalid total duration')
    # Long exports must not hit the former fixed two-hour limit merely
    # because they contain days of footage or generated black video.
    # Keep a finite deadline and honour explicit caller timeouts.
    if timeout is None:
        timeout = max(7200, math.ceil(total_seconds * 4 + 600))
    real = [c for c in cuts if c.source is not None]
    if not real:
        raise ValueError('at least one recording is required')
    source_data={}
    for source in {c.source for c in real}:
        if keep_running is not None and not keep_running(0.0):raise MediaError('FFmpeg export stopped')
        source_data[source]=probe(source,ffprobe=ffprobe)
    for cut in real:
        try:
            available = float(source_data[cut.source]['format']['duration'])
        except (KeyError, TypeError, ValueError) as exc:
            raise MediaError('recording duration cannot be verified') from exc
        if not math.isfinite(available) or cut.start + cut.duration > available + 0.05:
            raise MediaError('selected fragment exceeds available recording')
    video = next(s for s in source_data[real[0].source]['streams'] if s.get('codec_type') == 'video')
    width = int(video['width']) // 2 * 2
    height = int(video['height']) // 2 * 2
    if width < 2 or height < 2 or width > 7680 or height > 4320:
        raise MediaError('unsupported video dimensions')
    try:
        rate = Fraction(video.get('avg_frame_rate') or video.get('r_frame_rate') or '25/1')
        if rate <= 0 or rate > 120:
            raise ValueError('invalid frame rate')
    except (ValueError, ZeroDivisionError):
        rate = Fraction(25, 1)
    fps = f'{rate.numerator}/{rate.denominator}'
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ValueError('invalid export directory')
    # Files opened by FFmpeg are explicitly provided by the caller, never a concat list.
    args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-progress', 'pipe:1', '-stats_period', '1']
    inputs = []
    for cut in cuts:
        if cut.source is None:
            inputs.append(None)
        else:
            args += ['-ss', f'{cut.start:.6f}', '-t', f'{cut.duration:.6f}', '-protocol_whitelist', 'file,pipe', '-i', _source_argument(cut.source)]
            inputs.append(len([x for x in inputs if x is not None]))
    graph = []
    refs = []
    for i, (cut, src) in enumerate(zip(cuts, inputs)):
        dur = f'{cut.duration:.6f}'
        if src is None:
            graph.append(f"color=c=black:s={width}x{height}:r={fps}:d={dur},format=yuv420p,drawtext=text='BRAK NAGRANIA':fontcolor=white:fontsize=h/20:x=(w-text_w)/2:y=(h-text_h)/2[v{i}]")
            graph.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={dur},asetpts=PTS-STARTPTS[a{i}]')
        else:
            graph.append(f"[{src}:v:0]setpts=PTS-STARTPTS,scale=w='max(2,trunc(min({width},{height}*dar)/2)*2)':"
                         f"h='max(2,trunc(min({height},{width}/dar)/2)*2)',"
                         f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p,'
                         f'trim=duration={dur},setpts=PTS-STARTPTS[v{i}]')
            streams = source_data[cut.source].get('streams', [])
            if any(s.get('codec_type') == 'audio' for s in streams):
                graph.append(f'[{src}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
                             f'apad,atrim=duration={dur},asetpts=PTS-STARTPTS[a{i}]')
            else:
                graph.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={dur},asetpts=PTS-STARTPTS[a{i}]')
        refs.append(f'[v{i}][a{i}]')
    graph.append(''.join(refs) + f'concat=n={len(cuts)}:v=1:a=1[outv][outa]')
    args += ['-filter_complex', ';'.join(graph), '-map', '[outv]', '-map', '[outa]',
             '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-c:a', 'aac',
             '-t', f'{total_seconds:.6f}', '-movflags', '+faststart']
    fd, temporary = tempfile.mkstemp(prefix='.sunhill-export-', suffix='.mp4', dir=output.parent)
    os.close(fd)
    # mkstemp reserves the pathname. FFmpeg must be allowed to overwrite this private file.
    args[args.index('-n')] = '-y'
    try:
        pass_fds=tuple(sorted({c.source for c in real if isinstance(c.source,int)}))
        with tempfile.TemporaryFile() as error_log:
            proc=subprocess.Popen(args + [temporary],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                  stderr=error_log,pass_fds=pass_fds)
            observed=[0.0]
            def read_progress():
                for line in iter(proc.stdout.readline,b''):
                    if line.startswith(b'out_time_us='):
                        try:observed[0]=min(.99,max(0.,int(line.split(b'=',1)[1])/1_000_000/total_seconds))
                        except ValueError:pass
            reader=threading.Thread(target=read_progress,daemon=True);reader.start()
            deadline=time.monotonic()+timeout
            try:
                while True:
                    try:
                        code=proc.wait(timeout=min(1,max(.01,deadline-time.monotonic())))
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic()>=deadline:raise MediaError('FFmpeg export timed out')
                        if keep_running is not None and not keep_running(observed[0]):
                            raise MediaError('FFmpeg export stopped')
                if code != 0:
                    error_log.seek(0,os.SEEK_END);size=error_log.tell();error_log.seek(max(0,size-2000))
                    raise MediaError('FFmpeg export failed: '+error_log.read().decode('utf-8','replace'))
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
                reader.join(timeout=5)
                proc.stdout.close()
        rendered=probe(Path(temporary), ffprobe=ffprobe)
        try:rendered_duration=float(rendered['format']['duration'])
        except (KeyError,ValueError,TypeError) as exc:raise MediaError('cannot verify export duration') from exc
        if not math.isfinite(rendered_duration) or abs(rendered_duration-total_seconds)>max(.25,2/float(rate)):
            raise MediaError('export duration does not match selected range')
        # Atomic no-overwrite publish; a hard link makes concurrent collisions harmless.
        os.link(temporary, output)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaError('FFmpeg export failed') from exc
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def export_many(cuts: Sequence[Cut], output: Path, *, max_inputs: int = 16,
                keep_running: Callable[[float], bool] | None = None) -> None:
    """Join many cuts by rendering bounded batches in private storage.

    Adjacent missing sections are merged. The caller holds all source descriptors
    and enforces free-space limits. Originals are never modified.
    """
    if max_inputs < 2 or max_inputs > 128 or not cuts:
        raise ValueError('invalid batch size')
    merged=[]
    for cut in cuts:
        if cut.source is None and merged and merged[-1].source is None:
            merged[-1]=Cut(None,0,merged[-1].duration+cut.duration)
        else:merged.append(cut)
    if len(merged)<=max_inputs:
        return export(merged,output,keep_running=keep_running)
    import math
    count=len(merged);operations=1
    while count>max_inputs:
        count=math.ceil(count/max_inputs);operations+=count
    completed=[0]
    def report(local):
        return keep_running(min(.99,(completed[0]+local)/operations)) if keep_running else True
    with tempfile.TemporaryDirectory(prefix='.sunhill-batch-',dir=output.parent) as directory:
        work=merged;iteration=0
        while len(work)>max_inputs:
            groups=[work[i:i+max_inputs] for i in range(0,len(work),max_inputs)]
            for index in range(len(groups)-1,0,-1):
                if not any(c.source is not None for c in groups[index]):
                    if len(groups[index-1])<=1 or groups[index-1][-1].source is None:
                        raise MediaError('cannot resolve black segment dimensions')
                    groups[index].insert(0,groups[index-1].pop())
            next_pass=[]
            for index,group in enumerate(groups):
                part=Path(directory)/f'pass-{iteration}-{index}.mp4'
                export(group,part,keep_running=report)
                completed[0]+=1
                if not report(0):raise MediaError('FFmpeg export stopped')
                # Retain the logical timeline, not the container duration
                # (which can include encoder padding and frame rounding).
                next_pass.append(Cut(part,0,math.fsum(c.duration for c in group)))
            work=next_pass;iteration+=1
        export(work,output,keep_running=report)


def frame(source: Path | int, offset: float, *, thumbnail: bool = True,
          ffmpeg: str = 'ffmpeg', timeout: int = 20) -> bytes:
    """Extract a JPEG frame without a shell; bound resolution and output size."""
    if not math.isfinite(offset) or offset < 0:raise ValueError('invalid frame offset')
    args=[ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-ss',f'{offset:.6f}',
          '-protocol_whitelist','file,pipe','-i',_source_argument(source),'-frames:v','1']
    if thumbnail:args += ['-vf','scale=320:-2']
    args += ['-fs','10000001','-f','image2pipe','-vcodec','mjpeg','-q:v','3','pipe:1']
    try:
        result=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                              timeout=timeout,check=True,pass_fds=((source,) if isinstance(source,int) else ()))
    except (OSError,subprocess.SubprocessError) as exc:raise MediaError('frame extraction failed') from exc
    if len(result.stdout)>10_000_000 or not result.stdout.startswith(b'\xff\xd8') or not result.stdout.endswith(b'\xff\xd9'):
        raise MediaError('invalid frame image')
    return result.stdout
