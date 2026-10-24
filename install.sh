#!/bin/sh
set -eu
command -v python3 >/dev/null 2>&1 || { echo 'Install Python 3.10+: sudo apt install python3'; exit 1; }
ALPS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$ALPS_DIR/setup.py" "$@"
