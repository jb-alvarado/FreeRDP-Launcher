# SPDX-License-Identifier: Apache-2.0
"""Verified source downloads and safe archive extraction; no package installation."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import urllib.parse


def run(*arguments, **kwargs):
    print('+', ' '.join(map(str, arguments)), flush=True)
    return subprocess.run(list(map(str, arguments)), check=True, **kwargs)


def output(*arguments, **kwargs):
    return subprocess.check_output(list(map(str, arguments)), text=True, **kwargs).strip()


def load_environment(path):
    """Read literal dotenv values without evaluating shell commands or substitutions."""
    if not path.exists():
        return
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            raise ValueError(f'{path}:{number}: expected KEY=value')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'{path}:{number}: unmatched quote')
            value = value[1:-1]
        os.environ.setdefault(key, value)


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def extract(archive, destination):
    """Strip one top directory; reject traversal, devices and escaping link targets."""
    with tarfile.open(archive) as source:
        members = source.getmembers()
        roots = {Path(item.name).parts[0] for item in members if Path(item.name).parts}
        if len(roots) != 1:
            raise ValueError(f'Expected one archive root: {archive}')
        root = next(iter(roots))
        links = {Path(item.name) for item in members if item.issym() or item.islnk()}
        regular = {Path(item.name) for item in members if item.isfile()}
        # Never write through an archive-defined link, including forward references.
        # Checking only resolve() before extraction misses links not yet on disk.
        for item in members:
            path = Path(item.name)
            if any(parent in links for parent in path.parents):
                raise ValueError(f'Archive entry beneath a link: {item.name}')
            if item.islnk() and Path(item.linkname) not in regular:
                raise ValueError(f'Hard link must target a regular archive file: {item.name}')
            if path.is_absolute() or '..' in path.parts:
                raise ValueError(f'Unsafe archive path: {item.name}')
            resolved = (destination.parent / path).resolve()
            boundary = (destination.parent / root).resolve()
            if resolved != boundary and boundary not in resolved.parents:
                raise ValueError(f'Unsafe archive path: {item.name}')
            if item.issym() or item.islnk():
                target = (resolved.parent if item.issym() else destination.parent) / item.linkname
                target = target.resolve()
                if target != boundary and boundary not in target.parents:
                    raise ValueError(f'Unsafe archive link: {item.name}')
            elif not (item.isfile() or item.isdir()):
                raise ValueError(f'Unsupported archive entry: {item.name}')
        # Resolve again as links are created, so earlier links cannot redirect later
        # entries or link targets outside the source root.
        for item in members:
            resolved = (destination.parent / item.name).resolve()
            if resolved != boundary and boundary not in resolved.parents:
                raise ValueError(f'Unsafe archive path: {item.name}')
            if item.issym() or item.islnk():
                target = (resolved.parent if item.issym() else destination.parent) / item.linkname
                target = target.resolve()
                if target != boundary and boundary not in target.parents:
                    raise ValueError(f'Unsafe archive link: {item.name}')
            source.extract(item, destination.parent)
        # Forward symlink chains must also resolve within the final source tree.
        for link in links:
            target = (destination.parent / link).resolve()
            if target != boundary and boundary not in target.parents:
                raise ValueError(f'Unsafe final archive link: {link}')
        extracted = destination.parent / root
        if extracted != destination:
            extracted.rename(destination)


def download(name, specification, cache, sources):
    archive = cache / f'{name}-{specification["version"]}.tar.gz'
    if not archive.exists():
        temporary = archive.with_suffix('.download')
        try:
            print(f'Downloading {name} {specification["version"]}', flush=True)
            with urllib.request.urlopen(specification['url'], timeout=90) as response, temporary.open('wb') as handle:
                shutil.copyfileobj(response, handle)
            if digest(temporary) != specification['sha256']:
                raise ValueError(f'Checksum mismatch: {name}')
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    if digest(archive) != specification['sha256']:
        raise ValueError(f'Checksum mismatch: {archive}; remove the archive and retry')
    destination = sources / f'{name}-{specification["version"]}'
    # Extract pristine sources on every build. Build products live elsewhere, so
    # edited caches cannot silently diverge from the corresponding source archive.
    if destination.exists():
        shutil.rmtree(destination)
    stage = Path(tempfile.mkdtemp(prefix='.extract-', dir=sources))
    try:
        extract(archive, stage / destination.name)
        (stage / destination.name).rename(destination)
    finally:
        shutil.rmtree(stage)
    return destination, archive


def freerdp_source(config, project, cache, sources):
    repository = os.environ.get('FREERDP_REPOSITORY', config['repository'])
    branch = os.environ.get('FREERDP_BRANCH', config['branch'])
    if urllib.parse.urlparse(repository).scheme in ('http', 'https') and urllib.parse.urlparse(repository).username:
        raise ValueError('Use Git credential helpers or SSH, not credentials embedded in FREERDP_REPOSITORY')
    overridden = 'FREERDP_REPOSITORY' in os.environ or 'FREERDP_BRANCH' in os.environ
    commit = os.environ.get('FREERDP_COMMIT', '' if overridden else config['commit'])
    supplied_archive = os.environ.get('FREERDP_ARCHIVE')
    if supplied_archive:
        archive = Path(supplied_archive).resolve()
        expected = os.environ.get('FREERDP_ARCHIVE_SHA256', '')
        if not expected or digest(archive) != expected or not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise ValueError('FREERDP_ARCHIVE requires its SHA-256 and exact FREERDP_COMMIT from the release manifest')
        destination = sources / f'freerdp-{commit}'
        if destination.exists():
            shutil.rmtree(destination)
        stage = Path(tempfile.mkdtemp(prefix='.freerdp-', dir=sources))
        try:
            extract(archive, stage / destination.name)
            (stage / destination.name).rename(destination)
        finally:
            shutil.rmtree(stage)
        return destination, archive, dict(repository=repository, branch=branch, commit=commit,
                                          sha256=expected, license='Apache-2.0')
    local = os.environ.get('FREERDP_SOURCE')
    if local:
        checkout = (project / local).resolve()
        if output('git', '-C', checkout, 'status', '--porcelain', '--untracked-files=normal'):
            raise ValueError('FREERDP_SOURCE must be a clean checkout so its source archive matches the build')
        repository = output('git', '-C', checkout, 'remote', 'get-url', 'origin')
        resolved = output('git', '-C', checkout, 'rev-parse', 'HEAD')
    else:
        checkout = cache / 'freerdp-git'
        if not checkout.exists():
            run('git', 'init', checkout)
        # Fetch into a private cache, never switch or reset the user's checkout.
        run('git', '-C', checkout, 'fetch', '--depth=1', repository, commit or branch)
        resolved = output('git', '-C', checkout, 'rev-parse', 'FETCH_HEAD')
    if output('git', '-C', checkout, 'ls-tree', '-r', resolved).find('160000 commit ') >= 0:
        raise ValueError('FreeRDP submodules require explicit source packaging support')
    archive = cache / f'freerdp-{resolved}.tar.gz'
    # Always regenerate from Git objects; ignored files never influence this build.
    run('git', '-C', checkout, 'archive', '--format=tar.gz', '--prefix=freerdp/',
        '-o', archive, resolved)
    destination = sources / f'freerdp-{resolved}'
    if destination.exists():
        shutil.rmtree(destination)
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix='.freerdp-', dir=sources))
        try:
            extract(archive, stage / destination.name)
            (stage / destination.name).rename(destination)
        finally:
            shutil.rmtree(stage)
    return destination, archive, dict(repository=repository, branch=branch, commit=resolved,
                                      sha256=digest(archive), license='Apache-2.0')
