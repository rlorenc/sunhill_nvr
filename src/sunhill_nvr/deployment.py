"""Generate deployable service configuration for a clean Ubuntu instance."""
from __future__ import annotations
from pathlib import Path
from .config import Config
from .nginx_config import render as render_nginx
from .units import render as render_units
from . import lifecycle
import json

def generate(config: Config, output: Path, *, tls_certificate: Path | None=None, tls_key: Path | None=None, rtsp_only: bool=True) -> None:
    if not rtsp_only:raise ValueError('Use explicit RTSP-only migration with --update --rtsp-only')
    if output.is_symlink() or not output.is_dir():raise ValueError('invalid output directory')
    units=output/'units';units.mkdir(mode=0o750,exist_ok=False)
    rendered=render_units(config,rtsp_only=rtsp_only)
    for name,content in rendered.items():
        with (units/name).open('x',encoding='utf-8') as f:f.write(content)
    with (output/'nginx.conf').open('x',encoding='utf-8') as f:f.write(render_nginx(config.hostname,tls_certificate=tls_certificate,tls_key=tls_key))
    manifest=lifecycle.validate(lifecycle.default(rtsp_only=rtsp_only),set(rendered))
    with (output/'lifecycle.json').open('x',encoding='utf-8') as f:json.dump(manifest,f,separators=(',',':'))


def preflight_unit_targets(generated: Path, system_dir: Path) -> None:
    """Refuse collisions, including dangling symlinks, before system changes."""
    import re
    if generated.is_symlink() or not generated.is_dir():raise ValueError('invalid unit directory')
    names=list(generated.iterdir())
    if not names:raise ValueError('no generated units')
    for unit in names:
        if unit.is_symlink() or not unit.is_file() or not re.fullmatch(r'sunhill-[a-z0-9-]+\.(?:service|timer)',unit.name):
            raise ValueError('invalid generated unit')
        target=system_dir/unit.name
        if target.exists() or target.is_symlink():raise FileExistsError(str(target))
