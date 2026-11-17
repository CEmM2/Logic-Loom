//! logic_loom_api sidecar lifecycle.
//!
//! In debug builds it spawns `uv run logic-loom-api --auto-port` from the
//! workspace root. In packaged builds it launches the bundled sidecar binary
//! from Tauri resources, parses the `LOGIC_LOOM_API_READY:<port>` handshake line,
//! exposes the port to the frontend via Tauri state, and shuts the child down
//! gracefully on window close (SIGTERM, then SIGKILL after 5s).

use std::io::{BufRead, BufReader};
#[cfg(unix)]
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

#[derive(Debug)]
pub enum SidecarError {
    Spawn(std::io::Error),
    NoStdout,
    HandshakeTimeout,
    HandshakeFailed(String),
    Resolve(String),
}

impl std::fmt::Display for SidecarError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SidecarError::Spawn(e) => write!(f, "failed to spawn sidecar: {e}"),
            SidecarError::NoStdout => write!(f, "sidecar produced no stdout pipe"),
            SidecarError::HandshakeTimeout => write!(f, "sidecar did not announce READY within timeout"),
            SidecarError::HandshakeFailed(s) => write!(f, "handshake failed: {s}"),
            SidecarError::Resolve(s) => write!(f, "failed to resolve sidecar path: {s}"),
        }
    }
}

#[derive(Default)]
pub struct SidecarState {
    pub port: Mutex<Option<u16>>,
    pub child: Mutex<Option<Child>>,
    /// Per-launch secret the sidecar requires on every request (see
    /// `logic_loom_api/auth.py`). Handed to the frontend only through the
    /// `get_api_token` command, never through an event or the handshake line.
    pub token: Mutex<Option<String>>,
    /// Set by `shutdown`. A `launch` that finishes spawning after this is set
    /// kills its child instead of storing it, so a window closed during
    /// startup cannot orphan the sidecar.
    pub shutting_down: AtomicBool,
}

/// Environment variable the sidecar reads its token from. Contract with
/// `logic_loom_api.auth.TOKEN_ENV`.
const TOKEN_ENV: &str = "LOGIC_LOOM_API_TOKEN";

/// 32 bytes from the OS CSPRNG, hex-encoded (64 chars).
fn generate_token() -> Result<String, SidecarError> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|e| SidecarError::Resolve(format!("OS random source unavailable: {e}")))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

#[derive(Serialize, Clone)]
struct ReadyPayload {
    port: u16,
}

/// Returns the workspace root by walking up three levels from
/// `apps/desktop/src-tauri/`.
fn workspace_root() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest
        .ancestors()
        .nth(3)
        .map(PathBuf::from)
        .unwrap_or(manifest)
}

#[cfg(not(debug_assertions))]
fn bundled_sidecar_path(app: &AppHandle) -> Result<PathBuf, SidecarError> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|e| SidecarError::Resolve(format!("resource_dir unavailable: {e}")))?;

    let binary_name = if cfg!(windows) {
        "logic-loom-api.exe"
    } else {
        "logic-loom-api"
    };

    Ok(resource_dir.join("binaries").join(binary_name))
}

fn command_for_sidecar(_app: &AppHandle, _root: &Path) -> Result<Command, SidecarError> {
    #[cfg(debug_assertions)]
    {
        let mut cmd = Command::new("uv");
        cmd.arg("run")
            .arg("--directory")
            .arg(_root)
            .arg("logic-loom-api")
            .arg("--auto-port")
            .arg("--exit-on-stdin-eof");
        Ok(cmd)
    }

    #[cfg(not(debug_assertions))]
    {
        let sidecar = bundled_sidecar_path(_app)?;
        let mut cmd = Command::new(sidecar);
        cmd.arg("--auto-port").arg("--exit-on-stdin-eof");
        Ok(cmd)
    }
}

