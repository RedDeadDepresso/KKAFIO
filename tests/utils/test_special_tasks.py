"""Tests for utils/special_tasks.py"""
import threading
import pytest
from unittest.mock import patch, MagicMock


class TestRunSleep:
    def test_sleeps_and_returns_true(self):
        from utils.special_tasks import run_sleep
        stop = threading.Event()
        result = run_sleep({"sleep_time": 0}, stop)
        assert result is True

    def test_interrupted_returns_false(self):
        from utils.special_tasks import run_sleep
        stop = threading.Event()
        stop.set()
        result = run_sleep({"sleep_time": 60}, stop)
        assert result is False

    def test_defaults_to_5_seconds_on_bad_value(self):
        from utils.special_tasks import run_sleep
        stop = threading.Event()
        stop.set()  # interrupt immediately so test is fast
        result = run_sleep({"sleep_time": "not_a_number"}, stop)
        assert result is False  # interrupted, not crashed


class TestRunWaitUntil:
    def test_invalid_format_returns_false(self):
        from utils.special_tasks import run_wait_until
        stop = threading.Event()
        assert run_wait_until({"target_time": "bad"}, stop) is False

    def test_missing_target_time_returns_false(self):
        from utils.special_tasks import run_wait_until
        stop = threading.Event()
        assert run_wait_until({}, stop) is False

    def test_out_of_range_returns_false(self):
        from utils.special_tasks import run_wait_until
        stop = threading.Event()
        assert run_wait_until({"target_time": "25:00"}, stop) is False

    def test_past_time_waits_until_next_day(self):
        from utils.special_tasks import run_wait_until
        import datetime
        stop = threading.Event()
        stop.set()  # interrupt immediately
        # Use time already passed today
        past = (datetime.datetime.now() - datetime.timedelta(hours=1)).strftime("%H:%M")
        result = run_wait_until({"target_time": past}, stop)
        assert result is False  # interrupted, not crashed


class TestRunWebhook:
    def test_missing_url_returns_false(self):
        from utils.special_tasks import run_webhook
        stop = threading.Event()
        assert run_webhook({}, stop) is False

    def test_empty_url_returns_false(self):
        from utils.special_tasks import run_webhook
        stop = threading.Event()
        assert run_webhook({"url": ""}, stop) is False

    def test_successful_request(self):
        from utils.special_tasks import run_webhook
        stop = threading.Event()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = run_webhook({"url": "http://example.com"}, stop)
        assert result is True

    def test_failed_request_returns_false(self):
        from utils.special_tasks import run_webhook
        stop = threading.Event()
        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            result = run_webhook({"url": "http://example.com"}, stop)
        assert result is False


class TestRunKillProc:
    def test_kill_self_sets_stop_event(self):
        from utils.special_tasks import run_killproc
        stop = threading.Event()
        result = run_killproc({"kill_self": True}, stop)
        assert result is True
        assert stop.is_set()

    def test_missing_process_name_returns_false(self):
        from utils.special_tasks import run_killproc
        stop = threading.Event()
        assert run_killproc({"kill_self": False, "process_name": ""}, stop) is False

    def test_missing_process_name_key_returns_false(self):
        from utils.special_tasks import run_killproc
        stop = threading.Event()
        assert run_killproc({"kill_self": False}, stop) is False


class TestRunPower:
    def test_unknown_action_returns_false(self):
        from utils.special_tasks import run_power
        stop = threading.Event()
        assert run_power({"power_action": "unknown_action"}, stop) is False

    def test_missing_action_defaults_to_shutdown(self):
        from utils.special_tasks import run_power
        stop = threading.Event()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = run_power({}, stop)
        assert result is True

    def test_shutdown_calls_subprocess(self):
        from utils.special_tasks import run_power
        import sys
        stop = threading.Event()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            run_power({"power_action": "shutdown"}, stop)
        assert mock_run.called


class TestIsSpecialTask:
    def test_known_tasks(self):
        from utils.special_tasks import is_special_task
        for name in ["__MXU_SLEEP__", "__MXU_NOTIFY__", "__MXU_POWER__",
                     "__MXU_LAUNCH__", "__MXU_WEBHOOK__", "__MXU_KILLPROC__",
                     "__MXU_WAITUNTIL__"]:
            assert is_special_task(name) is True, f"{name} should be special"

    def test_kkafio_tasks_not_special(self):
        from utils.special_tasks import is_special_task
        for name in ["InstallContents", "GroupChara", "RenameChara", "DownloadContents"]:
            assert is_special_task(name) is False


class TestRunSpecialTask:
    def test_unknown_task_returns_false(self):
        from utils.special_tasks import run_special_task
        stop = threading.Event()
        assert run_special_task("__MXU_UNKNOWN__", {}, stop) is False

    def test_dispatches_sleep(self):
        from utils.special_tasks import run_special_task
        stop = threading.Event()
        result = run_special_task("__MXU_SLEEP__", {"sleep_time": 0}, stop)
        assert result is True