//! 桌面排障日志：关键路径日志同时写 stderr 与 app 数据目录下的
//! `logs/native.log`。安装版 GUI 没有控制台，stderr 会全部丢失，
//! 落盘文件是内测用户报障时的唯一第一现场；诊断包导出其尾部。

use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_LOG_BYTES: u64 = 4 * 1024 * 1024;
const ROTATED_SUFFIX: &str = ".1";

// 初始化失败（目录不可写等）时保持 None，write_line 静默降级为纯 stderr。
static LOG_PATH: OnceLock<Mutex<Option<PathBuf>>> = OnceLock::new();

pub fn init(log_dir: PathBuf) {
    let slot = LOG_PATH.get_or_init(|| Mutex::new(None));
    if let Ok(mut guard) = slot.lock() {
        if guard.is_none() && std::fs::create_dir_all(&log_dir).is_ok() {
            *guard = Some(log_dir.join("native.log"));
        }
    }
}

fn timestamp_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0)
}

/// 追加一行日志；超限时把当前文件轮转为 `.1`（覆盖旧轮转，封顶 2 份）。
/// 任何 IO 失败静默跳过——排障日志不允许拖垮采集链路。
fn append_line(path: &Path, line: &str) {
    if path
        .metadata()
        .is_ok_and(|meta| meta.len() >= MAX_LOG_BYTES)
    {
        let mut rotated = path.as_os_str().to_os_string();
        rotated.push(ROTATED_SUFFIX);
        let _ = std::fs::rename(path, PathBuf::from(rotated));
    }
    let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) else {
        return;
    };
    let _ = writeln!(file, "{} {}", timestamp_ms(), line);
}

pub fn write_line(line: &str) {
    let slot = match LOG_PATH.get() {
        Some(slot) => slot,
        None => return,
    };
    let Ok(guard) = slot.lock() else {
        return;
    };
    if let Some(path) = guard.as_ref() {
        append_line(path, line);
    }
}

/// tee 版 `eprintln!`：开发期照常上控制台，同时落 `logs/native.log`。
#[macro_export]
macro_rules! dlog {
    ($($arg:tt)*) => {{
        let __line = format!($($arg)*);
        eprintln!("{}", __line);
        $crate::diag_log::write_line(&__line);
    }};
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn scratch_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "aiming-cookie-diag-log-{}-{name}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("scratch dir");
        dir
    }

    #[test]
    fn append_line_persists_with_timestamp_prefix() {
        let log = scratch_dir("persist").join("native.log");
        append_line(&log, "capture-export: hello");
        let contents = fs::read_to_string(&log).expect("log file");
        assert!(contents.ends_with(" capture-export: hello\n"), "{contents}");
        let timestamp = contents.trim().split(' ').next().expect("timestamp");
        assert!(timestamp.parse::<u128>().is_ok());
    }

    #[test]
    fn append_line_rotates_oversized_log() {
        let dir = scratch_dir("rotate");
        let log = dir.join("native.log");
        fs::write(&log, vec![b'x'; MAX_LOG_BYTES as usize + 1]).expect("seed oversized log");
        append_line(&log, "after-limit");
        assert!(dir.join("native.log.1").is_file());
        let fresh = fs::read_to_string(&log).expect("fresh log");
        assert!(fresh.ends_with(" after-limit\n"), "{fresh}");
        assert_eq!(fresh.lines().count(), 1);
    }

    #[test]
    fn append_line_creates_missing_file_without_error() {
        let log = scratch_dir("create").join("native.log");
        append_line(&log, "first line");
        assert!(log.is_file());
    }

    // 全局单例只允许一个测试触碰，避免并行测试争抢 OnceLock。
    #[test]
    fn init_registers_global_sink_and_write_line_persists() {
        let dir = scratch_dir("global");
        let log = dir.join("native.log");
        init(dir.clone());
        write_line("via global sink");
        let contents = fs::read_to_string(&log).expect("log file");
        assert!(contents.ends_with(" via global sink\n"), "{contents}");
    }

    #[test]
    fn write_line_without_init_is_silent_noop() {
        write_line("no sink configured");
    }
}
