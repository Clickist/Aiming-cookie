# -*- coding: utf-8 -*-
"""offset_resolver 四级分辨率链单测（纯逻辑，不碰进程内存）。"""
import json

import pytest

from telemetry_capture import offset_resolver as orr


GOOD_ENTRY = {
    "label": "test",
    "rva_guobjectarray": "0x56666F8",
    "rva_slot_target": "0x531BD58",
    "rva_slot_scene": "0x53D4E18",
    "rva_blocks_expect": "0x5639290",
}
GOOD_DIGEST = "ab" * 32


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    """把四级链全部指向 tmp：包内表/缓存/云（关）。"""
    bundled = tmp_path / "offsets.json"
    bundled.write_text("{\n}", encoding="utf-8")
    cache = tmp_path / "offsets.local.json"
    monkeypatch.setenv("AIMING_COOKIE_OFFSETS_BUNDLED", str(bundled))
    monkeypatch.setenv("AIMING_COOKIE_OFFSETS_CACHE", str(cache))
    monkeypatch.delenv("AIMING_COOKIE_OFFSETS_URL", raising=False)
    monkeypatch.setattr(orr, "DEFAULT_CLOUD_URL", "")
    return type("Env", (), {"bundled": str(bundled), "cache": str(cache),
                            "digest": GOOD_DIGEST, "entry": GOOD_ENTRY})()


def _write_table(path, digest, entry):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({digest: entry}, f)


class TestStructural:
    def test_valid(self):
        assert orr.entry_structural_ok(GOOD_ENTRY)

    def test_slots_optional(self):
        e = {"rva_guobjectarray": "0x10", "rva_slot_target": None}
        assert orr.entry_structural_ok(e)

    def test_missing_guoa(self):
        assert not orr.entry_structural_ok({"label": "x"})

    def test_bad_hex(self):
        assert not orr.entry_structural_ok({"rva_guobjectarray": "xyz"})

    def test_bad_slot_hex(self):
        assert not orr.entry_structural_ok(
            {"rva_guobjectarray": "0x10", "rva_slot_target": "zz"})


class TestLoadTable:
    def test_missing_file(self, tmp_path):
        assert orr.load_table(tmp_path / "nope.json") is None

    def test_corrupt_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert orr.load_table(p) is None

    def test_skips_meta_and_nondicts(self, tmp_path):
        p = tmp_path / "t.json"
        p.write_text(json.dumps({"_format": "x", "k": "v", "a": {"b": 1}}), encoding="utf-8")
        assert orr.load_table(p) == {"a": {"b": 1}}


class TestResolutionChain:
    def test_bundled_hit(self, isolated_env):
        _write_table(isolated_env.bundled, isolated_env.digest, isolated_env.entry)
        d, ent, src = orr.resolve("/any/exe", digest=isolated_env.digest)
        assert src == "bundled" and ent["rva_guobjectarray"] == "0x56666F8"

    def test_cache_fallback(self, isolated_env):
        _write_table(isolated_env.cache, isolated_env.digest, isolated_env.entry)
        d, ent, src = orr.resolve("/any/exe", digest=isolated_env.digest)
        assert src == "cache"

    def test_bundled_bad_entry_falls_through(self, isolated_env):
        _write_table(isolated_env.bundled, isolated_env.digest,
                     {"rva_guobjectarray": "not-hex"})
        with pytest.raises(RuntimeError, match="RUNBOOK"):
            orr.resolve("/any/exe", digest=isolated_env.digest)

    def test_cloud_hit(self, isolated_env, monkeypatch):
        monkeypatch.setenv("AIMING_COOKIE_OFFSETS_URL", "https://example.test")
        monkeypatch.setattr(orr, "cloud_fetch",
                            lambda digest, base: dict(isolated_env.entry))
        d, ent, src = orr.resolve("/any/exe", digest=isolated_env.digest)
        assert src == "cloud"

    def test_auto_locates_and_caches(self, isolated_env, monkeypatch):
        seen = {}

        def fake_locate(p, exe_path):
            seen["called"] = True
            return {"label": "auto", "rva_guobjectarray": "0x1234",
                    "rva_slot_target": None, "rva_slot_scene": None,
                    "rva_blocks_expect": None}

        monkeypatch.setattr(orr, "locate_guoa", fake_locate)
        d, ent, src = orr.resolve("/any/exe", digest=isolated_env.digest, proc=object())
        assert src == "auto" and seen["called"]
        cached = json.load(open(isolated_env.cache, encoding="utf-8"))
        assert cached[isolated_env.digest]["rva_guobjectarray"] == "0x1234"

    def test_auto_needs_proc(self, isolated_env):
        with pytest.raises(RuntimeError, match="自适应定位失败"):
            orr.resolve("/any/exe", digest=isolated_env.digest, proc=None)

    def test_cloud_error_degrades(self, isolated_env, monkeypatch):
        # cloud_fetch 的真实契约：任何网络失败都在内部吞掉并返回 None
        monkeypatch.setenv("AIMING_COOKIE_OFFSETS_URL", "https://example.test")
        monkeypatch.setattr(orr, "cloud_fetch", lambda digest, base: None)
        with pytest.raises(RuntimeError):
            orr.resolve("/any/exe", digest=isolated_env.digest, proc=None)


class TestCacheWrite:
    def test_save_merges_and_drops_bad(self, isolated_env):
        _write_table(isolated_env.cache, "cd" * 32, {"rva_guobjectarray": "zz"})
        orr.save_cache_entry(isolated_env.cache, isolated_env.digest,
                             dict(GOOD_ENTRY, rva_guobjectarray="0xABCD"))
        table = json.load(open(isolated_env.cache, encoding="utf-8"))
        assert table[isolated_env.digest]["rva_guobjectarray"] == "0xABCD"
        assert "cd" * 32 not in table   # 坏条目被清理


class TestCloudFetch:
    def test_structural_gate(self, isolated_env, monkeypatch):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"label": "cloud", "rva_guobjectarray": "nothex"}).encode()

        monkeypatch.setenv("AIMING_COOKIE_OFFSETS_URL", "https://example.test")
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
        assert orr.cloud_fetch(isolated_env.digest, "https://example.test") is None