#[cfg(unix)]
fn configure_sidecar_command(cmd: &mut Command) {
    unsafe {
        cmd.pre_exec(|| {
            if libc::setpgid(0, 0) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
}

#[cfg(not(unix))]
fn configure_sidecar_command(_cmd: &mut Command) {}

/// Compute a `PATH` that prepends the bin dirs where the agent CLIs (claude,
/// codex, nlm) commonly live — `~/.local/bin`, `/opt/homebrew/bin`,
/// `/usr/local/bin` — ahead of the inherited PATH. A macOS Finder-launched
/// `.app` inherits only a minimal `/usr/bin:/bin:/usr/sbin:/sbin`, so without
/// this the sidecar's `shutil.which(...)` cannot resolve the CLI LLM providers
/// (see `host_llm/discovery.py`). Candidate dirs already present are dropped so
/// PATH is not duplicated; the inherited entries are preserved after them.
///
/// Pure (no env or filesystem access) so it is unit-testable. Nonexistent
/// candidate dirs are intentionally kept — `which` simply skips them, and it
/// keeps the result filesystem-independent.
#[cfg(unix)]
fn build_augmented_path(current: &std::ffi::OsStr, home: Option<PathBuf>) -> std::ffi::OsString {
    let mut prepend: Vec<PathBuf> = Vec::new();
    if let Some(home) = home {
        prepend.push(home.join(".local/bin"));
    }
    prepend.push(PathBuf::from("/opt/homebrew/bin"));
    prepend.push(PathBuf::from("/usr/local/bin"));

    let existing: Vec<PathBuf> = std::env::split_paths(current).collect();
    prepend.retain(|dir| !existing.contains(dir));

    let ordered = prepend.into_iter().chain(existing);
    std::env::join_paths(ordered).unwrap_or_else(|_| current.to_os_string())
}

/// Prepend the agent-CLI bin dirs onto the sidecar child's `PATH` so it can
/// resolve claude/codex/nlm even when launched from Finder. macOS/Linux only —
/// Windows resolves these tools differently and is left untouched.
#[cfg(unix)]
fn augment_path_for_cli_providers(cmd: &mut Command) {
    let current = std::env::var_os("PATH").unwrap_or_default();
    let home = std::env::var_os("HOME").map(PathBuf::from);
    cmd.env("PATH", build_augmented_path(&current, home));
}

#[cfg(not(unix))]
fn augment_path_for_cli_providers(_cmd: &mut Command) {}

#[cfg(unix)]
fn process_group_exists(pid: u32) -> bool {
    let rc = unsafe { libc::kill(-(pid as libc::pid_t), 0) };
    if rc == 0 {
        return true;
    }

    matches!(std::io::Error::last_os_error().raw_os_error(), Some(libc::EPERM))
}

#[cfg(unix)]
fn signal_process_group(pid: u32, signal: i32) {
    let _ = unsafe { libc::kill(-(pid as libc::pid_t), signal) };
}

/// Spawn the sidecar and block (in the calling thread) until it announces
/// `LOGIC_LOOM_API_READY:<port>`. Then the spawning thread continues to drain
/// stdout/stderr (so the child does not block on a full pipe) and forwards
/// log lines to this process's stderr.
pub fn launch(app: AppHandle) -> Result<u16, SidecarError> {
    let root = workspace_root();
    log::info!("spawning logic-loom-api; workspace root: {}", root.display());

    let token = generate_token()?;
    let mut cmd = command_for_sidecar(&app, &root)?;
    cmd.env(TOKEN_ENV, &token);
    augment_path_for_cli_providers(&mut cmd);
    configure_sidecar_command(&mut cmd);
    // stdin is a pipe we never write to. Its write end lives in this process
    // (inside `Child`), so the OS closes it when we exit for any reason,
    // crash and `kill -9` included, and the sidecar's `--exit-on-stdin-eof`
    // watcher sees EOF and shuts down. This is what stops orphaned sidecars.
    cmd.stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = cmd.spawn().map_err(SidecarError::Spawn)?;

    let stdout = child.stdout.take().ok_or(SidecarError::NoStdout)?;
    let stderr = child.stderr.take();
    let pid = child.id();

    // Stash child early so shutdown can find it even if handshake fails later.
    // Checked under the child lock: `shutdown` sets the flag before taking the
    // lock, so either it sees this child or we see the flag.
    let state = app.state::<Arc<SidecarState>>();
    {
        let mut guard = state.child.lock().unwrap();
        if state.shutting_down.load(Ordering::SeqCst) {
            drop(guard);
            terminate_child(child);
            return Err(SidecarError::HandshakeFailed(
                "app is shutting down; sidecar stopped".into(),
            ));
        }
        *guard = Some(child);
    }
    *state.token.lock().unwrap() = Some(token);

    // Drain stderr in a separate thread; just forward to our stderr.
    if let Some(err) = stderr {
        thread::spawn(move || {
            let reader = BufReader::new(err);
            for line in reader.lines().map_while(Result::ok) {
                eprintln!("[logic-loom-api] {line}");
            }
        });
    }

    // Read stdout line-by-line; the first matching READY line resolves the
    // port. After that, keep draining so the child never blocks on a full
    // pipe.
    let (tx, rx) = std::sync::mpsc::channel::<Result<u16, String>>();
    let app_for_thread = app.clone();
    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        let mut announced = false;
        for line in reader.lines().map_while(Result::ok) {
            if !announced {
                if let Some(port) = parse_ready_line(&line) {
                    announced = true;
                    {
                        let st = app_for_thread.state::<Arc<SidecarState>>();
                        *st.port.lock().unwrap() = Some(port);
                    }
                    let _ = app_for_thread.emit("logic-loom://ready", ReadyPayload { port });
                    let _ = tx.send(Ok(port));
                    continue;
                }
            }
            eprintln!("[logic-loom-api] {line}");
        }
        if !announced {
            let _ = tx.send(Err(format!("sidecar (pid {pid}) exited before announcing READY")));
        }
    });

    match rx.recv_timeout(Duration::from_secs(15)) {
        Ok(Ok(port)) => Ok(port),
        Ok(Err(msg)) => Err(SidecarError::HandshakeFailed(msg)),
        Err(_) => Err(SidecarError::HandshakeTimeout),
    }
}

fn parse_ready_line(line: &str) -> Option<u16> {
    let trimmed = line.trim();
    let rest = trimmed.strip_prefix("LOGIC_LOOM_API_READY:")?;
    rest.parse().ok()
}

/// Stop the sidecar: SIGTERM, wait up to 5s, then SIGKILL.
pub fn shutdown(app: &AppHandle) {
    let state = app.state::<Arc<SidecarState>>();
    state.shutting_down.store(true, Ordering::SeqCst);
    *state.port.lock().unwrap() = None;
    *state.token.lock().unwrap() = None;
    let child = state.child.lock().unwrap().take();
    if let Some(child) = child {
        terminate_child(child);
    }
}

/// Kill the whole tree under `pid` on Windows. `Child::kill` only reaches the
/// PyInstaller bootloader, leaving the Python process it started running.
#[cfg(windows)]
fn kill_process_tree(pid: u32) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let _ = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

/// SIGTERM the process group, wait up to 5s, then SIGKILL (Unix); kill the
/// process tree (Windows).
fn terminate_child(mut child: Child) {
    let pid = child.id();
    log::info!("shutting down logic-loom-api pid={pid}");

    #[cfg(unix)]
    {
        signal_process_group(pid, libc::SIGTERM);
    }
    #[cfg(windows)]
    {
        kill_process_tree(pid);
        let _ = child.kill();
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = child.kill();
    }

    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let exited = match child.try_wait() {
            Ok(Some(_)) => true,
            Ok(None) => false,
            Err(_) => true,
        };

        #[cfg(unix)]
        {
            if !process_group_exists(pid) {
                let _ = child.wait();
                return;
            }

            if Instant::now() >= deadline {
                signal_process_group(pid, libc::SIGKILL);
                let _ = child.wait();
                return;
            }

            if exited {
                thread::sleep(Duration::from_millis(100));
                continue;
            }

            thread::sleep(Duration::from_millis(100));
        }

        #[cfg(not(unix))]
        {
            if exited {
                return;
            }

            if Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                return;
            }

            thread::sleep(Duration::from_millis(100));
        }
    }
}

