"""Media tree namespace shared by web, workers and the pinned coordinator."""
from pathlib import Path
import re

# A dot-prefixed directory cannot be a camera slug or an archive camera root.
# Reserve only direct children to keep exclusion and service isolation simple.
RESERVED = re.compile(r"\.[A-Za-z0-9_-][A-Za-z0-9_.-]*\Z")

def overlap(a: Path,b: Path) -> bool:
    return a==b or a in b.parents or b in a.parents

def nested_auxiliary(archive: Path,other: Path) -> bool:
    return (other.parent==archive and bool(RESERVED.fullmatch(other.name))
            and other.name!='.sunhill-volume-id')

def validate_media_layout(archive: Path,staging: Path,exports: Path) -> None:
    for other in (staging,exports):
        if overlap(archive,other) and not nested_auxiliary(archive,other):
            raise ValueError('archive, staging and exports must be separate directories')
    if overlap(staging,exports):
        raise ValueError('staging and exports must be separate directories')
