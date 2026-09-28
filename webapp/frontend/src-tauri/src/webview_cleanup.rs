//! 启动期孤儿 WebView2 进程清理。
//!
//! 应用异常退出后系统会残留 msedgewebview2 进程池；下次启动新实例会并入旧
//! 浏览器进程，`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`（如自动化验收用的
//! `--remote-debugging-port`）被静默忽略。必须在任何 WebView2 环境创建之前
//! 清理——tauri 2 的 setup 钩子运行时 config 窗口（含 WebView2 环境）已建好，
//! 所以唯一够早的位置是 `run()` 最顶部、tauri builder 启动之前。
//!
//! 只杀同时满足两条的 msedgewebview2：
//! ① 命令行包含本应用的 WebView2 用户数据目录（tauri Windows 默认
//!    `%LOCALAPPDATA%/{identifier}`；实测 browser/gpu/utility/renderer/
//!    crashpad 的 `--user-data-dir` 都带该路径，区别仅在有无引号与大小写）；
//! ② 父进程在当前进程表里已不存在，或父进程在本次待杀集合里（杀掉浏览器
//!    进程后其子进程同属孤儿，不依赖 WebView2 job object 兜底）。
//!
//! 语义说明：「父进程已不存在」以 Toolhelp 快照的当前进程表为准——本应用
//! 无论崩溃还是正常退出，残留池的父（应用进程）都已不在，均属清理对象；
//! 父 pid 被系统复用会造成漏判「父还活着」，此时本轮放过、不误杀，代价只是
//! 残留多留一轮，保守方向是对的。其他应用（小组件、Outlook 等）的 WebView2
//! 父进程健在，连 PowerShell 查询都不会发生，零启动开销，且绝不按名误杀。
//!
//! 任何失败（快照失败、PowerShell 不可用、目录解析失败）一律静默返回 0，
//! 绝不阻塞或拖垮启动。

use std::collections::{HashMap, HashSet};
#[cfg(windows)]
use std::path::PathBuf;

/// WebView2 候选进程（Toolhelp 快照里 exe 名为 msedgewebview2.exe 的条目）。
struct WebviewProcess {
    pid: u32,
    parent_pid: u32,
}

/// 路径归一化：小写 + 正斜杠统一成反斜杠 + 去尾部分隔符，供包含比较。
fn normalize_path_key(path: &str) -> String {
    let mut key = path.to_lowercase().replace('/', "\\");
    while key.ends_with('\\') {
        key.pop();
    }
    key
}

/// 命令行是否包含本应用的 WebView2 用户数据目录。命中后还要求匹配串的
/// 下一字符是路径分隔符/引号/空格/结尾，避免 `…desktop` 误吞 `…desktop-plus`
/// 这类前缀相似的兄弟应用目录。
fn command_line_uses_user_data_dir(command_line: &str, user_data_dir: &str) -> bool {
    let dir = normalize_path_key(user_data_dir);
    if dir.is_empty() {
        return false;
    }
    let line = normalize_path_key(command_line);
    let mut from = 0;
    while let Some(offset) = line[from..].find(&dir) {
        let after = from + offset + dir.len();
        match line[after..].chars().next() {
            None | Some('"') | Some(' ') | Some('\\') | Some('/') => return true,
            _ => {}
        }
        from = after;
    }
    false
}

/// 从候选里选出要终止的 pid：命令行匹配本应用数据目录，且父进程已死，或
/// 父进程已在待杀集合。幂等迭代直到集合不再扩张；拿不到命令行的进程放过
/// （清理只做锦上添花，宁可漏杀不可误杀）。
fn select_kill_set(
    candidates: &[WebviewProcess],
    alive_pids: &HashSet<u32>,
    command_lines: &HashMap<u32, String>,
    user_data_dir: &str,
) -> Vec<u32> {
    let mut kill: HashSet<u32> = HashSet::new();
    loop {
        let mut grew = false;
        for candidate in candidates {
            if kill.contains(&candidate.pid) {
                continue;
            }
            let Some(command_line) = command_lines.get(&candidate.pid) else {
                continue;
            };
            if !command_line_uses_user_data_dir(command_line, user_data_dir) {
                continue;
            }
            let parent_gone = !alive_pids.contains(&candidate.parent_pid)
                || kill.contains(&candidate.parent_pid);
            if parent_gone {
                kill.insert(candidate.pid);
                grew = true;
            }
        }
        if !grew {
            break;
        }
    }
    let mut pids: Vec<u32> = kill.into_iter().collect();
    pids.sort_unstable();
    pids
}

