"""Check clean handover export and ordinary clone, without external Git access."""
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from sys import path

path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from package_handover import package, PREFIX, BUNDLE


class HandoverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@localhost')
        (self.repo / 'a.txt').write_text('first')
        self.git('add', '.')
        self.git('commit', '-m', 'initial')
        (self.repo / 'a.txt').write_text('second')
        self.git('commit', '-am', 'second')
        self.output = self.root / 'handover.zip'

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.PIPE)

    def test_zip_contains_current_tree_and_bundle_with_head(self):
        head = package(self.repo, self.output)
        self.assertEqual(head, self.git('rev-parse', 'HEAD').decode().strip())
        with zipfile.ZipFile(self.output) as zipped:
            self.assertEqual(zipped.read(PREFIX + 'a.txt'), b'second')
            bundle = self.root / BUNDLE
            bundle.write_bytes(zipped.read(PREFIX + BUNDLE))
        clone = self.root / 'clone'
        subprocess.run(['git', 'clone', '--quiet', str(bundle), str(clone)], check=True, capture_output=True)
        self.assertEqual((clone / 'a.txt').read_text(), 'second')
        self.assertEqual(subprocess.check_output(['git', '-C', str(clone), 'rev-list', '--count', 'HEAD']).strip(), b'2')
        with self.assertRaises(ValueError):package(self.repo, self.output)

    def test_dirty_tree_cannot_be_packaged(self):
        (self.repo / 'a.txt').write_text('uncommitted')
        with self.assertRaisesRegex(ValueError, 'commit all changes'):
            package(self.repo, self.output)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
