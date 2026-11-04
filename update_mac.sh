#!/usr/bin/env bash
# Update the bot from a freshly downloaded GitHub ZIP, keeping your settings and data.
#   1. Download the branch ZIP from GitHub in your browser (it lands in ~/Downloads).
#   2. In this folder run:  bash update_mac.sh
# Your .env (keys), .venv, logs/ and data_cache/ are never touched.
#
# Everything is inside main() so bash reads the whole script before running it: the update copies a new
# version of this very file into place, which would otherwise corrupt the run.
main() {
  set -euo pipefail
  local here src zip
  here="$(cd "$(dirname "$0")" && pwd)"
  cd ~/Downloads

  src="$(ls -td day-trading--*/ 2>/dev/null | head -1 || true)"
  zip="$(ls -t day-trading-*.zip 2>/dev/null | head -1 || true)"
  if [ -n "$zip" ] && { [ -z "$src" ] || [ "$zip" -nt "$src" ]; }; then
    rm -rf day-trading--*/
    unzip -q "$zip"
    src="$(ls -td day-trading--*/ | head -1)"
  fi
  if [ -z "$src" ]; then
    echo "No downloaded copy found in ~/Downloads. Download the ZIP from GitHub first." >&2
    exit 1
  fi

  echo "==> Updating $here from ~/Downloads/$src"
  # the GitHub download never contains .env, .venv, logs/ or data_cache/, so a plain copy can't overwrite them
  cp -R "${src%/}/." "$here/"
  rm -rf "$src"
  if [ -n "$zip" ]; then rm -f "$zip"; fi
  cd "$here"
  bash setup_mac.sh
}
main "$@"
exit
