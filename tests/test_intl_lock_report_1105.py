import importlib.util
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "workbuddy_intl_daily.py"
SPEC = importlib.util.spec_from_file_location("workbuddy_intl_lock_report_test", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Response:
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self.payload = payload
        self.text = text

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, post_response=None, get_response=None, post_error=None, get_error=None):
        self.trust_env = True
        self.post_response = post_response
        self.get_response = get_response
        self.post_error = post_error
        self.get_error = get_error
        self.post_calls = []
        self.get_calls = []

    def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        if self.post_error:
            raise self.post_error
        return self.post_response

    def get(self, *args, **kwargs):
        self.get_calls.append((args, kwargs))
        if self.get_error:
            raise self.get_error
        return self.get_response


WORKER = r'''
import importlib.util
from pathlib import Path
import sys
import time

source, directory, name = sys.argv[1:]
root = Path(directory)
spec = importlib.util.spec_from_file_location("intl_worker_" + name, source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.TOKEN_STORE = str(root / "wb_intl_tokens.json")
module.TOKEN_FILE = str(root / "WORKBUDDY_INTL_ACCESS_TOKEN.txt")

def refresh(at, rt):
    (root / (name + ".entered")).touch()
    if name == "first":
        deadline = time.monotonic() + 10
        while not (root / "release").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("release timeout")
            time.sleep(.01)
    return name + "-new-at", name + "-new-rt"

module.refresh_token = refresh
account = {
    "note": name,
    "uid": name,
    "token": name + "-old-at",
    "refresh_token": name + "-old-rt",
}
(root / (name + ".ready")).touch()
if not module._refresh_account_once(account, lambda message: None):
    raise RuntimeError("refresh failed")
'''


