# Replacing LGPL libraries

- FFmpeg is linked dynamically, using `@rpath` inside `FreeRDP.app/Contents/Frameworks`.
- Download the matching `*-sources.tar.gz` asset from the binary's GitHub release.
- The manifest, original FFmpeg sources and exact configure options are included.
- Build your modified FFmpeg as shared libraries for the same CPU, ABI and macOS target.
- Replace the matching `libavcodec`, `libavutil`, `libswscale` and `libswresample` dylibs in
  `FreeRDP.app/Contents/Frameworks`; retain their filenames and ABI-compatible install names.
- Set each replacement library's install ID to `@rpath/<filename>` with `install_name_tool`.
- Set its non-system dependency paths to `@rpath/<filename>` and add `@loader_path` as an rpath.
- Re-sign each replaced library locally: `codesign --force --sign - --timestamp=none <library>`.
- Re-sign the app: `codesign --force --sign - --timestamp=none FreeRDP.app`.
- Verify: `codesign --verify --deep --strict FreeRDP.app`.
- The app uses ad-hoc signing, without hardened runtime or library-validation restrictions.
- Modification and reverse engineering for debugging changes to LGPL components are permitted.
