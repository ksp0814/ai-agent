# Personal Agent

로컬 작업 공간에서 파일을 확인하고 Codex CLI 세션으로 코딩 작업을 수행하는 개인용 AI 코딩 워크벤치입니다. CLI와 데스크톱 앱을 제공합니다.

데스크톱 UI는 React + TypeScript + Tauri로 전환 중입니다. 기존 PySide6 앱은 Python 기능을 안정적으로 유지하기 위한 호환 실행 경로로 남겨두었습니다.

## 주요 특징

- 작업 공간을 추가·전환하고 마지막 상태를 자동 복원
- 작업 공간별 독립 Codex CLI 세션 유지
- 중앙 터미널과 파일 탭을 함께 사용하는 Orca 스타일 워크벤치 UI
- 프로젝트 파일 트리, 파일 검색, 폴더 확장 및 파일 열기
- `Ctrl+P`로 작업 공간·세션·파일 빠른 검색
- 새 Codex 세션 생성, 세션 전환 및 세션별 터미널 상태 유지
- Tauri 폴더 선택기를 통한 작업 공간 추가·전환
- 여러 작업 공간을 왼쪽 프로젝트 목록에 열어두고 빠르게 전환
- 하단 상태바의 Codex 사용률 표시 및 수동 새로고침
- Git 상태·변경 파일·파일 내용 열기
- React 화면에서 변경 승인 및 안전한 되돌리기(복구 보관함 이동)
- Git worktree를 여러 개 생성하고 작업 공간으로 자동 등록
- 터미널 출력 검색, 화면 지우기, 글자 크기 조절, 클립보드 단축키
- Codex 사용량과 초기화권 확인
- macOS PTY 및 Windows ConPTY 기반 터미널
- SQLite 기반 장기 메모리와 작업 기록 저장
- API 키 없이 사용할 수 있는 오프라인 기본 응답 모드

## 요구 사항

- Python 3.9 이상
- 기존 데스크톱 앱: PySide6
- 새 데스크톱 UI: Node.js, Rust, Cargo
- Codex CLI 로그인 사용 시 Codex CLI 설치 및 로그인
- Git 기능 사용 시 Git 설치

## 설치

### CLI

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
agent
```

Windows PowerShell에서는 다음을 사용합니다.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
agent
```

### 데스크톱 앱

```bash
python -m pip install -e ".[desktop]"
agent-desktop
```

가상환경을 활성화하지 않고 실행하려면 프로젝트 루트의 런처를 사용할 수 있습니다.

- Windows: `run-agent-desktop.cmd` 또는 `run-agent-desktop.vbs`
- macOS: `run-agent-desktop.command`

Windows 시작 오류 로그는 `%USERPROFILE%\.personal-agent\desktop-startup.log`에서 확인할 수 있습니다.

### React + Tauri 데스크톱 앱

React 개발 서버와 Tauri 셸을 실행하려면 Node.js와 Rust가 필요합니다.

```bash
cd frontend
npm install
npm run tauri dev
```

브라우저에서 React UI만 확인하려면 다음 명령을 사용합니다.

```bash
cd frontend
npm run dev
```

현재 React 화면은 작업 공간·파일 트리·파일 탭·다중 세션을 제공하며, 세션 목록은 작업 공간별로 복원됩니다. Tauri를 통해 Python 브리지의 실제 파일·Git 데이터와 Codex PTY/ConPTY 터미널 스트림을 사용하고, 변경 승인과 되돌리기는 기준선·복구 보관함을 사용합니다.

Orca가 제공하는 읽기 전용 `CODEX_HOME`을 상속한 경우에는 터미널이 프로젝트의 `.agent/codex-home`으로 자동 전환됩니다. 이 별도 홈을 처음 사용할 때는 해당 터미널에서 `codex login`을 한 번 실행해야 합니다.

## Codex CLI 사용

API 키 대신 Codex CLI 로그인 세션을 사용할 수 있습니다.

```bash
codex login
agent
```

중앙 터미널은 Windows에서 PowerShell(`pwsh.exe`, 없으면 `powershell.exe`)로 시작합니다. Codex를 사용하려면 터미널에서 직접 `codex`를 입력해 실행합니다. 기존 PySide6 데스크톱 앱에서는 작업 공간별 터미널 세션을 유지하며, 상단에는 열린 세션과 파일 탭만 표시합니다.

### Windows 배포

GitHub Actions는 `v*` 태그를 기준으로 Python 브리지를 PyInstaller 실행 파일로 패키징한 뒤 Tauri MSI/NSIS 설치 파일을 GitHub Release 초안으로 생성합니다. 로컬에서 같은 브리지를 만들려면 다음을 실행합니다.

```powershell
python -m pip install -e ".[desktop,deployment]"
python -m PyInstaller --onefile --name personal-agent-bridge --collect-all winpty --paths src --distpath frontend/src-tauri/resources --workpath .build/pyinstaller --specpath .build/pyinstaller --clean packaging/bridge_entry.py
cd frontend
npm ci
npm run tauri build
```

배포된 앱 사용자는 Python을 설치할 필요가 없지만, Codex CLI 설치와 `codex login`은 필요합니다.

## OpenAI API 사용

API 키와 모델을 환경변수로 설정하면 자연어 요청을 Responses API로 전달합니다.

```bash
export OPENAI_API_KEY="your-api-key"
export OPENAI_MODEL="your-model-id"
agent
```

호환 endpoint를 사용하려면 다음 환경변수도 설정할 수 있습니다.

```bash
export OPENAI_BASE_URL="https://your-compatible-endpoint/v1"
```

API 키는 저장소에 커밋하지 마세요. `.env.example`을 복사해 사용할 수 있으며 실제 `.env` 파일은 Git에서 제외됩니다.

## CLI 명령

```text
/help       사용 가능한 명령 확인
/ls         작업 공간 파일 목록
/read FILE  파일 읽기
/remember   장기 메모리 저장
/memory     저장된 메모리 확인
/git        Git 상태 확인
/diff       변경 내용 확인
/check      프로젝트 검증 실행
/history    작업 기록 확인
/resume     최근 작업 재개
/quit       종료
```

## 프로젝트 구조

```text
frontend/src/App.tsx            React 워크벤치 UI
frontend/src/bridge.ts          Tauri·Python 브리지 호출
frontend/src/TerminalPane.tsx   xterm.js 터미널 렌더링·입출력
frontend/src-tauri/src/lib.rs   Tauri 명령 경계
src/personal_agent/desktop.py   기존 PySide6 데스크톱 UI
src/personal_agent/terminal.py  PTY·ConPTY 터미널 세션
src/personal_agent/bridge.py    React/Tauri용 JSON-line 백엔드 브리지
src/personal_agent/cli.py       CLI 진입점과 명령 처리
src/personal_agent/tools.py     작업 공간·Git 파일 도구
tests/                          단위 테스트
```

## 검증

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import personal_agent.desktop"
git diff --check
```

Personal Agent는 현재 로컬 우선 MVP입니다. 향후 다양한 LLM provider와 캘린더·메일 같은 개인비서 도구를 연결할 수 있습니다.
