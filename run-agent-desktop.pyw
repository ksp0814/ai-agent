"""Start the desktop app without opening a console window."""

import ctypes
from pathlib import Path
import traceback


def report_startup_error(exc: Exception) -> None:
    log_path = Path.home() / ".personal-agent" / "desktop-startup.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(traceback.format_exc(), encoding="utf-8")
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            f"Personal Agent를 시작하지 못했습니다.\n\n로그: {log_path}\n\n{exc}",
            "Personal Agent",
            0x10,
        )
    except Exception:
        pass


try:
    from personal_agent.desktop import main

    main()
except Exception as exc:
    report_startup_error(exc)
