# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import plistlib
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from package import sign_app


class SigningChecks(unittest.TestCase):
    def test_nested_code_signed_before_bundle_and_main_not_signed_separately(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp) / 'Test.app'
            contents = app / 'Contents'
            contents.mkdir(parents=True)
            (contents / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': 'FreeRDPLauncher'}))
            main = contents / 'MacOS/FreeRDPLauncher'
            client = contents / 'MacOS/sdl-freerdp'
            library = contents / 'Frameworks/test.dylib'
            with patch('package.run') as run:
                sign_app(app, {main, client, library})
            targets = [call.args[-1] for call in run.call_args_list]
            self.assertEqual(targets, [library, client, app, app])
            self.assertEqual(run.call_args_list[-1].args, ('codesign', '--verify', '--deep', '--strict', app))

    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun') and shutil.which('codesign'),
                         'requires macOS signing tools')
    def test_sign_bundle_with_initially_unsigned_helper(self):
        for architecture in dict.fromkeys((platform.machine(), 'x86_64')):
            with self.subTest(architecture=architecture):
                self.check_unsigned_bundle(architecture)

    def check_unsigned_bundle(self, architecture):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = root / 'Test.app'
            macos = app / 'Contents/MacOS'
            macos.mkdir(parents=True)
            main = macos / 'FreeRDPLauncher'
            client = macos / 'sdl-freerdp'
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleExecutable': main.name, 'CFBundleIdentifier': 'org.example.signing-test',
                'CFBundlePackageType': 'APPL'}))
            source = root / 'main.c'
            source.write_text('int main(void) { return 0; }\n')
            subprocess.run(['xcrun', 'clang', '-arch', architecture, '-Wl,-no_adhoc_codesign', str(source), '-o', str(main)],
                           check=True, capture_output=True)
            shutil.copy2(main, client)
            unsigned = subprocess.run(['codesign', '--verify', str(client)], capture_output=True)
            self.assertNotEqual(unsigned.returncode, 0)
            # Reproduce the CI failure: signing the main executable first asks
            # codesign to validate the still-unsigned sibling helper.
            wrong_order = subprocess.run(
                ['codesign', '--force', '--sign', '-', '--timestamp=none', str(main)],
                capture_output=True, text=True)
            self.assertNotEqual(wrong_order.returncode, 0)
            self.assertIn('sdl-freerdp', wrong_order.stderr)
            sign_app(app, {main, client})
            subprocess.run(['codesign', '--verify', '--strict', str(client)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