/// 入口：返回实际终止的孤儿进程数。同步执行——终止必须在本次启动的
/// WebView2 环境创建之前完成，放进带超时的后台线程反而会与本次启动竞态。
#[cfg(windows)]
pub fn cleanup_orphans() -> usize {
    let Some((alive_pids, candidates)) = snapshot_processes() else {
        return 0;
    };
    // 没有候选，或所有候选父进程健在 → 不可能有孤儿。跳过昂贵的 PowerShell
    // 查询：其他应用的 WebView2 常驻是常态，不能让它们拖慢每次启动。
    if candidates.iter().all(|c| alive_pids.contains(&c.parent_pid)) {
        return 0;
    }
    let Some(user_data_dir) = webview2_user_data_dir() else {
        return 0;
    };
    let command_lines = webview_command_lines();
    if command_lines.is_empty() {
        return 0;
    }
    select_kill_set(&candidates, &alive_pids, &command_lines, &user_data_dir)
        .into_iter()
        .filter(|pid| terminate_process(*pid))
        .count()
}

#[cfg(not(windows))]
pub fn cleanup_orphans() -> usize {
    0
}

/// 与 tauri 的 Windows 默认一致（tauri 2.11.5 manager/webview.rs：无显式
/// data_directory 时强制 `LocalData/{identifier}`）。identifier 与
/// tauri.conf.json 保持一致；路径从环境变量解析，不硬编码用户名。
/// LOCALAPPDATA 缺失等异常环境 → None，静默跳过清理。
#[cfg(windows)]
fn webview2_user_data_dir() -> Option<String> {
    let local_app_data = std::env::var_os("LOCALAPPDATA")?;
    Some(
        PathBuf::from(local_app_data)
            .join("com.aimingcookie.desktop")
            .to_string_lossy()
            .into_owned(),
    )
}

/// Toolhelp 快照：返回（全部存活 pid，WebView2 候选）。失败返回 None。
#[cfg(windows)]
fn snapshot_processes() -> Option<(HashSet<u32>, Vec<WebviewProcess>)> {
    use winapi::um::handleapi::{CloseHandle, INVALID_HANDLE_VALUE};
    use winapi::um::processthreadsapi::GetCurrentProcessId;
    use winapi::um::tlhelp32::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };

    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snapshot == INVALID_HANDLE_VALUE {
            return None;
        }
        let mut entry: PROCESSENTRY32W = std::mem::zeroed();
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        let mut alive_pids = HashSet::new();
        let mut candidates = Vec::new();
        if Process32FirstW(snapshot, &mut entry) != 0 {
            loop {
                alive_pids.insert(entry.th32ProcessID);
                let exe = String::from_utf16_lossy(&entry.szExeFile);
                if exe
                    .trim_end_matches('\0')
                    .eq_ignore_ascii_case("msedgewebview2.exe")
                {
                    candidates.push(WebviewProcess {
                        pid: entry.th32ProcessID,
                        parent_pid: entry.th32ParentProcessID,
                    });
                }
                if Process32NextW(snapshot, &mut entry) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snapshot);
        // 兜底：本进程视为存活，防止 pid 巧合复用把「父=当前 pid」的残留
        // 进程误判成孤儿（虽然概率极低，误杀当前启动链路的代价不可接受）。
        alive_pids.insert(GetCurrentProcessId());
        Some((alive_pids, candidates))
    }
}

/// 借 PowerShell WMI 查 WebView2 候选的命令行（Toolhelp 拿不到），逐行
/// `pid\t命令行`。只在快照发现「父进程已死的候选」时才被调用（见
/// cleanup_orphans 的门禁），PowerShell 冷启动的开销只由脏状态支付。
/// 含换行的畸形命令行会让该行解析失败、进程被放过（保守方向）；任何失败
/// 返回空表 → 本轮不清理。命令行形态已实测：pid 与命令行以 tab 分隔，
/// `--user-data-dir` 的值可能带引号（browser/gpu/renderer）也可能不带
/// （crashpad），匹配逻辑对两者都成立。
#[cfg(windows)]
fn webview_command_lines() -> HashMap<u32, String> {
    use std::os::windows::process::CommandExt;

    let output = std::process::Command::new("powershell")
        .args([
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name = 'msedgewebview2.exe'\" \
             | ForEach-Object { [string]$_.ProcessId + [char]9 + $_.CommandLine }",
        ])
        .creation_flags(crate::NO_CHILD_WINDOW)
        .output();
    let Ok(output) = output else {
        return HashMap::new();
    };
    let mut command_lines = HashMap::new();
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        let Some((pid, command_line)) = line.split_once('\t') else {
            continue;
        };
        if let Ok(pid) = pid.trim().parse::<u32>() {
            command_lines.insert(pid, command_line.to_string());
        }
    }
    command_lines
}

