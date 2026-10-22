#!/usr/bin/env bash
# Voice Gate for Alexa+ - Unix/macOS Runner
set -e

if [ -f ".venv/bin/python" ]; then
    .venv/bin/python run.py "$@"
else
    python3 run.py "$@" || python run.py "$@"
fi
