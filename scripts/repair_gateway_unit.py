"""Install only the known go2rtc startup guard on incomplete installations."""
from pathlib import Path
from sunhill_nvr.config import load
from sunhill_nvr.units import render
from sunhill_nvr.upgrade_admin import trusted_file
from sunhill_nvr.upgrade_system import SystemAdapter

if __name__=='__main__':
    unit=Path('/etc/systemd/system/sunhill-go2rtc.service');trusted_file(unit)
    new=render(load(Path('/opt/sunhill-nvr/config/config.json')))[unit.name].encode()
    old=new.replace(b'ExecCondition=/usr/bin/test -s /opt/sunhill-nvr/control/current/go2rtc.json\n',b'')
    data=unit.read_bytes()
    if data not in (old,new,new.replace(b'ExecCondition=',b'ExecStartPre=',1)):raise SystemExit('Unexpected go2rtc unit; refusing to overwrite')
    if data!=new:
        import tempfile
        backup=Path(tempfile.mkdtemp(prefix='resume-gateway-',dir='/opt/sunhill-nvr/control/upgrade'))
        SystemAdapter._write_private(backup/unit.name,data)
        SystemAdapter._replace(unit,new)
