"""Offline guard tests; never start or contact the generated services."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import lib


class Guards(unittest.TestCase):
    def test_rejects_remote_and_ambiguous_targets_before_curl(self):
        for url in ('https://127.0.0.1:3000', 'http://localhost:3000',
                    'http://example.com', 'http://127.0.0.1.example.com',
                    'http://user:pass@127.0.0.1', 'http://127.0.0.1/#fragment'):
            with self.subTest(url=url), patch('lib.subprocess.run') as run:
                with self.assertRaises(ValueError):
                    lib.curl('GET', url)
                run.assert_not_called()
        self.assertEqual(lib.loopback_url('http://127.0.0.1:8000/api'), 'http://127.0.0.1:8000/api')

    def test_receipt_is_fresh_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(lib, 'OUTPUT', Path(directory)):
            report = lib.Report('claude-run1'); report.rec(1, 'Example', 'fail'); report.save()
            path = Path(directory) / 'results-claude-run1.md'
            original = path.read_bytes()
            self.assertIn(b'1 fail', original)
            self.assertNotIn(b'2026-07-11', original)
            with self.assertRaises(FileExistsError):
                report.save()
            self.assertEqual(path.read_bytes(), original)

    def test_published_inputs_still_match_snapshot(self):
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / 'analysis/acceptance/provenance.json').read_text())
        self.assertEqual(len(manifest['original_adapters']), 8)
        for name, expected in manifest['published_inputs'].items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), expected)


if __name__ == '__main__':
    unittest.main()
