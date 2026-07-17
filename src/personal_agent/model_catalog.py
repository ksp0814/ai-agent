import json
import subprocess
from typing import Any, Dict
from uuid import uuid4


def _call_codex(method: str, params: Dict[str, Any], executable: str = "codex") -> Dict[str, Any]:
    process = subprocess.Popen(
        [executable, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    requests = [
        {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "personal-agent", "title": "Personal Agent", "version": "0.1.0"}, "capabilities": {}}},
        {"method": "initialized", "params": {}},
        {"id": 2, "method": method, "params": params},
    ]
    try:
        process.stdin.write("\n".join(json.dumps(request) for request in requests) + "\n")
        process.stdin.flush()
        for line in process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != 2:
                continue
            return message.get("result", {})
        error = process.stderr.read().strip()
        raise RuntimeError(error or "Codex 모델 목록 응답이 없습니다.")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def fetch_codex_rate_limits(executable: str = "codex") -> Dict[str, Any]:
    """Read current Codex account rate-limit windows."""
    return _call_codex("account/rateLimits/read", {}, executable)


def consume_codex_reset_credit(credit_id: str = "", executable: str = "codex") -> str:
    """Consume one earned Codex rate-limit reset credit."""
    params = {"idempotencyKey": str(uuid4())}
    if credit_id:
        params["creditId"] = credit_id
    result = _call_codex("account/rateLimitResetCredit/consume", params, executable)
    return result.get("outcome", "unknown")
