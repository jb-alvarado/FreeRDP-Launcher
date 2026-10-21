# SPDX-License-Identifier: Apache-2.0
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import package
import release_signing as signing
from sources import digest


class DeveloperIDChecks(unittest.TestCase):
    def test_identity_requires_exactly_one_matching_team_and_certificate_type(self):
        def response(lines):
            return subprocess.CompletedProcess([], 0, stdout=lines)
        identity = 'A' * 40
        valid = f'1) {identity} "Developer ID Application: Example ({signing.TEAM_ID})"\n'
        for lines, accepted in [(valid, True), ('', False), (valid + valid, False),
                                (valid.replace('Developer ID Application', 'Apple Development'), False),
                                (valid.replace(signing.TEAM_ID, 'AAAAAAAAAA'), False)]:
            with self.subTest(lines=lines), patch.object(signing.subprocess, 'run', return_value=response(lines)):
                if accepted:
                    self.assertEqual(signing.select_identity('test.keychain', signing.TEAM_ID), identity)
                else:
                    with self.assertRaises(ValueError):
                        signing.select_identity('test.keychain', signing.TEAM_ID)

    def test_signature_requires_team_timestamp_and_runtime(self):
        valid = f'flags=0x10000(runtime)\nTeamIdentifier={signing.TEAM_ID}\nTimestamp=now\n'
        for text, accepted in [(valid, True), (valid.replace('Timestamp=', 'Signed Time='), False),
                               (valid.replace(signing.TEAM_ID, 'AAAAAAAAAA'), False),
                               (valid.replace('0x10000', '0x0'), False)]:
            with self.subTest(text=text), patch.object(package, 'run') as verify, \
                 patch.object(package.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stderr=text)):
                if accepted:
                    package.verify_developer_id(Path('app'), signing.TEAM_ID)
                    self.assertEqual(verify.call_args.args[-2], '=' + package.developer_id_requirement(signing.TEAM_ID))
                    self.assertIn('certificate leaf[field.1.2.840.113635.100.6.1.13]', verify.call_args.args[-2])
                else:
                    with self.assertRaises(ValueError):
                        package.verify_developer_id(Path('app'), signing.TEAM_ID)

    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('codesign') and shutil.which('xcrun'),
                         'requires macOS signing tools')
    def test_real_codesign_parses_inline_developer_id_requirement(self):
        # Ad-hoc code is valid, but is not signed by our Developer ID team.
        # Exit 3 means the requirement parsed and evaluated to false; a missing
        # '=' instead fails with exit 1 and "invalid requirement specification".
        with tempfile.TemporaryDirectory() as temp:
            source, binary = Path(temp) / 'main.c', Path(temp) / 'test-client'
            source.write_text('int main(void) { return 0; }\n')
            subprocess.run(['xcrun', 'clang', str(source), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run(['codesign', '--force', '--sign', '-', str(binary)], check=True, capture_output=True)
            with self.assertRaises(subprocess.CalledProcessError) as failure:
                package.verify_developer_id(binary, signing.TEAM_ID)
            self.assertEqual(failure.exception.returncode, 3)

    def test_developer_signing_preserves_entitlements_and_signs_inside_out(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp) / 'Test.app'
            (app / 'Contents/Resources').mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': 'FreeRDPLauncher'}))
            policy = {'com.apple.security.device.audio-input': True, 'com.apple.security.device.camera': True}
            (app / 'Contents/Resources/FreeRDPClient.entitlements').write_bytes(plistlib.dumps(policy))
            main = app / 'Contents/MacOS/FreeRDPLauncher'
            client = app / 'Contents/MacOS/sdl-freerdp'
            library = app / 'Contents/Frameworks/library.dylib'
            events = []
            entitlements = []
            def record(*args):
                events.append(args)
                if '--entitlements' in args:
                    entitlements.append(plistlib.loads(Path(args[args.index('--entitlements') + 1]).read_bytes()))
            original = {'existing.application.entitlement': True}
            with patch.object(package, 'run', side_effect=record), \
                 patch.object(package.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout=plistlib.dumps(original))), \
                 patch.object(package, 'verify_developer_id') as verify:
                package.sign_app(app, {main, client, library}, 'A' * 40, signing.TEAM_ID, 'test.keychain')
            signing_commands = events[:-1]
            self.assertEqual([command[-1] for command in signing_commands], [library, client, app])
            for command in signing_commands:
                self.assertIn('--timestamp', command)
                self.assertIn('runtime', command)
                self.assertNotIn('--deep', command)
                self.assertNotIn('--timestamp=none', command)
            self.assertEqual(entitlements[1], {**original, **policy})
            self.assertEqual(verify.call_count, 4)
            with self.assertRaises(ValueError):
                package.sign_app(app, {main, client}, '-', signing.TEAM_ID)


class NotarizationChecks(unittest.TestCase):
    def test_non_json_response_keeps_authentication_error_without_credentials(self):
        key = '-----BEGIN PRIVATE KEY-----\nsecret-key-line\n-----END PRIVATE KEY-----'
        result = subprocess.CompletedProcess([], 1, stdout='',
            stderr='HTTP 401: Invalid credentials for KEYIDSECRET\nsecret-key-line')
        with patch.object(signing.subprocess, 'run', return_value=result):
            with self.assertRaises(ValueError) as failure:
                signing.notarize(Path('test.dmg'), key, 'KEYIDSECRET', 'issuer')
        message = str(failure.exception)
        self.assertIn('notarytool submit failed (exit 1)', message)
        self.assertIn('HTTP 401: Invalid credentials', message)
        self.assertNotIn('KEYIDSECRET', message)
        self.assertNotIn('secret-key-line', message)

    def test_command_failure_reports_gatekeeper_reason_without_command_arguments(self):
        failure = subprocess.CalledProcessError(3, ['spctl', '--assess', 'secret-argument'],
            output=b'Assessment failed', stderr=b'rejected: invalid signature; password-secret')
        with patch.dict(os.environ, {'APPLE_CERT_PASSWORD': 'password-secret'}):
            message = signing.error_message(failure)
        self.assertIn('spctl failed (exit 3)', message)
        self.assertIn('Assessment failed', message)
        self.assertIn('rejected: invalid signature', message)
        self.assertNotIn('password-secret', message)
        self.assertNotIn('secret-argument', message)

    def test_log_retrieval_failure_keeps_redacted_diagnostics(self):
        submission = '12345678-1234-1234-1234-123456789abc'
        responses = [subprocess.CompletedProcess([], 0, stdout=json.dumps({'id': submission, 'status': 'Invalid'})),
                     subprocess.CompletedProcess([], 1, stdout='', stderr='Network unavailable KEYIDSECRET')]
        output = io.StringIO()
        with patch.object(signing.subprocess, 'run', side_effect=responses), \
             patch('sys.stderr', output):
            with self.assertRaises(ValueError):
                signing.notarize(Path('test.dmg'), 'private-key-secret', 'KEYIDSECRET', 'issuer')
        self.assertIn('notarytool log failed (exit 1)', output.getvalue())
        self.assertIn('Network unavailable', output.getvalue())
        self.assertNotIn('KEYIDSECRET', output.getvalue())

    def test_rejection_fetches_redacted_log_and_removes_private_key(self):
        submission = '12345678-1234-1234-1234-123456789abc'
        key_paths = []
        def run(command, **kwargs):
            key = Path(command[command.index('--key') + 1])
            key_paths.append(key)
            self.assertEqual(key.read_text(), 'private-key-secret')
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            if 'submit' in command:
                self.assertIn('--wait', command)
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps({'id': submission, 'status': 'Invalid'}))
            return subprocess.CompletedProcess(command, 0, stdout='Rejected private-key-secret KEYIDSECRET')
        output = io.StringIO()
        with patch.object(signing.subprocess, 'run', side_effect=run), redirect_stdout(output):
            with self.assertRaises(ValueError):
                signing.notarize(Path('test.dmg'), 'private-key-secret', 'KEYIDSECRET', 'issuer')
        self.assertNotIn('private-key-secret', output.getvalue())
        self.assertNotIn('KEYIDSECRET', output.getvalue())
        self.assertTrue(key_paths)
        self.assertTrue(all(not path.exists() for path in key_paths))

    def test_only_accepted_status_succeeds(self):
        for status, returncode, accepted in [('Accepted', 0, True), ('In Progress', 0, False),
                                            ('Rejected', 0, False), ('Accepted', 1, False)]:
            response = subprocess.CompletedProcess([], returncode, stdout=json.dumps({'status': status, 'id': '12345678-1234-1234-1234-123456789abc'}))
            with self.subTest(status=status, returncode=returncode), \
                 patch.object(signing.subprocess, 'run', return_value=response):
                if accepted:
                    signing.notarize(Path('test.dmg'), 'key', 'keyid', 'issuer')
                else:
                    with self.assertRaises(ValueError):
                        signing.notarize(Path('test.dmg'), 'key', 'keyid', 'issuer')

    def test_gatekeeper_requires_notarized_developer_id(self):
        for source, accepted in [('Notarized Developer ID', True), ('Developer ID', False), ('Unnotarized Developer ID', False)]:
            with self.subTest(source=source), patch.object(signing.subprocess, 'run', return_value=
                    subprocess.CompletedProcess([], 0, stdout='', stderr=f'accepted\nsource={source}\n')) as invoke:
                if accepted:
                    signing.assess(Path('test.dmg'), 'open')
                    self.assertIn('context:primary-signature', invoke.call_args.args[0])
                else:
                    with self.assertRaises(ValueError):
                        signing.assess(Path('test.dmg'), 'open')

    def test_image_validation_mounts_readonly_and_detaches_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            volume = Path(temp)
            app = volume / 'Test.app'
            app.mkdir()
            (volume / 'Applications').symlink_to('/Applications')
            mounted = plistlib.dumps({'system-entities': [{'mount-point': str(volume)}]})
            with patch.object(signing, 'verify_developer_id'), \
                 patch.object(signing, 'macho_files', return_value={app / 'helper'}), \
                 patch.object(signing, 'run') as command, \
                 patch.object(signing.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout=mounted)) as mount, \
                 patch.object(signing, 'assess', side_effect=[None, ValueError('Gatekeeper failed')]):
                with self.assertRaises(ValueError):
                    signing.validate_image(Path('test.dmg'), app.name, signing.TEAM_ID)
                self.assertIn('-readonly', mount.call_args.args[0])
                self.assertEqual(command.call_args.args, ('hdiutil', 'detach', volume))

    def test_stapling_exhaustion_fails_without_validation(self):
        failed = subprocess.CompletedProcess([], 65, stdout='', stderr='Ticket not found')
        with patch.object(signing.subprocess, 'run', return_value=failed) as invoke, \
             patch.object(signing.time, 'sleep'), patch.object(signing, 'run') as validate:
            with self.assertRaises(ValueError) as failure:
                signing.staple(Path('test.dmg'))
        self.assertIn('stapler staple failed (exit 65)', str(failure.exception))
        self.assertIn('Ticket not found', str(failure.exception))
        self.assertEqual(invoke.call_count, 5)
        validate.assert_not_called()


class FinalizationChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        (self.project / 'build/Test.app').mkdir(parents=True)
        (self.project / 'dist').mkdir()
        self.image = self.project / 'dist/Test-arm64.dmg'
        self.sources = self.project / 'dist/Test-arm64-sources.tar.gz'
        self.checksum = self.project / 'dist/Test-arm64-SHA256SUMS.txt'
        self.sources.write_bytes(b'original source archive')
        self.checksum.write_text('stale checksum')
        plan = dict(app='build/Test.app', dmg=str(self.image.relative_to(self.project)),
                    sources=str(self.sources.relative_to(self.project)), checksum=str(self.checksum.relative_to(self.project)),
                    sourceSHA256=digest(self.sources), architecture='arm64', volumeName='Test')
        (self.project / 'build/release-plan.json').write_text(json.dumps(plan))

    def test_final_checksum_is_after_stapling_and_validation_and_failures_block_it(self):
        for failure in (None, 'signature', 'notarize', 'staple', 'validate'):
            self.checksum.write_text('stale checksum')
            events = []
            def event(name):
                def invoke(*args, **kwargs):
                    events.append(name)
                    if failure == name:
                        raise ValueError('simulated failure')
                    if name == 'create':
                        self.image.write_bytes(b'unsigned image')
                    if name == 'staple':
                        self.image.write_bytes(b'stapled final image')
                    return 'submission-id'
                return invoke
            with self.subTest(failure=failure), ExitStack() as stack:
                stack.enter_context(patch.object(signing, 'select_identity', return_value='A' * 40))
                stack.enter_context(patch.object(signing, 'macho_files', return_value=set()))
                for function, name in [('sign_app', 'signature'), ('create_dmg', 'create'),
                                       ('notarize', 'notarize'), ('staple', 'staple'), ('validate_image', 'validate')]:
                    stack.enter_context(patch.object(signing, function, side_effect=event(name)))
                stack.enter_context(patch.object(signing, 'verify_developer_id'))
                stack.enter_context(patch.object(signing, 'run', side_effect=event('sign-dmg')))
                smoke = stack.enter_context(patch.object(signing.subprocess, 'run'))
                stack.enter_context(patch.dict(os.environ, {'APPLE_API_PRIVATE_KEY': 'private-secret', 'DYLD_TEST': 'bad'}))
                if failure:
                    with self.assertRaises(ValueError):
                        signing.finalize(self.project, 'keychain', signing.TEAM_ID, 'secret', 'id', 'issuer')
                    self.assertFalse(self.checksum.exists())
                    self.assertFalse((self.project / 'build/release-validation.json').exists())
                else:
                    signing.finalize(self.project, 'keychain', signing.TEAM_ID, 'secret', 'id', 'issuer')
                    self.assertEqual(events, ['signature', 'create', 'sign-dmg', 'notarize', 'staple', 'validate'])
                    self.assertIn(digest(self.image), self.checksum.read_text())
                    self.assertNotIn('APPLE_API_PRIVATE_KEY', smoke.call_args.kwargs['env'])
                    self.assertNotIn('DYLD_TEST', smoke.call_args.kwargs['env'])
                    self.assertEqual(self.sources.read_bytes(), b'original source archive')

    def test_wrong_team_or_missing_notary_credentials_fail_closed(self):
        for team, key in [('WRONGTEAM1', 'key'), (signing.TEAM_ID, '')]:
            with self.subTest(team=team), patch.object(signing, 'sign_app') as sign:
                with self.assertRaises(ValueError):
                    signing.finalize(self.project, 'keychain', team, key, 'id', 'issuer')
                sign.assert_not_called()


