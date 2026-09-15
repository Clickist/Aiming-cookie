use serde::Serialize;
use std::path::{Path, PathBuf};
#[cfg(windows)]
use std::process::Command;

const STEAM_APP_ID: &str = "824270";
const SCENARIOS_RELATIVE: &str = "FPSAimTrainer/Saved/SaveGames/Scenarios";
const MAX_SCENARIO_NAME_LEN: usize = 200;

#[derive(Debug, Serialize)]
pub struct ScenarioOpenResult {
    pub status: String,
    pub scenario_name: Option<String>,
    pub display_name: Option<String>,
    pub message: String,
}

/// Case- and whitespace-tolerant key: collapse whitespace runs, trim, lowercase.
fn normalize_scenario_name(value: &str) -> String {
    value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
}

/// Resolve a user-supplied scenario name against the local `Scenarios/*.sce`
/// directory. Returns the canonical on-disk stem when it matches.
fn resolve_local_scenario(scenario_name: &str, scenarios_dir: &Path) -> Option<String> {
    if scenario_name.is_empty()
        || scenario_name.len() > MAX_SCENARIO_NAME_LEN
        || scenario_name.chars().any(char::is_control)
    {
        return None;
    }
    let wanted = normalize_scenario_name(scenario_name);
    let entries = std::fs::read_dir(scenarios_dir).ok()?;
    let mut best: Option<String> = None;
    for entry in entries.flatten() {
        let path = entry.path();
        if path
            .extension()
            .and_then(|extension| extension.to_str())
            .map(|extension| extension.eq_ignore_ascii_case("sce"))
            != Some(true)
        {
            continue;
        }
        let Some(stem) = path.file_stem().and_then(|stem| stem.to_str()) else {
            continue;
        };
        if normalize_scenario_name(stem) != wanted {
            continue;
        }
        // Prefer the shortest canonical spelling so the deep link stays stable.
        best = Some(match best {
            Some(existing) if existing.len() <= stem.len() => existing,
            _ => stem.to_string(),
        });
    }
    best
}

fn add_dir(dir: PathBuf, dirs: &mut Vec<PathBuf>) {
    if dir.is_dir() && !dirs.contains(&dir) {
        dirs.push(dir);
    }
}

#[cfg(windows)]
fn registry_value(hive: &str, key: &str, name: &str) -> Option<String> {
    let output = Command::new("reg")
        .args(["query", &format!("{hive}\\{key}"), "/v", name])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    for line in text.lines() {
        let mut parts = line.split_whitespace();
        if parts.next() != Some(name) {
            continue;
        }
        let _value_type = parts.next();
        let value = parts.collect::<Vec<_>>().join(" ");
        if !value.is_empty() {
            return Some(value);
        }
    }
    None
}

#[cfg(windows)]
fn steam_roots() -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = Vec::new();
    for (hive, key, name) in [
        ("HKCU", r"Software\Valve\Steam", "SteamPath"),
        ("HKLM", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ] {
        if let Some(value) = registry_value(hive, key, name) {
            let path = PathBuf::from(value.replace('/', "\\"));
            if path.is_dir() && !roots.contains(&path) {
                roots.push(path);
            }
        }
    }
    roots
}

/// Every quoted token in the VDF that looks like an absolute Windows path.
fn quoted_library_paths(vdf: &str) -> Vec<PathBuf> {
    let bytes = vdf.as_bytes();
    let mut paths: Vec<PathBuf> = Vec::new();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] != b'"' {
            index += 1;
            continue;
        }
        let start = index + 1;
        let mut end = start;
        while end < bytes.len() && bytes[end] != b'"' {
            end += 1;
        }
        if end >= bytes.len() {
            break;
        }
        let decoded = vdf[start..end].replace("\\\\", "\\");
        let decoded_bytes = decoded.as_bytes();
        if decoded_bytes.len() > 3
            && decoded_bytes[1] == b':'
            && (decoded_bytes[2] == b'\\' || decoded_bytes[2] == b'/')
        {
            paths.push(PathBuf::from(decoded));
        }
        index = end + 1;
    }
    paths
}

fn quoted_value_after(text: &str, key: &str) -> Option<String> {
    let needle = format!("\"{key}\"");
    let start = text.find(&needle)? + needle.len();
    let rest = &text[start..];
    let open = rest.find('"')? + 1;
    let rest = &rest[open..];
    let close = rest.find('"')?;
    Some(rest[..close].to_string())
}

fn scenarios_dir_in_library(library: &Path) -> Option<PathBuf> {
    let manifest_path = library
        .join("steamapps")
        .join(format!("appmanifest_{STEAM_APP_ID}.acf"));
    let text = std::fs::read_to_string(manifest_path).ok()?;
    let install_name = quoted_value_after(&text, "installdir")?;
    let dir = library
        .join("steamapps")
        .join("common")
        .join(install_name)
        .join(SCENARIOS_RELATIVE);
    dir.is_dir().then_some(dir)
}

#[cfg(windows)]
fn collect_steam_scenarios_dirs(dirs: &mut Vec<PathBuf>) {
    for root in steam_roots() {
        let mut libraries = vec![root.clone()];
        if let Ok(vdf) = std::fs::read_to_string(root.join("steamapps").join("libraryfolders.vdf"))
        {
            for path in quoted_library_paths(&vdf) {
                if !libraries.contains(&path) {
                    libraries.push(path);
                }
            }
        }
        for library in libraries {
            if let Some(dir) = scenarios_dir_in_library(&library) {
                add_dir(dir, dirs);
            }
        }
    }
}

