# SPDX-License-Identifier: Apache-2.0
"""Bundle private dylibs, licenses, provenance and exact corresponding sources."""
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tarfile
import tempfile

from sources import digest, output, run

SYSTEM = ('/System/Library/', '/usr/lib/')


def dependencies(binary):
    lines = output('otool', '-L', binary).splitlines()[1:]
    libraries = [line.strip().split(' (compatibility version', 1)[0] for line in lines]
    if binary.suffix == '.dylib':
        identifier = output('otool', '-D', binary).splitlines()[-1]
        libraries = [library for library in libraries if library != identifier]
    return libraries


def rpaths(binary):
    return re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset', output('otool', '-l', binary))


def bundle_libraries(executables, prefix, frameworks):
    """Follow the dependency graph; reject accidental Homebrew/host libraries."""
    queue = list(executables)
    processed = set()
    while queue:
        binary = queue.pop(0)
        if binary in processed:
            continue
        processed.add(binary)
        for library in dependencies(binary):
            if library.startswith(SYSTEM):
                continue
            name = Path(library).name
            if library.startswith(('@rpath/', '@loader_path/')):
                candidate = prefix / 'lib' / name
            else:
                candidate = Path(library)
            resolved = candidate.resolve()
            if prefix.resolve() not in resolved.parents or not resolved.is_file():
                raise ValueError(f'Unapproved non-system dependency: {binary.name}: {library}')
            destination = frameworks / resolved.name
            if not destination.exists():
                shutil.copy2(resolved, destination)
                run('install_name_tool', '-id', '@rpath/' + destination.name, destination)
                queue.append(destination)
            run('install_name_tool', '-change', library, '@rpath/' + destination.name, binary)
        desired = '@loader_path' if binary.parent == frameworks else '@executable_path/../Frameworks'
        for path in rpaths(binary):
            if not path.startswith(SYSTEM) and path != desired:
                run('install_name_tool', '-delete_rpath', path, binary)
        if desired not in rpaths(binary):
            run('install_name_tool', '-add_rpath', desired, binary)
    # Check the final closure, including every transitive library.
    for binary in processed:
        for library in dependencies(binary):
            if library.startswith(SYSTEM):
                continue
            if not library.startswith('@rpath/') or not (frameworks / Path(library).name).is_file():
                raise ValueError(f'Unresolved bundled dependency: {binary.name}: {library}')
    return processed


def notice_paths(source):
    for path in sorted(source.rglob('*')):
        name = path.name.upper()
        if path.is_file() and not path.is_symlink() and (
            name.startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE')) or name in ('OFL.TXT', 'FTL.TXT')
            or any(part.upper() in ('LICENSES', 'LICENCES') for part in path.relative_to(source).parts[:-1])):
            yield path


def copy_notices(source, destination):
    files = []
    for path in notice_paths(source):
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        files.append(str(relative))
    if not files:
        raise ValueError(f'No license/notice files found: {source}')
    return files