#[tauri::command]
pub fn get_api_port(state: tauri::State<'_, Arc<SidecarState>>) -> Option<u16> {
    *state.port.lock().unwrap()
}

/// The per-launch API token. `None` until the sidecar has been spawned.
#[tauri::command]
pub fn get_api_token(state: tauri::State<'_, Arc<SidecarState>>) -> Option<String> {
    state.token.lock().unwrap().clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ready_line() {
        assert_eq!(parse_ready_line("LOGIC_LOOM_API_READY:54321"), Some(54321));
        assert_eq!(parse_ready_line("  LOGIC_LOOM_API_READY:1\n"), Some(1));
    }

    #[test]
    fn rejects_unrelated_lines() {
        assert_eq!(parse_ready_line("hello"), None);
        assert_eq!(parse_ready_line("LOGIC_LOOM_API_READY:not-a-port"), None);
        assert_eq!(parse_ready_line(""), None);
    }

    #[test]
    fn generated_tokens_are_64_hex_chars_and_distinct() {
        let a = generate_token().expect("token");
        let b = generate_token().expect("token");
        assert_eq!(a.len(), 64);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
    }

    #[cfg(unix)]
    #[test]
    fn sidecar_commands_run_in_their_own_process_group() {
        let mut cmd = Command::new("sh");
        cmd.arg("-c")
            .arg("cat >/dev/null")
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        configure_sidecar_command(&mut cmd);

        let mut child = cmd.spawn().expect("spawn test child");
        let pid = child.id() as libc::pid_t;
        let pgid = unsafe { libc::getpgid(pid) };

        assert_eq!(pgid, pid);
        assert!(process_group_exists(child.id()));

        signal_process_group(child.id(), libc::SIGKILL);
        let _ = child.wait();
    }

    #[cfg(unix)]
    #[test]
    fn augmented_path_prepends_cli_bin_dirs() {
        let current = std::ffi::OsString::from("/usr/bin:/bin");
        let out = build_augmented_path(&current, Some(PathBuf::from("/Users/tester")));
        let dirs: Vec<PathBuf> = std::env::split_paths(&out).collect();

        // ~/.local/bin is prepended first, then homebrew + /usr/local/bin.
        assert_eq!(dirs.first(), Some(&PathBuf::from("/Users/tester/.local/bin")));
        assert!(dirs.contains(&PathBuf::from("/opt/homebrew/bin")));
        assert!(dirs.contains(&PathBuf::from("/usr/local/bin")));
        // inherited entries are preserved after the prepended dirs.
        assert!(dirs.contains(&PathBuf::from("/usr/bin")));
        assert!(dirs.contains(&PathBuf::from("/bin")));
    }

    #[cfg(unix)]
    #[test]
    fn augmented_path_does_not_duplicate_existing_dirs() {
        // /opt/homebrew/bin already on PATH must not appear twice.
        let current = std::ffi::OsString::from("/opt/homebrew/bin:/usr/bin");
        let out = build_augmented_path(&current, None);
        let dirs: Vec<PathBuf> = std::env::split_paths(&out).collect();

        let homebrew_count = dirs
            .iter()
            .filter(|d| **d == PathBuf::from("/opt/homebrew/bin"))
            .count();
        assert_eq!(homebrew_count, 1);
        // /usr/local/bin was not present, so it is still added.
        assert!(dirs.contains(&PathBuf::from("/usr/local/bin")));
    }
}
