"""Convert legacy camera-local timestamps to UTC without silently guessing DST."""
from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

class NonexistentLocalTime(ValueError): pass
class AmbiguousLocalTime(ValueError): pass

def utc_candidates(local: datetime, zone: ZoneInfo) -> tuple[datetime, ...]:
    """Return zero, one or two UTC instants that map to a naive local timestamp."""
    if local.tzinfo is not None:
        raise ValueError('expected a naive camera-local time')
    values = set()
    for fold in (0, 1):
        candidate = local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(zone).replace(tzinfo=None) == local:
            values.add(candidate)
    return tuple(sorted(values))

def resolve_local(local: datetime, zone: ZoneInfo, *, fold: int | None = None) -> datetime:
    values = utc_candidates(local,zone)
    if not values:
        raise NonexistentLocalTime('camera timestamp falls in the DST spring gap')
    if len(values) == 1:
        if fold not in (None,0,1):
            raise ValueError('fold must be 0 or 1')
        return values[0]
    if fold not in (0,1):
        raise AmbiguousLocalTime('camera timestamp occurs twice; choose the offset')
    return local.replace(tzinfo=zone,fold=fold).astimezone(timezone.utc)
