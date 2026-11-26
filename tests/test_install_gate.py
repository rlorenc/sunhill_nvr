import subprocess,unittest
from pathlib import Path

class InstallGateTests(unittest.TestCase):
    def test_no_experimental_gate_or_signing_key_requirement(self):
        root=Path(__file__).resolve().parents[1]
        text=(root/'install.sh').read_text()
        self.assertNotIn('SUNHILL_EXPERIMENTAL_INSTALL',text)
        self.assertNotIn('release-public.pem',text)
        self.assertIn('storage=/opt/sunhill-nvr/media',text)
        self.assertIn('from sunhill_nvr import __version__',text)
        subprocess.run(['bash','-n',str(root/'install.sh')],check=True)
    def test_invalid_arguments_fail_before_install(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run(['bash',str(root/'install.sh'),'a','b','c'],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertRegex(result.stderr,r'Usage:|Run as root')

if __name__=='__main__':unittest.main()
