//! 自定义数据存储位置（自定义根）。
//!
//! 数据根的唯一权威是 Rust 壳：默认根由 Tauri `app_data_dir()` 给出。用户可在
//! 设置里把数据根改到任意盘；指针文件 `storage-location.json` **必须留在默认
//! 根**（它指向别处，所以自己不能被搬走），内容形如：
//!
//! ```json
//! {"schema":1,"custom_root":"D:\\ACData","updated_at":"2026-09-28T00:00:00Z"}
//! ```
//!
//! 生效根解析（`resolve_effective_root`）：无指针 / 指针损坏 / 自定义根不可用
//! → 回落默认根并只落一行日志，绝不让坏指针挡启动；自定义根不存在时自动创建。
//!
//! 迁移由**迁移记录**驱动（`storage-migration.json`，与指针同在默认根）：设置
//! 页改位置时记下 source（当时的生效根，数据实际所在）与 target（新位置），重启
//! 时按记录执行搬迁。这样「恢复默认位置」也能知道数据原来在哪个自定义根，而不
//! 必从指针反推。搬迁口径：逐顶层条目「逐文件比对（mtime 新者胜：目标缺失或源
//! 更新才拷，拷走原子替换）→ 逐文件对账（文件数 + 字节数；字节数不符但目标
//! mtime 不比源旧算目标胜，记入 superseded_files）→ 通过才删源」。迁移 pending
//! 期间生效根已是目标根，**目标侧新写入是权威**（mtime 新者胜；已知边界：FAT/
//! exFAT 的 mtime 粒度是 2 秒，同粒度内的先后无法区分，一律按目标胜处理），绝不
//! 拿旧源回滚目标的新数据；**对账不过绝不删源**。单条目失败分两
//! 类：文件被占用（os error 32/33，共享冲突/字节锁，自家后端与侧车常态持有）
//! 只把该条目留在 pending 继续搬其余，收尾时 pending 非空即 `partial`（下次启动
//! 续迁）；其他失败仍整体停机（`failed`），源原样保留。
//!
//! 根级**运行时痕迹不参与搬迁**（`MIGRATION_SKIP_ENTRIES` 逐项理由见常量注释）：
//! 它们要么被本应用进程在启动时创建并持有句柄（搬 = 自锁必败，2026-09-29 真机
//! P0 的直接成因），要么是每次启动都会重建/重写的瞬态文件，要么是权威位置就在
//! 默认根的元数据（位置指针、迁移记录）。作为折中，迁移末尾把旧 `logs/` 里目标
//! 尚缺的文件**复制**一份过去（不校验、不删源），保住诊断包的历史日志连续性；
//! 日志有轮转上限（MB 级），留在原地对磁盘占用可忽略。

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

/// 指针文件名。永远位于默认数据根（`app_data_dir()`）。
pub const STORAGE_LOCATION_FILE_NAME: &str = "storage-location.json";
/// 迁移记录文件名。与指针同在默认根。
pub const STORAGE_MIGRATION_FILE_NAME: &str = "storage-migration.json";
/// 指针 schema 版本。
pub const STORAGE_LOCATION_SCHEMA: u32 = 1;
/// 迁移预检保留余量：目标盘除源数据总量外还要留这么多空闲字节。
const MIGRATION_FREE_SPACE_HEADROOM_BYTES: u64 = 64 * 1024 * 1024;
/// 迁移记录写失败时的重试次数与间隔（并发读窗导致的共享冲突是瞬时的）。
const MIGRATION_WRITE_ATTEMPTS: u8 = 5;
const MIGRATION_WRITE_RETRY_DELAY: std::time::Duration = std::time::Duration::from_millis(20);
/// 迁移期间不搬的目录名（历史日志合并见 `merge_legacy_logs`）。
const MIGRATION_LOG_DIR_NAME: &str = "logs";
/// 迁移不搬的根级条目（目录名含 `logs/`）——壳/后端/侧车的运行时痕迹。
/// 搬它们只有坏处：要么必然撞上本应用进程自己持有的句柄（自锁必败），
/// 要么是每次启动都会重建/重写的瞬态文件，要么权威位置就在默认根。
/// 逐项理由：
/// - `.runtime.lock`：后端进程启动即创建并持独占字节锁直到退出（防双实例
///   同根互踩）。迁移与后端同机同时启动，搬它必然以共享冲突/锁定失败收场
///   （2026-09-29 真机 P0：首个条目即中止，整场迁移永远失败）；新根的锁由
///   后端启动时自行创建。
/// - `desktop-runtime.json`：壳写给子进程的运行时配置，每次启动重写。
/// - `coach-debug.log` / `coach-error.log`：coach 侧车进程持续追加的日志，
///   句柄常开，copy + 对账在语义上不成立（与 `logs/` 同理）。
/// - `logs/`：壳的日志句柄启动早期就绑定生效根，壳/Python/侧车三进程并发
///   追加，无法对账。
/// - `storage-location.json`：位置指针，权威位置在默认根，自己不能被搬走。
/// - `storage-migration.json`：迁移记录，续迁的驱动依据，绝不能动。
const MIGRATION_SKIP_ENTRIES: &[&str] = &[
    ".runtime.lock",
    "desktop-runtime.json",
    "coach-debug.log",
    "coach-error.log",
    MIGRATION_LOG_DIR_NAME,
    STORAGE_LOCATION_FILE_NAME,
    STORAGE_MIGRATION_FILE_NAME,
];
/// 原子拷贝的 temp 文件后缀：temp 命名为 `.{目标文件名}.acmigration-tmp`，与
/// 目标同目录；写失败即清理，崩溃残留由 `cleanup_migration_temps` 续迁开始扫除。
const MIGRATION_TEMP_SUFFIX: &str = ".acmigration-tmp";
/// superseded_files（目标胜清单）的条数上限：迁移记录文件被设置页轮询与反复
/// 序列化，不能被异常多的并发改写撑爆；超出上限的只体现在 diag 汇总的计数里。
const SUPERSEDED_FILES_LIMIT: usize = 200;

pub const MIGRATION_PHASE_PLANNED: &str = "planned";
pub const MIGRATION_PHASE_RUNNING: &str = "running";
pub const MIGRATION_PHASE_DONE: &str = "done";
pub const MIGRATION_PHASE_PARTIAL: &str = "partial";
pub const MIGRATION_PHASE_FAILED: &str = "failed";

// ── 指针 ────────────────────────────────────────────────────────────────────

#[derive(serde::Serialize, serde::Deserialize, Clone, Debug, PartialEq)]
pub struct StorageLocationPointer {
    pub schema: u32,
    pub custom_root: Option<String>,
    pub updated_at: String,
}

/// 解析指针。JSON 坏 / schema 不符 / custom_root 非法 → `None`（按默认走）。
pub fn parse_pointer(bytes: &[u8]) -> Option<StorageLocationPointer> {
    let value: serde_json::Value = serde_json::from_slice(bytes).ok()?;
    let object = value.as_object()?;
    if object.get("schema")?.as_u64()? != STORAGE_LOCATION_SCHEMA as u64 {
        return None;
    }
    let custom_root = match object.get("custom_root") {
        Some(serde_json::Value::Null) | None => None,
        Some(serde_json::Value::String(text)) if !text.trim().is_empty() => {
            Some(text.trim().to_string())
        }
        // 空串按「没设自定义根」处理；其他类型（数字/对象）是坏指针。
        Some(serde_json::Value::String(_)) => None,
        Some(_) => return None,
    };
    Some(StorageLocationPointer {
        schema: STORAGE_LOCATION_SCHEMA,
        custom_root,
        updated_at: object
            .get("updated_at")
            .and_then(|value| value.as_str())
            .unwrap_or_default()
            .to_string(),
    })
}

/// 从默认根读指针；`(None, None)` = 无指针（用默认），`(None, Some(原因))` = 坏指针。
pub fn read_pointer(default_root: &Path) -> (Option<StorageLocationPointer>, Option<String>) {
    let bytes = match fs::read(default_root.join(STORAGE_LOCATION_FILE_NAME)) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return (None, None),
        Err(error) => return (None, Some(format!("unreadable: {error}"))),
    };
    match parse_pointer(&bytes) {
        Some(pointer) => (Some(pointer), None),
        None => (None, Some("malformed".to_string())),
    }
}

/// 原子写指针。`custom_root = None` 即「恢复默认位置」。
pub fn write_pointer(default_root: &Path, custom_root: Option<&str>) -> Result<(), String> {
    let pointer = StorageLocationPointer {
        schema: STORAGE_LOCATION_SCHEMA,
        custom_root: custom_root.map(|value| value.to_string()),
        updated_at: now_iso_like(),
    };
    let payload = serde_json::to_vec_pretty(&pointer)
        .map_err(|_| "storage_location.serialize_failed".to_string())?;
    crate::atomic_write_file(&default_root.join(STORAGE_LOCATION_FILE_NAME), &payload)
        .map_err(|error| format!("storage_location.write_failed: {error}"))
}

/// 自定义根可用性：绝对路径、不是文件、能创建、能写。返回稳定错误码。
pub fn custom_root_is_usable(path: &Path) -> Result<(), String> {
    if !path.is_absolute() {
        return Err("storage_location.not_absolute".to_string());
    }
    if path.is_file() {
        return Err("storage_location.not_a_directory".to_string());
    }
    if !path.exists() {
        fs::create_dir_all(path).map_err(|_| "storage_location.create_failed".to_string())?;
    }
    // 写权限用一次性探测文件验证（Windows 上目录 ACL 与「可创建」不等价）。
    let probe = path.join(format!(".aiming-cookie-write-probe-{}", std::process::id()));
    match fs::write(&probe, b"probe") {
        Ok(()) => {
            let _ = fs::remove_file(&probe);
            Ok(())
        }
        Err(_) => Err("storage_location.not_writable".to_string()),
    }
}

/// 校验并归一化用户选择的存储位置：`None` = 恢复默认。
/// 拒绝：相对路径 / 指向文件 / 创建失败（盘符不存在）/ 不可写 / 与默认根互相嵌套。
pub fn normalize_custom_root(
    default_root: &Path,
    requested: Option<&str>,
) -> Result<Option<String>, String> {
    let Some(requested) = requested.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(None);
    };
    let candidate = PathBuf::from(requested);
    custom_root_is_usable(&candidate)?;
    if same_path(&candidate, default_root) {
        // 自定义根 = 默认根 → 视为恢复默认。
        return Ok(None);
    }
    if is_ancestor(&candidate, default_root) || is_ancestor(default_root, &candidate) {
        // 互相嵌套会让搬迁自我包含（源里含目标或目标里含源），必须拒绝。
        return Err("storage_location.nested".to_string());
    }
    Ok(Some(candidate.to_string_lossy().into_owned()))
}

