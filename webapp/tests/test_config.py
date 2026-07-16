from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from webapp.backend import config


_STEAM_HKCU_KEY = r"Software\Valve\Steam"
_STEAM_HKLM_KEY = r"SOFTWARE\WOW6432Node\Valve\Steam"


class _FakeRegistryKey:
    def __init__(self, hive: str, subkey: str) -> None:
        self.hive = hive
        self.subkey = subkey

    def __enter__(self) -> _FakeRegistryKey:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeWinreg(ModuleType):
    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_READ = 0x20019
    KEY_WOW64_32KEY = 0x0200
    KEY_WOW64_64KEY = 0x0100
    REG_SZ = 1

    def __init__(
        self,
        values: dict[tuple[str, str, str], object] | None = None,
        *,
        forbid_reads: bool = False,
    ) -> None:
        super().__init__("winreg")
        self.values = values or {}
        self.forbid_reads = forbid_reads
        self.opened: list[tuple[str, str]] = []

    def OpenKey(self, hive: str, subkey: str, *_args: object, **_kwargs: object) -> _FakeRegistryKey:
        if self.forbid_reads:
            raise AssertionError("Steam registry discovery must not run for an explicit override")
        if not any(key[:2] == (hive, subkey) for key in self.values):
            raise FileNotFoundError(subkey)
        self.opened.append((hive, subkey))
        return _FakeRegistryKey(hive, subkey)

    OpenKeyEx = OpenKey

    def QueryValueEx(self, key: _FakeRegistryKey, value_name: str) -> tuple[object, int]:
        try:
            return self.values[(key.hive, key.subkey, value_name)], self.REG_SZ
        except KeyError as error:
            raise FileNotFoundError(value_name) from error

    def QueryValue(self, key: _FakeRegistryKey, value_name: str) -> object:
        return self.QueryValueEx(key, value_name)[0]

    def CloseKey(self, _key: _FakeRegistryKey) -> None:
        return None


def _prepare_windows_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    values: dict[tuple[str, str, str], object] | None = None,
    *,
    forbid_registry_reads: bool = False,
) -> _FakeWinreg:
    for name in (
        "KOVAAK_INSTALL_DIR",
        "KOVAAK_STATS_DIR",
        "KOVAAK_PERFORMANCE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "missing-program-files-x86"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "missing-program-files"))
    fake_winreg = _FakeWinreg(values, forbid_reads=forbid_registry_reads)
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(config, "winreg", fake_winreg, raising=False)
    return fake_winreg


def _write_app_manifest(
    library: Path,
    *,
    appid: str = "824270",
    installdir: str = "FPSAimTrainer",
    malformed: bool = False,
) -> Path:
    steamapps = library / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    closing_brace = "" if malformed else "}\n"
    (steamapps / "appmanifest_824270.acf").write_text(
        "\n".join(
            (
                '"AppState"',
                "{",
                f'    "appid" "{appid}"',
                f'    "installdir" "{installdir}"',
                closing_brace,
            )
        ),
        encoding="utf-8",
    )
    return steamapps / "common" / installdir


def _write_libraryfolders(
    steam_root: Path,
    libraries: list[Path],
    *,
    malformed: bool = False,
) -> None:
    steamapps = steam_root / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, library in enumerate(libraries):
        escaped = str(library).replace("\\", "\\\\")
        entries.extend(
            (
                f'    "{index}"',
                "    {",
                f'        "path" "{escaped}"',
                "    }",
            )
        )
    closing_brace = "" if malformed else "}"
    (steamapps / "libraryfolders.vdf").write_text(
        "\n".join(('"libraryfolders"', "{", *entries, closing_brace)),
        encoding="utf-8",
    )


def test_explicit_kovaak_install_override_has_highest_priority_without_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    override = tmp_path / "does-not-need-to-exist"
    _prepare_windows_discovery(
        monkeypatch,
        tmp_path,
        forbid_registry_reads=True,
    )
    monkeypatch.setenv("KOVAAK_INSTALL_DIR", str(override))

    assert config.resolve_kovaak_install_dir() == override.resolve()


@pytest.mark.parametrize(
    ("hive", "subkey", "value_name"),
    (
        (_FakeWinreg.HKEY_CURRENT_USER, _STEAM_HKCU_KEY, "SteamPath"),
        (_FakeWinreg.HKEY_LOCAL_MACHINE, _STEAM_HKLM_KEY, "InstallPath"),
    ),
)
def test_windows_registry_steam_root_discovers_main_library_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hive: str,
    subkey: str,
    value_name: str,
) -> None:
    steam_root = tmp_path / f"steam-{hive.lower()}"
    install = _write_app_manifest(steam_root)
    install.mkdir(parents=True)
    _write_libraryfolders(steam_root, [])
    _prepare_windows_discovery(
        monkeypatch,
        tmp_path,
        {(hive, subkey, value_name): str(steam_root)},
    )

    assert config.resolve_kovaak_install_dir() == install.resolve()