#[cfg(windows)]
fn terminate_process(pid: u32) -> bool {
    use winapi::um::handleapi::CloseHandle;
    use winapi::um::processthreadsapi::{OpenProcess, TerminateProcess};
    use winapi::um::winnt::PROCESS_TERMINATE;

    unsafe {
        let handle = OpenProcess(PROCESS_TERMINATE, 0, pid);
        if handle.is_null() {
            return false;
        }
        let terminated = TerminateProcess(handle, 1) != 0;
        CloseHandle(handle);
        terminated
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(pid: u32, parent_pid: u32) -> WebviewProcess {
        WebviewProcess { pid, parent_pid }
    }

    fn our_command_line() -> String {
        format!(
            "\"C:\\Program Files (x86)\\Microsoft\\EdgeWebView\\Application\\153.0.4234.48\\\
             msedgewebview2.exe\" --user-data-dir=\"{dir}\\EBWebView\"",
            dir = "C:\\Users\\tester\\AppData\\Local\\com.aimingcookie.desktop",
        )
    }

    #[test]
    fn command_line_match_accepts_our_user_data_dir_forms() {
        let dir = "C:\\Users\\tester\\AppData\\Local\\com.aimingcookie.desktop";
        // 实测 browser/gpu/renderer 形态：路径带引号 + EBWebView 后缀
        assert!(command_line_uses_user_data_dir(&our_command_line(), dir));
        // 实测 crashpad 形态：路径无引号、正斜杠、大小写混合
        assert!(command_line_uses_user_data_dir(
            "msedgewebview2.exe --type=crashpad-handler \
             --user-data-dir=c:/users/tester/appdata/local/COM.AIMINGCOOKIE.DESKTOP/EBWebView \
             --database=c:/users/tester/appdata/local/com.aimingcookie.desktop/EBWebView/Crashpad",
            dir,
        ));
        // 命令行恰好以目录本体结尾（无后缀、无引号）
        assert!(command_line_uses_user_data_dir(
            "msedgewebview2.exe --user-data-dir=C:\\Users\\tester\\AppData\\Local\\com.aimingcookie.desktop",
            dir,
        ));
    }

    #[test]
    fn command_line_match_rejects_other_apps_and_prefix_lookalikes() {
        let dir = "C:\\Users\\tester\\AppData\\Local\\com.aimingcookie.desktop";
        // 其他应用的 WebView2 数据目录 → false
        assert!(!command_line_uses_user_data_dir(
            "msedgewebview2.exe --user-data-dir=\"C:\\Users\\tester\\AppData\\Local\\com.othervendor.shell\\EBWebView\"",
            dir,
        ));
        // 前缀相似（多出 -plus 段）→ 边界字符检查必须拒绝
        assert!(!command_line_uses_user_data_dir(
            "msedgewebview2.exe --user-data-dir=\"C:\\Users\\tester\\AppData\\Local\\com.aimingcookie.desktop-plus\\EBWebView\"",
            dir,
        ));
        assert!(!command_line_uses_user_data_dir("", dir));
        assert!(!command_line_uses_user_data_dir(
            "msedgewebview2.exe --type=renderer --noerrdialogs",
            dir,
        ));
    }

    #[test]
    fn kill_set_takes_matching_orphans_and_their_subtree() {
        // 拓扑：应用(10，已退出) → 浏览器(20) → renderer(30)/utility(31)
        // → crashpad(40，父是 renderer)。快照存活：20/30/31/40（应用进程 10
        // 已死，WebView2 池残留——正是要清理的孤儿场景）。
        let alive: HashSet<u32> = [20, 30, 31, 40].into_iter().collect();
        let candidates = vec![entry(20, 10), entry(30, 20), entry(31, 20), entry(40, 30)];
        let dir = "C:\\Users\\tester\\AppData\\Local\\com.aimingcookie.desktop";
        let command_lines: HashMap<u32, String> = [20, 30, 31, 40]
            .into_iter()
            .map(|pid| (pid, our_command_line()))
            .collect();
        assert_eq!(
            select_kill_set(&candidates, &alive, &command_lines, dir),
            vec![20, 30, 31, 40]
        );
    }

    #[test]
    fn kill_set_skips_live_parents_other_apps_and_unknown_cmdlines() {
        let alive: HashSet<u32> = [7, 10, 20, 30].into_iter().collect();
        let dir = "C:\\Users\\tester\\AppData\\Local\\com.aimingcookie.desktop";
        let our = our_command_line();
        let other_app = our.replace("com.aimingcookie.desktop", "com.othervendor.shell");
        let candidates = vec![
            entry(20, 7),    // 命令行匹配但父进程健在（正运行的实例）→ 不杀
            entry(30, 999),  // 父已死但拿不到命令行（PowerShell 缺口）→ 不杀
            entry(40, 999),  // 父已死但命令行是其他应用的目录 → 不杀
            entry(50, 999),  // 父已死 + 命令行匹配 → 杀
            entry(60, 50),   // 父在待杀集合 → 连带杀
        ];
        let command_lines: HashMap<u32, String> = [
            (20, our.clone()),
            (40, other_app),
            (50, our.clone()),
            (60, our),
        ]
        .into_iter()
        .collect();
        assert_eq!(
            select_kill_set(&candidates, &alive, &command_lines, dir),
            vec![50, 60]
        );
        // 空候选 → 空集
        assert!(select_kill_set(&[], &alive, &command_lines, dir).is_empty());
    }
}
