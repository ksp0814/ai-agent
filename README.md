# Personal Agent

개인비서와 코딩 작업을 하나의 로컬 우선 CLI에서 처리하기 위한 초기 MVP입니다.

## 실행

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
agent
```

LLM 없이도 기본 명령을 사용할 수 있습니다.

```text
/help
/ls
/read README.md
/remember 오늘부터 Python 프로젝트는 타입 힌트를 사용한다
/memory
/quit
```

## OpenAI API 연결

API 키와 사용할 모델을 환경변수로 설정하면 자연어 요청은 Responses API로 전달됩니다. API 키는 저장소에 넣지 마세요.

```bash
export OPENAI_API_KEY="your-api-key"
export OPENAI_MODEL="your-model-id"
PYTHONPATH="$PWD/src" python3 -m personal_agent.cli
```

또는 `.env.example`을 `.env`로 복사해 사용할 수 있습니다. 실제 키가 들어간 `.env`는 `.gitignore`에 등록되어 있으므로 커밋되지 않습니다.

호환 endpoint를 사용하려면 다음 환경변수도 설정할 수 있습니다.

```bash
export OPENAI_BASE_URL="https://your-compatible-endpoint/v1"
```

## 현재 기능

- 작업 공간 내부 파일 목록 조회 및 파일 읽기/쓰기
- 안전한 범위의 테스트 실행
- SQLite 기반 장기 메모리
- 변경 작업 전 승인 요청
- 모델 계층 교체를 위한 `ModelProvider` 인터페이스
- 기본 오프라인 응답 모드

현재는 의도적으로 외부 API 키가 없어도 실행됩니다. 다음 단계에서 원하는 LLM provider와 캘린더·메일 같은 개인비서 도구를 연결하면 됩니다.