/// 剥掉 Windows 扩展路径前缀：`fs::canonicalize()` 在 Windows 上返回
/// `\\?\E:\ACData`（verbatim）形态，这个形态一旦当生效根传出去（指针文件、
/// 子进程 `DATA_ROOT`、前端「当前位置」），后端按前缀比对「哪些文件属于数据
/// 根」就会全部失配（2026-09-29 真机：2.5GB Run 录像从占用分类里消失）。
/// 统一在这里剥掉：`\\?\E:\ACData` → `E:\ACData`，`\\?\UNC\server\share` →
/// `\\server\share`，其余形态原样返回。例外：剥完超过 260 字符（MAX_PATH）的
/// 路径保留前缀——不带 `\\?\` 的 Win32 API 处理不了超长路径，此时前缀是
/// 功能性的，不是泄漏。
pub fn strip_extended_prefix(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    let stripped = match text.strip_prefix(r"\\?\UNC\") {
        Some(rest) => Some(format!(r"\\{rest}")),
        None => text.strip_prefix(r"\\?\").map(str::to_string),
    };
    let Some(stripped) = stripped else {
        return path.to_path_buf();
    };
    if stripped.chars().count() > 260 {
        return path.to_path_buf();
    }
    PathBuf::from(stripped)
}

/// 返回解析后的绝对路径（已确认可写），供直接使用。
pub fn resolve_custom_root(requested: &str) -> PathBuf {
    strip_extended_prefix(&fs::canonicalize(requested).unwrap_or_else(|_| PathBuf::from(requested)))
}

/// 解析「生效数据根」：指针合法 + 自定义根可用 → 自定义根；否则默认根。
/// 返回值统一过 [`strip_extended_prefix`]：旧版本指针里可能已经落了 `\\?\`
/// 形态，读出来也必须以干净形态交给子进程与前端。
pub fn resolve_effective_root(default_root: &Path) -> PathBuf {
    let (pointer, problem) = read_pointer(default_root);
    if let Some(problem) = problem {
        crate::diag_log::write_line(&format!(
            "storage-location: ignoring pointer ({problem}); using default data root {}",
            default_root.display()
        ));
        return strip_extended_prefix(default_root);
    }
    let Some(custom_root) = pointer.and_then(|pointer| pointer.custom_root) else {
        return strip_extended_prefix(default_root);
    };
    let candidate = PathBuf::from(&custom_root);
    if let Err(reason) = custom_root_is_usable(&candidate) {
        crate::diag_log::write_line(&format!(
            "storage-location: ignoring custom root {custom_root} ({reason}); using default data root {}",
            default_root.display()
        ));
        return strip_extended_prefix(default_root);
    }
    if same_path(&candidate, default_root) {
        return strip_extended_prefix(default_root);
    }
    strip_extended_prefix(&candidate)
}

/// 路径等价比较（大小写不敏感；不存在时退化为字面比较）。
fn same_path(left: &Path, right: &Path) -> bool {
    let normalize = |path: &Path| -> String {
        let resolved = fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
        resolved
            .to_string_lossy()
            .replace('/', "\\")
            .trim_end_matches('\\')
            .to_lowercase()
    };
    normalize(left) == normalize(right)
}

/// `ancestor` 是否是 `path` 的祖先目录（严格包含，不含相等）。
fn is_ancestor(ancestor: &Path, path: &Path) -> bool {
    let normalize = |value: &Path| -> Vec<String> {
        fs::canonicalize(value)
            .unwrap_or_else(|_| value.to_path_buf())
            .to_string_lossy()
            .replace('/', "\\")
            .trim_end_matches('\\')
            .to_lowercase()
            .split('\\')
            .map(str::to_string)
            .collect()
    };
    let ancestor = normalize(ancestor);
    let path = normalize(path);
    ancestor.len() < path.len() && path[..ancestor.len()] == ancestor[..]
}

fn now_iso_like() -> String {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or(0);
    format_unix_seconds(seconds)
}

/// epoch 秒 → `YYYY-MM-DDTHH:MM:SSZ`（chrono 不在依赖里，用 days_from_civil 逆运算）。
fn format_unix_seconds(seconds: i64) -> String {
    let days = seconds.div_euclid(86_400);
    let seconds_of_day = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        seconds_of_day / 3600,
        (seconds_of_day % 3600) / 60,
        seconds_of_day % 60,
    )
}

/// 自 1970-01-01 起的天数 → 公历日期（Howard Hinnant 的 civil_from_days）。
fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let day_of_era = z - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = if month_prime < 10 {
        month_prime + 3
    } else {
        month_prime - 9
    };
    (if month <= 2 { year + 1 } else { year }, month, day)
}

// ── 迁移记录 ────────────────────────────────────────────────────────────────

#[derive(serde::Serialize, serde::Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct StorageMigrationState {
    /// 数据当前实际所在根（记录写入时的生效根）。
    pub source_root: String,
    /// 迁移目标根（记录写入时请求的新位置）。
    pub target_root: String,
    pub phase: String,
    pub moved_entries: Vec<String>,
    pub pending_entries: Vec<String>,
    /// 迁移期间保留目标侧更新版本的文件清单（`条目名/条目内相对路径`）。目标胜
    /// 是新者胜口径的正常结果，不是失败；有界（`SUPERSEDED_FILES_LIMIT`），旧记录
    /// 文件缺该字段时按空清单补齐。
    #[serde(default)]
    pub superseded_files: Vec<String>,
    /// 源数据总字节数（预检与进度用）。
    pub total_bytes: u64,
    pub copied_bytes: u64,
    pub error: Option<String>,
    pub updated_at: String,
}

/// 迁移记录读不出来时的稳定错误码（供状态命令回给前端）。
pub fn read_migration_state(default_root: &Path) -> Option<StorageMigrationState> {
    let bytes = fs::read(default_root.join(STORAGE_MIGRATION_FILE_NAME)).ok()?;
    serde_json::from_slice(&bytes).ok()
}

/// 原子写迁移记录。设置页会轮询该文件，并发读窗可能让 Windows 的原子替换
/// 因共享冲突失败（用户态替换不是排队操作）——短暂重试几次，仍失败才回报。
fn write_migration_state(default_root: &Path, state: &StorageMigrationState) -> Result<(), String> {
    let payload = serde_json::to_vec_pretty(state)
        .map_err(|_| "storage_migration.serialize_failed".to_string())?;
    let path = default_root.join(STORAGE_MIGRATION_FILE_NAME);
    let mut last_error = String::new();
    for attempt in 0..MIGRATION_WRITE_ATTEMPTS {
        match crate::atomic_write_file(&path, &payload) {
            Ok(()) => return Ok(()),
            Err(error) => {
                last_error = error.to_string();
                if attempt + 1 < MIGRATION_WRITE_ATTEMPTS {
                    std::thread::sleep(MIGRATION_WRITE_RETRY_DELAY);
                }
            }
        }
    }
    Err(format!("storage_migration.write_failed: {last_error}"))
}

/// 顶层条目里需要搬迁的项：排除指针、迁移记录与 logs（见模块头注释）。
fn migratable_entries(source: &Path) -> Vec<PathBuf> {
    let Ok(entries) = fs::read_dir(source) else {
        return Vec::new();
    };
    let mut paths: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| {
            let name = path
                .file_name()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_default();
            !MIGRATION_SKIP_ENTRIES.contains(&name.as_str()) && name != MIGRATION_LOG_DIR_NAME
        })
        .collect();
    paths.sort();
    paths
}

/// 源目录下的相对文件清单（相对路径 → 字节数）。符号链接不跟随（metadata 走
/// symlink_metadata），避免对账把链接指向的外部内容算进来。
fn collect_files(root: &Path) -> io::Result<Vec<(PathBuf, u64)>> {
    let mut files = Vec::new();
    let mut queue = vec![root.to_path_buf()];
    while let Some(dir) = queue.pop() {
        for entry in fs::read_dir(&dir)? {
            let entry = entry?;
            let metadata = entry.metadata()?;
            if metadata.is_dir() {
                queue.push(entry.path());
            } else if metadata.is_file() {
                files.push((entry.path(), metadata.len()));
            }
        }
    }
    Ok(files)
}

fn relative_files(root: &Path, source: &Path) -> io::Result<Vec<(PathBuf, u64)>> {
    Ok(collect_files(source)?
        .into_iter()
        .filter_map(|(path, size)| {
            path.strip_prefix(root)
                .ok()
                .map(|relative| (relative.to_path_buf(), size))
        })
        .collect())
}

fn total_bytes(files: &[(PathBuf, u64)]) -> u64 {
    files.iter().map(|(_, size)| *size).sum()
}

/// 原子拷贝：先写到目标同目录的确定性 temp（`.{文件名}.acmigration-tmp`）再原子
/// 替换落位。两个目的：崩了不会在目标路径留半份文件（半份只在 temp 名下，续迁
/// 开始扫除）；落位是原子 rename，读方看到的目标路径要么是旧内容要么是完整新
/// 内容。保留源 mtime：已拷文件 mtime == 源 mtime，天然成为断点续迁判据（同
/// mtime = 上轮已拷完）。返回是否真的拷贝落位：false = rename 前复查发现目标
/// 已被并发写成更新版本（目标胜），放弃覆盖。
fn copy_file(source: &Path, destination: &Path) -> io::Result<bool> {
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)?;
    }
    let file_name = destination
        .file_name()
        .map(|value| value.to_string_lossy().into_owned())
        .unwrap_or_default();
    let temp = destination.with_file_name(format!(".{file_name}{MIGRATION_TEMP_SUFFIX}"));
    let outcome = (|| -> io::Result<bool> {
        fs::copy(source, &temp)?;
        // 给 temp 带上源修改时间：搬迁不改变「最近使用」排序，同时 mtime 是续迁判据。
        let source_modified = fs::metadata(source)
            .and_then(|metadata| metadata.modified())
            .ok();
        if let Some(modified) = source_modified {
            let _ = filetime_set(&temp, modified);
        }
        // rename 前复查目标 mtime，缩小「比对之后目标被改写」的竞态窗：目标已
        // 更新 → 目标胜，放弃覆盖（源 mtime 不可读时同样保守放弃）。
        let superseded_by_target = match source_modified {
            Some(source_time) => fs::metadata(destination)
                .and_then(|metadata| metadata.modified())
                .map(|target_time| target_time >= source_time)
                .unwrap_or(false),
            None => true,
        };
        if superseded_by_target {
            return Ok(false);
        }
        crate::atomic_replace(&temp, destination)?;
        Ok(true)
    })();
    // 成功时 temp 已被改名走，这里的删除是幂等空操作；失败/放弃时清掉半份拷贝。
    let _ = fs::remove_file(&temp);
    outcome
}

/// Windows 共享冲突（32）/ 字节范围锁冲突（33）：文件正被别的句柄占用。
/// 自家后端（数据库、`.runtime.lock`）与侧车（日志）在迁移期间常态持有这些
/// 句柄——这是并发运行的常态，不是致命错误。
fn is_lock_error(error: &io::Error) -> bool {
    matches!(error.raw_os_error(), Some(32) | Some(33))
}

/// 单条目搬迁的错误。`locked` 标记「文件被占用（os error 32/33）」：该类失败
/// 只把条目留在 pending、继续搬其余条目，收尾记 `partial`（重启续迁）；其余
/// 错误仍整体停机（`failed`，源原样保留）。`message` 是记录与日志用的原文。
#[derive(Debug)]
struct MigrationEntryError {
    message: String,
    locked: bool,
}

impl MigrationEntryError {
    fn other(message: String) -> Self {
        Self {
            message,
            locked: false,
        }
    }

    fn from_io(context: &str, error: &io::Error) -> Self {
        Self {
            message: format!("{context}: {error}"),
            locked: is_lock_error(error),
        }
    }
}

/// 逐文件对账（任务口径：文件数 + 字节数）：源清单里每个文件都必须在目标存在
/// 且字节数一致。只比对源清单，目标多出来的文件（迁移期间子进程新写的）不影响
/// 结论——否则并发写入会让迁移永远无法通过校验。例外：字节数不符但目标 mtime
/// 不比源旧（迁移窗口内目标侧被并发改写），按新者胜口径算目标胜——豁免并把该
/// 相对路径记入返回的 superseded 清单（源随后随条目整体删除）；其余不符保持
/// Err（fail-closed，源原样保留）。
fn verify_copy(source: &Path, destination: &Path) -> Result<(u64, u64, Vec<String>), String> {
    let files = relative_files(source, source).map_err(|error| format!("scan: {error}"))?;
    let mut missing_relative: Option<PathBuf> = None;
    let mut mismatched: Option<(PathBuf, u64, u64)> = None;
    let mut superseded: Vec<String> = Vec::new();
    for (relative, size) in &files {
        let target = destination.join(relative);
        match fs::metadata(&target) {
            Ok(metadata) if metadata.len() == *size => {}
            Ok(metadata) => {
                let source_time = fs::metadata(source.join(relative))
                    .and_then(|metadata| metadata.modified())
                    .ok();
                let target_time = metadata.modified().ok();
                let target_won = match (source_time, target_time) {
                    (Some(source_time), Some(target_time)) => target_time >= source_time,
                    // mtime 不可读时无法证明目标更新：保持 fail-closed。
                    _ => false,
                };
                if target_won {
                    superseded.push(relative.to_string_lossy().into_owned());
                } else {
                    mismatched.get_or_insert((relative.clone(), *size, metadata.len()));
                }
            }
            Err(_) => {
                missing_relative.get_or_insert(relative.clone());
            }
        }
    }
    if let Some((relative, expected, actual)) = mismatched {
        return Err(format!(
            "byte mismatch at {}: expected {expected}, found {actual}",
            relative.display()
        ));
    }
    if let Some(relative) = missing_relative {
        return Err(format!("missing at target: {}", relative.display()));
    }
    Ok((files.len() as u64, total_bytes(&files), superseded))
}

