import argparse
from pathlib import Path

import os

from .agent import Agent, OfflineProvider, OpenAIProvider
from .config import Settings
from .memory import Memory
from .policy import Policy
from .tools import WorkspaceTools


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal assistant and coding agent")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    settings = Settings.from_workspace(args.workspace)

    def approve(description: str) -> bool:
        return input(f"승인할까요? {description} [y/N] ").strip().lower() == "y"

    api_key = os.getenv("OPENAI_API_KEY")
    model_name = os.getenv("OPENAI_MODEL")
    if api_key and model_name:
        model = OpenAIProvider(api_key, model_name, os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
        mode = f"OpenAI API 모드 · model: {model_name}"
    else:
        model = OfflineProvider()
        mode = "오프라인 모드 · OPENAI_API_KEY와 OPENAI_MODEL을 설정하면 API 모드로 전환"
    agent = Agent(WorkspaceTools(settings.workspace), Memory(settings.database), Policy(approve), model)
    print(f"Personal Agent · workspace: {settings.workspace}")
    print(mode)
    print("/help 로 명령을 확인하세요. 종료: /quit")
    while True:
        try:
            request = input("\nYou> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if request.strip() == "/quit":
            break
        print(f"Agent> {agent.handle(request)}")


if __name__ == "__main__":
    main()
