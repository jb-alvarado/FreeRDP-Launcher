# SPDX-License-Identifier: Apache-2.0
"""Single build pipeline: verified sources, private prefix, relocatable application."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

from sources import download, freerdp_source, load_environment, output, run
from build_cache import load_native_cache, native_key, save_native_cache

PROJECT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', action='store_true', help='also create binary and corresponding-source release archives')
    parser.add_argument('--prepare-release', action='store_true', help='prepare app and sources for mandatory Developer ID signing and notarization')
    parser.add_argument('--rebuild-native', action='store_true', help='rebuild dependencies and refresh the FreeRDP source')
    args = parser.parse_args()
    if args.prepare_release:
        args.release = True
    load_environment(PROJECT / '.env')
    config = json.loads((PROJECT / 'build-config.json').read_text())
    arch = os.environ.get('ARCH', platform.machine())
    if arch not in ('arm64', 'x86_64') or arch != platform.machine():
        raise ValueError('Use a native arm64 or x86_64 machine; cross-compilation is not supported')
    deployment = os.environ.get('MACOSX_DEPLOYMENT_TARGET', config['deploymentTarget'])
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', deployment) or tuple(map(int, deployment.split('.'))) < (13, 0):
        raise ValueError('MACOSX_DEPLOYMENT_TARGET must be 13.0 or newer')
    jobs = int(os.environ.get('JOBS', min(os.cpu_count() or 4, 8)))
    if jobs < 1:
        raise ValueError('JOBS must be positive')
    ffmpeg = os.environ.get('WITH_FFMPEG', 'ON').upper()
    if ffmpeg not in ('ON', 'OFF'):
        raise ValueError('WITH_FFMPEG must be ON or OFF')
    if args.release and not re.fullmatch(r'v?\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', os.environ.get('RELEASE_VERSION', config['version'])):
        raise ValueError('RELEASE_VERSION must be a semantic version such as v1.0.0')
    if args.release and output('git', '-C', PROJECT, 'status', '--porcelain'):
        raise ValueError('Release packaging requires a clean launcher checkout; commit changes first')
    build = PROJECT / 'build'
    cache, sources = build / 'cache', build / 'sources'
    for directory in (cache, sources):
        directory.mkdir(parents=True, exist_ok=True)
    if args.prepare_release:
        (build / 'release-plan.json').unlink(missing_ok=True)
        (build / 'release-validation.json').unlink(missing_ok=True)
    selected = {key: value for key, value in config['dependencies'].items() if key != 'ffmpeg' or ffmpeg == 'ON'}
    key = native_key(PROJECT, selected, config['freerdp'], arch, deployment, ffmpeg)
    cached = None if args.rebuild_native else load_native_cache(build, key)
    if cached is None:
        # Invalidate before rebuilding, so interrupted builds cannot look complete.
        (build / '.native-cache.json').unlink(missing_ok=True)
        inputs, freerdp, freerdp_archive, manifest = build_native(
            config, build, cache, sources, selected, arch, deployment, ffmpeg, jobs, key)
        save_native_cache(build, key, inputs, freerdp, freerdp_archive, manifest)
    else:
        inputs, freerdp, freerdp_archive, manifest = cached
        print('Reusing built dependencies and FreeRDP; rebuilding launcher only.', flush=True)
    prefix = build / 'deps/local'
    launcher = build / 'freerdp/launcher'
    launcher.mkdir(exist_ok=True)
    swift_sources = sorted((PROJECT / 'Sources/FreeRDPLauncher').glob('*.swift'))
    command = ['xcrun', 'swiftc', '-parse-as-library', '-O', '-warnings-as-errors',
               '-target', f'{arch}-apple-macos{deployment}', '-module-cache-path', launcher / 'module-cache',
               '-framework', 'SwiftUI', '-framework', 'AppKit', '-framework', 'Security',
               *swift_sources, '-o', launcher / 'FreeRDPLauncher']
    run(*command, cwd=PROJECT)
    manifest['commands'].append(dict(argv=list(map(str, command)), cwd=str(PROJECT)))
    # Keep the actual source inputs and commands available for packaging/rebuilding.
    (build / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    from package import package
    package(PROJECT, config, build, prefix, inputs, freerdp, freerdp_archive, manifest, args.release, prepare_release=args.prepare_release)


def build_native(config, build, cache, sources, selected, arch, deployment, ffmpeg, jobs, key):
    inputs = {key: download(key, value, cache, sources) for key, value in selected.items()}
    freerdp, freerdp_archive, freerdp_info = freerdp_source(config['freerdp'], PROJECT, cache, sources)
    # Source archives cannot discover their revision from a parent project's Git tree.
    (freerdp / '.source_version').write_text(freerdp_info['commit'] + '\n')
    # Build flags, libraries and source revisions cannot bleed between configurations.
    state = dict(key=key, freerdp=freerdp_info)
    state_file = build / '.configuration.json'
    # This function only runs after a cache miss or an explicit rebuild. Start
    # clean even when settings match: an edited/extra installed library must not
    # survive an incremental install and become trusted by the new cache record.
    for name in ('deps', 'freerdp', 'sdl3'):
        shutil.rmtree(build / name, ignore_errors=True)
    state_file.write_text(json.dumps(state, indent=2) + '\n')
    prefix = build / 'deps/local'
    objects = build / 'deps/compile'
    prefix.mkdir(parents=True, exist_ok=True)
    objects.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # Only our source-built libraries and macOS SDK libraries are eligible.
    for key in ('CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'LIBRARY_PATH',
                'DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH', 'CMAKE_PREFIX_PATH'):
        env.pop(key, None)
    env.update(CC=output('xcrun', '--find', 'clang'), CXX=output('xcrun', '--find', 'clang++'),
               SDKROOT=output('xcrun', '--show-sdk-path'), MACOSX_DEPLOYMENT_TARGET=deployment,
               CFLAGS=f'-O2 -arch {arch} -mmacosx-version-min={deployment}',
               CXXFLAGS=f'-O2 -arch {arch} -mmacosx-version-min={deployment}',
               LDFLAGS=f'-arch {arch} -mmacosx-version-min={deployment}',
               PKG_CONFIG_PATH=str(prefix / 'lib/pkgconfig'), PKG_CONFIG_LIBDIR=str(prefix / 'lib/pkgconfig'))
    commands = []

    def execute(*command, cwd=None):
        commands.append(dict(argv=list(map(str, command)), cwd=str(cwd or PROJECT)))
        return run(*command, cwd=cwd or PROJECT, env=env)

    common = ['-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release', f'-DCMAKE_OSX_ARCHITECTURES={arch}',
              f'-DCMAKE_OSX_DEPLOYMENT_TARGET={deployment}', f'-DCMAKE_INSTALL_PREFIX={prefix}',
              f'-DCMAKE_PREFIX_PATH={prefix}', '-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local',
              '-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF', '-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF',
              '-DCMAKE_INSTALL_LIBDIR=lib', '-DBUILD_SHARED_LIBS=ON']

    def cmake(name, *options, source=None, directory=None):
        directory = directory or objects / name
        execute('cmake', '-S', source or inputs[name][0], '-B', directory, *common, *options)
        execute('cmake', '--build', directory, '--parallel', jobs)
        execute('cmake', '--install', directory)

    cmake('zlib', '-DZLIB_BUILD_SHARED=ON', '-DZLIB_BUILD_STATIC=OFF', '-DZLIB_BUILD_TESTING=OFF')
    ssl_build = objects / 'openssl'
    ssl_build.mkdir(exist_ok=True)
    execute('perl', inputs['openssl'][0] / 'Configure',
            'darwin64-arm64-cc' if arch == 'arm64' else 'darwin64-x86_64-cc',
            f'--prefix={prefix}', '--libdir=lib', 'shared', 'no-tests', 'no-apps',
            'no-docs', 'no-module', cwd=ssl_build)
    execute('make', f'-j{jobs}', cwd=ssl_build)
    execute('make', 'install_sw', cwd=ssl_build)
    cmake('freetype', '-DFT_DISABLE_ZLIB=ON', '-DFT_DISABLE_BZIP2=ON', '-DFT_DISABLE_PNG=ON',
          '-DFT_DISABLE_BROTLI=ON', '-DFT_DISABLE_HARFBUZZ=ON')
    cmake('harfbuzz', '-DHB_HAVE_FREETYPE=ON', '-DHB_HAVE_CORETEXT=OFF',
          '-DHB_HAVE_CAIRO=OFF', '-DHB_HAVE_GLIB=OFF', '-DHB_HAVE_ICU=OFF',
          '-DHB_HAVE_GRAPHITE2=OFF', '-DHB_BUILD_UTILS=OFF', '-DHB_BUILD_SUBSET=OFF',
          '-DHB_BUILD_RASTER=OFF', '-DHB_BUILD_VECTOR=OFF', '-DHB_BUILD_GPU=OFF',
          '-DHB_BUILD_GPU_DEMO=OFF')
    cmake('sdl', '-DSDL_SHARED=ON', '-DSDL_STATIC=OFF', '-DSDL_TEST_LIBRARY=OFF',
          '-DSDL_TESTS=OFF', '-DSDL_EXAMPLES=OFF', '-DSDL_X11=OFF')
    cmake('sdlTtf', '-DSDLTTF_VENDORED=OFF', '-DSDLTTF_HARFBUZZ=ON', '-DSDLTTF_PLUTOSVG=OFF',
          '-DSDLTTF_SAMPLES=OFF', '-DSDLTTF_STRICT=ON')
    if ffmpeg == 'ON':
        ff_build = objects / 'ffmpeg'
        ff_build.mkdir(exist_ok=True)
        execute(inputs['ffmpeg'][0] / 'configure', f'--prefix={prefix}', f'--arch={arch}',
                '--target-os=darwin', f'--cc={env["CC"]}', f'--cxx={env["CXX"]}',
                f'--extra-cflags={env["CFLAGS"]}', f'--extra-ldflags={env["LDFLAGS"]}',
                '--enable-shared', '--disable-static', '--disable-autodetect', '--disable-everything',
                '--disable-gpl', '--disable-nonfree', '--disable-version3', '--disable-programs',
                '--disable-doc', '--disable-network', '--disable-avdevice', '--disable-avformat',
                '--disable-avfilter', '--disable-x86asm',
                '--enable-avcodec', '--enable-avutil', '--enable-swscale', '--enable-swresample',
                '--enable-decoder=h264,aac,mp3,opus,pcm_s16le,pcm_u16le,pcm_u8,pcm_alaw,pcm_mulaw,adpcm_ms,adpcm_ima_wav',
                '--enable-encoder=aac,pcm_s16le,pcm_u16le,pcm_u8,pcm_alaw,pcm_mulaw,adpcm_ms,adpcm_ima_wav',
                '--enable-parser=h264', cwd=ff_build)
        execute('make', f'-j{jobs}', cwd=ff_build)
        execute('make', 'install', cwd=ff_build)
        header = (ff_build / 'config.h').read_text()
        for flag in ('GPL', 'GPLV3', 'NONFREE', 'VERSION3'):
            if re.search(rf'^#define CONFIG_{flag} 1$', header, re.M):
                raise ValueError(f'Unexpected FFmpeg license option: {flag}')
        (build / 'ffmpeg-build-config.h').write_text(header)
    elif not (freerdp / 'libfreerdp/codec/h264_videotoolbox.c').exists():
        raise ValueError('WITH_FFMPEG=OFF needs a FreeRDP revision with native Apple VideoToolbox support')
    options = ['-DUSE_VERSION_FROM_GIT_TAG=OFF', f'-DWITH_FFMPEG={ffmpeg}', f'-DWITH_DSP_FFMPEG={ffmpeg}',
               f'-DWITH_VIDEO_FFMPEG={ffmpeg}', f'-DWITH_SWSCALE={ffmpeg}',
               '-DWITH_FFMPEG_HWACCEL=OFF', '-DOPENSSL_USE_STATIC_LIBS=OFF', f'-DOPENSSL_ROOT_DIR={prefix}',
               '-DZLIB_ROOT=' + str(prefix), '-DWITH_CLIENT_MAC=OFF', '-DWITH_CLIENT_SDL=ON',
               '-DWITH_CLIENT_SDL2=OFF', '-DWITH_CLIENT_SDL3=ON', '-DWITH_CLIENT_SDL_VERSIONED=OFF',
               '-DWITH_SDL_LINK_SHARED=ON', '-DWITH_SDL_IMAGE_DIALOGS=OFF', '-DSDL_USE_COMPILED_RESOURCES=ON',
               '-DWITH_VERBOSE_WINPR_ASSERT=ON',
               '-DBUILD_TESTING=OFF', '-DWITH_X11=OFF', '-DWITH_SERVER=OFF', '-DWITH_SAMPLE=OFF',
               '-DWITH_CAIRO=OFF', '-DWITH_KRB5=OFF', '-DWITH_JSON_DISABLED=ON', '-DWITH_WINPR_JSON=OFF',
               '-DWITH_AAD=OFF', '-DCHANNEL_URBDRC=OFF', '-DWITH_MANPAGES=OFF', '-DWITH_WINPR_TOOLS=OFF',
               '-DWITH_URIPARSER=OFF', '-DWITH_PKCS11=OFF', '-DWITH_TIMEZONE_ICU=OFF', '-DWITH_JPEG=OFF',
               '-DWITH_OPENH264=OFF', '-DWITH_GSM=OFF', '-DWITH_LAME=OFF', '-DWITH_FAAD2=OFF',
               '-DWITH_FAAC=OFF', '-DWITH_FDK_AAC=OFF', '-DWITH_SOXR=OFF', '-DWITH_AOM=OFF',
               '-DWITH_DAV1D=OFF', '-DWITH_YUV=OFF', '-DWITH_OPUS=OFF', '-DWITH_CUPS=OFF',
               '-DWITH_CLIENT_SDL_IMAGE=OFF', '-DWITH_CCACHE=OFF', '-DWITH_CLANG_FORMAT=OFF']
    cmake('freerdp', *options, source=freerdp, directory=build / 'freerdp')
    manifest = dict(architecture=arch, deploymentTarget=deployment, ffmpeg=ffmpeg,
                    dependencies=selected, freerdp=freerdp_info, commands=commands,
                    compiler=output('xcrun', 'clang', '--version'), sdk=env['SDKROOT'])
    return inputs, freerdp, freerdp_archive, manifest



if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'Build failed: {error}', file=sys.stderr)
        sys.exit(1)
