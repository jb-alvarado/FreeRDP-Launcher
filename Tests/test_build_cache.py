# SPDX-License-Identifier: Apache-2.0
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build as pipeline
from build_cache import load_native_cache, native_key, save_native_cache
from sources import digest


class NativeCacheChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.build = self.project / 'build'
        self.prefix = self.build / 'deps/local'
        self.library = self.prefix / 'lib/test.dylib'
        self.library.parent.mkdir(parents=True)
        self.library.write_text('library')
        (self.build / 'deps/compile').mkdir()
        (self.build / 'freerdp').mkdir()
        self.source = self.build / 'sources/test-1'
        self.source.mkdir(parents=True)
        self.freerdp = self.build / 'sources/freerdp-commit'
        self.freerdp.mkdir()
        self.archive = self.build / 'cache/test.tar.gz'
        self.archive.parent.mkdir()
        self.archive.write_bytes(b'verified archive')
        self.inputs = {'test': (self.source, self.archive)}
        self.manifest = dict(ffmpeg='OFF', dependencies={'test': {'sha256': digest(self.archive)}},
                             freerdp={'sha256': digest(self.archive)}, commands=[])
        self.key = {'configuration': 'test'}
        self.save()

    def save(self):
        save_native_cache(self.build, self.key, self.inputs, self.freerdp, self.archive, self.manifest)

    def test_complete_build_reused_and_manifest_not_accumulated(self):
        result = load_native_cache(self.build, self.key)
        self.assertEqual(result[:3], (self.inputs, self.freerdp, self.archive))
        result[3]['commands'].append('gui')
        self.assertEqual(load_native_cache(self.build, self.key)[3]['commands'], [])
        self.assertIsNone(load_native_cache(self.build, {'configuration': 'changed'}))

    def test_missing_installed_dependency_triggers_rebuild(self):
        self.library.unlink()
        self.assertIsNone(load_native_cache(self.build, self.key))

    def test_modified_installed_binary_triggers_rebuild(self):
        self.library.write_text('modified binary')
        self.assertIsNone(load_native_cache(self.build, self.key))

    def test_added_installed_library_triggers_rebuild(self):
        self.library.with_name('unexpected.dylib').write_text('extra')
        self.assertIsNone(load_native_cache(self.build, self.key))

    def test_changed_notice_triggers_rebuild(self):
        for source in (self.source, self.freerdp):
            with self.subTest(source=source):
                notice = source / 'LICENSE'
                notice.write_text('original')
                self.save()
                notice.write_text('changed')
                self.assertIsNone(load_native_cache(self.build, self.key))
                notice.unlink()
                self.save()

    def test_missing_or_added_notice_triggers_rebuild(self):
        notice = self.source / 'LICENSE'
        notice.write_text('license')
        self.assertIsNone(load_native_cache(self.build, self.key))
        self.save()
        notice.unlink()
        self.assertIsNone(load_native_cache(self.build, self.key))

    def test_changed_symlink_target_triggers_rebuild(self):
        other = self.library.with_name('other.dylib')
        other.write_bytes(self.library.read_bytes())
        link = self.library.with_name('alias.dylib')
        link.symlink_to(self.library.name)
        self.save()
        link.unlink()
        link.symlink_to(other.name)
        self.assertIsNone(load_native_cache(self.build, self.key))

    def test_broken_library_symlink_triggers_rebuild(self):
        link = self.library.with_name('alias.dylib')
        link.symlink_to(self.library.name)
        self.save()
        link.unlink()
        link.symlink_to('missing.dylib')
        self.assertIsNone(load_native_cache(self.build, self.key))

    def test_missing_native_build_directory_triggers_rebuild(self):
        (self.build / 'freerdp').rmdir()
        self.assertIsNone(load_native_cache(self.build, self.key))

    def test_missing_source_directory_triggers_rebuild(self):
        self.source.rmdir()
        self.assertIsNone(load_native_cache(self.build, self.key))

    def test_corrupt_archive_triggers_rebuild(self):
        self.archive.write_bytes(b'wrong source')
        self.assertIsNone(load_native_cache(self.build, self.key))

    def test_missing_or_broken_completion_record_triggers_rebuild(self):
        record = self.build / '.native-cache.json'
        record.write_text('{interrupted')
        self.assertIsNone(load_native_cache(self.build, self.key))
        record.unlink()
        self.assertIsNone(load_native_cache(self.build, self.key))

    def test_supplied_archive_outside_build_is_supported(self):
        external = self.project / 'release-source.tar.gz'
        external.write_bytes(self.archive.read_bytes())
        save_native_cache(self.build, self.key, self.inputs, self.freerdp, external, self.manifest)
        self.assertEqual(load_native_cache(self.build, self.key)[2].resolve(), external.resolve())

    def test_repair_removes_modified_products_even_with_same_configuration(self):
        info = {'commit': 'a' * 40}
        (self.build / '.configuration.json').write_text(json.dumps(dict(key=self.key, freerdp=info)))
        unwanted = self.library.with_name('unexpected.dylib')
        unwanted.write_text('must not survive repair')
        with patch.object(pipeline, 'download', return_value=(self.source, self.archive)), \
             patch.object(pipeline, 'freerdp_source', return_value=(self.freerdp, self.archive, info)), \
             patch.object(pipeline, 'output', return_value='toolchain'), \
             patch.object(pipeline, 'run', side_effect=RuntimeError('stop before compiling')):
            with self.assertRaisesRegex(RuntimeError, 'stop before compiling'):
                pipeline.build_native({'freerdp': {}}, self.build, self.archive.parent,
                                      self.source.parent, {'zlib': {}}, 'arm64', '13.0', 'OFF', 1, self.key)
        self.assertFalse(unwanted.exists())
        self.assertFalse(self.library.exists())
        self.assertTrue(self.prefix.is_dir())

    def test_gui_build_skips_all_native_work(self):
        config = dict(deploymentTarget='13.0', dependencies={}, freerdp={})
        (self.project / 'build-config.json').write_text(json.dumps(config))
        with patch.object(pipeline, 'PROJECT', self.project), \
             patch.object(pipeline, 'native_key', return_value=self.key), \
             patch.object(pipeline, 'build_native') as native, \
             patch.object(pipeline, 'run') as run, \
             patch('package.package') as package, \
             patch.dict(os.environ, {'WITH_FFMPEG': 'OFF'}, clear=True), \
             patch.object(pipeline.platform, 'machine', return_value='arm64'), \
             patch.object(sys, 'argv', ['build.py']):
            pipeline.main()
        native.assert_not_called()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[:2], ('xcrun', 'swiftc'))
        package.assert_called_once()

    def test_gui_edits_do_not_change_native_key(self):
        scripts = self.project / 'scripts'
        scripts.mkdir()
        for name in ('build.py', 'sources.py', 'build_cache.py'):
            (scripts / name).write_text('native recipe')
        with patch('build_cache.output', return_value='toolchain'), patch.dict(os.environ, {}, clear=True):
            before = native_key(self.project, {}, {}, 'arm64', '13.0', 'OFF')
            (self.project / 'GUI.swift').write_text('changed GUI')
            self.assertEqual(before, native_key(self.project, {}, {}, 'arm64', '13.0', 'OFF'))
            self.assertNotEqual(before, native_key(self.project, {}, {}, 'arm64', '14.0', 'OFF'))
            self.assertNotEqual(before, native_key(self.project, {}, {}, 'arm64', '13.0', 'ON'))
            (scripts / 'build.py').write_text('changed recipe')
            self.assertNotEqual(before, native_key(self.project, {}, {}, 'arm64', '13.0', 'OFF'))


if __name__ == '__main__':
    unittest.main()
