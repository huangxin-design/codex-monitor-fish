#!/bin/sh
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  exec python3 launch.py "$@"
fi
printf '%s\n' 'Python 3 was not found. Ask Codex to launch this app with an available Python 3 interpreter.'
exit 1