/// Candidate local scenario directories, explicit overrides first.
fn local_scenarios_dirs() -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = Vec::new();
    if let Ok(explicit) = std::env::var("KOVAAK_SCENARIOS_DIR") {
        if !explicit.trim().is_empty() {
            add_dir(PathBuf::from(explicit), &mut dirs);
        }
    }
    if let Ok(install) = std::env::var("KOVAAK_INSTALL_DIR") {
        if !install.trim().is_empty() {
            add_dir(PathBuf::from(install).join(SCENARIOS_RELATIVE), &mut dirs);
        }
    }
    #[cfg(windows)]
    collect_steam_scenarios_dirs(&mut dirs);
    dirs
}

fn resolve_installed_scenario(scenario_name: &str) -> Option<String> {
    for dir in local_scenarios_dirs() {
        if let Some(canonical) = resolve_local_scenario(scenario_name, &dir) {
            return Some(canonical);
        }
    }
    None
}

fn percent_encode_component(value: &str) -> String {
    value
        .as_bytes()
        .iter()
        .map(|byte| match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                (*byte as char).to_string()
            }
            _ => format!("%{byte:02X}"),
        })
        .collect()
}

fn build_uri(display_name: &str) -> String {
    format!(
        "steam://run/{STEAM_APP_ID}/?action=jump-to-scenario;name={};mode=challenge",
        percent_encode_component(display_name),
    )
}

#[cfg(windows)]
fn dispatch_uri(uri: &str) -> Result<(), String> {
    // cmd 是控制台程序；不加 CREATE_NO_WINDOW 每次派发都会闪现黑色命令行窗口。
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let status = Command::new("cmd")
        .args(["/C", "start", "", uri])
        .creation_flags(CREATE_NO_WINDOW)
        .status()
        .map_err(|error| error.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("deep link dispatcher exited with {status}"))
    }
}

#[cfg(not(windows))]
fn dispatch_uri(_uri: &str) -> Result<(), String> {
    Err("desktop_unavailable".to_string())
}

#[tauri::command]
pub fn scenario_open(scenario_name: String) -> ScenarioOpenResult {
    let Some(display_name) = resolve_installed_scenario(&scenario_name) else {
        return ScenarioOpenResult {
            status: "scenario_unmapped".to_string(),
            scenario_name: None,
            display_name: None,
            message: "本机 KovaaK 没有这个场景，需要先订阅/下载。".to_string(),
        };
    };

    let uri = build_uri(&display_name);
    match dispatch_uri(&uri) {
        Ok(()) => ScenarioOpenResult {
            status: "scenario_dispatched".to_string(),
            scenario_name: Some(display_name.clone()),
            display_name: Some(display_name),
            message: "已请求打开 KovaaK，请确认目标场景已加载。".to_string(),
        },
        Err(error) if error == "desktop_unavailable" => ScenarioOpenResult {
            status: "desktop_unavailable".to_string(),
            scenario_name: Some(display_name.clone()),
            display_name: Some(display_name),
            message: "当前网页预览不能启动 KovaaK，请在桌面版中操作。".to_string(),
        },
        Err(_) => ScenarioOpenResult {
            status: "deep_link_dispatch_failed".to_string(),
            scenario_name: Some(display_name.clone()),
            display_name: Some(display_name),
            message: "未能请求打开 KovaaK，请确认 Steam 已安装后重试。".to_string(),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::{build_uri, resolve_local_scenario};
    use std::path::PathBuf;

    fn fixture_dir(name: &str, files: &[&str]) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "aiming-cookie-scenario-{name}-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&dir).expect("create fixture dir");
        for file in files {
            std::fs::write(dir.join(file), b"fixture").expect("write scenario fixture");
        }
        dir
    }

    #[test]
    fn resolves_a_local_scenario_with_tolerant_matching() {
        let dir = fixture_dir("hit", &["1wall 6targets small.sce", "pasu.sce"]);
        assert_eq!(
            resolve_local_scenario("1wall 6targets small", &dir).as_deref(),
            Some("1wall 6targets small"),
        );
        // Case- and whitespace-tolerant.
        assert_eq!(
            resolve_local_scenario("  1Wall   6Targets   Small  ", &dir).as_deref(),
            Some("1wall 6targets small"),
        );
        assert_eq!(
            resolve_local_scenario("pasu", &dir).as_deref(),
            Some("pasu")
        );
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn rejects_a_scenario_that_is_not_installed_locally() {
        let dir = fixture_dir("miss", &["1wall 6targets small.sce"]);
        assert!(resolve_local_scenario("vt multiclick 120", &dir).is_none());
        assert!(resolve_local_scenario("", &dir).is_none());
        assert!(resolve_local_scenario("scenario:static.1wall@1", &dir).is_none());
        assert!(resolve_local_scenario(&"x".repeat(201), &dir).is_none());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn builds_the_fixed_kovaak_deep_link_with_encoding() {
        assert_eq!(
            build_uri("1wall 6targets small"),
            "steam://run/824270/?action=jump-to-scenario;name=1wall%206targets%20small;mode=challenge",
        );
    }
}
