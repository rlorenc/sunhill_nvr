"""Build a deterministic timeline plan without invoking FFmpeg or touching files."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

@dataclass(frozen=True)
class Clip:
    path: Path
    start: datetime
    duration: float

@dataclass(frozen=True)
class Slice:
    path: Path
    offset: float
    duration: float
    start: datetime

@dataclass(frozen=True)
class Gap:
    start: datetime
    end: datetime

@dataclass(frozen=True)
class ExportPlan:
    segments: tuple[Slice, ...]
    gaps: tuple[Gap, ...]


def create_plan(clips: Iterable[Clip], start: datetime, end: datetime) -> ExportPlan:
    """Cover [start,end), avoiding duplicated content at overlaps; report gaps.

    Times must be all aware or all naive. Aware values and output are normalized to UTC.
    For repeated local times callers must resolve the fold before calling this function.
    """
    import math
    start_aware = start.utcoffset() is not None
    if (end.utcoffset() is not None) != start_aware:
        raise ValueError('mixed aware and naive datetimes')
    records = list(clips)
    if any((c.start.utcoffset() is not None) != start_aware for c in records):
        raise ValueError('mixed aware and naive datetimes')
    # Python datetime arithmetic can ignore offset changes when the same zoneinfo
    # object occurs on both sides. Convert to UTC first to preserve DST folds.
    if start_aware:
        start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        records = [Clip(c.path, c.start.astimezone(timezone.utc), c.duration) for c in records]
    if end <= start:
        raise ValueError('end must be after start')
    segments, gaps = [], []
    cursor = start
    for c in sorted(records, key=lambda x: (x.start, str(x.path))):
        if not math.isfinite(c.duration) or c.duration <= 0:
            raise ValueError('duration must be positive')
        clip_end = c.start + timedelta(seconds=c.duration)
        if clip_end <= cursor or c.start >= end:
            continue
        selected_start = max(c.start, cursor, start)
        selected_end = min(clip_end, end)
        if selected_start > cursor:
            gaps.append(Gap(cursor, selected_start))
        if selected_end > selected_start:
            segments.append(Slice(c.path, (selected_start-c.start).total_seconds(),
                                  (selected_end-selected_start).total_seconds(), selected_start))
            cursor = selected_end
        if cursor >= end:
            break
    if cursor < end:
        gaps.append(Gap(cursor, end))
    return ExportPlan(tuple(segments), tuple(gaps))
