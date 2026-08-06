#!/bin/bash
# Wrapper so launchd has a stable entry point with the right working dir/venv.
# Calls the venv's Python directly (absolute path) rather than sourcing
# activate, since launchd's minimal environment doesn't reliably pick up
# PATH changes from an activated shell.
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python3 delta_tracker.py
