"""Tests for utils/config.py"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mxu_config(instances: list[dict]) -> dict:
    return {"instances": instances}


def _make_instance(name="Test", tasks=None, global_opts=None):
    return {
        "id": "abc123",
        "name": name,
        "globalOptionValues": global_opts or {},
        "tasks": tasks or [],
    }


def _folder_opt(path: str) -> dict:
    return {"type": "folder", "path": path}


def _select_opt(case_name: str) -> dict:
    return {"type": "select", "caseName": case_name}


def _switch_opt(value: bool) -> dict:
    return {"type": "switch", "value": value}


def _textarea_opt(text: str) -> dict:
    return {"type": "textarea", "text": text}


# ── _extract_opt ──────────────────────────────────────────────────────────────

class TestExtractOpt:
    def setup_method(self):
        from utils.config import _extract_opt
        self.extract = _extract_opt

    def test_folder(self):
        assert self.extract({"K": _folder_opt("/game")}, "K") == "/game"

    def test_switch_true(self):
        assert self.extract({"K": _switch_opt(True)}, "K") is True

    def test_switch_false(self):
        assert self.extract({"K": _switch_opt(False)}, "K") is False

    def test_select(self):
        assert self.extract({"K": _select_opt("Yes")}, "K") == "Yes"

    def test_textarea(self):
        assert self.extract({"K": _textarea_opt("hello")}, "K") == "hello"

    def test_missing_key(self):
        assert self.extract({}, "missing") is None

    def test_file_list(self):
        from utils.config import _extract_opt
        v = {"type": "file_list", "paths": ["/a", "/b"]}
        assert _extract_opt({"K": v}, "K") == ["/a", "/b"]


# ── _extract_special_task_params ─────────────────────────────────────────────

class TestExtractSpecialTaskParams:
    def setup_method(self):
        from utils.config import _extract_special_task_params
        self.extract = _extract_special_task_params

    def test_sleep_option(self):
        opt = {"__MXU_SLEEP_OPTION__": {"type": "input", "values": {"sleep_time": "5"}}}
        result = self.extract(opt)
        assert result == {"sleep_time": "5"}

    def test_power_select(self):
        opt = {"__MXU_POWER_OPTION__": {"type": "select", "caseName": "shutdown"}}
        result = self.extract(opt)
        assert result["power_action"] == "shutdown"

    def test_launch_wait_switch(self):
        opt = {"__MXU_LAUNCH_WAIT_OPTION__": {"type": "switch", "value": True}}
        result = self.extract(opt)
        assert result["wait_for_exit"] is True

    def test_kill_self_switch(self):
        opt = {"__MXU_KILLPROC_SELF_OPTION__": {"type": "switch", "value": False}}
        result = self.extract(opt)
        assert result["kill_self"] is False

    def test_empty(self):
        assert self.extract({}) == {}

    def test_unknown_key_falls_back_to_generic(self):
        opt = {"__UNKNOWN_OPTION__": {"type": "input", "values": {"foo": "bar"}}}
        result = self.extract(opt)
        assert result.get("foo") == "bar"


# ── _translate / GamePath / GameType ─────────────────────────────────────────

class TestTranslate:
    def setup_method(self):
        from utils.config import Config
        self.Config = Config

    def _translate(self, inst, mxu=None):
        from utils.config import Config
        if mxu is None:
            mxu = _make_mxu_config([inst])
        return Config._translate(inst, mxu)

    def test_gamepath_from_global_opt(self):
        inst = _make_instance(global_opts={"GamePath": _folder_opt("/game")})
        data, _ = self._translate(inst)
        assert data["Core"]["GamePath"] == "/game"

    def test_gametype_from_global_opt(self):
        inst = _make_instance(global_opts={
            "GamePath": _folder_opt("/game"),
            "GameType": _select_opt("KoikatsuSunshine"),
        })
        data, _ = self._translate(inst)
        assert data["Core"]["GameType"] == "KoikatsuSunshine"

    def test_gametype_defaults_to_koikatsu(self):
        inst = _make_instance(global_opts={"GamePath": _folder_opt("/game")})
        data, _ = self._translate(inst)
        assert data["Core"]["GameType"] == "Koikatsu"

    def test_gamepath_fallback_to_other_instance(self):
        inst0 = _make_instance("I0", global_opts={"GamePath": _folder_opt("/game")})
        inst1 = _make_instance("I1", global_opts={})
        mxu = _make_mxu_config([inst0, inst1])
        data, _ = self.Config._translate(inst1, mxu)
        assert data["Core"]["GamePath"] == "/game"

    def test_task_order_enabled_only(self):
        tasks = [
            {"taskName": "InstallContents", "enabled": True,  "optionValues": {}},
            {"taskName": "RemoveContents",  "enabled": False, "optionValues": {}},
            {"taskName": "GroupChara",       "enabled": True,  "optionValues": {}},
        ]
        inst = _make_instance(tasks=tasks)
        _, order = self._translate(inst)
        names = [e["name"] for e in order]
        assert "InstallContents" in names
        assert "RemoveContents" not in names
        assert "GroupChara" in names

    def test_task_order_preserves_instance_order(self):
        tasks = [
            {"taskName": "GroupChara",       "enabled": True, "optionValues": {}},
            {"taskName": "InstallContents", "enabled": True, "optionValues": {}},
        ]
        inst = _make_instance(tasks=tasks)
        _, order = self._translate(inst)
        names = [e["name"] for e in order]
        assert names.index("GroupChara") < names.index("InstallContents")

    def test_special_task_in_order(self):
        tasks = [
            {"taskName": "__MXU_SLEEP__", "enabled": True,
             "optionValues": {"__MXU_SLEEP_OPTION__": {"type": "input", "values": {"sleep_time": "3"}}}},
            {"taskName": "InstallContents", "enabled": True, "optionValues": {}},
        ]
        inst = _make_instance(tasks=tasks)
        _, order = self._translate(inst)
        assert order[0]["name"] == "__MXU_SLEEP__"
        assert order[0]["params"]["sleep_time"] == "3"
        assert order[1]["name"] == "InstallContents"

    def test_no_stale_filterconvertkks_in_defaults(self):
        from utils.config import _TASK_DEFAULTS
        assert "FilterConvertKKS" not in _TASK_DEFAULTS


# ── list_instances ────────────────────────────────────────────────────────────

class TestListInstances:
    def test_returns_index_and_name(self, tmp_path):
        from utils.config import list_instances
        cfg = _make_mxu_config([
            _make_instance("Alpha"),
            _make_instance("Beta"),
        ])
        p = tmp_path / "config.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        result = list_instances(str(p))
        assert result == [(0, "Alpha"), (1, "Beta")]

    def test_missing_file_returns_empty(self, tmp_path):
        from utils.config import list_instances
        result = list_instances(str(tmp_path / "nonexistent.json"))
        assert result == []

    def test_invalid_json_returns_empty(self, tmp_path):
        from utils.config import list_instances
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")
        assert list_instances(str(p)) == []
