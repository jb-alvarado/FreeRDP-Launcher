# SPDX-License-Identifier: Apache-2.0
"""Finalize a prepared release; never fall back to ad-hoc signing or skip Apple acceptance."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import time

from package import create_dmg, sign_app, verify_developer_id
from sources import digest, run

TEAM_ID = '758MCFP8H8'
MACHO = {bytes.fromhex(value) for value in (
    'feedface', 'cefaedfe', 'feedfacf', 'cffaedfe', 'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca')}


def safe_output(value, *secrets):
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    value = value or ''
    credentials = [*secrets, *(os.environ.get(name, '') for name in (
        'APPLE_CERT_P12_BASE64', 'APPLE_CERT_PASSWORD', 'APPLE_API_PRIVATE_KEY', 'APPLE_API_KEY_ID'))]
    # Also redact individual key lines if a tool echoes only part of a PEM key.
    fragments = {part for secret in credentials if secret for part in (secret, *secret.splitlines()) if part.strip()}
    for fragment in sorted(fragments, key=len, reverse=True):
        value = value.replace(fragment, '[redacted]')
    return value


def failure_details(operation, result, *secrets):
    details = '\n'.join(safe_output(value, *secrets).strip()
                        for value in (result.stdout, result.stderr) if value)
    return f'{operation} failed (exit {result.returncode})' + (f':\n{details}' if details else '')


def error_message(error):
    if isinstance(error, subprocess.CalledProcessError):
        # Include the tool name, never its arguments (which may contain credentials).
        command = error.cmd[0] if isinstance(error.cmd, (list, tuple)) else 'Command'
        return failure_details(Path(command).name, error)
    return safe_output(str(error)) if isinstance(error, ValueError) else type(error).__name__


def macho_files(app):
    result = set()
    for path in app.rglob('*'):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as handle:
                if handle.read(4) in MACHO:
                    result.add(path)
    if not result:
        raise ValueError('No Mach-O components found in prepared app')
    return result


def select_identity(keychain, team_id):
    response = subprocess.run(['security', 'find-identity', '-v', '-p', 'codesigning', str(keychain)],
                              check=True, capture_output=True, text=True).stdout
    matches = re.findall(r'\b([0-9A-Fa-f]{40}) "Developer ID Application: [^"\n]+ \(' +
                         re.escape(team_id) + r'\)"', response)
    if len(matches) != 1:
        raise ValueError('Expected exactly one valid Developer ID Application identity for the configured team')
    return matches[0]


def notarize(image, private_key, key_id, issuer):
    # The key exists only during Apple's submission/log retrieval, outside the repo.
    with tempfile.TemporaryDirectory(prefix='freerdp-notary-', dir=os.environ.get('RUNNER_TEMP')) as temp:
        key = Path(temp) / 'AuthKey.p8'
        descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(private_key)
        authentication = ['--key', str(key), '--key-id', key_id, '--issuer', issuer]
        result = subprocess.run(['xcrun', 'notarytool', 'submit', str(image), *authentication,
                                 '--wait', '--timeout', '30m', '--output-format', 'json'],
                                capture_output=True, text=True)
        try:
            response = json.loads(result.stdout)
        except ValueError:
            raise ValueError('Apple notarization did not return valid JSON; no artifacts will be uploaded.\n' +
                             failure_details('notarytool submit', result, private_key, key_id)) from None
        submission = response.get('id', '')
        if (result.returncode != 0 or response.get('status') != 'Accepted'
                or not re.fullmatch(r'[0-9a-fA-F-]{36}', submission)):
            if re.fullmatch(r'[0-9a-fA-F-]{36}', submission):
                log = subprocess.run(['xcrun', 'notarytool', 'log', submission, *authentication],
                                     capture_output=True, text=True)
                print(safe_output(log.stdout, private_key, key_id), flush=True)
                if log.returncode:
                    print(failure_details('notarytool log', log, private_key, key_id), file=sys.stderr)
            raise ValueError('Apple notarization was not Accepted; publication is blocked.\n' +
                             failure_details('notarytool submit', result, private_key, key_id))
        print(f'Apple notarization Accepted: {submission}', flush=True)
        return submission


def staple(image):
    # Accepted tickets can take a short time to propagate to Apple's CDN.
    for attempt in range(5):
        result = subprocess.run(['xcrun', 'stapler', 'staple', str(image)], capture_output=True, text=True)
        if result.returncode == 0:
            run('xcrun', 'stapler', 'validate', image)
            return
        if attempt < 4:
            time.sleep(10)
    raise ValueError('Stapling failed after Apple accepted the image; publication is blocked.\n' +
                     failure_details('stapler staple', result))


def assess(path, kind):
    command = ['spctl', '--assess', '--type', kind, '--verbose=2']
    if kind == 'open':
        command += ['--context', 'context:primary-signature']
    result = subprocess.run([*command, str(path)], check=True, capture_output=True, text=True)
    if 'source=Notarized Developer ID' not in result.stdout + result.stderr:
        raise ValueError(f'Gatekeeper did not recognize notarized Developer ID code: {path.name}')


def validate_image(image, app_name, team_id):
    verify_developer_id(image, team_id, runtime=False)
    run('hdiutil', 'verify', image)
    assess(image, 'open')
    mounted = subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-plist', str(image)],
                             check=True, capture_output=True)
    volumes = [Path(entry['mount-point']) for entry in plistlib.loads(mounted.stdout)['system-entities']
               if 'mount-point' in entry]
    try:
        if len(volumes) != 1:
            raise ValueError('Expected a single mounted release volume')
        app = volumes[0] / app_name
        link = volumes[0] / 'Applications'
        if not app.is_dir() or not link.is_symlink() or os.readlink(link) != '/Applications':
            raise ValueError('DMG must contain the app and Applications shortcut')
        run('codesign', '--verify', '--deep', '--strict', app)
        for binary in macho_files(app):
            verify_developer_id(binary, team_id)
        verify_developer_id(app, team_id)
        assess(app, 'execute')
    finally:
        for volume in volumes:
            run('hdiutil', 'detach', volume)


def write_checksums(image, sources, destination):
    destination.write_text(''.join(f'{digest(path)}  {path.name}\n' for path in (image, sources)))


def finalize(project, keychain, team_id, private_key, key_id, issuer):
    (project / 'build/release-validation.json').unlink(missing_ok=True)
    if team_id != TEAM_ID:
        raise ValueError(f'APPLE_TEAM_ID must be {TEAM_ID}')
    if not all((private_key, key_id, issuer)):
        raise ValueError('All Apple notarization credentials are required')
    plan = json.loads((project / 'build/release-plan.json').read_text())
    paths = {}
    for name in ('app', 'dmg', 'sources', 'checksum'):
        path = (project / plan[name]).resolve()
        if project.resolve() not in path.parents:
            raise ValueError('Release plan path escapes the project')
        paths[name] = path
    paths['checksum'].unlink(missing_ok=True)
    if digest(paths['sources']) != plan['sourceSHA256']:
        raise ValueError('Prepared source archive changed before release signing')
    identity = select_identity(keychain, team_id)
    app, image = paths['app'], paths['dmg']
    sign_app(app, macho_files(app), identity=identity, team_id=team_id, keychain=keychain)
    # Run the signed client's version check without forwarding Apple credentials.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('APPLE_', 'DYLD_', 'SIGNING_'))}
    subprocess.run([str(app / 'Contents/MacOS/sdl-freerdp'), '/version'],
                   check=True, timeout=30, env=environment)
    create_dmg(app, image, plan['volumeName'])
    run('codesign', '--force', '--sign', identity, '--keychain', keychain, '--timestamp', image)
    verify_developer_id(image, team_id, runtime=False)
    submission = notarize(image, private_key, key_id, issuer)
    staple(image)
    validate_image(image, app.name, team_id)
    # Stapling mutates the image; only the final validated bytes are hashed.
    if digest(paths['sources']) != plan['sourceSHA256']:
        raise ValueError('Source archive changed during release finalization')
    write_checksums(image, paths['sources'], paths['checksum'])
    receipt = dict(architecture=plan['architecture'], teamID=team_id, status='Accepted',
                   submissionID=submission, stapled=True, dmgSHA256=digest(image))
    (project / 'build/release-validation.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'Signed, notarized and stapled release: {image}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keychain', required=True)
    args = parser.parse_args()
    finalize(Path(__file__).resolve().parent.parent, args.keychain,
             os.environ.get('APPLE_TEAM_ID', ''), os.environ.get('APPLE_API_PRIVATE_KEY', ''),
             os.environ.get('APPLE_API_KEY_ID', ''), os.environ.get('APPLE_ISSUER_ID', ''))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'Release signing failed: {error_message(error)}', file=sys.stderr)
        sys.exit(1)
