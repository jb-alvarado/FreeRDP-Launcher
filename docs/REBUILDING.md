# Rebuilding the corresponding source

- Use a Mac with the same architecture, macOS deployment target and compatible Xcode tools.
- Install build tools manually: `xcode-select --install`; `brew install cmake ninja pkgconf`.
- Extract `launcher.tar.gz` from this source package.
- In `FreeRDP-Launcher`, create `build/cache` and copy the dependency `.tar.gz` archives there.
- Leave the `freerdp-<commit>.tar.gz` file beside this README.
- Read the matching `build-manifest.json` for exact source revisions and build commands.
- Run from `FreeRDP-Launcher` (the following needs no dependency downloads):

```sh
export FREERDP_ARCHIVE="$(find .. -maxdepth 1 -name 'freerdp-*.tar.gz' -print -quit)"
export FREERDP_COMMIT="$(python3 -c 'import json; print(json.load(open("../build-manifest.json"))["freerdp"]["commit"])')"
export FREERDP_ARCHIVE_SHA256="$(python3 -c 'import json; print(json.load(open("../build-manifest.json"))["freerdp"]["sha256"])')"
export WITH_FFMPEG="$(python3 -c 'import json; print(json.load(open("../build-manifest.json"))["ffmpeg"])')"
export MACOSX_DEPLOYMENT_TARGET="$(python3 -c 'import json; print(json.load(open("../build-manifest.json"))["deploymentTarget"])')"
./build.sh
```

- Output: `build/FreeRDP.app` and the `build/bin/sdl-freerdp` command-line wrapper.
- No upstream code is patched; `.source_version` records the exported FreeRDP commit.
- `ffmpeg-config.mak` and
  `ffmpeg-build-config.h` record FFmpeg's selected components and configuration.
- After changing a dependency's source, follow that project's build instructions and
  `RELINKING.md` to replace its shared libraries. Source-cache folders are reset by `build.sh`.
- Exact binary hashes are not promised across SDK/compiler versions or code-signing runs.
