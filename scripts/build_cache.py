# SPDX-License-Identifier: Apache-2.0
"""Reuse completed native builds while rebuilding the Swift launcher every time."""
import json
import os
from pathlib import Path

from sources import digest, output
from package import notice_paths


def native_key(project, dependencies, freerdp, arch, deployment, ffmpeg):
    overrides = {name: os.environ[name] for name in (
        'FREERDP_REPOSITORY', 'FREERDP_BRANCH', 'FREERDP_COMMIT',
        'FREERDP_SOURCE', 'FREERDP_ARCHIVE', 'FREERDP_ARCHIVE_SHA256'
    ) if name in os.environ}
    local = overrides.get('FREERDP_SOURCE')
    archive = overrides.get('FREERDP_ARCHIVE')
    source = None
    if archive:
        source = digest(Path(archive).resolve())
    elif local:
        checkout = (project / local).resolve()
        if output('git', '-C', checkout, 'status', '--porcelain', '--untracked-files=normal'):
            raise ValueError('FREERDP_SOURCE must be a clean checkout so its source archive matches the build')
        source = dict(path=str(checkout), commit=output('git', '-C', checkout, 'rev-parse', 'HEAD'),
                      repository=output('git', '-C', checkout, 'remote', 'get-url', 'origin'))
    return dict(architecture=arch, deployment=deployment, ffmpeg=ffmpeg,
                dependencies=dependencies, freerdp=freerdp, overrides=overrides, source=source,
                compiler=output('xcrun', 'clang', '--version'), sdk=output('xcrun', '--show-sdk-path'),
                recipes={name: digest(project / 'scripts' / name)
                         for name in ('build.py', 'sources.py', 'build_cache.py')})


def integrity(build, inputs, freerdp):
    paths = {path for path in (build / 'deps/local').rglob('*')
             if path.is_file() or path.is_symlink()}
    if 'ffmpeg' in inputs:
        paths.update((build / 'ffmpeg-build-config.h', build / 'deps/compile/ffmpeg/ffbuild/config.mak'))
    for source in [freerdp, *(source for source, _ in inputs.values())]:
        paths.update(notice_paths(source))
    return {os.path.relpath(path, build): dict(
                sha256=digest(path), link=os.readlink(path) if path.is_symlink() else None)
            for path in sorted(paths)}


def load_native_cache(build, key):
    try:
        data = json.loads((build / '.native-cache.json').read_text())
        if data['key'] != key:
            return None
        if not all((build / path).is_file() for path in data['files']):
            return None
        if not all((build / path).is_dir() for path in data['directories']):
            return None
        inputs = {name: tuple(build / path for path in paths) for name, paths in data['inputs'].items()}
        manifest = data['manifest']
        for name, (_, archive) in inputs.items():
            if digest(archive) != manifest['dependencies'][name]['sha256']:
                return None
        freerdp, archive = (build / path for path in data['freerdp'])
        if digest(archive) != manifest['freerdp']['sha256']:
            return None
        if integrity(build, inputs, freerdp) != data['integrity']:
            return None
        return inputs, freerdp, archive, manifest
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_native_cache(build, key, inputs, freerdp, archive, manifest):
    def relative(path):
        return os.path.relpath(path, build)

    # Record installed headers, libraries, symlink targets and the client. Missing
    # products trigger repair even if only part of the dependency prefix was removed.
    files = {relative(path) for path in (build / 'deps/local').rglob('*') if path.is_file()}
    directories = {relative(path) for path in (build / 'deps/local', build / 'deps/compile',
                                               build / 'freerdp', freerdp)}
    for source, source_archive in inputs.values():
        directories.add(relative(source))
        files.add(relative(source_archive))
    files.add(relative(archive))
    if manifest['ffmpeg'] == 'ON':
        files.update(('ffmpeg-build-config.h', 'deps/compile/ffmpeg/ffbuild/config.mak'))
    data = dict(key=key, inputs={name: [relative(path) for path in paths] for name, paths in inputs.items()},
                freerdp=[relative(freerdp), relative(archive)], manifest=manifest,
                files=sorted(files), directories=sorted(directories),
                integrity=integrity(build, inputs, freerdp))
    temporary = build / '.native-cache.tmp'
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(build / '.native-cache.json')
