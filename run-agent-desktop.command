#!/bin/zsh

set -e
cd "$(dirname "$0")"

if [[ ! -x ".venv/bin/agent-desktop" ]]; then
  echo "Personal Agent desktop launcher is not installed in .venv."
  echo "Run:"
  echo "  python3 -m venv .venv"
  echo "  .venv/bin/python -m pip install -e \".[desktop]\""
  read -r "REPLY?Press Enter to close..."
  exit 1
fi

exec .venv/bin/agent-desktop --workspace "$PWD"