def package(project, config, build, prefix, inputs, freerdp, freerdp_archive, manifest, release):
    install = build
    install.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.package-', dir=install))
    try:
        app = stage / (config['appName'] + '.app')
        contents = app / 'Contents'
        macos, resources, frameworks = (contents / name for name in ('MacOS', 'Resources', 'Frameworks'))
        macos.mkdir(parents=True)
        frameworks.mkdir()
        shutil.copytree(project / 'Resources', resources)
        client = macos / 'sdl-freerdp'
        launcher = macos / 'FreeRDPLauncher'
        shutil.copy2(prefix / 'bin/sdl-freerdp', client)
        shutil.copy2(build / 'freerdp/launcher/FreeRDPLauncher', launcher)
        binaries = bundle_libraries([client, launcher], prefix, frameworks)
        minimums = [manifest['deploymentTarget']]
        for binary in binaries:
            if output('lipo', '-archs', binary) != manifest['architecture']:
                raise ValueError(f'Unexpected architecture: {binary}')
            minimums += re.findall(r'\bminos (\d+(?:\.\d+)+)', output('otool', '-l', binary))
        minimum = max(minimums, key=lambda v: tuple(map(int, v.split('.'))))
        if tuple(map(int, minimum.split('.'))) > tuple(map(int, manifest['deploymentTarget'].split('.'))):
            raise ValueError(f'A library requires macOS {minimum}, exceeding the requested deployment target')
        manifest['libraries'] = sorted(path.name for path in frameworks.iterdir())
        manifest['notices'] = {}
        licenses = resources / 'Licenses'
        for name, (source, _) in inputs.items():
            manifest['notices'][name] = copy_notices(source, licenses / name)
        manifest['notices']['freerdp'] = copy_notices(freerdp, licenses / 'freerdp')
        shutil.copy2(project / 'LICENSE', resources / 'LICENSE')
        shutil.copy2(project / 'NOTICE', resources / 'NOTICE')
        shutil.copy2(project / 'THIRD_PARTY_NOTICES.md', resources)
        shutil.copy2(project / 'docs/RELINKING.md', resources)
        if release:
            manifest['launcherCommit'] = output('git', '-C', project, 'rev-parse', 'HEAD')
        (resources / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        copyright_text = ('FreeRDP Launcher and FreeRDP: Apache-2.0. SDL: zlib license. '
                          'Portions of this software are copyright © 2026 The FreeType Project '
                          '(https://freetype.org). All rights reserved. '
                          'Third-party licenses and source details are included in Resources.')
        if manifest['ffmpeg'] == 'ON':
            copyright_text += ' This software uses libraries from the FFmpeg project under the LGPLv2.1 or later.'
        version = os.environ.get('RELEASE_VERSION', config['version']).removeprefix('v')
        match = re.fullmatch(r'(\d+\.\d+\.\d+)(?:-[A-Za-z0-9.-]+)?', version)
        if not match:
            raise ValueError('RELEASE_VERSION must be a semantic version, such as v1.0.0')
        info = dict(CFBundleExecutable='FreeRDPLauncher', CFBundleName=config['appName'],
                    CFBundleDisplayName=config['appName'], CFBundleIdentifier=config['bundleIdentifier'],
                    CFBundlePackageType='APPL', CFBundleIconFile='FreeRDP.icns',
                    CFBundleShortVersionString=match[1], CFBundleVersion=os.environ.get('BUILD_NUMBER', '1'),
                    CFBundleDevelopmentRegion='en', CFBundleLocalizations=['en', 'de'],
                    NSHighResolutionCapable=True, LSMinimumSystemVersion=minimum,
                    LSApplicationCategoryType='public.app-category.utilities',
                    NSHumanReadableCopyright=copyright_text,
                    NSLocalNetworkUsageDescription='FreeRDP connects to remote desktops on your local network.',
                    NSMicrophoneUsageDescription='FreeRDP can redirect your microphone to a remote desktop.',
                    NSCameraUsageDescription='FreeRDP can redirect your camera to a remote desktop.')
        with (contents / 'Info.plist').open('wb') as handle:
            plistlib.dump(info, handle)
        (contents / 'PkgInfo').write_bytes(b'APPL????')
        for binary in sorted(binaries):
            run('codesign', '--force', '--sign', '-', '--timestamp=none', binary)
        run('codesign', '--force', '--sign', '-', '--timestamp=none', app)
        run('codesign', '--verify', '--deep', '--strict', app)
        run('plutil', '-lint', contents / 'Info.plist')
        # CLI-only smoke check: no window, server connection, credentials or user preferences.
        environment = os.environ.copy()
        for key in list(environment):
            if key.startswith('DYLD_'):
                del environment[key]
        subprocess.run([str(client), '/version'], env=environment, check=True, timeout=30)
        destination = install / app.name
        if destination.exists():
            shutil.rmtree(destination)
        app.rename(destination)
        cli = install / 'bin'
        cli.mkdir(exist_ok=True)
        shim = cli / 'sdl-freerdp'
        shim.write_text('#!/bin/bash\nset -euo pipefail\n'
                        'HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"\n'
                        f'exec "$HERE/../{destination.name}/Contents/MacOS/sdl-freerdp" "$@"\n')
        shim.chmod(0o755)
        # Retire the previous output paths only after the new bundle is verified.
        legacy = build / 'sdl3'
        if (legacy / app.name).is_dir():
            shutil.rmtree(legacy / app.name)
        (legacy / 'bin/sdl-freerdp').unlink(missing_ok=True)
        for directory in (legacy / 'bin', legacy):
            try:
                directory.rmdir()
            except OSError:
                pass  # Preserve any unrelated files in the old output directory.
        print(f'App created: {destination}\nMinimum macOS: {minimum}', flush=True)
        if release:
            create_release(project, config, build, destination, inputs, freerdp_archive, manifest, version)
    finally:
        shutil.rmtree(stage)


def create_dmg(app, destination, volume_name):
    """Create a compressed, read-only disk image with drag-to-Applications install."""
    stage = Path(tempfile.mkdtemp(prefix='.dmg-', dir=destination.parent))
    try:
        contents = stage / 'contents'
        contents.mkdir()
        run('ditto', app, contents / app.name)
        (contents / 'Applications').symlink_to('/Applications')
        image = stage / 'release.dmg'
        run('hdiutil', 'create', '-volname', volume_name, '-srcfolder', contents,
            '-format', 'UDZO', '-fs', 'HFS+', image)
        run('hdiutil', 'verify', image)
        image.replace(destination)
    finally:
        shutil.rmtree(stage)


def create_release(project, config, build, app, inputs, freerdp_archive, manifest, version):
    dist = project / 'dist'
    dist.mkdir(exist_ok=True)
    name = f'{config["appName"]}-{version}-macos-{manifest["architecture"]}'
    stage = Path(tempfile.mkdtemp(prefix='.release-', dir=dist))
    try:
        sources = stage / 'sources'
        sources.mkdir()
        for _, archive in inputs.values():
            shutil.copy2(archive, sources)
        shutil.copy2(freerdp_archive, sources)
        launcher_archive = sources / 'launcher.tar.gz'
        run('git', '-C', project, 'archive', '--format=tar.gz', '--prefix=FreeRDP-Launcher/',
            '-o', launcher_archive, 'HEAD')
        manifest['launcherCommit'] = output('git', '-C', project, 'rev-parse', 'HEAD')
        (sources / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        if manifest['ffmpeg'] == 'ON':
            shutil.copy2(build / 'deps/compile/ffmpeg/ffbuild/config.mak', sources / 'ffmpeg-config.mak')
            shutil.copy2(build / 'ffmpeg-build-config.h', sources)
        shutil.copy2(project / 'docs/REBUILDING.md', sources / 'README.md')
        shutil.copy2(project / 'docs/RELINKING.md', sources)
        shutil.copy2(project / 'THIRD_PARTY_NOTICES.md', sources)
        shutil.copy2(project / 'LICENSE', sources)
        shutil.copy2(project / 'NOTICE', sources)
        source_tar = dist / (name + '-sources.tar.gz')
        with tarfile.open(source_tar, 'w:gz') as target:
            target.add(sources, arcname=name + '-sources')
        disk_image = dist / (name + '.dmg')
        create_dmg(app, disk_image, config['appName'] + ' ' + version)
        artifacts = [disk_image, source_tar]
        checksum = dist / (name + '-SHA256SUMS.txt')
        checksum.write_text(''.join(f'{digest(path)}  {path.name}\n' for path in artifacts))
        for path in [*artifacts, checksum]:
            print(f'Release artifact: {path}', flush=True)
    finally:
        shutil.rmtree(stage)
