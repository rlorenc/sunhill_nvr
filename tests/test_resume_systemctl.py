"""Run the actual shell helper under errexit with systemctl state transitions."""
import os,subprocess,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class ResumeSystemctlTests(unittest.TestCase):
    def exercise(self,mode):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);log=root/'log';mock=root/'systemctl'
            mock.write_text("""#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
with open(os.environ['LOG'],'a') as log:log.write(' '.join(args)+'\\n')
if args[0]=='reset-failed':
    if args[-1] in ('sunhill-go2rtc.service','sunhill-ftp.service'):
        print('Unit not loaded',file=sys.stderr);sys.exit(1)
    sys.exit(0)
if args[0]=='show':
    mode=os.environ['MODE']
    if mode=='bus':sys.exit(1)
    print('LoadState='+('not-found' if mode=='missing' else 'loaded'))
    print('ActiveState='+('failed' if mode=='failed' else 'inactive'))
    sys.exit(0)
if args[0]=='start':sys.exit(0)
sys.exit(2)
""")
            mock.chmod(0o755)
            env=dict(os.environ,PATH=tmp+os.pathsep+os.environ['PATH'],LOG=str(log),MODE=mode)
            result=subprocess.run(['bash','-euo','pipefail','-c','source "$1"; reset_resume_failed_units sunhill-controller.service sunhill-go2rtc.service sunhill-ftp.service sunhill-ftp-policy.service; systemctl start sunhill-controller.service','bash',str(ROOT/'scripts/install_helpers.sh')],env=env,capture_output=True,text=True)
            return result,log.read_text()
    def test_stopped_units_do_not_abort_resume(self):
        result,log=self.exercise('inactive')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('start sunhill-controller.service',log)
        self.assertIn('Unit already inactive: sunhill-ftp.service',result.stderr)
    def test_missing_failed_or_unreachable_units_fail_closed(self):
        for mode in ('missing','failed','bus'):
            with self.subTest(mode=mode):
                result,log=self.exercise(mode)
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('start sunhill-controller.service',log)
