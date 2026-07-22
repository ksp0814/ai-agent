"""Frozen entry point for the local Python bridge."""

from __future__ import annotations

import sys

from personal_agent.bridge import main as bridge_main
from personal_agent.terminal_bridge import main as terminal_main


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--terminal":
        sys.argv.pop(1)
        terminal_main()
        return

    bridge_main()


if __name__ == "__main__":
    main()