/// 把源目录搬进目标：逐文件补齐（mtime 新者胜）→ 逐文件对账 → 通过才删源。
///
/// 目标目录**只增不删**：迁移是重启时后台跑的，同一进程的子进程已经把新数据写进
/// 目标根（新会话、新日志、新的 run），清整目录会把它们一起删掉。迁移期间**目标
/// 侧新写入是权威**（mtime 新者胜）：目标缺失或源 mtime 更新才拷贝覆盖；目标同
/// mtime 或更新就保留目标——同 mtime 同字节数视为上轮已拷完（`copy_file` 保留源
/// mtime），字节数不同的由 `verify_copy` 统一豁免并记入 superseded 清单。已知
/// 边界：FAT/exFAT 的 mtime 粒度是 2 秒，同粒度内的先后无法区分，一律按目标胜
/// 处理——宁可少覆盖，不回滚目标的新数据。
fn migrate_entry(
    source: &Path,
    destination: &Path,
) -> Result<(u64, Vec<String>), MigrationEntryError> {
    let files = relative_files(source, source)
        .map_err(|error| MigrationEntryError::from_io("scan", &error))?;
    for (relative, _) in &files {
        let target = destination.join(relative);
        // 断点续迁判据（mtime 新者胜）：目标缺失或源更新才拷；目标同 mtime 或
        // 更新一律不动目标。
        if let Ok(target_metadata) = fs::metadata(&target) {
            let source_time = fs::metadata(source.join(relative))
                .and_then(|metadata| metadata.modified())
                .ok();
            let target_time = target_metadata.modified().ok();
            let source_wins = match (source_time, target_time) {
                (Some(source_time), Some(target_time)) => source_time > target_time,
                // mtime 不可读：无证据不覆盖，保守按目标胜处理。
                _ => false,
            };
            if !source_wins {
                continue;
            }
        }
        // false = rename 前复查发现目标已更新（目标胜），由下方对账豁免记录。
        let _copied = copy_file(&source.join(relative), &target).map_err(|error| {
            MigrationEntryError::from_io(&format!("copy {}", relative.display()), &error)
        })?;
    }
    let (_, bytes, superseded) =
        verify_copy(source, destination).map_err(MigrationEntryError::other)?;
    // 对账通过（字节数逐文件对上，目标侧改写的已按目标胜豁免）才允许删源；失败
    // 就在这里停，源原样保留。删源也可能撞上仍被持有的句柄（如正开着的数据库）
    // → 同样按锁定类留在 pending。
    fs::remove_dir_all(source)
        .map_err(|error| MigrationEntryError::from_io("remove source", &error))?;
    Ok((bytes, superseded))
}

/// 单文件条目的搬迁：同样新者胜判据，先对账后删源。返回 (字节数, 目标胜清单
/// ——单文件条目就是文件名本身)。
fn migrate_file(
    source: &Path,
    destination: &Path,
) -> Result<(u64, Vec<String>), MigrationEntryError> {
    let source_metadata = fs::metadata(source)
        .map_err(|error| MigrationEntryError::from_io("stat", &error))?;
    let size = source_metadata.len();
    let source_time = source_metadata.modified().ok();
    let name = source
        .file_name()
        .map(|value| value.to_string_lossy().into_owned())
        .unwrap_or_default();
    if let Ok(target_metadata) = fs::metadata(destination) {
        let target_time = target_metadata.modified().ok();
        let source_wins = match (source_time, target_time) {
            (Some(source_time), Some(target_time)) => source_time > target_time,
            // mtime 不可读：无证据不覆盖，保守按目标胜处理。
            _ => false,
        };
        if !source_wins {
            // 同 mtime 同字节数 = 上轮已拷完（copy_file 保留源 mtime）：只补删源。
            if target_metadata.len() == size {
                fs::remove_file(source)
                    .map_err(|error| MigrationEntryError::from_io("remove source", &error))?;
                return Ok((size, Vec::new()));
            }
            // 目标侧更新（或同 mtime 内容不同）：目标胜，不覆盖、记入清单。
            fs::remove_file(source)
                .map_err(|error| MigrationEntryError::from_io("remove source", &error))?;
            return Ok((size, vec![name]));
        }
    }
    // false = rename 前复查发现目标已更新（目标胜），保留目标、删源。
    let copied = copy_file(source, destination)
        .map_err(|error| MigrationEntryError::from_io("copy", &error))?;
    if !copied {
        fs::remove_file(source)
            .map_err(|error| MigrationEntryError::from_io("remove source", &error))?;
        return Ok((size, vec![name]));
    }
    let target_metadata = fs::metadata(destination)
        .map_err(|error| MigrationEntryError::from_io("verify", &error))?;
    if target_metadata.len() != size {
        // 拷完后目标又被并发改写：目标 mtime 不比源旧 → 目标胜，保留新内容并删
        // 源；否则对不上账，fail-closed（源原样保留）。
        let target_won = match (source_time, target_metadata.modified().ok()) {
            (Some(source_time), Some(target_time)) => target_time >= source_time,
            _ => false,
        };
        if target_won {
            fs::remove_file(source)
                .map_err(|error| MigrationEntryError::from_io("remove source", &error))?;
            return Ok((size, vec![name]));
        }
        return Err(MigrationEntryError::other(format!(
            "byte mismatch: expected {size}, found {}",
            target_metadata.len()
        )));
    }
    fs::remove_file(source)
        .map_err(|error| MigrationEntryError::from_io("remove source", &error))?;
    Ok((size, Vec::new()))
}

/// 目标盘可用空间（字节）。取不到返回 None（跳过预检，不阻塞迁移）。
#[cfg(windows)]
fn available_space(path: &Path) -> Option<u64> {
    use std::os::windows::ffi::OsStrExt;
    use winapi::shared::ntdef::ULARGE_INTEGER;
    use winapi::um::fileapi::GetDiskFreeSpaceExW;

    let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut free: ULARGE_INTEGER = unsafe { std::mem::zeroed() };
    let ok = unsafe {
        GetDiskFreeSpaceExW(
            wide.as_ptr(),
            &mut free,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    };
    (ok != 0).then(|| unsafe { *free.QuadPart_mut() })
}

#[cfg(not(windows))]
fn available_space(_path: &Path) -> Option<u64> {
    None
}

/// 目录总字节数（预检用）。
pub fn directory_total_bytes(root: &Path) -> u64 {
    collect_files(root)
        .map(|files| total_bytes(&files))
        .unwrap_or(0)
}

/// 待迁顶层条目的总字节数（进度与预检的口径：不含 logs 与指针等跳过项）。
fn entries_total_bytes(entries: &[PathBuf]) -> u64 {
    entries
        .iter()
        .map(|path| {
            if path.is_dir() {
                directory_total_bytes(path)
            } else {
                fs::metadata(path)
                    .map(|metadata| metadata.len())
                    .unwrap_or(0)
            }
        })
        .sum()
}

/// 迁移前预检：目标盘可用空间必须容得下源数据总量 + 余量。
pub fn precheck_free_space(source_root: &Path, target_root: &Path) -> Result<(), (u64, u64)> {
    let required = directory_total_bytes(source_root);
    match available_space(target_root) {
        Some(free) if free < required.saturating_add(MIGRATION_FREE_SPACE_HEADROOM_BYTES) => {
            Err((required, free))
        }
        _ => Ok(()),
    }
}

/// 设置文件修改时间：搬迁不改变「最近使用」排序（std 的 `File::set_modified`）。
fn filetime_set(path: &Path, modified: SystemTime) -> io::Result<()> {
    std::fs::OpenOptions::new()
        .write(true)
        .open(path)?
        .set_modified(modified)
}

/// 旧日志目录的文件级合并：目标缺失的同名文件复制一份过去，不校验、不删源。
/// 唯一目的是让诊断包在改位置后仍能读到历史日志（失败只落一行日志）。
fn merge_legacy_logs(source_root: &Path, target_root: &Path) {
    let source_logs = source_root.join(MIGRATION_LOG_DIR_NAME);
    if !source_logs.is_dir() {
        return;
    }
    let target_logs = target_root.join(MIGRATION_LOG_DIR_NAME);
    if fs::create_dir_all(&target_logs).is_err() {
        return;
    }
    let Ok(entries) = fs::read_dir(&source_logs) else {
        return;
    };
    for entry in entries.filter_map(Result::ok) {
        let target = target_logs.join(entry.file_name());
        if target.exists() || !entry.path().is_file() {
            continue;
        }
        let _ = fs::copy(entry.path(), &target);
    }
}

/// 扫除目标树里崩溃残留的原子拷贝 temp（`.名字.acmigration-tmp`）：temp 与目标
/// 同目录，可能嵌在任何子层级。删不掉就留着（隐藏文件，不参与对账，无碍）。
fn cleanup_migration_temps(root: &Path) {
    let Ok(files) = collect_files(root) else {
        return;
    };
    for (path, _) in files {
        let is_temp = path
            .file_name()
            .is_some_and(|name| name.to_string_lossy().ends_with(MIGRATION_TEMP_SUFFIX));
        if is_temp {
            let _ = fs::remove_file(path);
        }
    }
}

/// 执行迁移（重启时在后台线程调用）。
pub fn run_migration(
    default_root: &Path,
    source_root: &Path,
    target_root: &Path,
    mut state: StorageMigrationState,
) -> StorageMigrationState {
    let fail = |mut state: StorageMigrationState, message: String| -> StorageMigrationState {
        state.phase = MIGRATION_PHASE_FAILED.to_string();
        state.error = Some(message.clone());
        state.updated_at = now_iso_like();
        let _ = write_migration_state(default_root, &state);
        crate::diag_log::write_line(&format!("storage-migration: failed: {message}"));
        state
    };

    if same_path(source_root, target_root) {
        state.phase = MIGRATION_PHASE_DONE.to_string();
        state.pending_entries = Vec::new();
        state.error = None;
        state.updated_at = now_iso_like();
        let _ = write_migration_state(default_root, &state);
        return state;
    }
    if let Err(error) = fs::create_dir_all(target_root) {
        return fail(state, format!("target_unavailable: {error}"));
    }
    // 续迁开始先扫掉上次崩溃残留在目标侧的半份拷贝 temp（原子拷贝的落点）。
    cleanup_migration_temps(target_root);
    if let Err((required, available)) = precheck_free_space(source_root, target_root) {
        return fail(
            state,
            format!(
                "insufficient_space: need {required} bytes + headroom, available {available} bytes"
            ),
        );
    }

    let entries = migratable_entries(source_root);
    let pending: Vec<String> = entries
        .iter()
        .map(|path| {
            path.file_name()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_default()
        })
        .collect();
    state.phase = MIGRATION_PHASE_RUNNING.to_string();
    state.pending_entries = pending.clone();
    state.total_bytes = entries_total_bytes(&entries);
    state.copied_bytes = 0;
    state.moved_entries = Vec::new();
    state.superseded_files = Vec::new();
    state.error = None;
    state.updated_at = now_iso_like();
    if let Err(error) = write_migration_state(default_root, &state) {
        return fail(state, error);
    }

    let mut moved: Vec<String> = Vec::new();
    let mut copied_bytes = 0u64;
    let mut superseded_count = 0usize;
    for path in &entries {
        let name = path
            .file_name()
            .map(|value| value.to_string_lossy().into_owned())
            .unwrap_or_default();
        let destination = target_root.join(&name);
        let result = if path.is_dir() {
            migrate_entry(path, &destination)
        } else {
            migrate_file(path, &destination)
        };
        match result {
            Ok((bytes, entry_superseded)) => {
                copied_bytes += bytes;
                moved.push(name.clone());
                // 目标胜清单：条目名做前缀定位到顶层条目；有界，超出上限的只进
                // diag 汇总计数。
                superseded_count += entry_superseded.len();
                for relative in entry_superseded {
                    if state.superseded_files.len() < SUPERSEDED_FILES_LIMIT {
                        state.superseded_files.push(
                            Path::new(&name)
                                .join(relative)
                                .to_string_lossy()
                                .into_owned(),
                        );
                    }
                }
            }
            // 文件被占用（os error 32/33）：条目留在 pending、继续搬其余。
            // 0929 真机的教训：第一个条目失败就全盘放弃，流程每次启动必失败。
            Err(error) if error.locked => {
                crate::diag_log::write_line(&format!(
                    "storage-migration: {name} is in use, left pending for next launch ({})",
                    error.message
                ));
            }
            Err(error) => {
                state.moved_entries = moved;
                state.pending_entries = pending
                    .iter()
                    .filter(|entry| !state.moved_entries.contains(entry))
                    .cloned()
                    .collect();
                state.copied_bytes = copied_bytes;
                return fail(state, format!("{name}: {}", error.message));
            }
        }
        state.moved_entries = moved.clone();
        state.pending_entries = pending
            .iter()
            .filter(|entry| !moved.contains(entry))
            .cloned()
            .collect();
        state.copied_bytes = copied_bytes;
        state.updated_at = now_iso_like();
        if let Err(error) = write_migration_state(default_root, &state) {
            return fail(state, error);
        }
        if moved.contains(&name) {
            crate::diag_log::write_line(&format!(
                "storage-migration: moved {name} ({} bytes) to {}",
                copied_bytes,
                target_root.display()
            ));
        }
    }

    // 目标胜汇总：全量清单在迁移记录里（有界），diag 只落一条有界摘要。
    if superseded_count > 0 {
        let samples: Vec<String> = state.superseded_files.iter().take(8).cloned().collect();
        crate::diag_log::write_line(&format!(
            "storage-migration: {} file(s) kept the newer target version (target wins on mtime): {}{}",
            superseded_count,
            samples.join(", "),
            if state.superseded_files.len() > 8 { ", ..." } else { "" }
        ));
    }

    // 仍有被占用而搬不动的条目：phase=partial，指针保持 set_location 写下的
    // 目标方向（迁移完成时才会刷新），下次启动 start_pending_migration 续迁。
    if !state.pending_entries.is_empty() {
        state.phase = MIGRATION_PHASE_PARTIAL.to_string();
        state.error = Some(format!(
            "locked_entries: {}",
            state.pending_entries.join(", ")
        ));
        state.updated_at = now_iso_like();
        if let Err(error) = write_migration_state(default_root, &state) {
            crate::diag_log::write_line(&format!(
                "storage-migration: partial but status write failed: {error}"
            ));
        }
        crate::diag_log::write_line(&format!(
            "storage-migration: partial; {} entries in use, resumes next launch ({} -> {})",
            state.pending_entries.len(),
            source_root.display(),
            target_root.display()
        ));
        return state;
    }

    // 日志目录特殊处理：只补目标缺失的历史日志，不搬不删。
    merge_legacy_logs(source_root, target_root);

    state.phase = MIGRATION_PHASE_DONE.to_string();
    state.moved_entries = moved;
    state.pending_entries = Vec::new();
    state.copied_bytes = copied_bytes;
    state.error = None;
    state.updated_at = now_iso_like();
    if let Err(error) = write_migration_state(default_root, &state) {
        crate::diag_log::write_line(&format!(
            "storage-migration: migrated but status write failed: {error}"
        ));
    }
    // 迁移成功 = 指针里的位置真正生效：原子重写一次指针（刷新 updated_at）。
    // 目标就是默认根时写 null（「恢复默认」的语义，而不是把默认根记成自定义根）。
    let pointer_target =
        (!same_path(target_root, default_root)).then(|| target_root.to_string_lossy().into_owned());
    if let Err(error) = write_pointer(default_root, pointer_target.as_deref()) {
        crate::diag_log::write_line(&format!(
            "storage-migration: migrated but pointer refresh failed: {error}"
        ));
    }
    crate::diag_log::write_line(&format!(
        "storage-migration: complete; {} -> {}",
        source_root.display(),
        target_root.display()
    ));
    state
}

// ── Tauri 状态与命令支撑 ────────────────────────────────────────────────────

/// 设置命令的错误：只回稳定码 + 可选数字（文案在前端字典）。
#[derive(serde::Serialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct StorageLocationError {
    pub code: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub required_bytes: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub available_bytes: Option<u64>,
}

