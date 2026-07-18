# Personal Agent

개인비서와 코딩 작업을 하나의 로컬 우선 CLI에서 처리하기 위한 초기 MVP입니다.

## 실행

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
agent
```

## 데스크톱 앱

macOS와 Windows용 데스크톱 UI는 PySide6로 실행합니다.

```bash
python -m pip install -e ".[desktop]"
agent-desktop
```

데스크톱 앱 중앙 화면은 xterm.js 터미널에서 실제 Codex CLI 세션을 표시합니다. macOS는 PTY, Windows는 ConPTY 기반으로 동작합니다. 작업 공간마다 별도 Codex CLI 세션과 터미널 화면을 유지합니다. 파일 변경 승인은 중앙 터미널에서 Codex CLI 방식으로 처리됩니다.

LLM 없이도 기본 명령을 사용할 수 있습니다.

```text
/help
/ls
/read README.md
/remember 오늘부터 Python 프로젝트는 타입 힌트를 사용한다
/memory
/git
/diff
/check
/history
/resume
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

## Codex 로그인 사용

OpenAI API 키 대신 Codex CLI 로그인 세션을 사용할 수 있습니다.

```bash
codex login
agent
```

API 환경변수가 없고 `codex` 명령이 설치되어 있으면 Personal Agent가 자동으로 `codex exec`를 호출합니다. 이 경로는 모델이 작업 공간을 직접 수정하지 않도록 read-only 모드로 실행됩니다. 파일 수정은 Personal Agent의 승인 흐름을 사용합니다.

호환 endpoint를 사용하려면 다음 환경변수도 설정할 수 있습니다.

```bash
export OPENAI_BASE_URL="https://your-compatible-endpoint/v1"
```

## 현재 기능

- 작업 공간 내부 파일 목록 조회 및 파일 읽기/쓰기
- 안전한 범위의 테스트 실행
- SQLite 기반 장기 메모리
- 변경 작업 전 승인 요청
- 데스크톱 앱의 파일 변경 Diff 확인 및 변경 승인·되돌리기
- 여러 변경 파일 일괄 승인·되돌리기
- 변경 파일 전용 목록과 신규·삭제·대용량·바이너리 상태 표시
- 신규 파일 되돌리기 시 복구 보관함으로 안전하게 이동
- 마지막 작업 공간 자동 복원 및 작업 공간별 Codex 세션 유지
- 작업 공간별 Codex 세션 생성·전환·이름 변경·닫기
- 여러 세션 사용 시 동일 파일 동시 수정 경고
- 터미널 출력 검색·화면 지우기·글자 크기 조절·클립보드 단축키
- xterm.js 기반 Codex CLI 터미널과 macOS PTY·Windows ConPTY 지원
- Git 브랜치·변경 상태 표시 및 파일별 Git Diff 확인
- Codex CLI·로그인·Git·PTY 환경 진단 및 진단 새로고침
- Codex CLI 세션 중지·재시작과 macOS PTY·Windows ConPTY 지원
- 다크 워크벤치 UI와 터미널 중심 레이아웃
- 모델 계층 교체를 위한 `ModelProvider` 인터페이스
- 기본 오프라인 응답 모드
- 작업 기록 저장 및 최근 작업 재개
- Git 상태·diff 확인
- 고정된 프로젝트 테스트 검증 실행(`/check`)

현재는 의도적으로 외부 API 키가 없어도 실행됩니다. 다음 단계에서 원하는 LLM provider와 캘린더·메일 같은 개인비서 도구를 연결하면 됩니다.