def test_windows_discovery_fails_closed_when_main_library_vdf_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steam_root = tmp_path / "steam"
    install = _write_app_manifest(steam_root)
    install.mkdir(parents=True)
    _prepare_windows_discovery(
        monkeypatch,
        tmp_path,
        {(_FakeWinreg.HKEY_CURRENT_USER, _STEAM_HKCU_KEY, "SteamPath"): str(steam_root)},
    )

    assert config.resolve_kovaak_install_dir() is None


def test_windows_discovery_fails_closed_when_main_library_vdf_is_malformed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steam_root = tmp_path / "steam"
    install = _write_app_manifest(steam_root)
    install.mkdir(parents=True)
    _write_libraryfolders(steam_root, [], malformed=True)
    _prepare_windows_discovery(
        monkeypatch,
        tmp_path,
        {(_FakeWinreg.HKEY_CURRENT_USER, _STEAM_HKCU_KEY, "SteamPath"): str(steam_root)},
    )

    assert config.resolve_kovaak_install_dir() is None


def test_windows_libraryfolders_discovers_additional_library_and_deduplicates_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steam_root = tmp_path / "steam"
    extra_library = tmp_path / "steam-library"
    install = _write_app_manifest(extra_library)
    install.mkdir(parents=True)
    _write_libraryfolders(
        steam_root,
        [steam_root, extra_library, extra_library],
    )
    _prepare_windows_discovery(
        monkeypatch,
        tmp_path,
        {
            (_FakeWinreg.HKEY_CURRENT_USER, _STEAM_HKCU_KEY, "SteamPath"): str(steam_root),
            (_FakeWinreg.HKEY_LOCAL_MACHINE, _STEAM_HKLM_KEY, "InstallPath"): str(steam_root),
        },
    )

    assert config.resolve_kovaak_install_dir() == install.resolve()


@pytest.mark.parametrize("case", ("missing_registry", "malformed_registry", "missing_manifest"))
def test_windows_discovery_fails_closed_for_missing_or_malformed_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    values: dict[tuple[str, str, str], object] = {}
    if case == "malformed_registry":
        values[(_FakeWinreg.HKEY_CURRENT_USER, _STEAM_HKCU_KEY, "SteamPath")] = 824270
    elif case == "missing_manifest":
        steam_root = tmp_path / "steam"
        (steam_root / "steamapps").mkdir(parents=True)
        _write_libraryfolders(steam_root, [])
        values[(_FakeWinreg.HKEY_CURRENT_USER, _STEAM_HKCU_KEY, "SteamPath")] = str(steam_root)
    _prepare_windows_discovery(monkeypatch, tmp_path, values)

    assert config.resolve_kovaak_install_dir() is None


def test_windows_discovery_ignores_malformed_libraryfolders_even_with_plausible_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steam_root = tmp_path / "steam"
    extra_library = tmp_path / "steam-library"
    install = _write_app_manifest(extra_library)
    install.mkdir(parents=True)
    _write_libraryfolders(steam_root, [extra_library], malformed=True)
    _prepare_windows_discovery(
        monkeypatch,
        tmp_path,
        {(_FakeWinreg.HKEY_CURRENT_USER, _STEAM_HKCU_KEY, "SteamPath"): str(steam_root)},
    )

    assert config.resolve_kovaak_install_dir() is None


@pytest.mark.parametrize("appid", ("1824270", "8242700", "not-an-app-id"))
def test_windows_discovery_requires_exact_app_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    appid: str,
) -> None:
    steam_root = tmp_path / "steam"
    install = _write_app_manifest(steam_root, appid=appid)
    install.mkdir(parents=True)
    _write_libraryfolders(steam_root, [])
    _prepare_windows_discovery(
        monkeypatch,
        tmp_path,
        {(_FakeWinreg.HKEY_CURRENT_USER, _STEAM_HKCU_KEY, "SteamPath"): str(steam_root)},
    )

    assert config.resolve_kovaak_install_dir() is None


