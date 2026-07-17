from dataclasses import dataclass
import json
import os
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .memory import Memory
from .policy import Policy
from .tools import WorkspaceTools


class ModelProvider(Protocol):
    def respond(self, prompt: str, context: list[str]) -> str: ...


class OfflineProvider:
    def respond(self, prompt: str, context: list[str]) -> str:
        return "오프라인 모드입니다. /help에서 사용할 수 있는 명령을 확인하세요."


class OpenAIProvider:
    """Minimal Responses API client with no third-party dependency."""

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", timeout: int = 60):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def respond(self, prompt: str, context: list[str]) -> str:
        memory = "\n".join(f"- {item}" for item in context) or "(없음)"
        payload = {
            "model": self.model,
            "store": False,
            "input": [
                {"role": "developer", "content": "당신은 개인비서 겸 코딩 에이전트입니다. 답변은 한국어로 간결하고 실행 가능하게 작성하세요."},
                {"role": "developer", "content": f"사용자 장기 기억:\n{memory}"},
                {"role": "user", "content": prompt},
            ],
        }
        request = Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"OpenAI API 오류({exc.code}): {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"OpenAI API 네트워크 오류: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("OpenAI API 요청 시간이 초과되었습니다.") from exc
        return self._extract_text(body)

    @staticmethod
    def _extract_text(body: dict) -> str:
        if isinstance(body.get("output_text"), str):
            return body["output_text"]
        chunks = []
        for item in body.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
        if chunks:
            return "\n".join(chunks)
        raise RuntimeError("OpenAI API 응답에서 텍스트를 찾지 못했습니다.")


@dataclass
class Agent:
    tools: WorkspaceTools
    memory: Memory
    policy: Policy
    model: ModelProvider

    def handle(self, request: str) -> str:
        request = request.strip()
        if not request:
            return "요청을 입력해 주세요."
        if request == "/help":
            return "명령: /ls, /read <파일>, /write <파일> <내용>, /test, /remember <내용>, /memory, /quit"
        if request == "/ls":
            files = self.tools.list_files()
            return "\n".join(files) if files else "작업 공간에 파일이 없습니다."
        if request.startswith("/read "):
            try:
                return self.tools.read_file(request[6:].strip())
            except (ValueError, FileNotFoundError, UnicodeDecodeError) as exc:
                return f"파일을 읽지 못했습니다: {exc}"
        if request.startswith("/remember "):
            content = request[10:].strip()
            if not content:
                return "기억할 내용을 입력해 주세요."
            self.memory.remember(content)
            return "기억했습니다."
        if request == "/memory":
            memories = self.memory.recent()
            return "\n".join(f"- {item}" for item in memories) if memories else "저장된 기억이 없습니다."
        if request == "/test":
            return self.tools.run_tests()
        if request.startswith("/write "):
            parts = request[7:].split(" ", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                return "사용법: /write <파일> <내용>"
            relative, content = parts
            try:
                self.tools.resolve(relative)
            except ValueError as exc:
                return f"파일을 쓸 수 없습니다: {exc}"
            if not self.policy.authorize(f"파일 수정: {relative}"):
                return "승인되지 않아 파일을 수정하지 않았습니다."
            self.tools.write_file(relative, content)
            return f"{relative} 파일을 수정했습니다."
        try:
            return self.model.respond(request, self.memory.recent())
        except RuntimeError as exc:
            return f"모델 호출에 실패했습니다: {exc}"
