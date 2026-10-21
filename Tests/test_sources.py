# SPDX-License-Identifier: Apache-2.0
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sources import digest, download, extract, freerdp_source, load_environment


class SourceChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def archive(self, entries):
        path = self.root / 'input.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name, value, link_type in entries:
                member = tarfile.TarInfo(name)
                if link_type:
                    member.type = link_type
                    member.linkname = value
                    archive.addfile(member)
                else:
                    data = value.encode()
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
        return path

    def test_dotenv_is_literal_and_preserves_shell_overrides(self):
        env = self.root / '.env'
        sentinel = self.root / 'must-not-exist'
        env.write_text(f'JOBS=3\nVALUE="$(touch {sentinel})"\nEMPTY=\n')
        with patch.dict(os.environ, {'JOBS': '7'}, clear=True):
            load_environment(env)
            self.assertEqual(os.environ['JOBS'], '7')
            self.assertEqual(os.environ['VALUE'], f'$(touch {sentinel})')
            self.assertEqual(os.environ['EMPTY'], '')
            self.assertFalse(sentinel.exists())

    def test_dotenv_rejects_broken_input(self):
        env = self.root / '.env'
        env.write_text('INVALID\n')
        with self.assertRaises(ValueError):
            load_environment(env)

    def test_checksum_failure_leaves_no_usable_archive(self):
        archive = self.archive([('root/file', 'source', None)])
        cache, sources = self.root / 'cache', self.root / 'sources'
        cache.mkdir()
        sources.mkdir()
        with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
            download('example', dict(version='1', url=archive.as_uri(), sha256='0' * 64), cache, sources)
        self.assertEqual(list(cache.iterdir()), [])

    def test_valid_archive_and_pristine_source_reextraction(self):
        archive = self.archive([('root/file', 'original', None)])
        cache, sources = self.root / 'cache', self.root / 'sources'
        cache.mkdir()
        sources.mkdir()
        spec = dict(version='1', url=archive.as_uri(), sha256=digest(archive))
        destination, _ = download('example', spec, cache, sources)
        (destination / 'file').write_text('modified')
        download('example', spec, cache, sources)
        self.assertEqual((destination / 'file').read_text(), 'original')

    def test_archive_cannot_escape_through_paths_or_links(self):
        cases = [('root/../escape', 'payload', None),
                 ('root/absolute', '/etc/passwd', tarfile.SYMTYPE),
                 ('root/link', '../../escape', tarfile.SYMTYPE),
                 ('root/hard', '../escape', tarfile.LNKTYPE)]
        for entry in cases:
            with self.subTest(entry=entry):
                archive = self.archive([entry])
                stage = self.root / 'stage'
                stage.mkdir(exist_ok=True)
                with self.assertRaises(ValueError):
                    extract(archive, stage / 'source')
        self.assertFalse((self.root / 'escape').exists())

    def test_archive_rejects_chained_and_forward_link_traversal(self):
        entries = [('root/a', '.', tarfile.SYMTYPE),
                   ('root/a/b', '..', tarfile.SYMTYPE),
                   ('root/a/b/c', '..', tarfile.SYMTYPE),
                   ('root/b/c/escaped', 'payload', None)]
        for ordered in (entries, list(reversed(entries))):
            with self.subTest(forward=ordered != entries):
                stage = self.root / 'stage'
                stage.mkdir(exist_ok=True)
                with self.assertRaises(ValueError):
                    extract(self.archive(ordered), stage / 'source')
                self.assertFalse((self.root / 'escaped').exists())
                self.assertFalse((stage / 'escaped').exists())

    def test_archive_supports_safe_symbolic_and_hard_links(self):
        archive = self.archive([('root/file', 'source', None),
                                ('root/symbolic', 'file', tarfile.SYMTYPE),
                                ('root/hard', 'root/file', tarfile.LNKTYPE)])
        destination = self.root / 'source'
        extract(archive, destination)
        self.assertEqual((destination / 'symbolic').read_text(), 'source')
        self.assertEqual((destination / 'hard').read_text(), 'source')

    def test_corresponding_source_can_be_used_without_git_or_network(self):
        archive = self.archive([('freerdp/CMakeLists.txt', '# source', None)])
        sources = self.root / 'sources'
        sources.mkdir()
        config = dict(repository='https://example.invalid/repo.git', branch='test', commit='a' * 40)
        with patch.dict(os.environ, {'FREERDP_ARCHIVE': str(archive),
                                    'FREERDP_ARCHIVE_SHA256': digest(archive),
                                    'FREERDP_COMMIT': config['commit']}, clear=True):
            source, original, info = freerdp_source(config, self.root, self.root, sources)
        self.assertEqual((source / 'CMakeLists.txt').read_text(), '# source')
        self.assertEqual(original, archive.resolve())
        self.assertEqual(info['sha256'], digest(archive))


if __name__ == '__main__':
    unittest.main()