impl StorageLocationError {
    fn code(code: &str) -> Self {
        Self {
            code: code.to_string(),
            required_bytes: None,
            available_bytes: None,
        }
    }
}

#[derive(serde::Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct StorageLocationStatus {
    pub effective_root: String,
    pub default_root: String,
    /// 指针里请求的自定义根（可能尚未生效/正在迁移）。
    pub custom_root: Option<String>,
    /// 请求的位置 ≠ 生效根：需要重启才会切换。
    pub restart_required: bool,
    pub migration: Option<StorageMigrationState>,
}

/// 未完成记录下改目标是否安全：只有数据仍全部在 source（source == 当前生效根）
/// 时覆盖记录才不丢续迁入口、不把旧目标错当权威。
fn retargeting_is_safe(record: &StorageMigrationState, effective_root: &Path) -> bool {
    let source_matches_effective = same_path(Path::new(&record.source_root), effective_root);
    match record.phase.as_str() {
        // 还没重启生效过：数据全在 source，改目标只是改主意。
        MIGRATION_PHASE_PLANNED => source_matches_effective,
        // 目标侧预检类失败（建目标目录失败 / 目标盘空间不足）发生在任何条目搬迁
        // 之前，且只有从未进过搬迁循环的记录才会带这类 error：进过循环的记录
        // 收尾时 pending = 全部条目（至少含失败条目），后续预检失败只覆盖 error
        // 不清 pending。所以 moved 与 pending 都空 + 预检类 error = 任何一轮都
        // 没进过循环，旧目标至多是空目录，source 完整。moved 非空（旧目标已有
        // 权威数据）、pending 非空（上轮可能有部分拷贝）或 source != 生效根
        // （重启后新写入已落旧目标）都不能放行；source_missing 因前缀不符天然
        // 被挡（源目录缺失时无法证明数据还在 source）。
        MIGRATION_PHASE_FAILED => {
            source_matches_effective
                && record.moved_entries.is_empty()
                && record.pending_entries.is_empty()
                && record.error.as_deref().is_some_and(|error| {
                    error.starts_with("target_unavailable")
                        || error.starts_with("insufficient_space")
                })
        }
        // partial / running：目标已被写入或正在写入，一律走续迁，不放行。
        _ => false,
    }
}

/// 壳内单例：默认根、生效根与迁移现场。
pub struct StorageLocationState {
    default_root: PathBuf,
    effective_root: PathBuf,
    migration: Mutex<Option<StorageMigrationState>>,
}

impl StorageLocationState {
    /// 解析生效根（坏指针只落日志、按默认走），返回单例。
    pub fn initialize(default_root: PathBuf) -> Self {
        let effective_root = resolve_effective_root(&default_root);
        Self {
            default_root,
            effective_root,
            migration: Mutex::new(None),
        }
    }

    pub fn effective_root(&self) -> &Path {
        &self.effective_root
    }

    pub fn status(&self) -> StorageLocationStatus {
        let (pointer, _) = read_pointer(&self.default_root);
        let custom_root = pointer.and_then(|pointer| pointer.custom_root);
        let requested_root = custom_root
            .as_deref()
            .map(PathBuf::from)
            .unwrap_or_else(|| self.default_root.clone());
        let restart_required = !same_path(&requested_root, &self.effective_root);
        // 迁移现场优先取本进程内存态（字节级进度更细），否则读记录文件。
        let migration = self
            .migration
            .lock()
            .ok()
            .and_then(|guard| guard.clone())
            .or_else(|| read_migration_state(&self.default_root));
        StorageLocationStatus {
            effective_root: self.effective_root.to_string_lossy().into_owned(),
            default_root: self.default_root.to_string_lossy().into_owned(),
            custom_root,
            restart_required,
            migration,
        }
    }

    /// 启动时若有未完成的迁移记录，则起后台线程续迁（不阻塞主线程与 UI）。
    pub fn start_pending_migration(self: &Arc<Self>) {
        let Some(mut state) = read_migration_state(&self.default_root) else {
            return;
        };
        if state.phase == MIGRATION_PHASE_DONE {
            return;
        }
        let source_root = PathBuf::from(&state.source_root);
        let target_root = PathBuf::from(&state.target_root);
        if !source_root.is_dir() {
            // 源目录不在（外置盘未插好等）：记录 failed，源既不删也只等下次启动。
            state.phase = MIGRATION_PHASE_FAILED.to_string();
            state.error = Some(format!("source_missing: {}", source_root.display()));
            state.updated_at = now_iso_like();
            let _ = write_migration_state(&self.default_root, &state);
            crate::diag_log::write_line(&format!(
                "storage-migration: source missing {}; will retry next launch",
                source_root.display()
            ));
            return;
        }
        if same_path(&source_root, &target_root) {
            let mut state = state;
            state.phase = MIGRATION_PHASE_DONE.to_string();
            state.pending_entries = Vec::new();
            state.updated_at = now_iso_like();
            let _ = write_migration_state(&self.default_root, &state);
            return;
        }
        if let Ok(mut guard) = self.migration.lock() {
            // 先把内存态置为 running 再起线程：setup 完成前 WebView 不能发命令，
            // 此后 set_location 就会因 migration_running 而拒绝——避免用户在后台
            // 搬迁进行中改写记录（记录被覆盖会让搬迁搬去旧目标）。
            state.phase = MIGRATION_PHASE_RUNNING.to_string();
            state.updated_at = now_iso_like();
            *guard = Some(state.clone());
        }
        let this = Arc::clone(self);
        std::thread::spawn(move || {
            let finished = run_migration(&this.default_root, &source_root, &target_root, state);
            if let Ok(mut guard) = this.migration.lock() {
                *guard = Some(finished);
            }
        });
    }

    /// 迁移是否在进行中（设置命令据此拒绝并发改位置）。
    fn migration_running(&self) -> bool {
        self.migration
            .lock()
            .ok()
            .and_then(|guard| guard.as_ref().map(|state| state.phase.clone()))
            .is_some_and(|phase| phase == MIGRATION_PHASE_RUNNING)
    }

    /// 设置存储位置：校验 → 预检空间 → 原子写指针 + 迁移记录（重启生效）。
    pub fn set_location(
        &self,
        requested: Option<String>,
    ) -> Result<StorageLocationStatus, StorageLocationError> {
        if self.migration_running() {
            return Err(StorageLocationError::code(
                "storage_location.migration_in_progress",
            ));
        }
        // 未完成的迁移记录挡住改位置（含恢复默认）：记录的 source_root 指向仍有
        // 待迁数据的旧根，覆盖记录会让旧数据失去续迁入口。两类例外（数据都还没
        // 离开 source，覆盖记录才安全）：planned 且 source == 当前生效根（还没
        // 重启生效过）；failed 且 source == 当前生效根、错误是目标侧预检类、
        // moved 与 pending 都空（任何一轮都没进过搬迁循环，判定见
        // retargeting_is_safe）。其余（source_missing、已搬过条目、上轮进过循环、
        // source != 生效根）一律仍挡。done 代表旧源已清空，不挡。
        if let Some(record) = read_migration_state(&self.default_root) {
            if record.phase != MIGRATION_PHASE_DONE
                && !retargeting_is_safe(&record, &self.effective_root)
            {
                return Err(StorageLocationError::code(
                    "storage_location.migration_unfinished",
                ));
            }
        }
        if let Some(problem) = read_pointer(&self.default_root).1 {
            // 坏指针不阻止用户覆盖写入新位置（新指针会把坏文件原子替换掉）。
            crate::diag_log::write_line(&format!(
                "storage-location: overwriting pointer ({problem})"
            ));
        }
        let normalized = normalize_custom_root(&self.default_root, requested.as_deref())
            .map_err(|code| StorageLocationError::code(&code))?;
        let resolved_effective = match &normalized {
            Some(path) => resolve_custom_root(path),
            None => self.default_root.clone(),
        };
        // 预检目标盘空间：不足直接拒绝（不写指针）。
        if !same_path(&resolved_effective, &self.effective_root) {
            if let Err((required, available)) =
                precheck_free_space(&self.effective_root, &resolved_effective)
            {
                return Err(StorageLocationError {
                    code: "storage_location.insufficient_space".to_string(),
                    required_bytes: Some(required),
                    available_bytes: Some(available),
                });
            }
        }
        // record-first：先落迁移记录（planned）再写指针。指针写失败时记录已在，
        // 下次启动 start_pending_migration 自动续迁（run_migration 完成时本就会
        // 写指针）；若先写指针，失败会让指针指向新根却无迁移记录，旧数据看似
        // 全丢（实际滞留旧根）。
        let state = StorageMigrationState {
            source_root: self.effective_root.to_string_lossy().into_owned(),
            target_root: resolved_effective.to_string_lossy().into_owned(),
            phase: if same_path(&resolved_effective, &self.effective_root) {
                MIGRATION_PHASE_DONE.to_string()
            } else {
                MIGRATION_PHASE_PLANNED.to_string()
            },
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 0,
            copied_bytes: 0,
            error: None,
            updated_at: now_iso_like(),
        };
        write_migration_state(&self.default_root, &state)
            .map_err(|_| StorageLocationError::code("storage_location.write_failed"))?;
        write_pointer(&self.default_root, normalized.as_deref())
            .map_err(|_| StorageLocationError::code("storage_location.write_failed"))?;
        if let Ok(mut guard) = self.migration.lock() {
            *guard = Some(state);
        }
        crate::diag_log::write_line(&format!(
            "storage-location: location set to {} (effective after restart)",
            resolved_effective.display()
        ));
        Ok(self.status())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "aiming-cookie-storage-location-{}-{name}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("mkdir");
        root
    }

