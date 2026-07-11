use rand::RngCore;
use serde::Serialize;
use serde_json::Value;
use std::ffi::OsString;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const STARTUP_TIMEOUT: Duration = Duration::from_secs(15);
const SHUTDOWN_GRACE: Duration = Duration::from_secs(2);
const TOKEN_ENV: &str = "AIMING_COOKIE_DESKTOP_TOKEN";

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeConnection {
    pub base_url: String,
    pub token: String,
}

pub struct RuntimeProcess {
    child: Option<Child>,
    connection: RuntimeConnection,
}

impl RuntimeProcess {
    pub fn start(project_root: &Path, app_data_dir: &Path) -> Result<Self, String> {
        std::fs::create_dir_all(app_data_dir)
            .map_err(|error| format!("failed to create app data directory: {error}"))?;

        let token = create_launch_token();
        let database_path = app_data_dir.join("aiming_cookie.db");
        let database_url = format!("sqlite+aiosqlite:///{}", database_path.display());
        let mut command = Command::new(python_executable());
        command
            .arg("-m")
            .arg("webapp.backend.desktop_runtime")
            .current_dir(project_root)
            .env(TOKEN_ENV, &token)
            .env("DATA_ROOT", app_data_dir)
            .env("VIDEO_TMP_DIR", app_data_dir)
            .env("DATABASE_URL", database_url)
            .env(
                "CORS_ORIGINS",
                "http://localhost:3000,http://tauri.localhost,tauri://localhost",
            )
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        configure_process_group(&mut command);

        let mut child = command
            .spawn()
            .map_err(|error| format!("failed to start local Python runtime: {error}"))?;
        let stdout = match child.stdout.take() {
            Some(stdout) => stdout,
            None => {
                terminate_process_tree(&mut child);
                return Err("local runtime stdout was unavailable".to_string());
            }
        };
        let stderr = child.stderr.take();

        if let Some(stderr) = stderr {
            let stderr_token = token.clone();
            thread::spawn(move || {
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    eprintln!(
                        "[desktop-runtime] {}",
                        redact_secret(&line, &stderr_token),
                    );
                }
            });
        }

        let (sender, receiver) = mpsc::sync_channel(1);
        thread::spawn(move || {
            let mut line = String::new();
            let result = BufReader::new(stdout)
                .read_line(&mut line)
                .map_err(|error| format!("failed to read runtime readiness: {error}"))
                .and_then(|bytes| {
                    if bytes == 0 {
                        Err("local runtime exited before readiness".to_string())
                    } else {
                        parse_readiness_line(&line)
                    }
                });
            let _ = sender.send(result);
        });

        let port = match receiver.recv_timeout(STARTUP_TIMEOUT) {
            Ok(Ok(port)) => port,
            Ok(Err(error)) => {
                terminate_process_tree(&mut child);
                return Err(error);
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                terminate_process_tree(&mut child);
                return Err("local runtime readiness timed out".to_string());
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                terminate_process_tree(&mut child);
                return Err("local runtime readiness channel closed".to_string());
            }
        };

        if matches!(child.try_wait(), Ok(Some(_))) {
            return Err("local runtime exited immediately after readiness".to_string());
        }

        Ok(Self {
            child: Some(child),
            connection: RuntimeConnection {
                base_url: format!("http://127.0.0.1:{port}"),
                token,
            },
        })
    }

    pub fn connection(&self) -> RuntimeConnection {
        self.connection.clone()
    }

    pub fn shutdown(&mut self) {
        if let Some(mut child) = self.child.take() {
            terminate_process_tree(&mut child);
        }
    }
}

impl Drop for RuntimeProcess {
    fn drop(&mut self) {
        self.shutdown();
    }
}

pub struct RuntimeState {
    runtime: Mutex<Option<RuntimeProcess>>,
}

impl RuntimeState {
    pub fn new(runtime: RuntimeProcess) -> Self {
        Self {
            runtime: Mutex::new(Some(runtime)),
        }
    }

    pub fn connection(&self) -> Result<RuntimeConnection, String> {
        self.runtime
            .lock()
            .map_err(|_| "local runtime state is unavailable".to_string())?
            .as_ref()
            .map(RuntimeProcess::connection)
            .ok_or_else(|| "local runtime is not running".to_string())
    }

    pub fn shutdown(&self) {
        if let Ok(mut guard) = self.runtime.lock() {
            guard.take();
        }
    }
}

pub fn project_root() -> PathBuf {
    std::env::var_os("AIMING_COOKIE_PROJECT_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."))
}

fn python_executable() -> OsString {
    std::env::var_os("AIMING_COOKIE_PYTHON").unwrap_or_else(|| {
        if cfg!(windows) {
            OsString::from("python")
        } else {
            OsString::from("python3")
        }
    })
}

fn create_launch_token() -> String {
    let mut bytes = [0_u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn redact_secret(text: &str, token: &str) -> String {
    if token.is_empty() {
        text.to_string()
    } else {
        text.replace(token, "[REDACTED]")
    }
}

fn parse_readiness_line(line: &str) -> Result<u16, String> {
    let value: Value = serde_json::from_str(line)
        .map_err(|_| "local runtime emitted malformed readiness JSON".to_string())?;
    let object = value
        .as_object()
        .filter(|object| object.len() == 2)
        .ok_or_else(|| "local runtime emitted malformed readiness message".to_string())?;
    if object.get("type").and_then(Value::as_str) != Some("ready") {
        return Err("local runtime emitted malformed readiness message".to_string());
    }
    let port = object
        .get("port")
        .and_then(Value::as_u64)
        .filter(|port| (1..=u16::MAX as u64).contains(port))
        .ok_or_else(|| "local runtime emitted malformed readiness message".to_string())?;
    Ok(port as u16)
}

#[cfg(unix)]
fn configure_process_group(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    command.process_group(0);
}

#[cfg(windows)]
fn configure_process_group(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
    command.creation_flags(CREATE_NEW_PROCESS_GROUP);
}

#[cfg(not(any(unix, windows)))]
fn configure_process_group(_command: &mut Command) {}

fn terminate_process_tree(child: &mut Child) {
    if matches!(child.try_wait(), Ok(Some(_))) {
        return;
    }

    #[cfg(unix)]
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGTERM);
    }

    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }

    #[cfg(not(any(unix, windows)))]
    {
        let _ = child.kill();
    }

    let deadline = Instant::now() + SHUTDOWN_GRACE;
    while Instant::now() < deadline {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return;
        }
        thread::sleep(Duration::from_millis(20));
    }

    #[cfg(unix)]
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(test)]
mod tests {
    use super::{create_launch_token, parse_readiness_line, redact_secret};

    #[test]
    fn readiness_protocol_accepts_only_exact_dynamic_port_message() {
        assert_eq!(
            parse_readiness_line(r#"{"type":"ready","port":43127}"#).unwrap(),
            43127,
        );
        for invalid in [
            "not-json",
            r#"{"type":"ready"}"#,
            r#"{"type":"ready","port":0}"#,
            r#"{"type":"ready","port":65536}"#,
            r#"{"type":"ready","port":"43127"}"#,
            r#"{"type":"ready","port":43127,"token":"secret"}"#,
        ] {
            assert!(parse_readiness_line(invalid).is_err());
        }
    }

    #[test]
    fn launch_tokens_are_fresh_high_entropy_and_redacted() {
        let first = create_launch_token();
        let second = create_launch_token();
        assert_ne!(first, second);
        assert_eq!(first.len(), 64);
        assert_eq!(redact_secret(&format!("token={first}"), &first), "token=[REDACTED]");
    }
}
