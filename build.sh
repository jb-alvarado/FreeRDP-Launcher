#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Download, build and package the macOS launcher and its dependencies.
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "This build requires macOS." >&2
  exit 1
fi
for tool in python3 cmake ninja pkg-config git make perl xcrun; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing build tool: $tool" >&2
    echo "Install Xcode command-line tools manually: xcode-select --install" >&2
    echo "Install other build tools manually: brew install cmake ninja pkgconf" >&2
    exit 1
  fi
done
exec python3 "$PROJECT_ROOT/scripts/build.py" "$@"