    /// 默认根之外的目标目录（自定义根不能嵌在默认根里，否则搬迁自我包含）。
    fn custom_root_outside(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "aiming-cookie-storage-location-{}-{name}-outside",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        root
    }

    fn write_pointer_json(root: &Path, custom_root: Option<&str>) {
        let payload = match custom_root {
            Some(path) => format!(
                r#"{{"schema":1,"custom_root":{},"updated_at":"2026-09-28T00:00:00Z"}}"#,
                serde_json::to_string(path).unwrap()
            ),
            None => {
                r#"{"schema":1,"custom_root":null,"updated_at":"2026-09-28T00:00:00Z"}"#.to_string()
            }
        };
        fs::write(root.join(STORAGE_LOCATION_FILE_NAME), payload).expect("write pointer");
    }

    #[test]
    fn parse_pointer_accepts_only_well_formed_schema_one_pointers() {
        assert_eq!(
            parse_pointer(
                br#"{"schema":1,"custom_root":"D:\\ACData","updated_at":"2026-09-28T00:00:00Z"}"#
            )
            .and_then(|pointer| pointer.custom_root),
            Some("D:\\ACData".to_string())
        );
        assert_eq!(
            parse_pointer(br#"{"schema":1,"custom_root":null,"updated_at":""}"#)
                .and_then(|pointer| pointer.custom_root),
            None
        );
        for invalid in [
            &br#"not json"#[..],
            &br#"{"schema":2,"custom_root":"D:\\ACData"}"#[..],
            &br#"{"custom_root":"D:\\ACData"}"#[..],
            &br#"{"schema":1,"custom_root":42}"#[..],
            &br#"{"schema":1,"custom_root":{}}"#[..],
        ] {
            assert!(
                parse_pointer(invalid).is_none(),
                "{:?}",
                String::from_utf8_lossy(invalid)
            );
        }
    }

    #[test]
    fn read_pointer_distinguishes_missing_from_malformed() {
        let root = scratch("read-pointer");
        assert_eq!(read_pointer(&root), (None, None));
        fs::write(root.join(STORAGE_LOCATION_FILE_NAME), "not json").expect("write");
        let (pointer, problem) = read_pointer(&root);
        assert!(pointer.is_none());
        assert_eq!(problem.as_deref(), Some("malformed"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn resolve_effective_root_falls_back_on_missing_malformed_or_unusable_pointers() {
        let root = scratch("resolve");
        // 无指针 → 默认根
        assert_eq!(resolve_effective_root(&root), root);
        // 坏指针 → 默认根（不挡启动）
        fs::write(root.join(STORAGE_LOCATION_FILE_NAME), "{").expect("write malformed");
        assert_eq!(resolve_effective_root(&root), root);
        // 指向不存在盘符 → 默认根
        write_pointer_json(&root, Some("Q:\\aiming-cookie-does-not-exist"));
        assert_eq!(resolve_effective_root(&root), root);
        // 合法自定义根 → 自定义根，且不存在时自动创建
        let custom = root.join("custom-root");
        write_pointer_json(&root, Some(&custom.to_string_lossy()));
        assert_eq!(resolve_effective_root(&root), custom);
        assert!(custom.is_dir());
        // 自定义根 = 默认根 → 视为默认，不触发迁移
        write_pointer_json(&root, Some(&root.to_string_lossy()));
        assert_eq!(resolve_effective_root(&root), root);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn strip_extended_prefix_strips_verbatim_and_unc_prefixes_but_keeps_long_paths() {
        // verbatim 盘符路径 → 干净形态
        assert_eq!(
            strip_extended_prefix(Path::new(r"\\?\E:\ACData")),
            PathBuf::from(r"E:\ACData")
        );
        // verbatim UNC → 普通 UNC
        assert_eq!(
            strip_extended_prefix(Path::new(r"\\?\UNC\server\share")),
            PathBuf::from(r"\\server\share")
        );
        // 普通路径（盘符 / UNC / 含正斜杠）原样返回
        assert_eq!(
            strip_extended_prefix(Path::new(r"E:\ACData")),
            PathBuf::from(r"E:\ACData")
        );
        assert_eq!(
            strip_extended_prefix(Path::new(r"\\server\share")),
            PathBuf::from(r"\\server\share")
        );
        assert_eq!(
            strip_extended_prefix(Path::new("E:/ACData")),
            PathBuf::from("E:/ACData")
        );
        // 长路径（剥完 > 260 字符）：保留前缀，否则 Win32 API 处理不了
        let long = format!(r"\\?\E:\{}", "a".repeat(300));
        assert_eq!(
            strip_extended_prefix(Path::new(&long)),
            PathBuf::from(&long)
        );
    }

    #[cfg(windows)]
    #[test]
    fn resolve_effective_root_strips_a_verbatim_prefix_stored_in_the_pointer() {
        // 旧版本写下的指针已经带 \\?\ 前缀：生效根读出来必须是干净形态
        //（子进程 DATA_ROOT 与前端「当前位置」的出口都在这里）。
        let root = scratch("verbatim-pointer");
        let custom = custom_root_outside("verbatim-pointer");
        fs::create_dir_all(&custom).expect("mkdir");
        let verbatim = format!(r"\\?\{}", custom.to_string_lossy());
        write_pointer_json(&root, Some(&verbatim));
        assert_eq!(resolve_effective_root(&root), custom);
        let _ = fs::remove_dir_all(&custom);
        let _ = fs::remove_dir_all(&root);
    }

    #[cfg(windows)]
    #[test]
    fn set_location_records_a_clean_target_root_without_verbatim_prefix() {
        // set_location 对目标做 canonicalize（Windows 返回 \\?\ 形态），
        // 落进迁移记录 target_root 的值必须已剥干净。
        let root = scratch("set-verbatim");
        let custom = custom_root_outside("set-verbatim");
        let state = StorageLocationState::initialize(root.clone());
        state
            .set_location(Some(custom.to_string_lossy().into_owned()))
            .expect("set location");
        let record = read_migration_state(&root).expect("record");
        assert!(
            !record.target_root.starts_with(r"\\?\"),
            "target_root leaked the verbatim prefix: {}",
            record.target_root
        );
        assert!(same_path(Path::new(&record.target_root), &custom));
        let _ = fs::remove_dir_all(&custom);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn write_pointer_round_trips_and_leaves_no_temp_artifacts() {
        let root = scratch("write-pointer");
        write_pointer(&root, Some("D:\\ACData")).expect("write custom");
        let (pointer, problem) = read_pointer(&root);
        assert_eq!(problem, None);
        assert_eq!(
            pointer.and_then(|pointer| pointer.custom_root),
            Some("D:\\ACData".to_string())
        );
        write_pointer(&root, None).expect("write default");
        assert_eq!(
            read_pointer(&root)
                .0
                .and_then(|pointer| pointer.custom_root),
            None
        );
        let names: Vec<String> = fs::read_dir(&root)
            .expect("read")
            .filter_map(Result::ok)
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .collect();
        assert_eq!(names, vec![STORAGE_LOCATION_FILE_NAME.to_string()]);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn normalize_custom_root_rejects_relative_file_and_nested_paths() {
        let root = scratch("normalize");
        assert_eq!(
            normalize_custom_root(&root, Some("relative/dir")),
            Err("storage_location.not_absolute".to_string())
        );
        let file = root.join("plain-file");
        fs::write(&file, b"x").expect("write");
        assert_eq!(
            normalize_custom_root(&root, Some(&file.to_string_lossy())),
            Err("storage_location.not_a_directory".to_string())
        );
        // 空串 / 空白 / None → 恢复默认
        assert_eq!(normalize_custom_root(&root, None), Ok(None));
        assert_eq!(normalize_custom_root(&root, Some("   ")), Ok(None));
        // 与默认根等价 → 恢复默认
        assert_eq!(
            normalize_custom_root(&root, Some(&root.to_string_lossy())),
            Ok(None)
        );
        // 默认根的父/子目录都拒绝（搬迁会自我包含）
        let child = root.join("nested-child");
        fs::create_dir_all(&child).expect("mkdir");
        assert_eq!(
            normalize_custom_root(&root, Some(&child.to_string_lossy())),
            Err("storage_location.nested".to_string())
        );
        let parent = root.parent().expect("parent").to_path_buf();
        assert_eq!(
            normalize_custom_root(&root, Some(&parent.to_string_lossy())),
            Err("storage_location.nested".to_string())
        );
        // 正常路径（默认根之外的同级目录）：返回解析后的绝对路径
        let outside = std::env::temp_dir().join(format!(
            "aiming-cookie-storage-location-{}-normalize-outside",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&outside);
        assert_eq!(
            normalize_custom_root(&root, Some(&outside.to_string_lossy())),
            Ok(Some(outside.to_string_lossy().into_owned()))
        );
        assert!(outside.is_dir());
        let _ = fs::remove_dir_all(&outside);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn run_migration_copies_verifies_and_removes_source_entries() {
        let root = scratch("migrate");
        let source = root.join("source");
        let target = root.join("target");
        fs::create_dir_all(source.join("runs/7")).expect("mkdir");
        fs::write(source.join("runs/7/meta.json"), b"meta").expect("write");
        fs::write(source.join("runs/7/video.mp4"), vec![b'v'; 128]).expect("write");
        fs::write(source.join("desktop-runtime.json"), b"{}").expect("write");
        // 指针 / 迁移记录 / logs 留在默认根，绝不跟着搬。
        fs::write(source.join(STORAGE_LOCATION_FILE_NAME), b"{}").expect("write");
        fs::write(source.join(STORAGE_MIGRATION_FILE_NAME), b"{}").expect("write");
        fs::create_dir_all(source.join("logs")).expect("mkdir");
        fs::write(source.join("logs/backend.log.1"), b"rotated").expect("write");

        let state = StorageMigrationState {
            source_root: source.to_string_lossy().into_owned(),
            target_root: target.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_PLANNED.to_string(),
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 0,
            copied_bytes: 0,
            error: None,
            updated_at: String::new(),
        };
        let finished = run_migration(&root, &source, &target, state);

        assert_eq!(finished.phase, MIGRATION_PHASE_DONE);
        assert_eq!(finished.total_bytes, finished.copied_bytes);
        assert!(finished.pending_entries.is_empty());
        assert_eq!(
            fs::read(target.join("runs/7/meta.json")).expect("read"),
            b"meta"
        );
        assert_eq!(
            fs::read(target.join("runs/7/video.mp4")).expect("read"),
            vec![b'v'; 128]
        );
        assert!(!source.join("runs").exists());
        assert!(source.join(STORAGE_LOCATION_FILE_NAME).is_file());
        assert!(source.join(STORAGE_MIGRATION_FILE_NAME).is_file());
        // 日志：只补目标缺失的历史日志，源日志目录保留。
        assert_eq!(
            fs::read(target.join("logs/backend.log.1")).expect("read"),
            b"rotated"
        );
        assert!(source.join("logs/backend.log.1").is_file());
        // 记录里落的是完成态，指针被原子刷新为迁移目标。
        let record = read_migration_state(&root).expect("record");
        assert_eq!(record.phase, MIGRATION_PHASE_DONE);
        let pointer = fs::read(root.join(STORAGE_LOCATION_FILE_NAME)).expect("pointer");
        assert_eq!(
            parse_pointer(&pointer).and_then(|pointer| pointer.custom_root),
            Some(target.to_string_lossy().into_owned())
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn run_migration_keeps_the_source_when_verification_cannot_pass() {
        let root = scratch("verify-fail");
        let source = root.join("source");
        let target = root.join("target");
        fs::create_dir_all(source.join("runs")).expect("mkdir");
        fs::write(source.join("runs/meta.json"), b"meta").expect("write");
        // 目标根被占成普通文件：copy 阶段就失败，源必须原样保留。
        fs::write(&target, b"blocked").expect("write");
        let state = StorageMigrationState {
            source_root: source.to_string_lossy().into_owned(),
            target_root: target.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_PLANNED.to_string(),
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 0,
            copied_bytes: 0,
            error: None,
            updated_at: String::new(),
        };

        let finished = run_migration(&root, &source, &target, state);

        assert_eq!(finished.phase, MIGRATION_PHASE_FAILED);
        assert!(finished.error.is_some());
        assert!(
            source.join("runs/meta.json").is_file(),
            "source must survive a failed migration"
        );
        assert_eq!(
            read_migration_state(&root).expect("record").phase,
            MIGRATION_PHASE_FAILED
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn run_migration_resumes_by_skipping_already_copied_files() {
        let root = scratch("resume");
        let source = root.join("source");
        let target = root.join("target");
        fs::create_dir_all(source.join("runs/7")).expect("mkdir");
        fs::write(source.join("runs/7/meta.json"), b"meta").expect("write");
        fs::write(source.join("runs/7/video.mp4"), vec![b'v'; 64]).expect("write");
        // 上一轮已拷完的文件（copy_file 保留源 mtime）：同 mtime 同字节数 → 跳过不重拷。
        fs::create_dir_all(target.join("runs/7")).expect("mkdir");
        copy_file(&source.join("runs/7/video.mp4"), &target.join("runs/7/video.mp4"))
            .expect("precopy");
        // 断点处目标被并发改写（内容更新、mtime 更晚）：目标胜，保留目标新内容。
        fs::write(target.join("runs/7/meta.json"), b"stale-but-newer").expect("write");
        let newer = std::time::SystemTime::now() + std::time::Duration::from_secs(3600);
        filetime_set(&target.join("runs/7/meta.json"), newer).expect("set newer mtime");
        // 目标独有文件：不参与对账，更不许被清掉。
        fs::write(target.join("runs/7/partial.tmp"), b"partial").expect("write");
        // 已搬完但未删源的条目：只补删源目录。
        fs::create_dir_all(source.join("analyses")).expect("mkdir");
        fs::write(source.join("analyses/overview.json"), b"overview").expect("write");
        fs::create_dir_all(target.join("analyses")).expect("mkdir");
        copy_file(
            &source.join("analyses/overview.json"),
            &target.join("analyses/overview.json"),
        )
        .expect("precopy");
        // 迁移期间子进程写进目标的新文件：不参与对账，更不许被清掉。
        fs::create_dir_all(target.join("sessions")).expect("mkdir");
        fs::write(target.join("sessions/1.json"), b"fresh").expect("write");

        let state = StorageMigrationState {
            source_root: source.to_string_lossy().into_owned(),
            target_root: target.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_RUNNING.to_string(),
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 0,
            copied_bytes: 0,
            error: None,
            updated_at: String::new(),
        };
        let finished = run_migration(&root, &source, &target, state);

        assert_eq!(finished.phase, MIGRATION_PHASE_DONE, "{:?}", finished.error);
        // 同 mtime 同字节数（上轮已拷完）：跳过，内容原样。
        assert_eq!(
            fs::read(target.join("runs/7/video.mp4")).expect("read"),
            vec![b'v'; 64]
        );
        // 目标被改写的文件保留目标侧新内容，不被旧源回滚。
        assert_eq!(
            fs::read(target.join("runs/7/meta.json")).expect("read"),
            b"stale-but-newer"
        );
        assert!(
            finished
                .superseded_files
                .iter()
                .any(|path| path.ends_with("meta.json")),
            "superseded must record the target-won file: {:?}",
            finished.superseded_files
        );
        // 目标目录只增不删：新会话写进去的数据与「目标独有」的文件都原样保留。
        assert_eq!(
            fs::read(target.join("sessions/1.json")).expect("read"),
            b"fresh"
        );
        assert!(target.join("runs/7/partial.tmp").exists());
        assert!(!source.join("runs").exists());
        assert!(!source.join("analyses").exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn run_migration_keeps_newer_target_files_and_records_them_as_superseded() {
        // 核心回归：迁移 pending 期间生效根已是目标根，子进程往目标写新数据。
        // 源里的同名文件是旧内容，续迁绝不能拿旧源覆盖目标的新文件（mtime 新者胜）。
        let root = scratch("target-wins");
        let source = root.join("source");
        let target = root.join("target");
        fs::create_dir_all(source.join("sessions")).expect("mkdir");
        fs::write(source.join("sessions/meta.json"), br#"{"v":1}"#).expect("write");
        fs::create_dir_all(target.join("sessions")).expect("mkdir");
        // 目标内容更新且字节数不同：旧判据（同字节数跳过、否则覆盖）会拿旧源
        // 回滚目标——这正是本回归要钉死的缺陷。
        fs::write(target.join("sessions/meta.json"), br#"{"v":2,"bigger":true}"#)
            .expect("write");
        // mtime 显式拨晚 1 小时：免疫文件系统时间戳粒度，保证「目标严格更新」。
        let newer = std::time::SystemTime::now() + std::time::Duration::from_secs(3600);
        filetime_set(&target.join("sessions/meta.json"), newer).expect("set newer mtime");

        let state = StorageMigrationState {
            source_root: source.to_string_lossy().into_owned(),
            target_root: target.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_RUNNING.to_string(),
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 0,
            copied_bytes: 0,
            error: None,
            updated_at: String::new(),
        };
        let finished = run_migration(&root, &source, &target, state);

        assert_eq!(finished.phase, MIGRATION_PHASE_DONE, "{:?}", finished.error);
        // 目标新内容原样保留，没被旧源覆盖；源随条目搬迁删除。
        assert_eq!(
            fs::read(target.join("sessions/meta.json")).expect("read"),
            br#"{"v":2,"bigger":true}"#
        );
        assert!(!source.join("sessions").exists());
        // 目标胜清单（条目名/相对路径）落进完成态记录。
        assert!(
            finished
                .superseded_files
                .iter()
                .any(|path| path.ends_with("meta.json")),
            "superseded must contain the target-won file: {:?}",
            finished.superseded_files
        );
        let record = read_migration_state(&root).expect("record");
        assert_eq!(record.superseded_files, finished.superseded_files);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn run_migration_sweeps_crash_leftover_temp_files_from_target() {
        // 原子拷贝的 temp 与目标同目录；崩溃残留（任意嵌套层级）在续迁开始时清掉。
        let root = scratch("temp-cleanup");
        let source = root.join("source");
        let target = root.join("target");
        fs::create_dir_all(source.join("runs")).expect("mkdir");
        fs::write(source.join("runs/meta.json"), b"meta").expect("write");
        fs::create_dir_all(target.join("runs")).expect("mkdir");
        fs::write(target.join("runs/.meta.json.acmigration-tmp"), b"half").expect("write");
        fs::write(target.join(".orphan.acmigration-tmp"), b"half").expect("write");

        let state = StorageMigrationState {
            source_root: source.to_string_lossy().into_owned(),
            target_root: target.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_RUNNING.to_string(),
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 0,
            copied_bytes: 0,
            error: None,
            updated_at: String::new(),
        };
        let finished = run_migration(&root, &source, &target, state);

        assert_eq!(finished.phase, MIGRATION_PHASE_DONE, "{:?}", finished.error);
        assert!(!target.join("runs/.meta.json.acmigration-tmp").exists());
        assert!(!target.join(".orphan.acmigration-tmp").exists());
        assert_eq!(fs::read(target.join("runs/meta.json")).expect("read"), b"meta");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn run_migration_moves_top_level_files_with_byte_accounting() {
        let root = scratch("files");
        let source = root.join("source");
        let target = root.join("target");
        fs::create_dir_all(&source).expect("mkdir");
        fs::write(source.join("capture-enabled"), br#"{"enabled":true}"#).expect("write");
        fs::write(source.join("coach-error.log"), b"coach").expect("write");
        let state = StorageMigrationState {
            source_root: source.to_string_lossy().into_owned(),
            target_root: target.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_PLANNED.to_string(),
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 0,
            copied_bytes: 0,
            error: None,
            updated_at: String::new(),
        };

        let finished = run_migration(&root, &source, &target, state);

        assert_eq!(finished.phase, MIGRATION_PHASE_DONE, "{:?}", finished.error);
        assert_eq!(
            fs::read(target.join("capture-enabled")).expect("read"),
            br#"{"enabled":true}"#
        );
        assert!(!source.join("capture-enabled").exists());
        // coach-error.log 在排除清单里（侧车常开的运行时痕迹）：留在源，不搬。
        assert!(source.join("coach-error.log").is_file());
        assert!(!target.join("coach-error.log").exists());
        // total 只算数据条目（capture-enabled 的 16 字节）。
        assert_eq!(finished.total_bytes, 16);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn run_migration_leaves_runtime_trace_entries_at_the_source() {
        let root = scratch("excluded");
        let source = root.join("source");
        let target = root.join("target");
        fs::create_dir_all(source.join("runs")).expect("mkdir");
        fs::write(source.join("runs/meta.json"), b"meta").expect("write");
        // 运行时痕迹：本应用自己启动就会创建/持有/重写的文件。
        fs::write(source.join(".runtime.lock"), b"locked").expect("write");
        fs::write(source.join("desktop-runtime.json"), b"{}").expect("write");
        fs::write(source.join("coach-debug.log"), b"debug").expect("write");
        fs::write(source.join("coach-error.log"), b"error").expect("write");
        fs::create_dir_all(source.join("logs")).expect("mkdir");
        fs::write(source.join("logs/native.log"), b"log").expect("write");
        // 指针与迁移记录（源 = 默认根方向的镜像）：权威位置在默认根，绝不搬。
        fs::write(source.join(STORAGE_LOCATION_FILE_NAME), b"{}").expect("write");
        fs::write(source.join(STORAGE_MIGRATION_FILE_NAME), b"{}").expect("write");

        let state = StorageMigrationState {
            source_root: source.to_string_lossy().into_owned(),
            target_root: target.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_PLANNED.to_string(),
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 0,
            copied_bytes: 0,
            error: None,
            updated_at: String::new(),
        };

        let finished = run_migration(&root, &source, &target, state);

        assert_eq!(finished.phase, MIGRATION_PHASE_DONE, "{:?}", finished.error);
        assert_eq!(finished.pending_entries, Vec::<String>::new());
        // 数据条目正常搬迁。
        assert!(target.join("runs/meta.json").is_file());
        assert!(!source.join("runs").exists());
        // 运行时痕迹一项都不搬：留在源，目标不出现。
        for name in [
            ".runtime.lock",
            "desktop-runtime.json",
            "coach-debug.log",
            "coach-error.log",
            STORAGE_LOCATION_FILE_NAME,
            STORAGE_MIGRATION_FILE_NAME,
        ] {
            assert!(source.join(name).is_file(), "{name} stays at source");
            assert!(!target.join(name).exists(), "{name} must not migrate");
        }
        assert!(source.join("logs/native.log").is_file());
        // total 只算数据条目（runs/meta.json 的 4 字节），运行时痕迹不计入。
        assert_eq!(finished.total_bytes, 4);
        let _ = fs::remove_dir_all(&root);
    }

    #[cfg(windows)]
    #[test]
    fn run_migration_keeps_a_locked_entry_pending_and_finishes_partial() {
        use std::os::windows::io::AsRawHandle;
        use winapi::shared::ntdef::HANDLE;
        use winapi::um::fileapi::LockFileEx;
        use winapi::um::minwinbase::{
            LOCKFILE_EXCLUSIVE_LOCK, LOCKFILE_FAIL_IMMEDIATELY, OVERLAPPED,
        };

        let root = scratch("locked");
        let source = root.join("source");
        let target = root.join("target");
        fs::create_dir_all(source.join("runs")).expect("mkdir");
        fs::write(source.join("runs/meta.json"), b"meta").expect("write");
        fs::write(source.join("aiming_cookie.db"), vec![b'd'; 32]).expect("write");

        // 模拟自家后端持有的字节范围锁：对 db 首字节加排他锁（LockFileEx），
        // 迁移复制读取该区域即触发 os error 33（ERROR_LOCK_VIOLATION）路径。
        let held = fs::OpenOptions::new()
            .read(true)
            .open(source.join("aiming_cookie.db"))
            .expect("open");
        let mut overlapped: OVERLAPPED = unsafe { std::mem::zeroed() };
        let handle = held.as_raw_handle() as HANDLE;
        let locked = unsafe {
            LockFileEx(
                handle,
                LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY,
                0,
                1,
                0,
                &mut overlapped,
            )
        };
        assert_ne!(locked, 0, "LockFileEx must succeed");

        let state = StorageMigrationState {
            source_root: source.to_string_lossy().into_owned(),
            target_root: target.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_RUNNING.to_string(),
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 0,
            copied_bytes: 0,
            error: None,
            updated_at: String::new(),
        };

        let finished = run_migration(&root, &source, &target, state);

        // 被锁条目留在 pending（phase=partial），其余条目照常搬完——这正是
        // 0929 真机 P0 的场景：不能因第一个条目被占用就放弃整场迁移。
        assert_eq!(
            finished.phase, MIGRATION_PHASE_PARTIAL,
            "{:?}",
            finished.error
        );
        assert_eq!(
            finished.pending_entries,
            vec!["aiming_cookie.db".to_string()]
        );
        assert_eq!(finished.moved_entries, vec!["runs".to_string()]);
        assert!(target.join("runs/meta.json").is_file());
        assert!(!source.join("runs").exists());
        // 锁住的源文件原地保留，目标侧没有它的半份拷贝。
        assert!(source.join("aiming_cookie.db").is_file());
        assert!(!target.join("aiming_cookie.db").exists());
        let record = read_migration_state(&root).expect("record");
        assert_eq!(record.phase, MIGRATION_PHASE_PARTIAL);
        assert_eq!(record.pending_entries, vec!["aiming_cookie.db".to_string()]);
        drop(held);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn migration_precheck_accepts_a_target_with_room_and_accounts_bytes() {
        let root = scratch("precheck");
        let source = root.join("source");
        fs::create_dir_all(source.join("runs")).expect("mkdir");
        fs::write(source.join("runs/meta.json"), vec![b'x'; 1024]).expect("write");
        assert_eq!(directory_total_bytes(&source), 1024);
        if available_space(&root).is_none() {
            // 非 Windows 取不到可用空间 → 预检跳过（不阻塞迁移）。
            assert_eq!(precheck_free_space(&source, &root), Ok(()));
        } else {
            assert_eq!(precheck_free_space(&source, &root), Ok(()));
        }
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn set_location_writes_pointer_and_migration_record_without_touching_effective_root() {
        let root = scratch("set-location");
        let custom = custom_root_outside("set-location");
        let state = StorageLocationState::initialize(root.clone());
        assert_eq!(state.effective_root(), root.as_path());
        let status = state
            .set_location(Some(custom.to_string_lossy().into_owned()))
            .expect("set location");
        // 指针与迁移记录立即写入，但生效根要到重启才切换。
        assert_eq!(state.effective_root(), root.as_path());
        assert!(status.restart_required);
        assert_eq!(
            status.custom_root.as_deref(),
            Some(custom.to_string_lossy().as_ref())
        );
        assert_eq!(
            status.migration.map(|migration| migration.phase),
            Some(MIGRATION_PHASE_PLANNED.to_string())
        );
        // 恢复默认：指针写 null，记录目标回到默认根。
        let restored = state.set_location(None).expect("restore");
        assert_eq!(restored.custom_root, None);
        assert_eq!(
            restored.migration.map(|migration| migration.phase),
            Some(MIGRATION_PHASE_DONE.to_string())
        );
        let _ = fs::remove_dir_all(&custom);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn set_location_rejects_invalid_targets_without_writing_a_pointer() {
        let root = scratch("set-invalid");
        let state = StorageLocationState::initialize(root.clone());
        assert_eq!(
            state
                .set_location(Some("relative".to_string()))
                .unwrap_err()
                .code,
            "storage_location.not_absolute"
        );
        assert_eq!(
            state
                .set_location(Some(root.join("child").to_string_lossy().into_owned()))
                .unwrap_err()
                .code,
            "storage_location.nested"
        );
        // 拒绝时不留任何指针：仍是「使用默认根」。
        assert!(!root.join(STORAGE_LOCATION_FILE_NAME).exists());
        assert!(!state.status().restart_required);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn set_location_keeps_an_unfinished_partial_record_instead_of_overwriting_it() {
        // 2026-10-16 review 第 5 条：A→B 迁移重启切到 B 后仍有条目被占用留在 A
        //（phase=partial）。此时 B→C 或恢复默认都必须被拒：覆盖记录会让 A 的
        // 待迁条目失去续迁入口。
        let root = scratch("retarget-partial");
        let old_source = custom_root_outside("retarget-partial-a");
        let current = custom_root_outside("retarget-partial-b");
        let next = custom_root_outside("retarget-partial-c");
        fs::create_dir_all(old_source.join("runs")).expect("mkdir");
        fs::write(old_source.join("runs/meta.json"), b"meta").expect("write");
        fs::create_dir_all(&current).expect("mkdir");
        // 重启切到 B 后的现场：指针指向 B（生效根 = B），partial 记录指向旧源 A。
        write_pointer_json(&root, Some(&current.to_string_lossy()));
        let partial = StorageMigrationState {
            source_root: old_source.to_string_lossy().into_owned(),
            target_root: current.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_PARTIAL.to_string(),
            // 与上方 fixture 一致：A 只有 runs 一个数据条目，整条被占用留 pending。
            moved_entries: Vec::new(),
            pending_entries: vec!["runs".to_string()],
            superseded_files: Vec::new(),
            total_bytes: 4,
            copied_bytes: 0,
            error: Some("locked_entries: runs".to_string()),
            updated_at: String::new(),
        };
        write_migration_state(&root, &partial).expect("write record");
        let state = StorageLocationState::initialize(root.clone());
        assert!(same_path(state.effective_root(), &current));

        // B→C 被拒，记录与指针原样保留。
        let error = state
            .set_location(Some(next.to_string_lossy().into_owned()))
            .unwrap_err();
        assert_eq!(error.code, "storage_location.migration_unfinished");
        assert_eq!(read_migration_state(&root).expect("record"), partial);
        assert_eq!(
            read_pointer(&root)
                .0
                .and_then(|pointer| pointer.custom_root),
            Some(current.to_string_lossy().into_owned())
        );
        // 恢复默认走同一道门。
        let restore = state.set_location(None).unwrap_err();
        assert_eq!(restore.code, "storage_location.migration_unfinished");
        assert_eq!(read_migration_state(&root).expect("record"), partial);
        for path in [&old_source, &current, &next, &root] {
            let _ = fs::remove_dir_all(path);
        }
    }

    #[test]
    fn set_location_keeps_a_failed_record_even_when_pending_entries_are_empty() {
        // 源缺失 failed（外置盘曾拔出）：pending_entries 为空不能证明旧源没有
        // 数据（盘插回来就有了），不得凭空清单放行覆盖。
        let root = scratch("retarget-failed");
        let old_source = custom_root_outside("retarget-failed-a");
        let current = custom_root_outside("retarget-failed-b");
        let next = custom_root_outside("retarget-failed-c");
        fs::create_dir_all(old_source.join("runs")).expect("mkdir");
        fs::write(old_source.join("runs/meta.json"), b"meta").expect("write");
        fs::create_dir_all(&current).expect("mkdir");
        write_pointer_json(&root, Some(&current.to_string_lossy()));
        let failed = StorageMigrationState {
            source_root: old_source.to_string_lossy().into_owned(),
            target_root: current.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_FAILED.to_string(),
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 4,
            copied_bytes: 0,
            error: Some(format!("source_missing: {}", old_source.display())),
            updated_at: String::new(),
        };
        write_migration_state(&root, &failed).expect("write record");
        let state = StorageLocationState::initialize(root.clone());

        let error = state
            .set_location(Some(next.to_string_lossy().into_owned()))
            .unwrap_err();
        assert_eq!(error.code, "storage_location.migration_unfinished");
        assert_eq!(read_migration_state(&root).expect("record"), failed);
        for path in [&old_source, &current, &next, &root] {
            let _ = fs::remove_dir_all(path);
        }
    }

    #[test]
    fn set_location_allows_retargeting_when_a_precheck_failed_migration_never_moved_data() {
        // 真实路径：计划 A→B 后目标位置被占成文件（盘拔出/路径被占的确定性形态），
        // 重启解析生效根回落 A，迁移在目标预检处失败、一条没搬。此时换目标/恢复
        // 默认必须放行——一刀切会堵死用户绕开坏目标的出路。
        let root = scratch("retarget-precheck-fail");
        let broken = custom_root_outside("retarget-precheck-fail-b");
        let next = custom_root_outside("retarget-precheck-fail-c");
        fs::create_dir_all(root.join("runs")).expect("mkdir");
        fs::write(root.join("runs/meta.json"), b"meta").expect("write");
        let planner = StorageLocationState::initialize(root.clone());
        planner
            .set_location(Some(broken.to_string_lossy().into_owned()))
            .expect("plan A -> B");
        // 目标位置此后被占成普通文件。
        fs::remove_dir(&broken).expect("drop target dir");
        fs::write(&broken, b"blocked").expect("occupy target with a file");
        // 重启：生效根回落 A（B 已不可用）。
        let state = Arc::new(StorageLocationState::initialize(root.clone()));
        assert!(same_path(state.effective_root(), &root));

        state.start_pending_migration();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
        while std::time::Instant::now() < deadline {
            if read_migration_state(&root)
                .is_some_and(|record| record.phase == MIGRATION_PHASE_FAILED)
            {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        let failed = read_migration_state(&root).expect("record");
        assert_eq!(failed.phase, MIGRATION_PHASE_FAILED);
        assert!(
            failed
                .error
                .as_deref()
                .is_some_and(|error| error.starts_with("target_unavailable")),
            "{:?}",
            failed.error
        );
        assert!(failed.moved_entries.is_empty());
        // 数据原样留在 A。
        assert!(root.join("runs/meta.json").is_file());

        // 换目标放行：新记录 planned，source 仍是 A，指针指向新目标。
        let status = state
            .set_location(Some(next.to_string_lossy().into_owned()))
            .expect("retarget past a precheck failure");
        let record = read_migration_state(&root).expect("record");
        assert_eq!(record.phase, MIGRATION_PHASE_PLANNED);
        assert!(same_path(Path::new(&record.source_root), &root));
        assert!(same_path(Path::new(&record.target_root), &next));
        assert_eq!(
            read_pointer(&root)
                .0
                .and_then(|pointer| pointer.custom_root),
            Some(next.to_string_lossy().into_owned())
        );
        assert_eq!(
            status.migration.map(|migration| migration.phase),
            Some(MIGRATION_PHASE_PLANNED.to_string())
        );
        // 恢复默认同样放行（planned 未生效现场）。
        let restored = state.set_location(None).expect("restore default");
        assert_eq!(
            restored.migration.map(|migration| migration.phase),
            Some(MIGRATION_PHASE_DONE.to_string())
        );
        for path in [&broken, &next, &root] {
            let _ = fs::remove_dir_all(path);
        }
    }

    #[test]
    fn set_location_allows_retargeting_a_space_precheck_failure_that_moved_nothing() {
        // 空间预检失败同样是搬迁前的失败：一条没搬、source==生效根 → 允许换目标。
        let root = scratch("retarget-space-fail");
        let current = custom_root_outside("retarget-space-fail-b");
        let next = custom_root_outside("retarget-space-fail-c");
        fs::create_dir_all(&current).expect("mkdir");
        write_pointer_json(&root, Some(&current.to_string_lossy()));
        let failed = StorageMigrationState {
            source_root: current.to_string_lossy().into_owned(),
            target_root: custom_root_outside("retarget-space-fail-t")
                .to_string_lossy()
                .into_owned(),
            phase: MIGRATION_PHASE_FAILED.to_string(),
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 4,
            copied_bytes: 0,
            error: Some(
                "insufficient_space: need 4 bytes + headroom, available 0 bytes".to_string(),
            ),
            updated_at: String::new(),
        };
        write_migration_state(&root, &failed).expect("write record");
        let state = StorageLocationState::initialize(root.clone());

        let status = state
            .set_location(Some(next.to_string_lossy().into_owned()))
            .expect("retarget past a space precheck failure");
        let record = read_migration_state(&root).expect("record");
        assert_eq!(record.phase, MIGRATION_PHASE_PLANNED);
        assert!(same_path(Path::new(&record.source_root), &current));
        assert!(same_path(Path::new(&record.target_root), &next));
        assert_eq!(
            status.migration.map(|migration| migration.phase),
            Some(MIGRATION_PHASE_PLANNED.to_string())
        );
        for path in [&current, &next, &root] {
            let _ = fs::remove_dir_all(path);
        }
    }

    #[test]
    fn set_location_blocks_a_failed_migration_that_already_moved_entries() {
        // failed 且已搬走部分条目：旧目标已有权威数据。pending 为空也不能放行
        //——放行判据是 moved 清单与 source==生效根，不是 pending 空不空。
        let root = scratch("retarget-failed-moved");
        let current = custom_root_outside("retarget-failed-moved-b");
        let next = custom_root_outside("retarget-failed-moved-c");
        let abandoned = custom_root_outside("retarget-failed-moved-t");
        fs::create_dir_all(&current).expect("mkdir");
        write_pointer_json(&root, Some(&current.to_string_lossy()));
        let failed = StorageMigrationState {
            source_root: current.to_string_lossy().into_owned(),
            target_root: abandoned.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_FAILED.to_string(),
            moved_entries: vec!["sessions".to_string()],
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 4,
            copied_bytes: 4,
            error: Some("analyses: byte mismatch at analyses/overview.json".to_string()),
            updated_at: String::new(),
        };
        write_migration_state(&root, &failed).expect("write record");
        let state = StorageLocationState::initialize(root.clone());

        let error = state
            .set_location(Some(next.to_string_lossy().into_owned()))
            .unwrap_err();
        assert_eq!(error.code, "storage_location.migration_unfinished");
        assert_eq!(read_migration_state(&root).expect("record"), failed);
        for path in [&current, &abandoned, &next, &root] {
            let _ = fs::remove_dir_all(path);
        }
    }

    #[test]
    fn set_location_blocks_a_failed_record_that_once_entered_the_migration_loop() {
        // 上轮进过搬迁循环（首个条目内部分拷贝后硬失败收尾：pending = 全部条目、
        // moved 空），随后一次预检失败把 error 覆盖成 target_unavailable——
        // pending 非空仍要挡：旧目标可能有上轮的部分拷贝，不能凭 moved 空放行。
        let root = scratch("retarget-loop-failed");
        let next = custom_root_outside("retarget-loop-failed-c");
        let failed = StorageMigrationState {
            source_root: root.to_string_lossy().into_owned(),
            target_root: custom_root_outside("retarget-loop-failed-t")
                .to_string_lossy()
                .into_owned(),
            phase: MIGRATION_PHASE_FAILED.to_string(),
            moved_entries: Vec::new(),
            pending_entries: vec!["runs".to_string()],
            superseded_files: Vec::new(),
            total_bytes: 4,
            copied_bytes: 0,
            error: Some("target_unavailable: access denied".to_string()),
            updated_at: String::new(),
        };
        write_migration_state(&root, &failed).expect("write record");
        let state = StorageLocationState::initialize(root.clone());

        let error = state
            .set_location(Some(next.to_string_lossy().into_owned()))
            .unwrap_err();
        assert_eq!(error.code, "storage_location.migration_unfinished");
        assert_eq!(read_migration_state(&root).expect("record"), failed);
        for path in [&next, &root] {
            let _ = fs::remove_dir_all(path);
        }
    }

    #[test]
    fn set_location_still_allows_retargeting_a_planned_migration_that_never_started() {
        // planned 且数据仍全在源（source == 当前生效根）：改目标 / 恢复默认合法，
        // 这是「还没重启就反悔」的正常路径，不能被未完成门误伤。
        let root = scratch("retarget-planned");
        let first = custom_root_outside("retarget-planned-b");
        let second = custom_root_outside("retarget-planned-c");
        let state = StorageLocationState::initialize(root.clone());
        state
            .set_location(Some(first.to_string_lossy().into_owned()))
            .expect("plan A -> B");

        let status = state
            .set_location(Some(second.to_string_lossy().into_owned()))
            .expect("retarget before restart");
        assert!(status.restart_required);
        let record = read_migration_state(&root).expect("record");
        assert_eq!(record.phase, MIGRATION_PHASE_PLANNED);
        assert!(same_path(Path::new(&record.source_root), &root));
        assert!(same_path(Path::new(&record.target_root), &second));
        // 恢复默认同样允许。
        let restored = state.set_location(None).expect("restore");
        assert_eq!(
            restored.migration.map(|migration| migration.phase),
            Some(MIGRATION_PHASE_DONE.to_string())
        );
        for path in [&first, &second, &root] {
            let _ = fs::remove_dir_all(path);
        }
    }

    #[test]
    fn set_location_allows_changing_location_again_after_a_done_migration() {
        // done 记录：旧源已清空，改位置不受未完成门限制；新记录 source 是当前生效根。
        let root = scratch("retarget-done");
        let current = custom_root_outside("retarget-done-b");
        let next = custom_root_outside("retarget-done-c");
        fs::create_dir_all(&current).expect("mkdir");
        write_pointer_json(&root, Some(&current.to_string_lossy()));
        let done = StorageMigrationState {
            source_root: root.to_string_lossy().into_owned(),
            target_root: current.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_DONE.to_string(),
            moved_entries: vec!["sessions".to_string()],
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 4,
            copied_bytes: 4,
            error: None,
            updated_at: String::new(),
        };
        write_migration_state(&root, &done).expect("write record");
        let state = StorageLocationState::initialize(root.clone());

        state
            .set_location(Some(next.to_string_lossy().into_owned()))
            .expect("set C after done");
        let record = read_migration_state(&root).expect("record");
        assert_eq!(record.phase, MIGRATION_PHASE_PLANNED);
        assert!(same_path(Path::new(&record.source_root), &current));
        assert!(same_path(Path::new(&record.target_root), &next));
        for path in [&current, &next, &root] {
            let _ = fs::remove_dir_all(path);
        }
    }

    #[cfg(windows)]
    #[test]
    fn set_location_blocks_retarget_until_a_locked_migration_finishes_resuming() {
        // 组合行为（走真实路径，不手工拼状态）：真实文件锁让 A→B 迁移留 partial
        // → 改目标被拒且记录原样 → 释放锁后 start_pending_migration 续迁到 done
        // → 再改目标恢复允许。
        use std::os::windows::io::AsRawHandle;
        use winapi::shared::ntdef::HANDLE;
        use winapi::um::fileapi::LockFileEx;
        use winapi::um::minwinbase::{
            LOCKFILE_EXCLUSIVE_LOCK, LOCKFILE_FAIL_IMMEDIATELY, OVERLAPPED,
        };

        let root = scratch("retarget-resume");
        let target = custom_root_outside("retarget-resume-b");
        let next = custom_root_outside("retarget-resume-c");
        fs::create_dir_all(root.join("runs")).expect("mkdir");
        fs::write(root.join("runs/meta.json"), b"meta").expect("write");
        // 模拟自家后端持有的字节范围锁：runs/meta.json 首字节排他锁。
        let held = fs::OpenOptions::new()
            .read(true)
            .open(root.join("runs/meta.json"))
            .expect("open");
        let mut overlapped: OVERLAPPED = unsafe { std::mem::zeroed() };
        let handle = held.as_raw_handle() as HANDLE;
        let locked = unsafe {
            LockFileEx(
                handle,
                LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY,
                0,
                1,
                0,
                &mut overlapped,
            )
        };
        assert_ne!(locked, 0, "LockFileEx must succeed");

        // 计划 A→B 后按记录执行迁移：runs 被锁 → partial，条目留在源。
        let state = Arc::new(StorageLocationState::initialize(root.clone()));
        state
            .set_location(Some(target.to_string_lossy().into_owned()))
            .expect("plan A -> B");
        let planned = read_migration_state(&root).expect("record");
        let finished = run_migration(&root, &root, &target, planned);
        assert_eq!(
            finished.phase, MIGRATION_PHASE_PARTIAL,
            "{:?}",
            finished.error
        );
        assert_eq!(finished.pending_entries, vec!["runs".to_string()]);

        // partial 现场改目标被拒：旧源 runs 仍有待迁数据，记录不得被覆盖。
        let error = state
            .set_location(Some(next.to_string_lossy().into_owned()))
            .unwrap_err();
        assert_eq!(error.code, "storage_location.migration_unfinished");
        assert_eq!(
            read_migration_state(&root).expect("record").phase,
            MIGRATION_PHASE_PARTIAL
        );

        // 释放锁后重启入口续迁：runs 搬到 B，记录落 done。
        drop(held);
        state.start_pending_migration();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
        while std::time::Instant::now() < deadline {
            if read_migration_state(&root)
                .is_some_and(|record| record.phase == MIGRATION_PHASE_DONE)
            {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        assert_eq!(
            read_migration_state(&root).expect("record").phase,
            MIGRATION_PHASE_DONE
        );
        assert!(target.join("runs/meta.json").is_file());
        assert!(!root.join("runs").exists());

        // done 之后改目标恢复允许：重新 initialize 模拟重启后的壳（续迁完成时
        // 指针已刷新，生效根 = B），新记录的 source 必须是 B 而不是旧 A。
        let restarted = StorageLocationState::initialize(root.clone());
        assert!(same_path(restarted.effective_root(), &target));
        let status = restarted
            .set_location(Some(next.to_string_lossy().into_owned()))
            .expect("set C after resume");
        let record = read_migration_state(&root).expect("record");
        assert_eq!(record.phase, MIGRATION_PHASE_PLANNED);
        assert!(same_path(Path::new(&record.source_root), &target));
        assert!(same_path(Path::new(&record.target_root), &next));
        assert_eq!(
            read_pointer(&root)
                .0
                .and_then(|pointer| pointer.custom_root),
            Some(next.to_string_lossy().into_owned())
        );
        assert_eq!(
            status.migration.map(|migration| migration.phase),
            Some(MIGRATION_PHASE_PLANNED.to_string())
        );
        for path in [&target, &next, &root] {
            let _ = fs::remove_dir_all(path);
        }
    }

    #[test]
    fn start_pending_migration_settles_records_without_a_source() {
        let root = scratch("pending-missing-source");
        let state = Arc::new(StorageLocationState::initialize(root.clone()));
        let missing = root.join("missing-source");
        let record = StorageMigrationState {
            source_root: missing.to_string_lossy().into_owned(),
            target_root: root.join("target").to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_RUNNING.to_string(),
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 0,
            copied_bytes: 0,
            error: None,
            updated_at: String::new(),
        };
        write_migration_state(&root, &record).expect("write record");

        state.start_pending_migration();

        let settled = read_migration_state(&root).expect("record");
        assert_eq!(settled.phase, MIGRATION_PHASE_FAILED);
        assert!(settled.error.unwrap_or_default().contains("source_missing"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn start_pending_migration_runs_the_recorded_migration() {
        let root = scratch("pending-run");
        let source = root.join("source");
        fs::create_dir_all(source.join("runs")).expect("mkdir");
        fs::write(source.join("runs/meta.json"), b"meta").expect("write");
        let target = root.join("target");
        let record = StorageMigrationState {
            source_root: source.to_string_lossy().into_owned(),
            target_root: target.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_PLANNED.to_string(),
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 0,
            copied_bytes: 0,
            error: None,
            updated_at: String::new(),
        };
        write_migration_state(&root, &record).expect("write record");
        let state = Arc::new(StorageLocationState::initialize(root.clone()));

        state.start_pending_migration();

        // 后台线程：等到记录落成完成态。
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
        while std::time::Instant::now() < deadline {
            if read_migration_state(&root)
                .is_some_and(|record| record.phase == MIGRATION_PHASE_DONE)
            {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        assert!(target.join("runs/meta.json").is_file());
        assert!(!source.join("runs").exists());
        let finished = read_migration_state(&root).expect("record");
        assert_eq!(finished.phase, MIGRATION_PHASE_DONE, "{:?}", finished.error);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn run_migration_back_to_default_clears_the_pointer_instead_of_recording_it() {
        let root = scratch("restore");
        let custom = root.join("custom");
        fs::create_dir_all(custom.join("runs")).expect("mkdir");
        fs::write(custom.join("runs/meta.json"), b"meta").expect("write");
        // 恢复默认方向：源 = 自定义根，目标 = 默认根（= 指针所在处）。
        write_pointer_json(&root, Some(&custom.to_string_lossy()));
        let state = StorageMigrationState {
            source_root: custom.to_string_lossy().into_owned(),
            target_root: root.to_string_lossy().into_owned(),
            phase: MIGRATION_PHASE_PLANNED.to_string(),
            moved_entries: Vec::new(),
            pending_entries: Vec::new(),
            superseded_files: Vec::new(),
            total_bytes: 0,
            copied_bytes: 0,
            error: None,
            updated_at: String::new(),
        };

        let finished = run_migration(&root, &custom, &root, state);

        assert_eq!(finished.phase, MIGRATION_PHASE_DONE, "{:?}", finished.error);
        assert!(root.join("runs/meta.json").is_file());
        assert!(!custom.join("runs").exists());
        assert_eq!(resolve_effective_root(&root), root);
        // 恢复默认后指针必须是 null（而不是把默认根记成自定义根）。
        assert_eq!(
            read_pointer(&root)
                .0
                .and_then(|pointer| pointer.custom_root),
            None
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn unix_epoch_formats_as_rfc3339_utc() {
        assert_eq!(format_unix_seconds(0), "1970-01-01T00:00:00Z");
        assert_eq!(format_unix_seconds(1_787_668_697), "2026-08-25T14:38:17Z");
        assert_eq!(format_unix_seconds(1_709_251_199), "2024-02-29T23:59:59Z");
    }
}
