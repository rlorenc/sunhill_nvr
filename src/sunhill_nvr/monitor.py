"""Camera recording-health state with transition-only notifications."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import math

# The health worker normally runs every 60 seconds. Allow scheduling jitter,
# the bounded systemd query and transient database contention.
HEALTH_MAX_AGE=180

def sample_fresh(checked_at, now: int, max_age: int) -> bool:
    return type(checked_at) is int and now-max_age<=checked_at<=now+30

class State(str, Enum):
    DISABLED='disabled'
    WAITING='waiting'
    RECORDING='recording'
    STALE='stale'
    OFFLINE='offline'
    CLOCK_SKEW='clock_skew'

@dataclass(frozen=True)
class Status:
    state: State
    last_recording: float | None
    elapsed: float | None

def camera_status(*, enabled: bool, reachable: bool | None, last_recording: float | None,
                  now: float, stale_after: float, started: float) -> Status:
    if not math.isfinite(now) or not math.isfinite(started) or not math.isfinite(stale_after) or stale_after <= 0:
        raise ValueError('invalid monitoring timestamps')
    if last_recording is not None and not math.isfinite(last_recording):
        raise ValueError('invalid last recording timestamp')
    if not enabled:
        return Status(State.DISABLED,last_recording,None)
    if last_recording is not None and last_recording > now+30:
        return Status(State.CLOCK_SKEW,last_recording,None)
    # Old archive files must not bypass the grace period after a camera is
    # enabled or its recording mode changes. Keep the timestamp for display.
    if (last_recording is None or last_recording < started) and now - started <= stale_after:
        return Status(State.WAITING,last_recording,max(0.,now-started))
    elapsed = max(0., now - (last_recording if last_recording is not None else started))
    if last_recording is not None and elapsed <= stale_after:
        return Status(State.RECORDING,last_recording,elapsed)
    if last_recording is None and elapsed <= stale_after:
        return Status(State.WAITING,None,elapsed)
    return Status(State.OFFLINE if reachable is False else State.STALE,last_recording,elapsed)

def should_notify(previous: State | None, current: State) -> bool:
    """Notify only on transitions to a fault, and on recovery from a fault."""
    faulty = {State.STALE,State.OFFLINE,State.CLOCK_SKEW}
    return previous != current and ((current in faulty)
                                    or (previous in faulty and current == State.RECORDING))
