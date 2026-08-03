use serde::{Deserialize, Serialize};
#[cfg(windows)]
use std::process::Command;

const REGISTRY_JSON: &str = include_str!("../../../../knowledge/scenarios/registry.v1.json");
const LAUNCH_MANIFEST_JSON: &str =
    include_str!("../../../../knowledge/scenarios/launch-manifest.v1.json");
const STEAM_APP_ID: &str = "824270";

#[derive(Debug, Deserialize)]
struct RegistryAsset {
    entries: Vec<RegistryEntry>,
}

#[derive(Debug, Deserialize)]
struct RegistryEntry {
    entry_id: String,
    entry_version: u32,
    status: String,
    display_name: String,
}

#[derive(Debug, Deserialize)]
struct LaunchManifestAsset {
    entries: Vec<LaunchManifestEntry>,
}

#[derive(Debug, Deserialize)]
struct LaunchManifestEntry {
    scenario_profile_ref: String,
    status: String,
}

#[derive(Debug, Serialize)]
pub struct ScenarioOpenResult {
    pub status: String,
    pub scenario_profile_ref: Option<String>,
    pub display_name: Option<String>,
    pub message: String,
}

fn profile_ref(entry: &RegistryEntry) -> String {
    format!("scenario:{}@{}", entry.entry_id, entry.entry_version)
}

fn resolve_scenario(scenario_profile_ref: &str) -> Option<String> {
    if scenario_profile_ref.len() > 240
        || !scenario_profile_ref.starts_with("scenario:")
        || scenario_profile_ref
            .chars()
            .any(|character| character.is_control())
    {
        return None;
    }

    let registry: RegistryAsset = serde_json::from_str(REGISTRY_JSON).ok()?;
    let manifest: LaunchManifestAsset = serde_json::from_str(LAUNCH_MANIFEST_JSON).ok()?;
    let launchable = manifest.entries.iter().any(|entry| {
        entry.status == "active" && entry.scenario_profile_ref == scenario_profile_ref
    });

    registry.entries.iter().find_map(|entry| {
        (entry.status == "active" && launchable && profile_ref(entry) == scenario_profile_ref)
            .then(|| entry.display_name.clone())
    })
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
    let status = Command::new("cmd")
        .args(["/C", "start", "", uri])
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
pub fn scenario_open(scenario_profile_ref: String) -> ScenarioOpenResult {
    let Some(display_name) = resolve_scenario(&scenario_profile_ref) else {
        return ScenarioOpenResult {
            status: "scenario_unmapped".to_string(),
            scenario_profile_ref: None,
            display_name: None,
            message: "该训练项目没有可验证的本地 KovaaK 场景。".to_string(),
        };
    };

    let uri = build_uri(&display_name);
    match dispatch_uri(&uri) {
        Ok(()) => ScenarioOpenResult {
            status: "scenario_dispatched".to_string(),
            scenario_profile_ref: Some(scenario_profile_ref),
            display_name: Some(display_name),
            message: "已请求打开 KovaaK，请确认目标场景已加载。".to_string(),
        },
        Err(error) if error == "desktop_unavailable" => ScenarioOpenResult {
            status: "desktop_unavailable".to_string(),
            scenario_profile_ref: Some(scenario_profile_ref),
            display_name: Some(display_name),
            message: "当前网页预览不能启动 KovaaK，请在桌面版中操作。".to_string(),
        },
        Err(_) => ScenarioOpenResult {
            status: "deep_link_dispatch_failed".to_string(),
            scenario_profile_ref: Some(scenario_profile_ref),
            display_name: Some(display_name),
            message: "未能请求打开 KovaaK，请确认 Steam 已安装后重试。".to_string(),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::{build_uri, resolve_scenario};

    #[test]
    fn resolves_only_an_active_exact_manifest_ref() {
        assert_eq!(
            resolve_scenario("scenario:static.1wall_6targets_small@1").as_deref(),
            Some("1wall 6targets small"),
        );
        assert!(resolve_scenario("scenario:static.1wall_6targets_small@99").is_none());
        assert!(resolve_scenario("1wall 6targets small").is_none());
    }

    #[test]
    fn builds_the_fixed_kovaak_deep_link() {
        assert_eq!(
            build_uri("1wall 6targets small"),
            "steam://run/824270/?action=jump-to-scenario;name=1wall%206targets%20small;mode=challenge",
        );
    }
}