class InternationalLockAndReportTests(unittest.TestCase):
    def _paths(self, root):
        return (
            mock.patch.object(MODULE, "TOKEN_STORE", str(root / "wb_intl_tokens.json")),
            mock.patch.object(MODULE, "TOKEN_FILE", str(root / "WORKBUDDY_INTL_ACCESS_TOKEN.txt")),
        )

    def test_same_account_threads_use_one_rotation_and_reload_latest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store_path = root / "wb_intl_tokens.json"
            store_path.write_text(json.dumps({
                "user": {
                    "access_token": "old-at",
                    "refresh_token": "old-rt",
                    "alias": "keep-me",
                }
            }), encoding="utf-8")
            calls = []
            calls_lock = threading.Lock()

            def refresh(at, rt):
                with calls_lock:
                    calls.append((at, rt))
                time.sleep(0.05)
                return "new-at", "new-rt"

            accounts = [
                {"note": "user", "uid": "user", "token": "old-at", "refresh_token": "old-rt"},
                {"note": "user", "uid": "user", "token": "old-at", "refresh_token": "old-rt"},
            ]
            store_patch, token_patch = self._paths(root)
            with store_patch, token_patch, mock.patch.object(MODULE, "refresh_token", side_effect=refresh):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(
                        lambda account: MODULE._refresh_account_once(account, lambda message: None),
                        accounts,
                    ))

            self.assertEqual([True, True], results)
            self.assertEqual([("old-at", "old-rt")], calls)
            saved = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual("new-at", saved["user"]["access_token"])
            self.assertEqual("new-rt", saved["user"]["refresh_token"])
            self.assertEqual("keep-me", saved["user"]["alias"])
            self.assertEqual(
                "new-at",
                (root / "WORKBUDDY_INTL_ACCESS_TOKEN.txt").read_text(encoding="utf-8"),
            )

    def test_external_rotation_during_failed_refresh_is_not_reported_as_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store_path = root / "wb_intl_tokens.json"
            store_path.write_text(json.dumps({
                "user": {"access_token": "old-at", "refresh_token": "old-rt"}
            }), encoding="utf-8")
            logs = []

            def refresh(at, rt):
                store_path.write_text(json.dumps({
                    "user": {
                        "access_token": "external-at",
                        "refresh_token": "external-rt",
                        "winner": "other-executor",
                    }
                }), encoding="utf-8")
                return None, "HTTP 401"

            account = {
                "note": "user",
                "uid": "user",
                "token": "old-at",
                "refresh_token": "old-rt",
            }
            store_patch, token_patch = self._paths(root)
            with store_patch, token_patch, mock.patch.object(MODULE, "refresh_token", side_effect=refresh):
                self.assertTrue(MODULE._refresh_account_once(account, logs.append))

            self.assertEqual(("external-at", "external-rt"), (account["token"], account["refresh_token"]))
            self.assertTrue(any("其他执行者" in message for message in logs))
            self.assertFalse(any("续期失败" in message for message in logs))
            saved = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual("other-executor", saved["user"]["winner"])

    def test_external_rotation_wins_cas_after_successful_refresh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store_path = root / "wb_intl_tokens.json"
            store_path.write_text(json.dumps({
                "user": {"access_token": "old-at", "refresh_token": "old-rt"}
            }), encoding="utf-8")

            def refresh(at, rt):
                store_path.write_text(json.dumps({
                    "user": {
                        "access_token": "external-at",
                        "refresh_token": "external-rt",
                    }
                }), encoding="utf-8")
                return "stale-at", "stale-rt"

            account = {
                "note": "user",
                "uid": "user",
                "token": "old-at",
                "refresh_token": "old-rt",
            }
            store_patch, token_patch = self._paths(root)
            with store_patch, token_patch, mock.patch.object(MODULE, "refresh_token", side_effect=refresh):
                self.assertTrue(MODULE._refresh_account_once(account, lambda message: None))

            saved = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual("external-at", saved["user"]["access_token"])
            self.assertEqual("external-rt", saved["user"]["refresh_token"])
            self.assertEqual(("external-at", "external-rt"), (account["token"], account["refresh_token"]))

    def test_store_and_at_file_are_replaced_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = {
                "first": {"access_token": "first-at", "refresh_token": "first-rt", "extra": 7},
                "second": {"access_token": "second-at", "refresh_token": "second-rt"},
            }
            store_patch, token_patch = self._paths(root)
            with store_patch, token_patch, mock.patch.object(
                    MODULE.os, "replace", wraps=os.replace) as replace:
                MODULE.save_store(store)

            self.assertEqual(2, replace.call_count)
            self.assertEqual(store, json.loads((root / "wb_intl_tokens.json").read_text(encoding="utf-8")))
            self.assertEqual(
                "first-at@second-at",
                (root / "WORKBUDDY_INTL_ACCESS_TOKEN.txt").read_text(encoding="utf-8"),
            )
            self.assertEqual([], list(root.glob("*.tmp-*")))

    def test_separate_processes_serialize_refresh_and_preserve_both_accounts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store_path = root / "wb_intl_tokens.json"
            store_path.write_text(json.dumps({
                name: {
                    "access_token": name + "-old-at",
                    "refresh_token": name + "-old-rt",
                    "extra": name,
                }
                for name in ("first", "second")
            }), encoding="utf-8")

            def start(name):
                return subprocess.Popen(
                    [sys.executable, "-c", WORKER, str(SOURCE), temp_dir, name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )

            first = start("first")
            second = None
            try:
                deadline = time.monotonic() + 10
                while not (root / "first.entered").exists() and time.monotonic() < deadline:
                    if first.poll() is not None:
                        self.fail("first process failed: " + first.communicate()[1])
                    time.sleep(0.01)
                self.assertTrue((root / "first.entered").exists())

                second = start("second")
                deadline = time.monotonic() + 10
                while not (root / "second.ready").exists() and time.monotonic() < deadline:
                    if second.poll() is not None:
                        self.fail("second process failed: " + second.communicate()[1])
                    time.sleep(0.01)
                self.assertTrue((root / "second.ready").exists())
                time.sleep(0.2)
                self.assertIsNone(second.poll())
                self.assertFalse((root / "second.entered").exists())

                (root / "release").touch()
                for process in (first, second):
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(0, process.returncode, stdout + stderr)
                self.assertTrue((root / "second.entered").exists())

                saved = json.loads(store_path.read_text(encoding="utf-8"))
                self.assertEqual("first-new-rt", saved["first"]["refresh_token"])
                self.assertEqual("second-new-rt", saved["second"]["refresh_token"])
                self.assertEqual("first", saved["first"]["extra"])
                self.assertEqual("second", saved["second"]["extra"])
            finally:
                (root / "release").touch()
                for process in (first, second):
                    if process and process.poll() is None:
                        process.kill()
                        process.communicate()

    def test_hydrated_old_at_preflight_401_refreshes_once_and_repreflights(self):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {
            "WORKBUDDY_INTL_TOKENS": "alias|old-at|uid|old-rt",
            "NO_NOTIFY": "1",
        }, clear=False), mock.patch.object(sys, "argv", ["workbuddy_intl_daily.py", "--report-only"]), \
                mock.patch.object(MODULE, "_hydrate_account_from_store", return_value=True), \
                mock.patch.object(MODULE, "preflight_restricted", side_effect=[
                    (Response(401, {"code": 401}), {"code": 401}),
                    (Response(200, {"code": 0}), {"code": 0}),
                ]) as preflight, \
                mock.patch.object(MODULE, "_refresh_account_once", side_effect=self._refresh_to_new_at) as refresh, \
                mock.patch.object(MODULE, "report_activity", return_value=(True, "active")) as report, \
                mock.patch.object(MODULE, "notify") as notify, \
                mock.patch.object(MODULE.time, "sleep"), redirect_stdout(output):
            result = MODULE.main()

        self.assertEqual(0, result)
        self.assertEqual(2, preflight.call_count)
        self.assertEqual("old-at", preflight.call_args_list[0].args[0])
        self.assertEqual("new-at", preflight.call_args_list[1].args[0])
        refresh.assert_called_once()
        report.assert_called_once_with("new-at", "uid")
        notify.assert_not_called()

    @staticmethod
    def _refresh_to_new_at(account, log_fn):
        account["token"] = "new-at"
        account["refresh_token"] = "new-rt"
        return True

    def test_401_after_refresh_blocks_report_with_stale_at(self):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {
            "WORKBUDDY_INTL_TOKENS": "alias|old-at|uid|old-rt",
            "NO_NOTIFY": "1",
        }, clear=False), mock.patch.object(sys, "argv", ["workbuddy_intl_daily.py", "--report-only"]), \
                mock.patch.object(MODULE, "_hydrate_account_from_store", return_value=True), \
                mock.patch.object(MODULE, "preflight_restricted", side_effect=[
                    (Response(401, {"code": 401}), {"code": 401}),
                    (Response(401, {"code": 401}), {"code": 401}),
                ]), \
                mock.patch.object(MODULE, "_refresh_account_once", side_effect=self._refresh_to_new_at) as refresh, \
                mock.patch.object(MODULE, "report_activity") as report, \
                mock.patch.object(MODULE, "notify") as notify, \
                mock.patch.object(MODULE.time, "sleep"), redirect_stdout(output):
            result = MODULE.main()

        self.assertEqual(0, result)
        refresh.assert_called_once()
        report.assert_not_called()
        notify.assert_not_called()
        self.assertIn("续期成功但预检仍返回 HTTP 401", output.getvalue())

    def test_empty_or_missing_report_code_is_not_success(self):
        for payload in ({}, {"data": {}}, {"code": None}):
            with self.subTest(payload=payload):
                session = FakeSession(post_response=Response(200, payload))
                with mock.patch.object(MODULE.requests, "Session", return_value=session):
                    ok, note = MODULE.report_activity("token", "uid")
                self.assertFalse(ok)
                self.assertIn("上报未确认", note)
                self.assertEqual([], session.get_calls)

    def test_streak_readback_failure_is_not_success(self):
        post_response = Response(200, {"code": 0})
        failures = [
            Response(500, {"code": 500}),
            Response(200, {}),
            Response(200, {"code": 0, "data": {}}),
        ]
        for streak_response in failures:
            with self.subTest(streak_response=streak_response.payload):
                report_session = FakeSession(post_response=post_response)
                streak_session = FakeSession(get_response=streak_response)
                with mock.patch.object(
                        MODULE.requests, "Session", side_effect=[report_session, streak_session]):
                    ok, note = MODULE.report_activity("token", "uid")
                self.assertFalse(ok)
                self.assertIn("上报请求已接受但回读失败", note)
                self.assertEqual(1, len(report_session.post_calls))
                self.assertEqual(1, len(streak_session.get_calls))

    def test_report_activity_requires_explicit_success_and_readback_days(self):
        report_session = FakeSession(post_response=Response(200, {"code": 0}))
        streak_session = FakeSession(get_response=Response(
            200, {"code": 0, "data": {"streak": {"days": 4}}}))
        with mock.patch.object(
                MODULE.requests, "Session", side_effect=[report_session, streak_session]):
            ok, note = MODULE.report_activity("token", "uid")
        self.assertTrue(ok)
        self.assertIn("连登 4 天", note)

    def test_report_only_reports_activity_without_detect_claim_or_keepalive(self):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {
            "WORKBUDDY_INTL_TOKENS": "alias|token|uid|refresh",
            "NO_NOTIFY": "1",
        }, clear=False), mock.patch.object(sys, "argv", ["workbuddy_intl_daily.py", "--report-only"]), \
                mock.patch.object(MODULE, "_hydrate_account_from_store", return_value=False), \
                mock.patch.object(MODULE, "_prepare_account_for_run", return_value=None), \
                mock.patch.object(MODULE, "report_activity", return_value=(True, "active")) as report, \
                mock.patch.object(MODULE, "detect_activity") as detect, \
                mock.patch.object(MODULE, "claim_daily") as claim, \
                mock.patch.object(MODULE, "keepalive") as keepalive, \
                mock.patch.object(MODULE, "web_conversation") as web, \
                mock.patch.object(MODULE, "notify") as notify, \
                mock.patch.object(MODULE.time, "sleep"), redirect_stdout(output):
            result = MODULE.main()

        self.assertEqual(0, result)
        report.assert_called_once_with("token", "uid")
        detect.assert_not_called()
        claim.assert_not_called()
        keepalive.assert_not_called()
        web.assert_not_called()
        notify.assert_not_called()
        self.assertIn("--report-only", MODULE.HELP_TEXT)
        self.assertIn("/v2/report", output.getvalue())
        self.assertIn("不领取任务/奖励", output.getvalue())

    def test_help_documents_report_only_without_loading_accounts(self):
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["workbuddy_intl_daily.py", "--help"]), \
                mock.patch.object(MODULE, "parse_accounts") as parse_accounts, redirect_stdout(output):
            result = MODULE.main()

        self.assertEqual(0, result)
        parse_accounts.assert_not_called()
        self.assertIn("--report-only", output.getvalue())
        self.assertIn("不探测或领取每日奖励", output.getvalue())


if __name__ == "__main__":
    unittest.main()