class PreparedReleaseChecks(unittest.TestCase):
    def test_preparation_preserves_complete_sources_but_creates_no_dmg_or_checksums(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            build = project / 'build'
            build.mkdir()
            (project / 'docs').mkdir()
            for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'docs/REBUILDING.md', 'docs/RELINKING.md'):
                (project / name).write_text(name)
            app = build / 'Test.app'
            app.mkdir()
            dependency = project / 'dependency.tar.gz'
            freerdp = project / 'freerdp-commit.tar.gz'
            dependency.write_bytes(b'dependency source archive')
            freerdp.write_bytes(b'freerdp source archive')
            dist = project / 'dist'
            dist.mkdir()
            (dist / 'Test-1.0.0-macos-arm64.dmg').write_bytes(b'stale image')
            (dist / 'Test-1.0.0-macos-arm64-SHA256SUMS.txt').write_text('stale checksum')
            def archive_launcher(*args):
                destination = Path(args[args.index('-o') + 1])
                with tarfile.open(destination, 'w:gz') as archive:
                    archive.add(project / 'LICENSE', arcname='FreeRDP-Launcher/LICENSE')
            with patch.object(package, 'run', side_effect=archive_launcher), \
                 patch.object(package, 'output', return_value='a' * 40), \
                 patch.object(package, 'create_dmg') as image:
                package.create_release(project, {'appName': 'Test'}, build, app,
                                       {'dependency': (project, dependency)}, freerdp,
                                       {'architecture': 'arm64', 'ffmpeg': 'OFF'}, '1.0.0', prepare_release=True)
                image.assert_not_called()
            plan = json.loads((build / 'release-plan.json').read_text())
            sources = project / plan['sources']
            self.assertEqual(digest(sources), plan['sourceSHA256'])
            self.assertFalse((project / plan['dmg']).exists())
            self.assertFalse((project / plan['checksum']).exists())
            with tarfile.open(sources) as archive:
                names = {Path(name).name for name in archive.getnames()}
                self.assertTrue({'launcher.tar.gz', dependency.name, freerdp.name, 'build-manifest.json',
                                 'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'RELINKING.md', 'README.md'} <= names)


if __name__ == '__main__':
    unittest.main()
