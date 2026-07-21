"""Start the desktop app without opening a console window."""

import ctypes
from datetime import datetime
from pathlib import Path
import sys
import traceback


LOG_PATH = Path.home() / ".personal-agent" / "desktop-startup.log"
PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if SRC_ROOT.is_dir() and str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def write_log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")


def report_startup_error(exc: Exception) -> None:
    write_log(traceback.format_exc())
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            f"Personal Agent를 시작하지 못했습니다.\n\n로그: {LOG_PATH}\n\n{exc}",
            "Personal Agent",
            0x10,
        )
    except Exception:
        pass


try:
    write_log("desktop startup")
    from personal_agent.desktop import main

    main()
except SystemExit as exc:
    write_log(f"desktop exit: code={exc.code!r}")
except Exception as exc:
    report_startup_error(exc)
