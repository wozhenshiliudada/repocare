"""Pinned public data preparation rejects altered streams and unsafe origins, without network."""
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
spec = importlib.util.spec_from_file_location('semantic_preparation', Path(__file__).resolve().parents[1] / 'scripts/prepare-semantic-model.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PreparationTests(unittest.TestCase):
    def test_pinned_stream_accepts_only_exact_bytes_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'data'
            sha = hashlib.sha256(b'abc').hexdigest()
            prepare.bounded_copy(io.BytesIO(b'abc'), path, 3, sha)
            with self.assertRaises(FileExistsError):
                prepare.bounded_copy(io.BytesIO(b'xyz'), path, 3, sha)
            self.assertEqual(path.read_bytes(), b'abc')

    def test_short_oversized_or_changed_download_cannot_be_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            for i, raw in enumerate([b'ab', b'abcd', b'xyz']):
                with self.subTest(raw=raw), self.assertRaises(ValueError):
                    prepare.bounded_copy(io.BytesIO(raw), Path(directory)/str(i), 3, hashlib.sha256(b'abc').hexdigest())

    def test_only_public_https_model_origins_are_allowed(self):
        for url in ['https://huggingface.co/a', 'https://us.aws.cdn.hf.co/model', 'https://www.apache.org/licenses/LICENSE-2.0.txt']:
            prepare.public_url(url)
        for url in ['http://huggingface.co/a', 'https://huggingface.co.evil.test/a', 'https://localhost/a',
                    'https://user:secret@huggingface.co/a', 'file:///model', 'https://huggingface.co:123/a']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                prepare.public_url(url)

    def test_offline_default_and_existing_output_never_start_network(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(prepare, 'build_opener', side_effect=AssertionError('network')):
            with self.assertRaises(FileNotFoundError):
                prepare.prepare(Path(directory))
            with self.assertRaises(FileExistsError):
                prepare.prepare(Path(directory), download=True)


if __name__ == '__main__':
    unittest.main()
