import tempfile
from pathlib import Path
import unittest
import flora_pilot as p


class CredentialFileTests(unittest.TestCase):
    def test_missing_file_preserves_prompt_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(p.load_local_credentials('FLORA_WD_', Path(tmp)/'.env'), {})

    def test_local_file_scopes_credentials_and_does_not_interpolate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'.env'
            path.write_text('# local\nFLORA_WD_BOT_USERNAME=Example@App\nFLORA_WD_BOT_PASSWORD="synthetic-${literal}"\nFLORA_TEST_BOT_PASSWORD=test-only\nUNRELATED=ignore\n',encoding='utf-8')
            self.assertEqual(p.load_local_credentials('FLORA_WD_',path),
                {'FLORA_WD_BOT_USERNAME':'Example@App','FLORA_WD_BOT_PASSWORD':'synthetic-${literal}'})
            self.assertEqual(p.load_local_credentials('FLORA_TEST_',path),{'FLORA_TEST_BOT_PASSWORD':'test-only'})

    def test_duplicate_keys_stop_without_exposing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'.env'
            path.write_text('FLORA_WD_BOT_PASSWORD=synthetic-secret\nFLORA_WD_BOT_PASSWORD=another\n',encoding='utf-8')
            with self.assertRaises(p.Stop) as result:
                p.load_local_credentials('FLORA_WD_',path)
            self.assertNotIn('synthetic-secret',str(result.exception))


if __name__=='__main__': unittest.main()
