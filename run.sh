#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
CONFIG="${CONFIG:-configs/starter.json}"
python supervisor.py --config "$CONFIG" "$@"