def test_windows_discovery_rejects_malformed_manifest_with_valid_looking_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steam_root = tmp_path / "steam"
    install = _write_app_manifest(steam_root, malformed=True)
    install.mkdir(parents=True)
    _write_libraryfolders(steam_root, [])
    _prepare_windows_discovery(
        monkeypatch,
        tmp_path,
        {(_FakeWinreg.HKEY_CURRENT_USER, _STEAM_HKCU_KEY, "SteamPath"): str(steam_root)},
    )

    assert config.resolve_kovaak_install_dir() is None


@pytest.mark.parametrize("kind", ("absolute", "parent", "multiple_segments"))
def test_windows_discovery_rejects_unsafe_installdir_even_when_target_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    steam_root = tmp_path / "steam"
    if kind == "absolute":
        target = tmp_path / "absolute-install"
        installdir = str(target)
    elif kind == "parent":
        target = steam_root / "steamapps" / "escaped"
        installdir = "../escaped"
    else:
        target = steam_root / "steamapps" / "common" / "nested" / "FPSAimTrainer"
        installdir = "nested/FPSAimTrainer"
    target.mkdir(parents=True)
    _write_app_manifest(steam_root, installdir=installdir)
    _write_libraryfolders(steam_root, [])
    _prepare_windows_discovery(
        monkeypatch,
        tmp_path,
        {(_FakeWinreg.HKEY_CURRENT_USER, _STEAM_HKCU_KEY, "SteamPath"): str(steam_root)},
    )

    assert config.resolve_kovaak_install_dir() is None


def test_windows_discovery_requires_existing_install_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steam_root = tmp_path / "steam"
    _write_app_manifest(steam_root)
    _write_libraryfolders(steam_root, [])
    _prepare_windows_discovery(
        monkeypatch,
        tmp_path,
        {(_FakeWinreg.HKEY_CURRENT_USER, _STEAM_HKCU_KEY, "SteamPath"): str(steam_root)},
    )

    assert config.resolve_kovaak_install_dir() is None


def test_windows_discovery_fails_closed_for_multiple_distinct_valid_installs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "steam-one"
    second_root = tmp_path / "steam-two"
    first_install = _write_app_manifest(first_root)
    second_install = _write_app_manifest(second_root)
    first_install.mkdir(parents=True)
    second_install.mkdir(parents=True)
    _write_libraryfolders(first_root, [])
    _write_libraryfolders(second_root, [])
    _prepare_windows_discovery(
        monkeypatch,
        tmp_path,
        {
            (_FakeWinreg.HKEY_CURRENT_USER, _STEAM_HKCU_KEY, "SteamPath"): str(first_root),
            (_FakeWinreg.HKEY_LOCAL_MACHINE, _STEAM_HKLM_KEY, "InstallPath"): str(second_root),
        },
    )

    assert config.resolve_kovaak_install_dir() is None


@pytest.mark.parametrize("override_kind", ("stats", "performance"))
def test_stats_and_performance_overrides_independently_replace_derived_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override_kind: str,
) -> None:
    install = tmp_path / "FPSAimTrainer-install"
    override = tmp_path / f"custom-{override_kind}"
    monkeypatch.setenv("KOVAAK_INSTALL_DIR", str(install))
    monkeypatch.delenv("KOVAAK_STATS_DIR", raising=False)
    monkeypatch.delenv("KOVAAK_PERFORMANCE_DIR", raising=False)
    monkeypatch.setenv(
        "KOVAAK_STATS_DIR" if override_kind == "stats" else "KOVAAK_PERFORMANCE_DIR",
        str(override),
    )

    stats, performance = config.resolve_kovaak_data_dirs()

    assert stats == (
        override.resolve()
        if override_kind == "stats"
        else install.resolve() / "FPSAimTrainer" / "stats"
    )
    assert performance == (
        override.resolve()
        if override_kind == "performance"
        else install.resolve() / "FPSAimTrainer" / "performances"
    )


def test_non_windows_without_overrides_returns_no_kovaak_directories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.sys, "platform", "linux")
    for name in (
        "KOVAAK_INSTALL_DIR",
        "KOVAAK_STATS_DIR",
        "KOVAAK_PERFORMANCE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    assert config.resolve_kovaak_install_dir() is None
    assert config.resolve_kovaak_data_dirs() == (None, None)
