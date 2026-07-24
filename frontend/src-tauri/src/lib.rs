mod commands {
    use std::collections::HashMap;
    use std::io::{BufRead, BufReader, Write};
    use std::path::PathBuf;
    use std::process::{Child, ChildStdin, Command, Stdio};
    use std::sync::{Arc, Mutex};
    use std::thread;

    #[cfg(windows)]
    use std::os::windows::process::CommandExt;

    #[cfg(windows)]
    const CREATE_NO_WINDOW: u32 = 0x08000000;

    use serde::Serialize;
    use serde_json::Value;
    use tauri::{AppHandle, Emitter, Manager, State};

    pub struct TerminalState(pub Mutex<HashMap<String, ManagedTerminal>>);

    pub struct ManagedTerminal {
        child: Child,
        stdin: ChildStdin,
        output: Arc<Mutex<String>>,
    }

    const TERMINAL_BUFFER_LIMIT: usize = 64 * 1024;

    #[derive(Clone, Debug, Serialize)]
    pub struct TerminalEvent {
        pub session_id: String,
        pub event: String,
        pub data: Option<String>,
        pub message: Option<String>,
    }

    impl Default for TerminalState {
        fn default() -> Self {
            Self(Mutex::new(HashMap::new()))
        }
    }

    impl TerminalState {
        pub fn stop_all(&self) {
            if let Ok(mut terminals) = self.0.lock() {
                for (_, mut terminal) in terminals.drain() {
                    let _ = write_control(&mut terminal.stdin, serde_json::json!({"command": "stop"}));
                    let _ = terminal.child.kill();
                    let _ = terminal.child.wait();
                }
            }
        }
    }

    #[tauri::command]
    pub fn bridge_request(app: AppHandle, workspace: String, request: Value) -> Result<Value, String> {
        let request_json = serde_json::to_string(&request)
            .map_err(|error| format!("요청을 직렬화하지 못했습니다: {error}"))?;
        let mut command = if let Some(bridge) = bundled_bridge_path(&app) {
            let mut command = Command::new(bridge);
            command.args([workspace.as_str(), "--request", request_json.as_str()]);
            command
        } else {
            let project_root = project_root()?;
            let python_path = project_root.join("src");
            let mut command = Command::new(python_executable(&project_root));
            command
                .env("PYTHONPATH", &python_path)
                .args(["-m", "personal_agent.bridge", workspace.as_str(), "--request", request_json.as_str()]);
            command
        };
        configure_child_process(&mut command);
        let output = command
            .current_dir(&workspace)
            .output()
            .map_err(|error| format!("Python 브리지를 시작하지 못했습니다: {error}"))?;
        if !output.status.success() {
            return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
        }
        let response: Value = serde_json::from_slice(&output.stdout)
            .map_err(|error| format!("Python 브리지 응답을 해석하지 못했습니다: {error}"))?;
        if response.get("ok") != Some(&Value::Bool(true)) {
            return Err(response["error"].as_str().unwrap_or("Python 브리지 요청 실패").to_string());
        }
        Ok(response["result"].clone())
    }

    fn project_root() -> Result<PathBuf, String> {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .canonicalize()
            .map_err(|error| format!("프로젝트 루트를 찾지 못했습니다: {error}"))
    }

    fn python_executable(root: &PathBuf) -> PathBuf {
        let virtualenv_python = if cfg!(windows) {
            root.join(".venv").join("Scripts").join("python.exe")
        } else {
            root.join(".venv").join("bin").join("python")
        };
        if virtualenv_python.is_file() {
            virtualenv_python
        } else {
            PathBuf::from("python")
        }
    }

    fn configure_child_process(command: &mut Command) {
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
    }

    fn bundled_bridge_path(app: &AppHandle) -> Option<PathBuf> {
        if cfg!(debug_assertions) {
            return None;
        }
        let executable = if cfg!(windows) {
            "personal-agent-bridge.exe"
        } else {
            "personal-agent-bridge"
        };
        let candidates = [
            app.path().resource_dir().ok().map(|path| path.join("resources").join(executable)),
            app.path().resource_dir().ok().map(|path| path.join(executable)),
            Some(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources").join(executable)),
        ];
        candidates.into_iter().flatten().find(|path| path.is_file())
    }

    fn write_control(stdin: &mut ChildStdin, payload: Value) -> Result<(), String> {
        writeln!(stdin, "{payload}").map_err(|error| format!("터미널 명령을 전달하지 못했습니다: {error}"))?;
        stdin.flush().map_err(|error| format!("터미널 명령을 flush하지 못했습니다: {error}"))
    }

    #[tauri::command]
    pub fn start_terminal(
        app: AppHandle,
        state: State<'_, TerminalState>,
        session_id: String,
        workspace: String,
    ) -> Result<bool, String> {
        {
            let terminals = state.0.lock().map_err(|_| "터미널 상태 잠금에 실패했습니다.".to_string())?;
            if terminals.contains_key(&session_id) {
                return Ok(true);
            }
        }
        let mut command = if let Some(bridge) = bundled_bridge_path(&app) {
            let mut command = Command::new(bridge);
            command.args(["--terminal", workspace.as_str()]);
            command
        } else {
            let root = project_root()?;
            let mut command = Command::new(python_executable(&root));
            command
                .env("PYTHONPATH", root.join("src"))
                .args(["-m", "personal_agent.terminal_bridge", workspace.as_str()]);
            command
        };
        configure_child_process(&mut command);
        let mut child = command
            .current_dir(&workspace)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|error| format!("Python 터미널 브리지를 시작하지 못했습니다: {error}"))?;
        let mut stdin = child.stdin.take().ok_or_else(|| "터미널 stdin을 열지 못했습니다.".to_string())?;
        let stdout = child.stdout.take().ok_or_else(|| "터미널 stdout을 열지 못했습니다.".to_string())?;
        write_control(&mut stdin, serde_json::json!({"command": "start"}))?;
        let output_buffer = Arc::new(Mutex::new(String::new()));
        let buffer_for_reader = Arc::clone(&output_buffer);

        let session_for_reader = session_id.clone();
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines().flatten() {
                let parsed: Value = match serde_json::from_str(&line) {
                    Ok(value) => value,
                    Err(error) => {
                        let _ = app.emit("terminal-event", TerminalEvent {
                            session_id: session_for_reader.clone(),
                            event: "error".to_string(),
                            data: None,
                            message: Some(format!("터미널 이벤트 해석 실패: {error}")),
                        });
                        continue;
                    }
                };
                if parsed["event"] == "output" {
                    if let Some(data) = parsed["data"].as_str() {
                        if let Ok(mut buffer) = buffer_for_reader.lock() {
                            buffer.push_str(data);
                            if buffer.len() > TERMINAL_BUFFER_LIMIT {
                                let trim_to = buffer.len() - TERMINAL_BUFFER_LIMIT;
                                if let Some((boundary, _)) = buffer.char_indices().find(|(index, _)| *index >= trim_to) {
                                    buffer.drain(..boundary);
                                }
                            }
                        }
                    }
                }
                let _ = app.emit("terminal-event", TerminalEvent {
                    session_id: session_for_reader.clone(),
                    event: parsed["event"].as_str().unwrap_or("error").to_string(),
                    data: parsed["data"].as_str().map(str::to_string),
                    message: parsed["message"].as_str().map(str::to_string),
                });
            }
        });

        let mut terminals = state.0.lock().map_err(|_| "터미널 상태 잠금에 실패했습니다.".to_string())?;
        terminals.insert(session_id, ManagedTerminal { child, stdin, output: output_buffer });
        Ok(false)
    }

    #[tauri::command]
    pub fn terminal_buffer(state: State<'_, TerminalState>, session_id: String) -> Result<String, String> {
        let terminals = state.0.lock().map_err(|_| "터미널 상태 잠금에 실패했습니다.".to_string())?;
        let terminal = terminals.get(&session_id).ok_or_else(|| "터미널 세션이 없습니다.".to_string())?;
        terminal.output.lock().map(|buffer| buffer.clone()).map_err(|_| "터미널 출력 잠금에 실패했습니다.".to_string())
    }

    #[tauri::command]
    pub fn write_terminal(state: State<'_, TerminalState>, session_id: String, text: String) -> Result<(), String> {
        let mut terminals = state.0.lock().map_err(|_| "터미널 상태 잠금에 실패했습니다.".to_string())?;
        let terminal = terminals.get_mut(&session_id).ok_or_else(|| "터미널 세션이 없습니다.".to_string())?;
        write_control(&mut terminal.stdin, serde_json::json!({"command": "write", "text": text}))
    }

    #[tauri::command]
    pub fn resize_terminal(state: State<'_, TerminalState>, session_id: String, columns: u16, rows: u16) -> Result<(), String> {
        let mut terminals = state.0.lock().map_err(|_| "터미널 상태 잠금에 실패했습니다.".to_string())?;
        let terminal = terminals.get_mut(&session_id).ok_or_else(|| "터미널 세션이 없습니다.".to_string())?;
        write_control(&mut terminal.stdin, serde_json::json!({"command": "resize", "columns": columns, "rows": rows}))
    }

    #[tauri::command]
    pub fn stop_terminal(state: State<'_, TerminalState>, session_id: String) -> Result<(), String> {
        let mut terminals = state.0.lock().map_err(|_| "터미널 상태 잠금에 실패했습니다.".to_string())?;
        if let Some(mut terminal) = terminals.remove(&session_id) {
            let _ = write_control(&mut terminal.stdin, serde_json::json!({"command": "stop"}));
            let _ = terminal.child.kill();
            let _ = terminal.child.wait();
        }
        Ok(())
    }
}

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(commands::TerminalState::default())
        .invoke_handler(tauri::generate_handler![
            commands::bridge_request,
            commands::start_terminal,
            commands::terminal_buffer,
            commands::write_terminal,
            commands::resize_terminal,
            commands::stop_terminal
        ])
        .build(tauri::generate_context!())
        .expect("error while building Personal Agent");
    app.run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app.try_state::<commands::TerminalState>() {
                    state.stop_all();
                }
            }
        });
}
