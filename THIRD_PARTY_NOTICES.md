# Third-party notices

FreeRDP Launcher is distributed under Apache-2.0. Dependencies retain their own licenses;
this license does not relicense FFmpeg, fonts or other third-party code.

| Component | Applicable license | Project |
| --- | --- | --- |
| FreeRDP, WinPR and FreeRDP icon | Apache-2.0, with bundled notices retained | https://www.freerdp.com/ |
| OpenSSL 3.x | Apache-2.0 | https://openssl-library.org/ |
| SDL3 and SDL_ttf | zlib | https://libsdl.org/ |
| zlib | zlib | https://zlib.net/ |
| FreeType | FreeType License (FTL), selected instead of its GPL alternative | https://freetype.org/ |
| HarfBuzz | MIT-style licenses; see COPYING | https://harfbuzz.github.io/ |
| Open Sans fonts embedded by FreeRDP | SIL Open Font License 1.1 | https://github.com/googlefonts/opensans |
| FFmpeg, when enabled | LGPL-2.1-or-later, with MIT/BSD notices retained | https://ffmpeg.org/ |

Portions of this software are copyright © 2026 The FreeType Project (https://freetype.org).
All rights reserved. FreeType is used under the FreeType License, not the GPL alternative.
See the original copyright notices in `Licenses/freetype/` for the full list of years and authors.

This software uses libraries from the FFmpeg project under the LGPLv2.1 or later.
FFmpeg is built without GPL, nonfree or version3 options, external codec libraries,
network protocols, demuxers, muxers, filters or command-line programs. Only the selected
RDP audio/video codecs, H.264 parser, resampling and scaling are included. There is no
x264, x265, FDK-AAC, libmp3lame or OpenH264 dependency. When FFmpeg is disabled, no FFmpeg
libraries are distributed; the build manifest records the selected configuration.

For completeness, this product acknowledges the Independent JPEG Group: this software
is based in part on the work of the Independent JPEG Group. No modifications are made
to FFmpeg's IJG-derived source files. The selected FFmpeg build does not enable JPEG.

The app's `Contents/Resources/Licenses/` directory contains upstream license and notice
files, including licenses of embedded fonts and code. `build-manifest.json` identifies
exact dependency versions, source checksums, the FreeRDP revision and build commands.
The matching `*-sources.tar.gz` release asset contains the original dependency archives,
the exact FreeRDP source snapshot, launcher sources and build instructions. Download it
from the same GitHub release as the binary; retain both assets when redistributing.

The shared FFmpeg libraries may be replaced with compatible modified versions. See
`RELINKING.md`. No license imposed by this project prohibits modification or reverse
engineering for debugging modifications to LGPL components. There is no additional EULA.
macOS system frameworks/libraries are supplied by Apple and are not redistributed here.

License compliance does not grant codec patent rights or trademark rights. No affiliation
with or endorsement by upstream projects is claimed. Modified dependency selections or
FreeRDP forks require review of the licenses of any code they introduce.
