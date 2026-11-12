import ast
import base64
import contextlib
import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import errno
import time
import types
import unittest
from unittest import mock
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests


SOURCE_PATH = Path(__file__).resolve().parents[1] / "workbuddy_daily.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
FUNCTIONS = {
    node.name: node
    for node in TREE.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}


def assignment_value(name):
    for node in TREE.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name
               for target in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


def load_functions(*names, **globals_):
    namespace = {"__builtins__": __builtins__, **globals_}
    if set(names) & {"interpret_failure", "run_account"}:
        names = ("is_unapproved_channel", *names)
    if set(names) & {"_bootstrap_store", "refresh_all", "auto_refresh", "_refresh_account_once", "load_accounts"}:
        names = ("_token_store_lock", "_write_refresh_recovery", "_save_refresh_store", "_load_refresh_store", *names)
        namespace.update(contextmanager=contextlib.contextmanager, threading=threading,
                         errno=errno, _TOKEN_STORE_THREAD_LOCK=threading.Lock(), uuid=uuid)
    if "_rebuild_token_file" in names and "_jwt_claims" not in names:
        names = ("_jwt_claims", *names)
    body = [FUNCTIONS[name] for name in names]
    module = ast.Module(body=body, type_ignores=[])
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {}

    def json(self):
        return self.payload


class FakeStreamResponse(FakeResponse):
    def __init__(self, status_code=200, payload=None, lines=None):
        super().__init__(status_code, payload)
        self.lines = list(lines or [])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_lines(self, decode_unicode=False):
        yield from self.lines


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)


def jwt_token(payload):
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return "eyJ." + encoded + ".sig"


class CoreBehaviorTests(unittest.TestCase):
    def test_token_sanity_recognizes_domestic_issuers_without_trusting_lookalikes(self):
        namespace = load_functions("_jwt_payload_local", "_token_sanity",
                                   json=json, base64=base64)
        def token(issuer, kind):
            payload = base64.urlsafe_b64encode(json.dumps(
                {"iss": issuer, "typ": kind}, separators=(",", ":")
            ).encode()).decode().rstrip("=")
            return "eyJ." + payload + ".signature"
        sanity = namespace["_token_sanity"]
        for issuer in ("https://www.codebuddy.cn/auth/realms/copilot",
                       "https://www.workbuddy.cn/auth/realms/copilot",
                       "https://copilot.tencent.com/auth/realms/copilot"):
            with self.subTest(issuer=issuer):
                self.assertEqual([], sanity(token(issuer, "Offline"), token(issuer, "Bearer")))
        for issuer in ("https://codebuddy.cn.evil.example/auth/realms/copilot",
                       "https://www.workbuddy.cn.evil.example/auth/realms/copilot",
                       "https://copilot.tencent.com.evil.example/auth/realms/copilot",
                       "https://www.codebuddy.ai/auth/realms/copilot"):
            with self.subTest(issuer=issuer):
                self.assertTrue(any("签发域" in tip for tip in
                                    sanity(token(issuer, "Offline"), "")))
        self.assertTrue(any("typ=Bearer" in tip for tip in
                            sanity(token("https://www.workbuddy.cn/auth/realms/copilot", "Bearer"), "")))

    def test_claim_ready_prefers_claimable_rows_and_correct_platform(self):
        desktop_tasks = {
            "code": 0,
            "data": {"tasks": [
                {"task_code": "desktop_ready", "accept_status": "accepted",
                 "progress": {"current": 1, "target": 1}},
                {"task_code": "fallback_done", "accept_status": "completed",
                 "progress": {}},
                {"task_code": "already_done", "accept_status": "claimed",
                 "claimed": True, "progress": {"current": 1, "target": 1}},
            ]},
        }
        mp_tasks = {
            "code": 0,
            "data": {"tasks": [
                {"task_code": "desktop_ready", "accept_status": "accepted",
                 "progress": {"current": 0, "target": 1}},
                {"task_code": "mp_ready", "accept_status": "accepted",
                 "progress": {"current": 2, "target": 2}},
            ]},
        }

        class Session:
            def __init__(self):
                self.posts = []

            def get(self, url, **kwargs):
                headers = kwargs.get("headers")
                return FakeResponse(payload=mp_tasks if headers else desktop_tasks)

            def post(self, url, **kwargs):
                self.posts.append((url, kwargs.get("headers")))
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"credit": 10, "energy": 1},
                })

        sleep_calls = []
        module = load_functions(
            "_api_code_ok", "_claim_response_ok", "claim", "_mp_claim",
            "claim_ready_tasks",
            _as_int=lambda value, default=0: int(value or default),
            BASE="https://example.test",
            MP_HEADER={"X-Client-Platform": "miniprogram"},
            MP_TASK_CODES=set(),
            DESKTOP_ONLY_TASK_CODES=set(),
            time=type("Time", (), {"sleep": staticmethod(sleep_calls.append)}),
        )
        session = Session()
        logs = []
        module["claim_ready_tasks"](session, logs.append)

        claimed_urls = [url.rsplit("/", 2)[-2] for url, _ in session.posts]
        self.assertEqual({"desktop_ready", "fallback_done", "mp_ready"}, set(claimed_urls))
        self.assertEqual(3, len(session.posts))
        self.assertEqual(
            ["desktop_ready", "fallback_done", "mp_ready"],
            sorted(claimed_urls),
        )
        self.assertIn("达标补领完成", " ".join(logs))
        empty_ok, _, _ = module["_claim_response_ok"](
            FakeResponse(payload={}), {})
        self.assertFalse(empty_ok)

    def test_sign_checks_status_before_claim_and_does_not_fake_empty_success(self):
        calls = []

        def api_retry(session, method, url, **kwargs):
            calls.append(url)
            if url.endswith("checkin-activity-status"):
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"active": True, "today_checked_in": False},
                })
            return FakeResponse(payload={"code": 0, "data": {}})

        module = load_functions(
            "_api_code_ok", "t_sign",
            api_retry=api_retry,
            BASE="https://example.test",
            is_enterprise_denial=lambda status, payload: False,
            _checkin_status=lambda session: {"today_checked_in": False},
            _checkin_tail=lambda session: "（签到连签2天）",
            t_daily_activity=lambda *args: calls.append("daily-activity"),
            _sign_retry_record=lambda uid, now=None: (None, False),
            _is_retryable_checkin_failure=lambda status, payload: False,
            time=time,
        )
        logs = []
        module["t_sign"](object(), "u1", "nick", logs.append)
        self.assertEqual(1, sum("checkin-activity-status" in c for c in calls))
        self.assertIn("daily-checkin", " ".join(calls))
        self.assertIn("签到未确认", " ".join(logs))
        self.assertNotIn("签到成功", " ".join(logs))

    def test_sign_credit_delayed_is_success_not_missing_credit(self):
        calls = []

        def api_retry(session, method, url, **kwargs):
            calls.append(url)
            if url.endswith("checkin-activity-status"):
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"active": True, "today_checked_in": False},
                })
            return FakeResponse(payload={
                "code": 0,
                "data": {"credit_delayed": True, "is_streak_day": True},
            })

        module = load_functions(
            "_api_code_ok", "t_sign",
            api_retry=api_retry,
            BASE="https://example.test",
            is_enterprise_denial=lambda status, payload: False,
            _checkin_status=lambda session: {"today_checked_in": True},
            _checkin_tail=lambda session: "（签到连签3天）",
            t_daily_activity=lambda *args: calls.append("daily-activity"),
            _sign_retry_record=lambda uid, now=None: (None, False),
            _is_retryable_checkin_failure=lambda status, payload: False,
            time=time,
        )
        logs = []
        state = {}
        module["t_sign"](object(), "u1", "nick", logs.append, state)
        text = " ".join(logs)
        self.assertIn("签到成功", text)
        self.assertIn("积分待入账", text)
        self.assertNotIn("回执缺少 credit", text)
        self.assertTrue(state.get("checked_in"))

    def test_sign_10001_requires_fresh_checked_in_state(self):
        fresh_states = [{}, {"today_checked_in": True}]

        def api_retry(session, method, url, **kwargs):
            if url.endswith("checkin-activity-status"):
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"active": True, "today_checked_in": False},
                })
            return FakeResponse(payload={
                "code": 10001,
                "msg": "operation rejected",
            })

        module = load_functions(
            "_api_code_ok", "t_sign",
            api_retry=api_retry,
            BASE="https://example.test",
            is_enterprise_denial=lambda status, payload: False,
            _checkin_status=lambda session: fresh_states.pop(0),
            _checkin_tail=lambda session: "",
            t_daily_activity=lambda *args: None,
            _sign_retry_record=lambda uid, now=None: (None, False),
            _is_retryable_checkin_failure=lambda status, payload: False,
            time=time,
        )
        first_logs = []
        module["t_sign"](object(), "u1", "nick", first_logs.append)
        self.assertIn("未确认", " ".join(first_logs))

        second_logs = []
        module["t_sign"](object(), "u1", "nick", second_logs.append)
        self.assertIn("状态回读确认今日已签", " ".join(second_logs))

    def test_accept_verify_requires_positive_state_readback(self):
        class Session:
            def __init__(self):
                self.posts = 0

            def post(self, url, **kwargs):
                self.posts += 1
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"results": [{"status": "accepted"}]},
                })

        # 每轮 post 后读两次：第 1 次调用 4 读全空 → 失败；第 2 次调用末读落账 → 成功
        states = [
            (None, None, None), (None, None, None),
            (None, None, None), (None, None, None),
            (None, None, None), (None, None, None),
            (None, None, None), ("accepted", 0, 1),
        ]
        module = load_functions(
            "_accept_with_verify",
            BASE="https://example.test",
            WRITE_GAP=0,
            ACCEPT_SETTLE_GAP=0,
            prog=lambda session, code: states.pop(0),
            time=type("Time", (), {"sleep": staticmethod(lambda seconds: None)}),
        )
        session = Session()
        logs = []
        self.assertFalse(module["_accept_with_verify"](session, "new_task", logs.append))
        self.assertTrue(module["_accept_with_verify"](session, "new_task", logs.append))
        self.assertEqual(4, session.posts)

    def test_accept_verify_treats_already_accepted_as_success(self):
        """上游对已登记任务回 status=already_accepted，不能当成失败。"""

        class Session:
            def post(self, url, **kwargs):
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"results": [{"task_code": "skill_share",
                                          "status": "already_accepted"}]},
                })

        module = load_functions(
            "_accept_with_verify",
            BASE="https://example.test",
            WRITE_GAP=0,
            ACCEPT_SETTLE_GAP=0,
            prog=lambda session, code: ("accepted", 0, 1),
            time=type("Time", (), {"sleep": staticmethod(lambda seconds: None)}),
        )
        logs = []
        self.assertTrue(module["_accept_with_verify"](Session(), "skill_share", logs.append))
        self.assertEqual([], logs)

    def test_accept_all_survives_async_settle(self):
        """批量 accept 回 accepted，但回读要等几秒才翻状态——不能判成未落账。"""

        class Session:
            def get(self, url, **kwargs):
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"tasks": [{"task_code": "skill_share",
                                        "accept_status": "not_accepted"}]},
                })

            def post(self, url, **kwargs):
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"results": [{"task_code": "skill_share",
                                          "status": "accepted"}]},
                })

        reads = iter([(None, None, None), ("not_accepted", None, None),
                      ("accepted", 0, 1)])
        module = load_functions(
            "t_accept_all",
            BASE="https://example.test",
            ACCEPT_BATCH_SETTLE=0,
            ACCEPT_BATCH_TRIES=3,
            time=type("Time", (), {"sleep": staticmethod(lambda seconds: None)}),
            prog=lambda session, code: next(reads),
            ensure_first_buddy=lambda session, uid, nick, log: True,
            _accept_with_verify=lambda session, code, log: False,
        )
        logs = []
        module["t_accept_all"](Session(), "u1", "nick", logs.append)
        text = " ".join(logs)
        self.assertNotIn("未落账", text)
        self.assertIn("全部登记生效", text)

    def test_accept_all_repairs_prerequisite_then_confirms_state(self):
        class Session:
            def get(self, url, **kwargs):
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"tasks": [{
                        "task_code": "dependent_task",
                        "accept_status": "not_accepted",
                    }]},
                })

            def post(self, url, **kwargs):
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"results": [{
                        "task_code": "dependent_task",
                        "status": "error",
                        "message": "prerequisite not met: first_buddy (missing)",
                    }]},
                })

        module = load_functions(
            "t_accept_all",
            BASE="https://example.test",
            ACCEPT_BATCH_SETTLE=0,
            ACCEPT_BATCH_TRIES=3,
            time=type("Time", (), {"sleep": staticmethod(lambda seconds: None)}),
            prog=lambda session, code: ("accepted", 0, 1),
            ensure_first_buddy=lambda session, uid, nick, log: True,
            _accept_with_verify=lambda session, code, log: True,
        )
        logs = []
        module["t_accept_all"](Session(), "u1", "nick", logs.append)
        text = " ".join(logs)
        self.assertIn("待前置「first_buddy」满足", text)
        self.assertIn("Buddy 已就绪", text)
        self.assertIn("全部登记生效", text)

    def test_unknown_tasks_discovers_mp_rows_when_desktop_fails(self):
        class Session:
            def get(self, url, **kwargs):
                if kwargs.get("headers"):
                    return FakeResponse(payload={
                        "code": 0,
                        "data": {"tasks": [{
                            "task_code": "mp_new_task",
                            "title": "小程序新任务",
                            "task_desc": "完成一次动作",
                            "accept_status": "not_accepted",
                        }]},
                    })
                if url.endswith("/activity/growth/tasks") and "/v2/" not in url:
                    return FakeResponse(payload={
                        "code": 0,
                        "data": {"tasks": [
                            {"code": "first_chat", "status": "not_accepted"},
                            {"code": "wechat_linked", "status": "not_accepted"},
                        ]},
                    })
                raise RuntimeError("desktop unavailable")

        module = load_functions(
            "t_unknown_tasks",
            BASE="https://example.test",
            MP_HEADER={"X-Client-Platform": "miniprogram"},
            LEVEL_TASK_CODES={
                "first_chat", "skill_installed", "wechat_linked",
                "expert_summoned", "template_used",
            },
            beijing_today=lambda: __import__("datetime").date(2026, 10, 23),
        )
        logs = []
        module["t_unknown_tasks"](Session(), "u1", "nick", logs.append)
        text = " ".join(logs)
        self.assertIn("任务发现部分口径失败", text)
        self.assertIn("mp_new_task", text)
        self.assertIn("未覆盖新任务", text)
        self.assertIn("等级任务(只发现): first_chat", text)
        self.assertIn("等级任务需客户端: wechat_linked", text)

    def test_sequential_tasks_7_uses_pc_flow_and_web_claim(self):
        calls = []
        module = load_functions(
            "t_sequential_tasks_7",
            _growth_task_row=lambda session, code: {
                "task_code": code,
                "accept_status": "accepted",
                "progress": {"current": 0, "target": 1},
                "tag": "PC 限定",
                "jump_url": "workbuddy://playbook",
            },
            _as_int=lambda value: int(value or 0),
            _accept_with_verify=lambda session, code, log: True,
            desktop_playbook_events=lambda uid, nick: [{
                "eventCode": "playbook_prompt_send",
                "conversationId": "conv-pc",
            }],
            report_desktop_events=lambda session, uid, nick, events:
                calls.append(("desktop", events)) or 200,
            time=type("Time", (), {
                "sleep": staticmethod(lambda seconds: None),
            }),
            prog=lambda session, code: ("completed", 1, 1),
            claim=lambda session, code, log:
                calls.append(("claim", code)) or True,
        )
        logs = []
        module["t_sequential_tasks_7"](
            object(), "uid", "nick", logs.append)
        self.assertEqual("desktop", calls[0][0])
        self.assertEqual(
            "playbook_prompt_send", calls[0][1][0]["eventCode"])
        self.assertEqual(("claim", "Sequential_Tasks_7"), calls[1])
        self.assertNotIn(
            "Sequential_Tasks_7", assignment_value("MP_TASK_CODES"))
        self.assertIn("PC灵感功能", " ".join(logs))

    def test_sequential_tasks_7_does_not_claim_when_progress_stays_zero(self):
        claimed = []
        module = load_functions(
            "t_sequential_tasks_7",
            _growth_task_row=lambda session, code: {
                "task_code": code,
                "accept_status": "accepted",
                "progress": {"current": 0, "target": 1},
            },
            _as_int=lambda value: int(value or 0),
            _accept_with_verify=lambda session, code, log: True,
            desktop_playbook_events=lambda uid, nick: [],
            report_desktop_events=lambda *args: 200,
            time=type("Time", (), {
                "sleep": staticmethod(lambda seconds: None),
            }),
            prog=lambda session, code: ("accepted", 0, 1),
            claim=lambda session, code, log:
                claimed.append(code) or True,
        )
        logs = []
        module["t_sequential_tasks_7"](
            object(), "uid", "nick", logs.append)
        self.assertEqual([], claimed)
        self.assertIn("进度未点亮", " ".join(logs))

    def test_mp_task_target_zero_waits_without_fake_progress(self):
        reported = []
        claimed = []
        module = load_functions(
            "_mp_do_task", "_wait_mp_target",
            _mp_task_row=lambda session, code: {
                "task_code": code,
                "accept_status": "accepted",
                "progress": {"current": 0, "target": 0},
            },
            _mp_prog=lambda session, code: ("accepted", 0, 0),
            _mp_claim=lambda session, code, log:
                claimed.append(code) or True,
            mp_report=lambda session, uid, nick, events:
                reported.append(events) or 200,
            time=type("Time", (), {
                "sleep": staticmethod(lambda seconds: None),
            }),
            MP_TARGET_WAIT_ROUNDS=2,
            MP_TARGET_WAIT_SECONDS=0,
            WRITE_GAP=0,
            MP_CHAT_GAP=0,
        )
        logs = []
        module["_mp_do_task"](
            object(), "uid", "nick", "Sequential_Tasks_6", logs.append,
            lambda index: [{"eventCode": "chat_request_send"}],
            "小程序对话×10", target=10,
        )
        self.assertEqual([], reported)
        self.assertEqual([], claimed)
        self.assertIn("不按 1 冒领", " ".join(logs))

    def test_401_refresh_preserves_other_accounts_and_stable_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tokens.json"
            original = {"first": {"access_token": "old-at", "refresh_token": "old-rt", "extra": "keep"},
                        "second": {"access_token": "other-at", "refresh_token": "other-rt"}}
            path.write_text(json.dumps(original), encoding="utf-8")
            calls = []
            module = load_functions("_refresh_account_once", os=os, json=json, uuid=uuid, time=time,
                REFRESH_STORE=str(path), refresh_one=lambda rt, at: calls.append((rt, at)) or ("new-at", "new-rt"),
                _rebuild_token_file=lambda store: None)
            account = {"note": "first", "access_token": "old-at", "refresh_token": "old-rt"}
            self.assertTrue(module["_refresh_account_once"](account, lambda message: None))
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(original["second"], saved["second"])
            self.assertEqual("keep", saved["first"]["extra"])
            self.assertEqual("new-rt", saved["first"]["refresh_token"])
            self.assertEqual("first", account["note"])
            self.assertEqual([("old-rt", "old-at")], calls)

    def test_401_refresh_rejects_unreadable_or_changed_pool_before_network(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tokens.json"
            refresh = mock.Mock()
            module = load_functions("_refresh_account_once", os=os, json=json, uuid=uuid, time=time,
                REFRESH_STORE=str(path), refresh_one=refresh, _rebuild_token_file=mock.Mock())
            account = {"note": "first", "refresh_token": "old-rt"}
            for contents in (None, "{", "[]", json.dumps({"first": {"refresh_token": "different"}})):
                with self.subTest(contents=contents):
                    if contents is not None:
                        path.write_text(contents, encoding="utf-8")
                    self.assertFalse(module["_refresh_account_once"](account, lambda message: None))
                    if contents is not None:
                        self.assertEqual(contents, path.read_text(encoding="utf-8"))
            refresh.assert_not_called()

    def test_401_refresh_write_failure_keeps_original_and_recovery_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tokens.json"
            original = json.dumps({"first": {"refresh_token": "old-rt"}, "second": {"refresh_token": "other-rt"}})
            path.write_text(original, encoding="utf-8")
            module = load_functions("_refresh_account_once", os=os, json=json, uuid=uuid, time=time,
                REFRESH_STORE=str(path), refresh_one=lambda rt, at: ("new-at", "new-rt"),
                _rebuild_token_file=mock.Mock())
            with mock.patch.object(os, "replace", side_effect=OSError("failed")):
                with self.assertRaises(RuntimeError):
                    module["_refresh_account_once"]({"note": "first", "refresh_token": "old-rt"}, lambda message: None)
            self.assertEqual(original, path.read_text(encoding="utf-8"))
            copies = list(Path(temp_dir).glob("tokens.json.tmp-*"))
            self.assertEqual(1, len(copies))
            self.assertEqual("new-rt", json.loads(copies[0].read_text(encoding="utf-8"))["first"]["refresh_token"])

    def test_401_concurrent_refresh_preserves_both_accounts_and_token_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tokens.json"
            token_file = Path(temp_dir) / "access.txt"
            path.write_text(json.dumps({name: {"access_token": name + "-old-at", "refresh_token": name + "-old-rt"}
                                        for name in ("first", "second")}), encoding="utf-8")
            entered, release = threading.Event(), threading.Event()
            calls = []
            def refresh(rt, at):
                calls.append(rt)
                if rt == "first-old-rt":
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("test timed out")
                return rt.replace("old-rt", "new-at"), rt.replace("old-rt", "new-rt")
            module = load_functions("_refresh_account_once", "_rebuild_token_file", os=os, json=json, time=time,
                REFRESH_STORE=str(path), TOKEN_FILE=str(token_file), refresh_one=refresh,
                jwt_user=lambda token: token.split("-")[0])
            accounts = [{"note": name, "access_token": name + "-old-at", "refresh_token": name + "-old-rt"}
                        for name in ("first", "second")]
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(module["_refresh_account_once"], accounts[0], lambda message: None)
                self.assertTrue(entered.wait(5))
                second = executor.submit(module["_refresh_account_once"], accounts[1], lambda message: None)
                try:
                    self.assertEqual(["first-old-rt"], calls)
                finally:
                    release.set()
                self.assertTrue(first.result(timeout=5))
                self.assertTrue(second.result(timeout=5))
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("first-new-rt", saved["first"]["refresh_token"])
            self.assertEqual("second-new-rt", saved["second"]["refresh_token"])
            self.assertEqual({"first-new-at", "second-new-at"}, set(token_file.read_text(encoding="utf-8").split("@")))

    def test_401_same_account_concurrent_refresh_uses_one_rotation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tokens.json"
            path.write_text(json.dumps({"first": {"access_token": "old-at", "refresh_token": "old-rt"}}), encoding="utf-8")
            entered, release = threading.Event(), threading.Event()
            calls = []
            def refresh(rt, at):
                calls.append(rt)
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test timed out")
                return "new-at", "new-rt"
            module = load_functions("_refresh_account_once", os=os, json=json, time=time, REFRESH_STORE=str(path),
                                    refresh_one=refresh, _rebuild_token_file=mock.Mock())
            accounts = [{"note": "first", "access_token": "old-at", "refresh_token": "old-rt"} for _ in range(2)]
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(module["_refresh_account_once"], accounts[0], lambda message: None)
                self.assertTrue(entered.wait(5))
                second = executor.submit(module["_refresh_account_once"], accounts[1], lambda message: None)
                release.set()
                self.assertTrue(first.result(timeout=5))
                self.assertTrue(second.result(timeout=5))
            self.assertEqual(["old-rt"], calls)
            self.assertEqual([("new-at", "new-rt")] * 2,
                             [(account["access_token"], account["refresh_token"]) for account in accounts])

    def test_401_reuses_credentials_rotated_by_another_executor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tokens.json"
            path.write_text(json.dumps({"first": {"access_token": "new-at", "refresh_token": "new-rt"}}), encoding="utf-8")
            refresh = mock.Mock()
            module = load_functions("_refresh_account_once", os=os, json=json, time=time, REFRESH_STORE=str(path),
                                    refresh_one=refresh, _rebuild_token_file=mock.Mock())
            account = {"note": "first", "access_token": "old-at", "refresh_token": "old-rt"}
            self.assertTrue(module["_refresh_account_once"](account, lambda message: None))
            self.assertEqual(("new-at", "new-rt"), (account["access_token"], account["refresh_token"]))
            refresh.assert_not_called()

    def test_401_merges_changes_during_refresh_and_rejects_same_account_conflict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tokens.json"
            original = {"first": {"access_token": "old-at", "refresh_token": "old-rt", "extra": "old"},
                        "second": {"refresh_token": "second-old"}}
            for conflict in (False, True):
                with self.subTest(conflict=conflict):
                    path.write_text(json.dumps(original), encoding="utf-8")
                    def refresh(rt, at):
                        other = json.loads(path.read_text(encoding="utf-8"))
                        other["second"]["refresh_token"] = "second-new"
                        other["first"]["extra"] = "changed"
                        if conflict:
                            other["first"]["refresh_token"] = "external-rt"
                        path.write_text(json.dumps(other), encoding="utf-8")
                        return "new-at", "new-rt"
                    module = load_functions("_refresh_account_once", os=os, json=json, time=time,
                        REFRESH_STORE=str(path), refresh_one=refresh, _rebuild_token_file=mock.Mock())
                    account = {"note": "first", "refresh_token": "old-rt"}
                    if conflict:
                        with self.assertRaises(RuntimeError):
                            module["_refresh_account_once"](account, lambda message: None)
                        copies = list(Path(temp_dir).glob("tokens.json.tmp-*"))
                        self.assertEqual("new-rt", json.loads(copies[0].read_text(encoding="utf-8"))["first"]["refresh_token"])
                    else:
                        self.assertTrue(module["_refresh_account_once"](account, lambda message: None))
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual("second-new", saved["second"]["refresh_token"])
                    self.assertEqual("changed", saved["first"]["extra"])
                    self.assertEqual("external-rt" if conflict else "new-rt", saved["first"]["refresh_token"])

    def test_refresh_one_sends_cn_header_subset_and_empty_body(self):
        session = FakeSession([FakeResponse(payload={
            "code": 0,
            "data": {"accessToken": "new-at", "refreshToken": "new-rt"},
        })])
        requests_module = type("Requests", (), {
            "Session": staticmethod(lambda: session),
        })
        module = load_functions(
            "refresh_one",
            requests=requests_module,
            REFRESH_URL="https://copilot.tencent.com/v2/plugin/auth/token/refresh",
            REFRESH_DOMAIN="www.workbuddy.cn",
            uid_of=lambda token: "uid-1",
            _new_message_id=lambda: "0123456789abcdef0123456789abcdef",
        )
        self.assertEqual(("new-at", "new-rt"),
                         module["refresh_one"]("old-rt", "old-at"))
        method, url, kwargs = session.calls[0]
        self.assertEqual("POST", method)
        self.assertEqual(
            "https://copilot.tencent.com/v2/plugin/auth/token/refresh", url)
        self.assertEqual({}, kwargs["json"])
        self.assertEqual({
            "X-Refresh-Token": "old-rt",
            "X-Auth-Refresh-Source": "plugin",
            "Content-Type": "application/json",
            "X-Request-ID": "0123456789abcdef0123456789abcdef",
            "X-Domain": "www.workbuddy.cn",
            "Authorization": "Bearer old-at",
            "X-User-Id": "uid-1",
        }, kwargs["headers"])

    def test_refresh_one_without_at_keeps_legacy_identity_shape(self):
        session = FakeSession([FakeResponse(payload={
            "code": 0,
            "data": {"accessToken": "new-at"},
        })])
        requests_module = type("Requests", (), {
            "Session": staticmethod(lambda: session),
        })
        module = load_functions(
            "refresh_one",
            requests=requests_module,
            REFRESH_URL="https://copilot.tencent.com/v2/plugin/auth/token/refresh",
            REFRESH_DOMAIN="www.workbuddy.cn",
            uid_of=lambda token: "should-not-be-used",
            _new_message_id=lambda: "0123456789abcdef0123456789abcdef",
        )
        module["refresh_one"]("old-rt")
        headers = session.calls[0][2]["headers"]
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("X-User-Id", headers)
        self.assertEqual({
            "X-Refresh-Token",
            "X-Auth-Refresh-Source",
            "Content-Type",
            "X-Request-ID",
            "X-Domain",
        }, set(headers))

    def test_module_import_can_refresh_due_account_with_uid_header(self):
        """ACCOUNTS 初始化早于 uid_of 时，续期会 NameError 且请求根本发不出去。"""
        now = int(time.time())
        old_at = jwt_token({
            "typ": "Bearer", "sub": "sub-1",
            "preferred_username": "13800000000", "exp": now - 60,
        })
        old_rt = jwt_token({
            "typ": "Offline", "sub": "sub-1",
            "preferred_username": "13800000000",
        })
        new_at = jwt_token({
            "typ": "Bearer", "sub": "sub-1",
            "preferred_username": "13800000000", "exp": now + 86400 * 30,
        })
        new_rt = jwt_token({
            "typ": "Offline", "sub": "sub-1",
            "preferred_username": "13800000000", "jti": "new",
        })
        calls = []

        class Session:
            trust_env = True

            def post(self, url, **kwargs):
                calls.append((url, kwargs.get("headers", {})))
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"accessToken": new_at, "refreshToken": new_rt},
                })

        fake_requests = types.ModuleType("requests")
        fake_requests.Session = Session
        previous_requests = sys.modules.get("requests")
        previous_state = os.environ.get("WORKBUDDY_STATE_DIR")
        previous_env = os.environ.get("WORKBUDDY_REFRESH_TOKEN")
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["WORKBUDDY_STATE_DIR"] = temp_dir
            os.environ["WORKBUDDY_REFRESH_TOKEN"] = "13800000000:%s:%s" % (
                old_at, old_rt)
            sys.modules["requests"] = fake_requests
            namespace = {
                "__name__": "workbuddy_import_probe",
                "__file__": str(SOURCE_PATH),
                "__builtins__": __builtins__,
            }
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    exec(compile(SOURCE, str(SOURCE_PATH), "exec"), namespace)
            finally:
                if previous_requests is not None:
                    sys.modules["requests"] = previous_requests
                else:
                    sys.modules.pop("requests", None)
                if previous_state is None:
                    os.environ.pop("WORKBUDDY_STATE_DIR", None)
                else:
                    os.environ["WORKBUDDY_STATE_DIR"] = previous_state
                if previous_env is None:
                    os.environ.pop("WORKBUDDY_REFRESH_TOKEN", None)
                else:
                    os.environ["WORKBUDDY_REFRESH_TOKEN"] = previous_env

        self.assertEqual(1, len(calls), "续期请求应实际发出，不能被 NameError 吞掉")
        self.assertEqual("sub-1", calls[0][1].get("X-User-Id"))
        self.assertEqual(1, len(namespace["ACCOUNTS"]))
        self.assertEqual(new_rt, namespace["ACCOUNTS"][0]["refresh_token"])

    def test_env_merge_repeated_runs_preserve_rotated_refresh_token(self):
        """相同 env 输入重复运行：按 JWT subject 合并，不能覆盖已轮换的 RT。"""
        now = int(time.time())
        old_at = jwt_token({
            "typ": "Bearer", "sub": "sub-1",
            "preferred_username": "13800000000", "exp": now + 86400 * 30,
        })
        old_rt = jwt_token({
            "typ": "Offline", "sub": "sub-1",
            "preferred_username": "13800000000", "jti": "original",
        })
        rotated_at = jwt_token({
            "typ": "Bearer", "sub": "sub-1",
            "preferred_username": "13800000000", "exp": now + 86400 * 30,
        })
        rotated_rt = jwt_token({
            "typ": "Offline", "sub": "sub-1",
            "preferred_username": "13800000000", "jti": "rotated",
        })
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = os.path.join(temp_dir, "wb_refresh_tokens.json")
            with open(store_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "old-note": {
                        "refresh_token": rotated_rt,
                        "access_token": rotated_at,
                        "updated": time.strftime("%Y-%m-%d %H:%M"),
                    },
                }, handle, ensure_ascii=False)
            previous_env = os.environ.get("WORKBUDDY_REFRESH_TOKEN")
            os.environ["WORKBUDDY_REFRESH_TOKEN"] = "new-note:%s:%s" % (
                old_at, old_rt)
            refresh_calls = []
            module = load_functions(
                "auto_refresh", "_account_identity", "_parse_env_tokens",
                "_token_kind", "_token_user", "_jwt_claims",
                os=os, json=json, base64=base64, time=time,
                REFRESH_STORE=store_path,
                TOKEN_FILE=os.path.join(temp_dir, "WORKBUDDY_ACCESS_TOKEN.txt"),
                refresh_one=lambda rt, at="": (
                    refresh_calls.append((rt, at)) or (rotated_at, rotated_rt)),
                _token_sanity=lambda rt, at: [],
                _rebuild_token_file=lambda store: None,
            )
            try:
                outputs = []
                for _ in range(2):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        module["auto_refresh"]()
                    outputs.append(output.getvalue())
            finally:
                if previous_env is None:
                    os.environ.pop("WORKBUDDY_REFRESH_TOKEN", None)
                else:
                    os.environ["WORKBUDDY_REFRESH_TOKEN"] = previous_env
            with open(store_path, encoding="utf-8") as handle:
                store = json.load(handle)

        self.assertEqual(["old-note"], list(store))
        self.assertEqual(rotated_rt, store["old-note"]["refresh_token"])
        self.assertEqual([], refresh_calls)
        self.assertFalse(any("新增" in line for line in outputs))

    def test_env_merge_falls_back_to_user_when_tokens_have_no_subject(self):
        now = int(time.time())
        old_at = jwt_token({"typ": "Bearer", "exp": now + 86400 * 30})
        old_rt = jwt_token({"typ": "Offline"})
        rotated_rt = jwt_token({"typ": "Offline", "jti": "rotated"})
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = os.path.join(temp_dir, "wb_refresh_tokens.json")
            with open(store_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "13800000000": {
                        "refresh_token": rotated_rt,
                        "access_token": old_at,
                        "updated": time.strftime("%Y-%m-%d %H:%M"),
                    },
                }, handle, ensure_ascii=False)
            previous_env = os.environ.get("WORKBUDDY_REFRESH_TOKEN")
            os.environ["WORKBUDDY_REFRESH_TOKEN"] = "13800000000:%s:%s" % (
                old_at, old_rt)
            module = load_functions(
                "auto_refresh", "_account_identity", "_parse_env_tokens",
                "_token_kind", "_token_user", "_jwt_claims",
                os=os, json=json, base64=base64, time=time,
                REFRESH_STORE=store_path,
                TOKEN_FILE=os.path.join(temp_dir, "WORKBUDDY_ACCESS_TOKEN.txt"),
                refresh_one=lambda rt, at="": (old_at, rotated_rt),
                _token_sanity=lambda rt, at: [],
                _rebuild_token_file=lambda store: None,
            )
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    module["auto_refresh"]()
            finally:
                if previous_env is None:
                    os.environ.pop("WORKBUDDY_REFRESH_TOKEN", None)
                else:
                    os.environ["WORKBUDDY_REFRESH_TOKEN"] = previous_env
            with open(store_path, encoding="utf-8") as handle:
                store = json.load(handle)

        self.assertEqual(rotated_rt, store["13800000000"]["refresh_token"])
        self.assertEqual(1, len(store))

    def test_env_merge_without_stable_identity_preserves_existing_account(self):
        now = int(time.time())
        at = jwt_token({"typ": "Bearer", "exp": now + 86400 * 30})
        rt = jwt_token({"typ": "Offline", "jti": "env"})
        rotated_rt = jwt_token({"typ": "Offline", "jti": "rotated"})
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = os.path.join(temp_dir, "wb_refresh_tokens.json")
            with open(store_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "13800000000": {
                        "refresh_token": rotated_rt,
                        "access_token": at,
                        "updated": time.strftime("%Y-%m-%d %H:%M"),
                    },
                }, handle, ensure_ascii=False)
            previous_env = os.environ.get("WORKBUDDY_REFRESH_TOKEN")
            # AT:RT 且两个 token 都没有 sub / 手机号：没有稳定身份
            os.environ["WORKBUDDY_REFRESH_TOKEN"] = "%s:%s" % (at, rt)
            module = load_functions(
                "auto_refresh", "_account_identity", "_parse_env_tokens",
                "_token_kind", "_token_user", "_jwt_claims",
                os=os, json=json, base64=base64, time=time,
                REFRESH_STORE=store_path,
                TOKEN_FILE=os.path.join(temp_dir, "WORKBUDDY_ACCESS_TOKEN.txt"),
                refresh_one=lambda rt, at="": (at, rotated_rt),
                _token_sanity=lambda rt, at: [],
                _rebuild_token_file=lambda store: None,
            )
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    module["auto_refresh"]()
            finally:
                if previous_env is None:
                    os.environ.pop("WORKBUDDY_REFRESH_TOKEN", None)
                else:
                    os.environ["WORKBUDDY_REFRESH_TOKEN"] = previous_env
            with open(store_path, encoding="utf-8") as handle:
                store = json.load(handle)

        self.assertEqual(["13800000000"], list(store))
        self.assertEqual(rotated_rt, store["13800000000"]["refresh_token"])

    def test_black_cat_missing_task_does_not_run_fake_chat_attempts(self):
        chats = []
        module = load_functions(
            "t_black_cat",
            prog=lambda session, code: (None, None, None),
            within_night_window=lambda: True,
            beijing_now=lambda: type("Now", (), {"hour": 23})(),
            webchat=lambda *args: chats.append(args) or ("conv", "reply"),
            time=type("Time", (), {
                "sleep": staticmethod(lambda seconds: None),
            }),
        )
        logs = []
        module["t_black_cat"](object(), "uid", "nick", logs.append)
        self.assertEqual([], chats)
        self.assertIn("未下发 black_cat", " ".join(logs))

    def test_claim_ready_is_idempotent_across_scans(self):
        class Session:
            def __init__(self):
                self.claimed = False
                self.posts = []

            def get(self, url, **kwargs):
                status = "claimed" if self.claimed else "accepted"
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"tasks": [{
                        "task_code": "chat_5",
                        "accept_status": status,
                        "progress": {"current": 5, "target": 5},
                    }]},
                })

            def post(self, url, **kwargs):
                self.posts.append((url, kwargs.get("headers")))
                self.claimed = True
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"already_claimed": False, "credit": 100, "energy": 5},
                })

        module = load_functions(
            "_api_code_ok", "_claim_response_ok", "claim", "_mp_claim",
            "claim_ready_tasks",
            _as_int=lambda value, default=0: int(value or default),
            BASE="https://example.test",
            MP_HEADER={"X-Client-Platform": "miniprogram"},
            MP_TASK_CODES=set(),
            DESKTOP_ONLY_TASK_CODES=set(),
            time=type("Time", (), {"sleep": staticmethod(lambda seconds: None)}),
        )
        session = Session()
        first_logs = []
        second_logs = []
        module["claim_ready_tasks"](session, first_logs.append)
        module["claim_ready_tasks"](session, second_logs.append)
        self.assertEqual(1, len(session.posts))
        self.assertIn("达标补领完成: 1 项", " ".join(first_logs))
        self.assertIn("无达标未领奖励", " ".join(second_logs))

    def test_travel_claim_uses_record_id_and_real_reward(self):
        response_queue = [
            FakeResponse(payload={"code": 0, "data": {"has_buddy": True}}),
            FakeResponse(payload={
                "code": 0,
                "data": {
                    "state": "arrived",
                    "record_id": "record-42",
                    "daily_limit_reached": True,
                },
            }),
            FakeResponse(payload={
                "code": 0,
                "data": {"reward_credit": 30},
            }),
            FakeResponse(payload={
                "code": 0,
                "data": {"state": "idle", "daily_limit_reached": True},
            }),
        ]
        session = FakeSession(response_queue)
        module = load_functions(
            "_api_code_ok", "_dig", "_read_travel_status", "_travel_observe",
            "t_travel_prepare", "t_travel_depart", "t_travel",
            BASE="https://example.test")
        logs = []
        module["t_travel"](session, "u1", "nick", logs.append)
        claim_call = [call for call in session.calls
                      if call[1].endswith("/buddy/travel/claim")][0]
        self.assertEqual({"record_id": "record-42"}, claim_call[2]["json"])
        self.assertIn("+30积分", " ".join(logs))

    def test_travel_blindbox_cycle_claims_before_opening_and_departs_last(self):
        class Session:
            def __init__(self):
                self.state = "arrived"
                self.opens = 0
                self.calls = []

            def get(self, url, **kwargs):
                path = url.split("/v2/activity/growth", 1)[1]
                self.calls.append(("GET", path))
                if path == "/buddy/visible":
                    return FakeResponse(payload={
                        "code": 0, "data": {"has_buddy": True, "buddy_visible": True}})
                if path == "/buddy/travel/status":
                    return FakeResponse(payload={
                        "code": 0,
                        "data": {
                            "state": self.state,
                            "record_id": "record-1",
                            "daily_limit_reached": False,
                            "arrive_at": 0,
                            "server_now": 0,
                        },
                    })
                if path == "/buddy/quota":
                    return FakeResponse(payload={
                        "code": 0, "data": {"balance": 30, "affordable": 3}})
                if path == "/buddy/travel/config":
                    return FakeResponse(payload={
                        "code": 0,
                        "data": {"locations": [{"id": "loc-1", "name": "公园"}]},
                    })
                raise AssertionError("unexpected GET: " + path)

            def post(self, url, **kwargs):
                path = url.split("/v2/activity/growth", 1)[1]
                self.calls.append(("POST", path))
                if path == "/buddy/travel/claim":
                    self.state = "idle"
                    return FakeResponse(payload={
                        "code": 0, "data": {"reward_credit": 30}})
                if path == "/buddy/open":
                    self.opens += 1
                    return FakeResponse(payload={
                        "code": 0,
                        "data": {"results": [{
                            "instance": {"name": "道具", "rarity": "R"}}]},
                    })
                if path == "/buddy/travel/depart":
                    self.state = "traveling"
                    return FakeResponse(payload={
                        "code": 0,
                        "data": {"arrive_at": 3600, "server_now": 0},
                    })
                raise AssertionError("unexpected POST: " + path)

        module = load_functions(
            "_api_code_ok", "_dig", "_read_travel_status", "_travel_observe",
            "t_travel_prepare", "t_travel_probe", "t_travel_depart",
            "t_blindbox", "t_travel_blindbox_cycle",
            BASE="https://example.test",
            time=type("Time", (), {
                "sleep": staticmethod(lambda seconds: None),
            }),
        )
        session = Session()
        logs = []
        module["t_travel_blindbox_cycle"](
            session, "u1", "nick", logs.append,
            do_travel=True, do_blindbox=True)

        claim_index = session.calls.index(("POST", "/buddy/travel/claim"))
        status_indexes = [
            i for i, call in enumerate(session.calls)
            if call == ("GET", "/buddy/travel/status")
        ]
        quota_index = session.calls.index(("GET", "/buddy/quota"))
        depart_index = session.calls.index(("POST", "/buddy/travel/depart"))
        first_open_index = session.calls.index(("POST", "/buddy/open"))

        self.assertEqual(2, len(status_indexes))
        self.assertLess(claim_index, status_indexes[1])
        self.assertLess(status_indexes[1], quota_index)
        self.assertLess(quota_index, first_open_index)
        self.assertLess(first_open_index, depart_index)
        self.assertEqual(3, session.opens)
        self.assertEqual("traveling", session.state)

    def test_blindbox_opens_zero_three_or_five_times_after_idle(self):
        class Session:
            def __init__(self, affordable):
                self.affordable = affordable
                self.opens = 0
                self.calls = []

            def get(self, url, **kwargs):
                path = url.split("/v2/activity/growth", 1)[1]
                self.calls.append(("GET", path))
                if path == "/buddy/visible":
                    return FakeResponse(payload={
                        "code": 0, "data": {"has_buddy": True, "buddy_visible": True}})
                if path == "/buddy/travel/status":
                    return FakeResponse(payload={
                        "code": 0,
                        "data": {"state": "idle", "daily_limit_reached": True},
                    })
                if path == "/buddy/quota":
                    return FakeResponse(payload={
                        "code": 0,
                        "data": {
                            "balance": self.affordable * 10,
                            "affordable": self.affordable,
                        },
                    })
                raise AssertionError("unexpected GET: " + path)

            def post(self, url, **kwargs):
                path = url.split("/v2/activity/growth", 1)[1]
                self.calls.append(("POST", path))
                if path == "/buddy/open":
                    self.opens += 1
                    return FakeResponse(payload={
                        "code": 0,
                        "data": {"results": [{
                            "instance": {"name": "道具", "rarity": "R"}}]},
                    })
                raise AssertionError("unexpected POST: " + path)

        module = load_functions(
            "_api_code_ok", "_dig", "_read_travel_status", "_travel_observe",
            "t_travel_prepare", "t_travel_probe", "t_travel_depart",
            "t_blindbox", "t_travel_blindbox_cycle",
            BASE="https://example.test",
            time=type("Time", (), {
                "sleep": staticmethod(lambda seconds: None),
            }),
        )
        for affordable, expected in ((0, 0), (3, 3), (9, 5)):
            with self.subTest(affordable=affordable):
                session = Session(affordable)
                module["t_travel_blindbox_cycle"](
                    session, "u1", "nick", lambda message: None,
                    do_travel=False, do_blindbox=True)
                self.assertEqual(expected, session.opens)
                self.assertFalse(any(
                    path == "/buddy/travel/depart"
                    for _, path in session.calls))

    def test_travel_blindbox_cycle_blocks_unsafe_travel_states(self):
        class Session:
            def __init__(self, state, claim_ok=True, state_after_claim="idle"):
                self.state = state
                self.claim_ok = claim_ok
                self.state_after_claim = state_after_claim
                self.calls = []

            def get(self, url, **kwargs):
                path = url.split("/v2/activity/growth", 1)[1]
                self.calls.append(("GET", path))
                if path == "/buddy/visible":
                    return FakeResponse(payload={
                        "code": 0, "data": {"has_buddy": True, "buddy_visible": True}})
                if path == "/buddy/travel/status":
                    return FakeResponse(payload={
                        "code": 0,
                        "data": {
                            "state": self.state,
                            "record_id": "record-1",
                            "daily_limit_reached": False,
                            "arrive_at": 0,
                            "server_now": 0,
                        },
                    })
                if path == "/buddy/quota":
                    return FakeResponse(payload={
                        "code": 0, "data": {"balance": 90, "affordable": 9}})
                if path == "/buddy/travel/config":
                    raise AssertionError("unsafe state must not read depart config")
                raise AssertionError("unexpected GET: " + path)

            def post(self, url, **kwargs):
                path = url.split("/v2/activity/growth", 1)[1]
                self.calls.append(("POST", path))
                if path == "/buddy/travel/claim":
                    if not self.claim_ok:
                        return FakeResponse(500, {"code": 500, "msg": "claim failed"})
                    self.state = self.state_after_claim
                    return FakeResponse(payload={
                        "code": 0, "data": {"reward_credit": 30}})
                if path == "/buddy/open":
                    raise AssertionError("unsafe state must not open blind box")
                if path == "/buddy/travel/depart":
                    raise AssertionError("unsafe state must not depart")
                raise AssertionError("unexpected POST: " + path)

        module = load_functions(
            "_api_code_ok", "_dig", "_read_travel_status", "_travel_observe",
            "t_travel_prepare", "t_travel_probe", "t_travel_depart",
            "t_blindbox", "t_travel_blindbox_cycle",
            BASE="https://example.test",
            time=type("Time", (), {
                "sleep": staticmethod(lambda seconds: None),
            }),
        )
        cases = (
            ("traveling", True, "idle"),
            ("arrived", False, "idle"),
            ("", True, "idle"),
            ("unexpected", True, "idle"),
            ("arrived", True, "traveling"),
        )
        for state, claim_ok, state_after_claim in cases:
            with self.subTest(
                    state=state, claim_ok=claim_ok,
                    state_after_claim=state_after_claim):
                session = Session(state, claim_ok, state_after_claim)
                module["t_travel_blindbox_cycle"](
                    session, "u1", "nick", lambda message: None,
                    do_travel=True, do_blindbox=True)
                paths = [path for _, path in session.calls]
                self.assertNotIn("/buddy/open", paths)
                self.assertNotIn("/buddy/travel/config", paths)
                self.assertNotIn("/buddy/travel/depart", paths)

    def test_webchat_success_stops_and_failure_deletes(self):
        module = load_functions(
            "_web_conversation_limit_hit", "_web_conversation_action",
            "_web_conversation_prune", "_web_conversation_release",
            "_web_conversation_create", "webchat2",
            BASE="https://example.test",
            WEB_CONVERSATIONS_PATH="/console/webchat/conversations",
            WEB_CONVERSATION_ACTIVE_STATUSES=("working", "creating", "pending"),
            WEB_CONVERSATION_PRUNE_KEEP=8,
            requests=requests, json=json, uuid=uuid,
        )

        class Session:
            def __init__(self, chat_status, lines):
                self.chat_status = chat_status
                self.lines = lines
                self.headers = {}
                self.calls = []
                self.create_count = 0

            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                if url.endswith("/console/webchat/conversations"):
                    self.create_count += 1
                    return FakeResponse(
                        payload={"code": 0, "data": {"conversationId": "conv-1"}})
                if url.endswith("/chat/completions"):
                    return FakeStreamResponse(self.chat_status, lines=self.lines)
                return FakeResponse(payload={"code": 0, "data": {}})

        success = Session(
            200,
            ['data: {"id":"cmb-1","choices":[{"delta":{"content":"OK"}}]}',
             "data: [DONE]"],
        )
        conv_id, text, message_id = module["webchat2"](
            success, "activity", "你好")
        self.assertEqual(("conv-1", "OK", "cmb-1"), (conv_id, text, message_id))
        urls = [url for url, _ in success.calls]
        self.assertTrue(any(url.endswith("/conv-1/stop") for url in urls))
        self.assertFalse(any(url.endswith("/conv-1/delete") for url in urls))

        failed = Session(500, [])
        module["webchat2"](failed, "activity", "你好")
        urls = [url for url, _ in failed.calls]
        self.assertTrue(any(url.endswith("/conv-1/stop") for url in urls))
        self.assertTrue(any(url.endswith("/conv-1/delete") for url in urls))

    def test_web_conversation_limit_prunes_and_retries_create(self):
        class Session:
            def __init__(self):
                self.create_count = 0
                self.stops = []

            def get(self, url, **kwargs):
                rows = [{
                    "id": "old-%02d" % i,
                    "name": "activity-%08x" % i,
                    "status": "working",
                    "createdAt": "2026-10-22T10:%02d:00+08:00" % i,
                } for i in range(10)]
                return FakeResponse(payload={"data": {"conversations": rows}})

            def post(self, url, **kwargs):
                if url.endswith("/conversations"):
                    self.create_count += 1
                    if self.create_count == 1:
                        return FakeResponse(
                            429, {"code": 10105,
                                  "msg": "maximum number of active conversations(10)"})
                    return FakeResponse(
                        payload={"code": 0, "data": {"conversationId": "new-conv"}})
                if url.endswith("/stop"):
                    self.stops.append(url)
                    return FakeResponse(payload={"code": 0, "data": {}})
                return FakeResponse(payload={"code": 0, "data": {}})

        module = load_functions(
            "_web_conversation_limit_hit", "_web_conversation_action",
            "_web_conversation_prune", "_web_conversation_create",
            BASE="https://example.test",
            WEB_CONVERSATIONS_PATH="/console/webchat/conversations",
            WEB_CONVERSATION_ACTIVE_STATUSES=("working", "creating", "pending"),
            WEB_CONVERSATION_PRUNE_KEEP=8,
            requests=requests, json=json, uuid=uuid,
        )
        session = Session()
        conv_id = module["_web_conversation_create"](session, "activity")
        self.assertEqual("new-conv", conv_id)
        self.assertEqual(2, session.create_count)
        self.assertEqual(2, len(session.stops))
        self.assertTrue(session.stops[0].endswith("/old-00/stop"))

    def test_web_conversation_prune_is_account_wide_not_prefix_scoped(self):
        """账号级 10 条上限：其他专家前缀的活跃会话也必须能被清掉。"""
        class Session:
            def __init__(self):
                self.create_count = 0
                self.stops = []

            def get(self, url, **kwargs):
                names = ("expert-douyinyin", "expert-maidehao",
                         "expert-shangjia-xuanchuan", "expert-bowangwang")
                rows = [{
                    "id": "old-%02d" % i,
                    "name": "%s-%08x" % (names[i % len(names)], i),
                    "status": "working",
                    "createdAt": "2026-10-22T10:%02d:00+08:00" % i,
                } for i in range(10)]
                return FakeResponse(payload={"data": {"conversations": rows}})

            def post(self, url, **kwargs):
                if url.endswith("/conversations"):
                    self.create_count += 1
                    if self.create_count == 1:
                        return FakeResponse(
                            429, {"code": 10105,
                                  "msg": "maximum number of active conversations(10)"})
                    return FakeResponse(
                        payload={"code": 0,
                                 "data": {"conversationId": "new-conv"}})
                if url.endswith("/stop"):
                    self.stops.append(url)
                    return FakeResponse(payload={"code": 0, "data": {}})
                return FakeResponse(payload={"code": 0, "data": {}})

        module = load_functions(
            "_web_conversation_limit_hit", "_web_conversation_action",
            "_web_conversation_prune", "_web_conversation_create",
            BASE="https://example.test",
            WEB_CONVERSATIONS_PATH="/console/webchat/conversations",
            WEB_CONVERSATION_ACTIVE_STATUSES=("working", "creating", "pending"),
            WEB_CONVERSATION_PRUNE_KEEP=8,
            requests=requests, json=json, uuid=uuid,
        )
        session = Session()
        conv_id = module["_web_conversation_create"](
            session, "expert-douyinyin")
        self.assertEqual("new-conv", conv_id)
        self.assertEqual(2, len(session.stops))
        self.assertTrue(session.stops[0].endswith("/old-00/stop"))

    def test_makeup_candidates_use_server_calendar_and_exclude_made_up_dates(self):
        module = load_functions("_dig", "_makeup_candidates", re=re, math=math)
        streak_body = {
            "code": 0,
            "data": {
                "launch_date": "2026-10-10",
                "streak": {"makeup_dates": ["2026-10-19", "2026-10-20"]},
            },
        }
        heatmap_body = {
            "code": 0,
            "data": {
                "today": {"date": "2026-10-21"},
                "cells": [
                    {"date": "2026-09-30", "score": 0},
                    {"date": "2026-10-18", "score": 0},
                    {"date": "2026-10-19", "score": 0},
                    {"date": "2026-10-20", "score": 0},
                    {"date": "2026-10-21", "score": 0},
                ],
            },
        }
        candidates = module["_makeup_candidates"](streak_body, heatmap_body)
        self.assertEqual(["2026-10-18"], candidates)

    def test_compensation_local_guard_prevents_repeated_claims(self):
        class Session:
            def __init__(self):
                self.claim_calls = 0

            def post(self, url, **kwargs):
                if url.endswith("/compensation-status"):
                    return FakeResponse(payload={
                        "code": 0,
                        "data": {
                            "active": True,
                            "claimed": False,
                            "credit_num": 500,
                            "end_time": "2026-10-22 23:59:59",
                        },
                    })
                self.claim_calls += 1
                return FakeResponse(payload={"code": 0, "data": {}})

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "benefit-state.json")
            module = load_functions(
                "_as_int", "_api_code_ok", "_benefit_state_path",
                "_benefit_state_load",
                "_benefit_state_save", "_benefit_guard_key",
                "_benefit_guard_get", "_benefit_guard_mark",
                "_benefit_status", "_benefit_claim",
                "_benefit_actual_credit", "t_compensation",
                os=os, json=json, time=time, re=re, hashlib=hashlib,
                CBDOMAIN="https://example.test",
                BENEFIT_STATE_FILE=state_path,
                api_retry=lambda session, method, url, **kwargs: session.post(url),
                is_enterprise_denial=lambda status, payload: False,
            )
            session = Session()
            logs = []
            module["t_compensation"](session, "uid-1", logs.append)
            module["t_compensation"](session, "uid-1", logs.append)

            self.assertEqual(1, session.claim_calls)
            self.assertIn("未提供实际到账额", " ".join(logs))
            self.assertIn("本地防重", " ".join(logs))

    def test_event_ids_use_official_shapes_without_project_prefixes(self):
        module = load_functions(
            "_new_conversation_id", "_new_message_id", "mp_chat_event",
            uuid=uuid, time=time,
        )
        conv_id = module["_new_conversation_id"]()
        message_id = module["_new_message_id"]()
        self.assertRegex(
            conv_id,
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        )
        self.assertRegex(message_id, r"^[0-9a-f]{32}$")
        self.assertLess(abs(int(message_id[:12], 16) - int(time.time() * 1000)), 5000)

        event = module["mp_chat_event"]("uid", "nick", conv_id)
        self.assertEqual(conv_id, event["conversationId"])
        self.assertRegex(event["requestId"], r"^[0-9a-f]{32}$")
        self.assertEqual(event["requestId"], event["traceId"])
        self.assertEqual(event["requestId"], event["rootRequestId"])
        self.assertEqual("msg-" + event["requestId"][-8:], event["messageId"])
        encoded = json.dumps(event, ensure_ascii=False)
        for prefix in ("wb2api", "wbmp", "wb-daily", "fp-"):
            self.assertNotIn(prefix, encoded)

    def test_expert_mapping_parser_and_dynamic_jump_url_override(self):
        expected = {
            "expert_use_maidehao":
                ("ex_GDl31WahyIH0", "卖得好", "中国电商运营专家", "1.0.1"),
            "expert_use_douyinyin":
                ("ex_FbY5nlWTqDod", "斗音音", "抖音策略师", "1.0.2"),
            "expert_use_shangjia_xuanchuan":
                ("ex_Weo3x2EZuSDm", "商家宣传设计师",
                 "商家宣传页与海报设计师", "1.6.0"),
            "expert_use_bowangwang":
                ("ex_WZY136wV5aMt", "播旺旺", "直播电商教练", "1.0.2"),
        }
        specs = assignment_value("EXPERT_TASK_SPECS")
        names = assignment_value("TASK_NAME_CN")
        self.assertEqual(set(expected), set(specs))
        for code, (expert_id, display_name, profession, version) in expected.items():
            self.assertEqual(expert_id, specs[code]["id"])
            self.assertEqual(display_name, specs[code]["name"])
            self.assertEqual(profession, specs[code]["profession"])
            self.assertEqual(profession, names[code])
            self.assertEqual(version, specs[code]["version"])

        module = load_functions(
            "_parse_expert_deeplink", "_expert_id_from_jump_url",
            parse_qs=parse_qs, urlparse=urlparse, re=re,
            _EXPERT_ID_RE=re.compile(r"^ex_[A-Za-z0-9]{6,64}$"),
            _TRUSTED_JUMP_HOST_SUFFIXES=(
                "workbuddy.cn", "codebuddy.cn", "tencent.com"),
        )
        direct = "workbuddy://experts?expertId=ex_GDl31WahyIH0"
        wrapped = (
            "https://www.workbuddy.cn/jump?deeplink="
            "workbuddy%3A%2F%2Fexperts%3FexpertId%3Dex_FbY5nlWTqDod"
        )
        bare_parameter = (
            "deeplink=workbuddy://experts?expertId=ex_Weo3x2EZuSDm"
        )
        self.assertEqual("ex_GDl31WahyIH0",
                         module["_expert_id_from_jump_url"](direct))
        self.assertEqual("ex_FbY5nlWTqDod",
                         module["_expert_id_from_jump_url"](wrapped))
        self.assertEqual("ex_Weo3x2EZuSDm",
                         module["_expert_id_from_jump_url"](bare_parameter))
        invalid = [
            "https://evil.test/ex_GDl31WahyIH0",
            "https://evil.test/?next=workbuddy://experts?expertId=ex_GDl31WahyIH0",
            "https://evil.test/?deeplink=workbuddy://experts?expertId=ex_GDl31WahyIH0",
            "https://evil.test/?deeplink=https://evil.test/?expertId=ex_GDl31WahyIH0",
            "workbuddy://experts/other?expertId=ex_GDl31WahyIH0",
            "workbuddy://experts?expertId=not_an_expert",
            "workbuddy://experts?expertId=ex_GDl31WahyIH0&expertId=ex_FbY5nlWTqDod",
        ]
        for value in invalid:
            self.assertEqual("", module["_expert_id_from_jump_url"](value), value)

        class Session:
            def get(self, url, **kwargs):
                return FakeResponse(payload={
                    "data": {"tasks": [{
                        "task_code": "expert_use_maidehao",
                        "jump_url":
                            "workbuddy://experts?expertId=ex_Rotated12345",
                    }]},
                })

        integration = load_functions(
            "_parse_expert_deeplink", "_expert_id_from_jump_url",
            "_growth_task_rows", "_growth_task_row", "_expert_task_row",
            "_expert_spec_for_task",
            parse_qs=parse_qs, urlparse=urlparse, re=re,
            _EXPERT_ID_RE=re.compile(r"^ex_[A-Za-z0-9]{6,64}$"),
            _TRUSTED_JUMP_HOST_SUFFIXES=(
                "workbuddy.cn", "codebuddy.cn", "tencent.com"),
            EXPERT_TASK_SPECS=specs,
            TASK_NAME_CN=names,
            BASE="https://example.test",
            _mp_task_row=lambda session, code: None,
        )
        spec = integration["_expert_spec_for_task"](
            Session(), "expert_use_maidehao")
        self.assertEqual("ex_Rotated12345", spec["id"])
        self.assertEqual("卖得好", spec["name"])
        self.assertEqual("中国电商运营专家", spec["profession"])

    def test_market_expert_lookup_requires_exact_id(self):
        responses = [
            FakeResponse(payload={
                "data": {"experts": [
                    {
                        "expert_id": "ex_WrongExpert",
                        "display_name_zh": "抖音策略师",
                        "profession_zh": "抖音全域增长策略师",
                        "version": "9.9.9",
                    },
                    {
                        "expert_id": "ex_FbY5nlWTqDod",
                        "display_name_zh": "斗音音",
                        "profession_zh": "抖音策略师",
                        "version": "1.0.2",
                    },
                ]},
            }),
            FakeResponse(payload={"data": {"experts": []}}),
        ]
        calls = []

        def api_retry(session, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return responses.pop(0)

        module = load_functions(
            "_market_expert_by_id",
            api_retry=api_retry,
            CBDOMAIN="https://example.test",
            _EXPERT_ID_RE=re.compile(r"^ex_[A-Za-z0-9]{6,64}$"),
            _extract_name=lambda value: (
                value.get("zh") if isinstance(value, dict) else value),
        )
        found = module["_market_expert_by_id"](
            object(), "抖音策略师", "ex_FbY5nlWTqDod")
        self.assertEqual(
            {
                "id": "ex_FbY5nlWTqDod",
                "name": "斗音音",
                "profession": "抖音策略师",
                "version": "1.0.2",
            },
            found,
        )
        self.assertEqual("POST", calls[0][0])
        self.assertEqual(20, calls[0][2]["body"]["page_size"])
        self.assertIsNone(module["_market_expert_by_id"](
            object(), "抖音策略师", "ex_FbY5nlWTqDod"))

    def test_workbuddy_11140_is_account_restricted_not_token_expiry(self):
        module = load_functions(
            "interpret_failure", "is_account_restricted", json=json)
        positive_cases = (
            (403, {"code": 11140, "msg": "request illegal"}),
            (403, {"code": "11140", "message":
                   "Your account shows unusual activity and has been restricted."}),
            (403, '{"code":11140,"msg":"账号存在异常行为，已被限制使用"}'),
            (403, {"code": 11140, "displayMsg": "账户存在异常行为"}),
            (403, {"code": 11140, "displayMsg": "账户非法，已被限制使用"}),
        )
        for status, payload in positive_cases:
            with self.subTest(payload=payload):
                self.assertTrue(module["is_account_restricted"](status, payload))
        description, skip = module["interpret_failure"](*positive_cases[0])
        self.assertTrue(skip)
        self.assertIn("账号受限", description)
        self.assertNotIn("Token", description)
        self.assertFalse(module["is_account_restricted"](
            401, {"msg": "token expired"}))
        self.assertTrue(module["interpret_failure"](
            401, {"msg": "token expired"})[1])

    def test_workbuddy_11140_rate_limit_and_body_examples_are_not_restricted(self):
        module = load_functions(
            "interpret_failure", "is_account_restricted", json=json)
        negative_cases = (
            (429, {"code": 11140, "msg":
                   "The model provider is rate-limiting requests."}),
            (429, {"code": 11140, "msg": "too many requests"}),
            (403, {"code": 50400, "msg": "account restricted"}),
            (403, {"msg": "request illegal"}),
            (403, {"code": 11140, "msg": "normal",
                   "data": {"content": "code=11140 request illegal"}}),
            (403, "<html><title>11140 request illegal</title></html>"),
        )
        for status, payload in negative_cases:
            with self.subTest(payload=payload):
                self.assertFalse(module["is_account_restricted"](status, payload))
        description, skip = module["interpret_failure"](
            429, {"code": 11140,
                  "msg": "The model provider is rate-limiting requests."})
        self.assertFalse(skip)
        self.assertNotIn("账号受限", description)

    def test_local_expert_flow_uses_real_chat_and_local_events(self):
        states = [("accepted", 0, 1), ("completed", 1, 1), ("completed", 1, 1)]
        captured = {}

        def webchat2(session, name, prompt, meta):
            captured["meta"] = meta
            return "conv-1", "OK", "cmb-0123456789abcdef0123456789abcdef"

        def report_desktop_events(session, uid, nick, events):
            captured["events"] = events
            return 200

        module = load_functions(
            "_run_local_expert_task",
            re=re, uuid=uuid, json=json,
            time=type("Time", (), {
                "time": staticmethod(time.time),
                "sleep": staticmethod(lambda seconds: None),
            }),
            _EXPERT_ID_RE=re.compile(r"^ex_[A-Za-z0-9]{6,64}$"),
            prog=lambda session, code: states.pop(0),
            _expert_prog=lambda session, code: states.pop(0),
            _market_expert_by_id=lambda session, keyword, expected_id: {
                "name": "斗音音",
                "profession": "抖音策略师",
                "version": "1.0.2",
            },
            get_normal_experts=lambda count: [{
                "id": "ex_Dynamic12345",
                "name": "抖音策略师",
                "version": "1.0.9",
            }],
            webchat2=webchat2,
            desktop_chat_sequence=lambda uid, nick, conv, req, msg: [{
                "eventCode": "agent_task_created",
                "conversationId": conv,
                "requestId": req,
            }],
            report_desktop_events=report_desktop_events,
            report=lambda *args, **kwargs: 200,
            _new_message_id=lambda: "fallback-message",
            _new_conversation_id=lambda: "fallback-conversation",
        )
        logs = []
        module["_run_local_expert_task"](
            object(), "uid-1", "nick", logs.append, "expert_use_douyinyin",
            {"id": "ex_Dynamic12345", "name": "斗音音",
             "profession": "抖音策略师", "version": "1.0.0",
             "slug": "douyinyin"},
            conv_name="expert-douyinyin",
        )
        actual = [event for event in captured["events"]
                  if event.get("eventCode") == "expert_actual_use"][-1]
        created = [event for event in captured["events"]
                   if event.get("eventCode") == "agent_task_created"][0]
        self.assertEqual("ex_Dynamic12345", actual["id"])
        self.assertEqual("LOCAL", actual["mode"])
        self.assertEqual("斗音音", actual["name"])
        self.assertEqual("抖音策略师", actual["expertTitle"])
        self.assertEqual("1.0.2", actual["version"])
        self.assertTrue(created["has_expert"])
        self.assertEqual("ex_Dynamic12345", created["expert_id"])
        self.assertEqual("斗音音", created["expert_name"])
        self.assertEqual("ex_Dynamic12345",
                         captured["meta"]["codebuddy.ai"]["expertId"])
        growth = json.loads(
            captured["meta"]["codebuddy.ai"]["growthEvent"])
        self.assertEqual("斗音音", growth[0]["extra"]["name"])
        self.assertEqual(
            "抖音策略师", growth[0]["extra"]["expertTitle"])
        self.assertIn("completed 1/1", " ".join(logs))

    def test_dynamic_expert_family_can_resolve_id_from_unique_market_title(self):
        captured = {}
        states = [("accepted", 0, 1), ("completed", 1, 1), ("completed", 1, 1)]

        def report_desktop_events(session, uid, nick, events):
            captured["events"] = events
            return 200

        module = load_functions(
            "_run_local_expert_task",
            re=re, uuid=uuid, json=json,
            time=type("Time", (), {
                "time": staticmethod(time.time),
                "sleep": staticmethod(lambda seconds: None),
            }),
            _EXPERT_ID_RE=re.compile(r"^ex_[A-Za-z0-9]{6,64}$"),
            prog=lambda session, code: states.pop(0),
            _expert_prog=lambda session, code: states.pop(0),
            _market_expert_by_id=lambda session, keyword, expected_id: {
                "id": "ex_FutureExpert1",
                "name": "未来专家昵称",
                "profession": keyword,
                "version": "2.0.0",
            },
            get_normal_experts=lambda count: [],
            webchat2=lambda session, name, prompt, meta:
                ("conv-future", "OK", "cmb-0123456789abcdef0123456789abcdef"),
            desktop_chat_sequence=lambda uid, nick, conv, req, msg: [{
                "eventCode": "agent_task_created",
                "conversationId": conv,
                "requestId": req,
            }],
            report_desktop_events=report_desktop_events,
            report=lambda *args, **kwargs: 200,
            _new_message_id=lambda: "fallback-message",
            _new_conversation_id=lambda: "fallback-conversation",
        )
        module["_run_local_expert_task"](
            object(), "uid-1", "nick", lambda message: None,
            "expert_use_future",
            {"id": "", "name": "", "profession": "未来职业",
             "version": "", "slug": "future"},
        )
        actual = [event for event in captured["events"]
                  if event.get("eventCode") == "expert_actual_use"][-1]
        self.assertEqual("ex_FutureExpert1", actual["id"])
        self.assertEqual("未来专家昵称", actual["name"])
        self.assertEqual("未来职业", actual["expertTitle"])
        self.assertEqual("2.0.0", actual["version"])
        self.assertEqual("LOCAL", actual["mode"])

    def test_local_expert_flow_accepts_not_accepted_task_before_chat(self):
        """运行中才解锁的专家任务会停在 not_accepted，必须先补 accept。"""
        states = [("not_accepted", None, None), ("completed", 1, 1),
                  ("completed", 1, 1)]
        accepted = []
        captured = {}

        def webchat2(session, name, prompt, meta):
            captured["chat"] = (name, prompt, meta)
            return "conv-accepted", "OK", "cmb-0123456789abcdef0123456789abcdef"

        module = load_functions(
            "_run_local_expert_task",
            re=re, uuid=uuid, json=json,
            time=type("Time", (), {
                "time": staticmethod(time.time),
                "sleep": staticmethod(lambda seconds: None),
            }),
            _EXPERT_ID_RE=re.compile(r"^ex_[A-Za-z0-9]{6,64}$"),
            prog=lambda session, code: states.pop(0),
            _expert_prog=lambda session, code: states.pop(0),
            _accept_expert_task=lambda session, code, log: (
                accepted.append(code) or True),
            _market_expert_by_id=lambda session, keyword, expected_id: {
                "name": "斗音音",
                "profession": "抖音策略师",
                "version": "1.0.2",
            },
            get_normal_experts=lambda count: [],
            webchat2=webchat2,
            desktop_chat_sequence=lambda uid, nick, conv, req, msg: [],
            report_desktop_events=lambda session, uid, nick, events: 200,
            report=lambda *args, **kwargs: 200,
            _new_message_id=lambda: "fallback-message",
            _new_conversation_id=lambda: "fallback-conversation",
        )
        logs = []
        module["_run_local_expert_task"](
            object(), "uid-1", "nick", logs.append,
            "expert_use_douyinyin",
            {"id": "ex_Dynamic12345", "name": "斗音音",
             "profession": "抖音策略师", "version": "1.0.2",
             "slug": "douyinyin"},
            conv_name="expert-douyinyin",
        )
        self.assertEqual(["expert_use_douyinyin"], accepted)
        self.assertIn("chat", captured)
        self.assertIn("completed 1/1", " ".join(logs))

    def test_expert_prog_falls_back_to_mp_when_desktop_list_omits_task(self):
        """UI 能看到、桌面列表没下发时，专家任务必须回落 MP 口径。"""
        module = load_functions(
            "_expert_prog",
            prog=lambda session, code: (None, None, None),
            _mp_prog=lambda session, code: ("not_accepted", 0, 1),
        )
        self.assertEqual(
            ("not_accepted", 0, 1),
            module["_expert_prog"](object(), "expert_use_douyinyin"))

    def test_expert_use_tasks_claim_on_desktop_even_when_mp_rows_exist(self):
        codes = {
            "expert_use_maidehao",
            "expert_use_douyinyin",
            "expert_use_shangjia_xuanchuan",
            "expert_use_bowangwang",
        }
        desktop_tasks = {
            "data": {"tasks": [{
                "task_code": code,
                "accept_status": "accepted",
                "progress": {"current": 1, "target": 1},
            } for code in sorted(codes)]},
        }
        mp_tasks = {
            "data": {"tasks": [{
                "task_code": code,
                "accept_status": "accepted",
                "progress": {"current": 1, "target": 1},
            } for code in sorted(codes)]},
        }

        class Session:
            def __init__(self):
                self.posts = []

            def get(self, url, **kwargs):
                return FakeResponse(
                    payload=mp_tasks if kwargs.get("headers") else desktop_tasks)

            def post(self, url, **kwargs):
                self.posts.append((url, kwargs.get("headers")))
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"credit": 100, "energy": 5},
                })

        module = load_functions(
            "_api_code_ok", "_claim_response_ok", "claim", "_mp_claim",
            "claim_ready_tasks",
            _as_int=lambda value, default=0: int(value or default),
            BASE="https://example.test",
            MP_HEADER={"X-Client-Platform": "miniprogram"},
            MP_TASK_CODES=set(),
            DESKTOP_ONLY_TASK_CODES=frozenset(codes),
            time=type("Time", (), {"sleep": staticmethod(lambda seconds: None)}),
        )
        session = Session()
        logs = []
        module["claim_ready_tasks"](session, logs.append)

        claimed = {url.rsplit("/", 2)[-2] for url, _ in session.posts}
        self.assertEqual(codes, claimed)
        self.assertTrue(all(headers is None for _, headers in session.posts))
        self.assertIn("达标补领完成: 4 项", " ".join(logs))

    def test_mp_only_expert_task_claims_with_mp_headers(self):
        """专家任务只在 MP 列表出现时，claim 也必须走 MP 口径。"""
        mp_tasks = {
            "data": {"tasks": [{
                "task_code": "expert_use_douyinyin",
                "accept_status": "accepted",
                "progress": {"current": 1, "target": 1},
            }]},
        }
        desktop_tasks = {"data": {"tasks": []}}

        class Session:
            def __init__(self):
                self.posts = []

            def get(self, url, **kwargs):
                return FakeResponse(
                    payload=mp_tasks if kwargs.get("headers") else desktop_tasks)

            def post(self, url, **kwargs):
                self.posts.append((url, kwargs.get("headers")))
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"credit": 50, "energy": 5},
                })

        module = load_functions(
            "_api_code_ok", "_claim_response_ok", "claim", "_mp_claim",
            "claim_ready_tasks",
            _as_int=lambda value, default=0: int(value or default),
            BASE="https://example.test",
            MP_HEADER={"X-Client-Platform": "miniprogram"},
            MP_TASK_CODES=set(),
            DESKTOP_ONLY_TASK_CODES=frozenset({
                "expert_use_maidehao", "expert_use_douyinyin",
                "expert_use_shangjia_xuanchuan", "expert_use_bowangwang",
            }),
            time=type("Time", (), {"sleep": staticmethod(lambda seconds: None)}),
        )
        session = Session()
        logs = []
        module["claim_ready_tasks"](session, logs.append)

        self.assertEqual(1, len(session.posts))
        url, headers = session.posts[0]
        self.assertIn("expert_use_douyinyin", url)
        self.assertEqual({"X-Client-Platform": "miniprogram"}, headers)
        self.assertIn("达标补领完成: 1 项", " ".join(logs))

    def test_desktop_fingerprint_matches_cn_current_shape(self):
        module = load_functions(
            "derive_id", "desktop_fingerprint",
            hashlib=hashlib, time=time,
            DESKTOP_RELEASE_CN=1789632000000,
            DESKTOP_COMMIT_CN="c41253385fa07bebfcae8a7a4728914f09af96d0",
            DESKTOP_VERSION_CN="5.9.3",
        )
        fp = module["desktop_fingerprint"]("uid-1", "nick")
        self.assertEqual("5.9.3", fp["ideVersion"])
        self.assertEqual("5.9.3", fp["extVersion"])
        self.assertEqual("10.0.28000", fp["osVersion"])
        self.assertEqual(8, fp["cpuCores"])
        self.assertEqual(16, fp["memorySize"])
        self.assertRegex(fp["qimei36"], r"^[0-9a-f]{36}$")

    def test_report_desktop_headers_use_host_domain_and_message_id(self):
        captured = {}

        class Response:
            status_code = 200

        class Session:
            trust_env = True

            def post(self, url, **kwargs):
                captured["url"] = url
                captured["headers"] = kwargs["headers"]
                captured["json"] = kwargs["json"]
                return Response()

        requests_module = type("Requests", (), {"Session": Session})
        module = load_functions(
            "_new_message_id", "derive_id", "desktop_fingerprint",
            "report_desktop_events",
            hashlib=hashlib, time=time, uuid=uuid, requests=requests_module,
            DESKTOP_RELEASE_CN=1789632000000,
            DESKTOP_COMMIT_CN="c41253385fa07bebfcae8a7a4728914f09af96d0",
            DESKTOP_VERSION_CN="5.9.3",
            DESKTOP_UA="WorkBuddy/5.9.3 WorkBuddy/5.9.3 CLI/2.165.0",
            DESKTOP_BASE="https://copilot.tencent.com",
        )
        source = type("Source", (), {
            "headers": {"Authorization": "Bearer test"},
        })()
        status = module["report_desktop_events"](
            source, "uid-1", "nick", [{"eventCode": "chat_request_send"}])
        self.assertEqual(200, status)
        self.assertEqual("https://copilot.tencent.com/v2/report", captured["url"])
        self.assertEqual("copilot.tencent.com", captured["headers"]["X-Domain"])
        self.assertRegex(captured["headers"]["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_heatmap_today_score_reads_server_date(self):
        class Session:
            def get(self, url, **kwargs):
                return FakeResponse(payload={
                    "code": 0,
                    "data": {
                        "today": {"date": "2026-10-22"},
                        "cells": [
                            {"date": "2026-10-21", "score": 4},
                            {"date": "2026-10-22", "score": 12},
                        ],
                    },
                })

        module = load_functions(
            "_heatmap_today_score",
            BASE="https://example.test",
            beijing_today=lambda: __import__("datetime").date(2026, 10, 22),
        )
        self.assertEqual(12, module["_heatmap_today_score"](Session()))

    def test_daily_activity_requires_heatmap_and_uses_chat_fallback(self):
        calls = {"chat": 0}
        scores = [0, 12]

        def heatmap_score(session):
            return scores.pop(0)

        def webchat2(session, kind, prompt):
            calls["chat"] += 1
            return "conversation-id", "OK", "message-id"

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "activity-state.json")
            old_state = os.environ.get("WORKBUDDY_ACTIVITY_STATE")
            os.environ["WORKBUDDY_ACTIVITY_STATE"] = state_path
            try:
                module = load_functions(
                    "_new_conversation_id", "_new_message_id",
                    "t_daily_activity",
                    os=os, json=json, time=time, uuid=uuid,
                    CBDOMAIN="https://www.codebuddy.cn",
                    WORKBUDDY_UA_CN="WorkBuddy/5.9.3 WorkBuddy/5.9.3 CLI/2.165.0",
                    beijing_today=lambda: __import__("datetime").date(2026, 10, 22),
                    _env_enabled=lambda name, default=True: True,
                    _heatmap_today_score=heatmap_score,
                    report=lambda *args, **kwargs: 200,
                    webchat2=webchat2,
                )
                logs = []
                session = type("Session", (), {
                    "get": lambda self, url, **kwargs: FakeResponse(payload={
                        "data": {"streak": {"days": 1}},
                    }),
                })()
                module["t_daily_activity"](session, "uid-1", "nick", logs.append)
            finally:
                if old_state is None:
                    os.environ.pop("WORKBUDDY_ACTIVITY_STATE", None)
                else:
                    os.environ["WORKBUDDY_ACTIVITY_STATE"] = old_state

            self.assertEqual(1, calls["chat"])
            self.assertTrue(Path(state_path).exists())
            self.assertIn("真实对话兜底成功", " ".join(logs))

    def test_daily_activity_does_not_persist_unconfirmed_score(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "activity-state.json")
            old_state = os.environ.get("WORKBUDDY_ACTIVITY_STATE")
            os.environ["WORKBUDDY_ACTIVITY_STATE"] = state_path
            try:
                fake_time = type("Time", (), {
                    "time": staticmethod(time.time),
                    "sleep": staticmethod(lambda seconds: None),
                })
                module = load_functions(
                    "_new_conversation_id", "_new_message_id",
                    "t_daily_activity",
                    os=os, json=json, time=fake_time, uuid=uuid,
                    CBDOMAIN="https://www.codebuddy.cn",
                    WORKBUDDY_UA_CN="WorkBuddy/5.9.3 WorkBuddy/5.9.3 CLI/2.165.0",
                    beijing_today=lambda: __import__("datetime").date(2026, 10, 22),
                    _env_enabled=lambda name, default=True: True,
                    _heatmap_today_score=lambda session: 0,
                    report=lambda *args, **kwargs: 200,
                    webchat2=lambda *args: ("conv", "OK", "msg"),
                )
                logs = []
                session = type("Session", (), {})()
                module["t_daily_activity"](session, "uid-2", "nick", logs.append)
            finally:
                if old_state is None:
                    os.environ.pop("WORKBUDDY_ACTIVITY_STATE", None)
                else:
                    os.environ["WORKBUDDY_ACTIVITY_STATE"] = old_state

            self.assertFalse(Path(state_path).exists())
            self.assertIn("下轮继续重试", " ".join(logs))

    def test_serverchan_sctp_url_and_title_normalization(self):
        posted = {}

        class Response:
            def json(self):
                return {"code": 0}

        class Session:
            trust_env = True

            def post(self, url, **kwargs):
                posted["url"] = url
                posted["data"] = kwargs["data"]
                return Response()

        requests_module = type("Requests", (), {"Session": Session})
        os.environ["SERVERCHAN_SENDKEY"] = "sctp123tabcdef"
        try:
            module = load_functions(
                "_serverchan_notify",
                os=os, re=re, requests=requests_module,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                ok = module["_serverchan_notify"]("标题\n第二行", "正文")
        finally:
            os.environ.pop("SERVERCHAN_SENDKEY", None)

        self.assertTrue(ok)
        self.assertEqual(
            "https://123.push.ft07.com/send/sctp123tabcdef.send",
            posted["url"],
        )
        self.assertEqual("标题 第二行", posted["data"]["title"])

    def test_state_dir_env_overrides_default_locations(self):
        """WORKBUDDY_STATE_DIR 决定令牌库/AT文本/补偿防重的落点；不设则回退脚本目录。"""
        # _state_dir() 依赖模块的 __file__，这里用完整模块命名空间执行，
        # 不能用 load_functions 只抽函数体（那样 __file__ 不存在）。
        namespace = {"__builtins__": __builtins__, "__file__": str(SOURCE_PATH),
                     "__name__": "wb_state_dir_probe", "os": os}
        module_node = ast.Module(body=[FUNCTIONS["_state_dir"]], type_ignores=[])
        exec(compile(module_node, str(SOURCE_PATH), "exec"), namespace)
        state_dir = namespace["_state_dir"]

        previous = os.environ.pop("WORKBUDDY_STATE_DIR", None)
        try:
            default_dir = state_dir()
            self.assertEqual(str(SOURCE_PATH.parent), default_dir)

            with tempfile.TemporaryDirectory() as temp_dir:
                custom = os.path.join(temp_dir, "state")
                os.environ["WORKBUDDY_STATE_DIR"] = custom
                self.assertEqual(custom, state_dir())
                # 目录不存在时应自动创建，否则写入阶段会失败
                self.assertTrue(os.path.isdir(custom))

                # 空白值视同未设置，不能变成空目录名
                os.environ["WORKBUDDY_STATE_DIR"] = "   "
                self.assertEqual(str(SOURCE_PATH.parent), state_dir())
        finally:
            os.environ.pop("WORKBUDDY_STATE_DIR", None)
            if previous is not None:
                os.environ["WORKBUDDY_STATE_DIR"] = previous

    def test_state_files_follow_state_dir(self):
        """三个状态常量必须都跟着 WORKBUDDY_STATE_DIR 走，不能漏一个。"""
        for name in ("REFRESH_STORE", "TOKEN_FILE", "BENEFIT_STATE_FILE"):
            for node in TREE.body:
                if not isinstance(node, ast.Assign):
                    continue
                if not any(isinstance(t, ast.Name) and t.id == name
                           for t in node.targets):
                    continue
                source = ast.get_source_segment(SOURCE, node.value) or ""
                self.assertIn("STATE_DIR", source,
                              "%s 未使用 STATE_DIR：%s" % (name, source))
                break
            else:
                self.fail("未找到状态常量 %s" % name)


    def test_notify_policy_always_is_default(self):
        """不设 WORKBUDDY_NOTIFY 时必须每次推送，保持原行为。"""
        sent = []
        module = load_functions(
            "_notify_or_skip", "_notify_ok",
            os=os, print=lambda *a, **k: None,
            send_notify_all=lambda t, c: sent.append(t),
            build_summary=lambda s: "summary",
        )
        previous = os.environ.pop("WORKBUDDY_NOTIFY", None)
        try:
            module["_notify_or_skip"]([{"idx": 1, "checked_in": True}])
            self.assertEqual(1, len(sent))
            module["_notify_or_skip"]([{"idx": 1, "checked_in": False}])
            self.assertEqual(2, len(sent))
        finally:
            os.environ.pop("WORKBUDDY_NOTIFY", None)
            if previous is not None:
                os.environ["WORKBUDDY_NOTIFY"] = previous

    def test_notify_policy_fail_only_pushes_on_trouble(self):
        """fail 策略：全部签到成功则不推；任一账号未确认或异常中断则推。"""
        sent = []
        module = load_functions(
            "_notify_or_skip", "_notify_ok",
            os=os, print=lambda *a, **k: None,
            send_notify_all=lambda t, c: sent.append(t),
            build_summary=lambda s: "summary",
        )
        try:
            os.environ["WORKBUDDY_NOTIFY"] = "fail"
            # 全部成功 -> 静默
            module["_notify_or_skip"]([{"idx": 1, "checked_in": True},
                                       {"idx": 2, "checked_in": True}])
            self.assertEqual([], sent)
            # 有账号签到未确认 -> 推
            module["_notify_or_skip"]([{"idx": 1, "checked_in": True},
                                       {"idx": 2, "checked_in": False}])
            self.assertEqual(1, len(sent))
            # 账号异常中断（summary 无 checked_in 字段）-> 也要推
            module["_notify_or_skip"]([{"idx": 1, "checked_in": True},
                                       {"idx": 2, "done": 0, "rest": ["异常中断"]}])
            self.assertEqual(2, len(sent))
        finally:
            os.environ.pop("WORKBUDDY_NOTIFY", None)

    def test_notify_policy_zero_disables_push(self):
        """WORKBUDDY_NOTIFY=0 时完全不推，即使有异常。"""
        sent = []
        module = load_functions(
            "_notify_or_skip", "_notify_ok",
            os=os, print=lambda *a, **k: None,
            send_notify_all=lambda t, c: sent.append(t),
            build_summary=lambda s: "summary",
        )
        try:
            for value in ("0", "off", "none", "false"):
                os.environ["WORKBUDDY_NOTIFY"] = value
                module["_notify_or_skip"]([{"idx": 1, "checked_in": False}])
            self.assertEqual([], sent)
        finally:
            os.environ.pop("WORKBUDDY_NOTIFY", None)

    def test_sign_reports_checked_in_through_status_out(self):
        """t_sign 的成功分支必须回传 checked_in，供推送策略判断。"""
        class Response:
            status_code = 200

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class Session:
            def post(self, url, **kwargs):
                return Response({"code": 0, "data": {"today_checked_in": True}})

        module = load_functions(
            "t_sign",
            BASE="https://example.test",
            api_retry=lambda s, method, url, **kw: s.post(url),
            is_enterprise_denial=lambda code, payload: False,
            _api_code_ok=lambda code: True,
            _checkin_tail=lambda s: "",
            t_daily_activity=lambda *a: None,
            _checkin_status=lambda s: {"today_checked_in": True},
            _sign_retry_record=lambda uid, now=None: (None, False),
            time=time,
        )
        state = {}
        with contextlib.redirect_stdout(io.StringIO()):
            module["t_sign"](Session(), "uid", "nick", lambda m: None, state)
        self.assertTrue(state.get("checked_in"))


class SignRetryStateMachineTests(unittest.TestCase):
    def _sign_module(self, temp_dir, now=1_000):
        fake_time = types.SimpleNamespace(
            time=lambda: now,
            strftime=time.strftime,
            gmtime=time.gmtime,
        )
        return load_functions(
            "_as_int",
            "_sign_retry_path",
            "_sign_retry_key",
            "_sign_retry_load",
            "_sign_retry_save",
            "_sign_retry_record",
            "_schedule_sign_retry",
            "_sign_retry_clear",
            "_is_retryable_checkin_failure",
            "t_sign",
            "run_sign_retry_only",
            os=os,
            json=json,
            hashlib=hashlib,
            math=math,
            time=fake_time,
            STATE_DIR=str(temp_dir),
            SIGN_RETRY_FILE=str(Path(temp_dir) / "workbuddy_sign_retry.json"),
            SIGN_RETRY_DELAYS=(10 * 60, 40 * 60),
            beijing_today=lambda: __import__("datetime").date(2026, 11, 11),
            BASE="https://example.test",
            _api_code_ok=lambda code: code in (0, 200, "0", "200"),
            is_enterprise_denial=lambda status, payload: False,
            _checkin_tail=lambda session: "",
            _checkin_status=lambda session: {"today_checked_in": False},
            t_daily_activity=lambda *args: None,
        )

    def test_exact_transient_sign_failure_predicate(self):
        module = load_functions(
            "_as_int", "_is_retryable_checkin_failure", time=time)
        check = module["_is_retryable_checkin_failure"]
        self.assertTrue(check(500, {
            "code": 10001, "msg": "签到失败，请稍后重试"}))
        self.assertTrue(check("500", {
            "code": "10001", "message": "请求处理中，请稍后重试"}))
        self.assertFalse(check(400, {
            "code": 10001, "msg": "企业账号不支持该操作"}))
        self.assertFalse(check(429, {
            "code": 10001, "msg": "请求处理中，请稍后重试"}))
        self.assertFalse(check(500, {
            "code": 10000, "msg": "签到失败，请稍后重试"}))
        self.assertFalse(check(500, {
            "code": 10001, "msg": "其他业务错误"}))

    def test_retry_schedule_uses_first_failure_offsets_and_expires_after_two_retries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module = self._sign_module(temp_dir)
            first, exhausted = module["_schedule_sign_retry"]("uid-secret", now=1_000)
            self.assertFalse(exhausted)
            self.assertEqual(1, first["round"])
            self.assertEqual(1_600, first["next_at"])

            second, exhausted = module["_schedule_sign_retry"]("uid-secret", now=1_600)
            self.assertFalse(exhausted)
            self.assertEqual(2, second["round"])
            self.assertEqual(3_400, second["next_at"])

            third, exhausted = module["_schedule_sign_retry"]("uid-secret", now=3_400)
            self.assertIsNone(third)
            self.assertTrue(exhausted)
            state_path = Path(temp_dir) / "workbuddy_sign_retry.json"
            self.assertEqual({}, json.loads(state_path.read_text(encoding="utf-8")))
            self.assertNotIn("uid-secret", state_path.read_text(encoding="utf-8"))

    def test_not_due_retry_cron_performs_zero_upstream_requests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module = self._sign_module(temp_dir, now=1_000)
            module["_schedule_sign_retry"]("uid-1", now=1_000)
            calls = []
            module["api_retry"] = lambda *args, **kwargs: calls.append(args)
            state = {}
            logs = []
            module["t_sign"](
                object(), "uid-1", "nick", logs.append, state, retry_only=True)
            self.assertEqual([], calls)
            self.assertTrue(state.get("pending_retry"))
            self.assertEqual(1_600, state.get("retry_at"))
            self.assertIn("已排队", " ".join(logs))

    def test_retry_state_is_isolated_per_uid_and_expires_across_day(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module = self._sign_module(temp_dir, now=1_000)
            module["_schedule_sign_retry"]("uid-1", now=1_000)
            module["_schedule_sign_retry"]("uid-2", now=1_000)
            state_path = Path(temp_dir) / "workbuddy_sign_retry.json"
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(2, len(saved))
            self.assertNotIn("uid-1", state_path.read_text(encoding="utf-8"))

            module["beijing_today"] = lambda: __import__("datetime").date(2026, 11, 12)
            record, due = module["_sign_retry_record"]("uid-1", now=1_600)
            self.assertIsNone(record)
            self.assertFalse(due)

    def test_due_retry_checks_status_and_clears_when_already_signed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module = self._sign_module(temp_dir, now=1_600)
            module["_schedule_sign_retry"]("uid-1", now=1_000)
            calls = []

            def api_retry(session, method, url, **kwargs):
                calls.append(url)
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"active": True, "today_checked_in": True},
                })

            module["api_retry"] = api_retry
            state = {}
            module["t_sign"](
                object(), "uid-1", "nick", lambda message: None,
                state, retry_only=True)
            self.assertEqual(1, len(calls))
            self.assertTrue(calls[0].endswith("checkin-activity-status"))
            self.assertTrue(state.get("checked_in"))
            state_path = Path(temp_dir) / "workbuddy_sign_retry.json"
            self.assertEqual({}, json.loads(state_path.read_text(encoding="utf-8")))

    def test_exact_initial_500_10001_schedules_retry_but_other_errors_do_not(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module = self._sign_module(temp_dir, now=1_000)
            responses = [
                FakeResponse(payload={
                    "code": 0,
                    "data": {"active": True, "today_checked_in": False},
                }),
                FakeResponse(500, {
                    "code": 10001, "msg": "签到失败，请稍后重试"}),
            ]
            module["api_retry"] = lambda *args, **kwargs: responses.pop(0)
            state = {}
            logs = []
            module["t_sign"](
                object(), "uid-1", "nick", logs.append, state)
            self.assertTrue(state.get("pending_retry"))
            self.assertEqual(1, state.get("retry_round"))
            self.assertEqual(1_600, state.get("retry_at"))
            self.assertIn("已排第 1 轮补签", " ".join(logs))

    def test_retry_state_write_failure_is_reported_not_faked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module = self._sign_module(temp_dir, now=1_000)
            responses = [
                FakeResponse(payload={
                    "code": 0,
                    "data": {"active": True, "today_checked_in": False},
                }),
                FakeResponse(500, {
                    "code": 10001, "msg": "签到失败，请稍后重试"}),
            ]
            module["api_retry"] = lambda *args, **kwargs: responses.pop(0)
            module["_sign_retry_save"] = mock.Mock(side_effect=OSError("disk full"))
            state = {}
            logs = []
            module["t_sign"](
                object(), "uid-1", "nick", logs.append, state)
            self.assertFalse(state.get("pending_retry"))
            self.assertIn("补签状态落盘失败", " ".join(logs))

    def test_due_retry_status_exception_advances_to_next_scheduled_round(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module = self._sign_module(temp_dir, now=1_600)
            module["_schedule_sign_retry"]("uid-1", now=1_000)

            def fail(*args, **kwargs):
                raise RuntimeError("network down")

            module["api_retry"] = fail
            state = {}
            module["t_sign"](
                object(), "uid-1", "nick", lambda message: None,
                state, retry_only=True)
            self.assertTrue(state.get("pending_retry"))
            self.assertEqual(2, state.get("retry_round"))
            self.assertEqual(3_400, state.get("retry_at"))
            saved = json.loads((Path(temp_dir) / "workbuddy_sign_retry.json")
                               .read_text(encoding="utf-8"))
            self.assertEqual(1, len(saved))
            self.assertEqual(2, next(iter(saved.values()))["round"])

    def test_due_retry_status_exception_on_second_round_exhausts_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module = self._sign_module(temp_dir, now=3_400)
            module["_schedule_sign_retry"]("uid-1", now=1_000)
            module["_schedule_sign_retry"]("uid-1", now=1_600)

            def fail(*args, **kwargs):
                raise RuntimeError("network down")

            module["api_retry"] = fail
            state = {}
            module["t_sign"](
                object(), "uid-1", "nick", lambda message: None,
                state, retry_only=True)
            self.assertTrue(state.get("retry_exhausted"))
            self.assertFalse(state.get("pending_retry"))
            self.assertEqual({}, json.loads(
                (Path(temp_dir) / "workbuddy_sign_retry.json")
                .read_text(encoding="utf-8")))

    def test_due_retry_non_success_status_response_clears_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module = self._sign_module(temp_dir, now=1_600)
            module["_schedule_sign_retry"]("uid-1", now=1_000)
            module["api_retry"] = lambda *args, **kwargs: FakeResponse(
                403, {"code": 0, "data": {}})
            module["t_sign"](
                object(), "uid-1", "nick", lambda message: None, {},
                retry_only=True)
            self.assertEqual({}, json.loads(
                (Path(temp_dir) / "workbuddy_sign_retry.json")
                .read_text(encoding="utf-8")))

    def test_retry_only_runner_never_calls_full_account_flow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module = self._sign_module(temp_dir, now=1_600)
            calls = []
            module["ACCOUNTS"] = [
                {"access_token": "token-1", "note": "account-1"},
                {"access_token": "token-2", "note": "account-2"},
            ]
            module["uid_of"] = lambda token: token
            module["nickname_of"] = lambda token: token
            module["new_api"] = lambda token: ("session", token)
            module["_sign_retry_record"] = lambda uid, now=None: (
                ({"day": "2026-11-11", "round": 1, "next_at": 1_600}, True)
                if uid == "token-1" else (None, False)
            )
            module["t_sign"] = (
                lambda session, uid, nick, log, state, retry_only=False:
                calls.append((session, uid, retry_only)))
            with contextlib.redirect_stdout(io.StringIO()):
                module["run_sign_retry_only"]()
            self.assertEqual([(("session", "token-1"), "token-1", True)], calls)


class TaskDispatchGuardTests(unittest.TestCase):
    """prog() 对未下发任务返回 (None, None, None)：不能当成「未完成」继续发真实请求。

    issue #39：Model_chat_GLM5.2 / create_canvas / Buddy_App_QQ 已不再下发，
    旧判断 `st not in ("completed", "claimed")` 对 None 恒为真，导致每轮白扣积分。
    """

    def test_chat_task_skips_when_not_dispatched(self):
        webchats = []
        logs = []
        module = load_functions(
            "t_chat_n", time=time,
            prog=lambda s, code: (None, None, None),
            webchat=lambda *a, **k: webchats.append(a) or ("cid", "txt"),
            chat_request_events=lambda *a, **k: ([], None),
            report=lambda *a, **k: None,
        )
        module["t_chat_n"](object(), "uid", "nick", logs.append,
                           "Model_chat_GLM5.2", 1, ["你好"])
        self.assertEqual([], webchats)
        self.assertTrue(any("未下发" in line for line in logs))

    def test_chat_task_still_runs_when_dispatched(self):
        webchats = []
        module = load_functions(
            "t_chat_n", time=time,
            prog=lambda s, code: ("", 0, 3),
            webchat=lambda *a, **k: webchats.append(a) or ("cid", "txt"),
            chat_request_events=lambda *a, **k: ([], None),
            report=lambda *a, **k: None,
        )
        module["t_chat_n"](object(), "uid", "nick", lambda m: None, "chat_5", 1, ["你好"])
        self.assertEqual(1, len(webchats))

    def test_canvas_tasks_skip_when_not_dispatched(self):
        desktops, reports = [], []
        logs = []
        module = load_functions(
            "t_canvas_automation", time=time,
            prog=lambda s, code: (None, None, None),
            _desktop_run=lambda *a, **k: desktops.append(a) or True,
            report=lambda *a, **k: reports.append(a),
            PLAYBOOK_CASE={"id": "x", "name": "n", "type": "t"},
            uuid=uuid,
        )
        module["t_canvas_automation"](object(), "uid", "nick", logs.append)
        self.assertEqual([], desktops)
        self.assertEqual([], reports)
        self.assertEqual(3, len([line for line in logs if "未下发" in line]))

    def test_buddy_apps_skip_when_not_dispatched(self):
        sequences, reports = [], []
        module = load_functions(
            "t_buddy_apps", time=time,
            prog=lambda s, code: (None, None, None),
            desktop_buddy5_sequence=lambda *a, **k: sequences.append(a) or [],
            report_desktop_events=lambda *a, **k: reports.append(a),
            QQ_TPL="qq-tpl", WRITE_GAP=0,
        )
        module["t_buddy_apps"](object(), "uid", "nick", lambda m: None)
        self.assertEqual([], sequences)
        self.assertEqual([], reports)

    def test_team_task_skips_when_not_dispatched(self):
        chats, reports, logs = [], [], []
        module = load_functions(
            "t_team_3", sys=sys, os=os, uuid=uuid, json=json,
            __file__=str(SOURCE_PATH),
            time=types.SimpleNamespace(sleep=lambda _seconds: None),
            get_team_experts=lambda _limit: [{"id": "team-1", "name": "团队"}],
            prog=lambda _session, _code: (None, None, None),
            webchat=lambda *args, **kwargs: chats.append(args) or ("cid", "txt"),
            report=lambda *args, **kwargs: reports.append(args),
        )
        module["t_team_3"](object(), "uid", "nick", logs.append)
        self.assertEqual([], chats)
        self.assertEqual([], reports)
        self.assertTrue(any("Expert_team_use_3" in line and "未下发" in line for line in logs))

    def test_expert_task_skips_when_not_dispatched(self):
        reports, logs = [], []
        expert_lookups = []
        module = load_functions(
            "t_expert_5", sys=sys, os=os, uuid=uuid,
            __file__=str(SOURCE_PATH),
            time=types.SimpleNamespace(sleep=lambda _seconds: None),
            get_normal_experts=lambda limit: (
                expert_lookups.append(limit) or [{"id": "expert-1", "name": "专家"}]),
            prog=lambda _session, _code: (None, None, None),
            report=lambda *args, **kwargs: reports.append(args),
        )
        module["t_expert_5"](object(), "uid", "nick", logs.append)
        self.assertEqual([], reports)
        self.assertEqual([], expert_lookups)
        self.assertTrue(any("expert_5" in line and "未下发" in line for line in logs))

    def test_template_task_skips_when_not_dispatched(self):
        reports, logs = [], []
        module = load_functions(
            "t_template_5", uuid=uuid,
            time=types.SimpleNamespace(sleep=lambda _seconds: None),
            _fetch_scenes=lambda _session: (_ for _ in ()).throw(
                AssertionError("未下发任务不应请求场景列表")),
            prog=lambda _session, _code: (None, None, None),
            report=lambda *args, **kwargs: reports.append(args),
            _new_message_id=lambda: "request-id",
        )
        module["t_template_5"](object(), "uid", "nick", logs.append)
        self.assertEqual([], reports)
        self.assertTrue(any("template_5" in line and "未下发" in line for line in logs))

    def test_expert_task_still_runs_when_dispatched(self):
        reports = []
        module = load_functions(
            "t_expert_5", sys=sys, os=os, uuid=uuid,
            __file__=str(SOURCE_PATH),
            time=types.SimpleNamespace(sleep=lambda _seconds: None),
            get_normal_experts=lambda _limit: [{"id": "expert-1", "name": "专家"}],
            prog=lambda _session, _code: ("accepted", 4, 5),
            report=lambda *args, **kwargs: reports.append(args),
            _new_message_id=lambda: "request-id",
            _new_conversation_id=lambda: "conversation-id",
        )
        module["t_expert_5"](object(), "uid", "nick", lambda _message: None)
        self.assertEqual(1, len(reports))

    def test_build_summary_shows_credit_before_after_and_delta(self):
        module = load_functions(
            "build_summary", "task_cn", "mask_phone", "_clean_num",
            TASK_NAME_CN={}, time=time,
        )
        text = module["build_summary"]([{
            "idx": 1, "note": "13800138000", "credits": "旧文案",
            "credits_before": 100, "credits_after": 150,
            "usage": "75类资源", "level": 17, "checkin_streak": 31,
            "streak": 30, "energy": 15, "done": 1, "total": 2, "rest": [],
        }])
        self.assertIn("积分: 100 → 150（本轮 +50）", text)


class WebReportAndBuddyAppTests(unittest.TestCase):
    def test_report_web_event_posts_bare_array(self):
        captured = {}

        class Session:
            def post(self, url, **kwargs):
                captured["url"] = url
                captured["json"] = kwargs.get("json")
                return FakeResponse(payload={"code": 0, "msg": "OK"})

        module = load_functions(
            "report_web_event",
            api_retry=lambda session, method, url, body=None, **kwargs: (
                session.post(url, json=body, **kwargs)),
            BASE="https://example.test",
            derive_id=lambda uid, salt: "stable-id",
            time=time,
        )
        response = module["report_web_event"](
            Session(), "uid-1", "nick", "web_element_click",
            "https://example.test/doc", "element-1", "资料库",
        )

        self.assertEqual(200, response.status_code)
        self.assertIsInstance(captured["json"], list)
        self.assertEqual("web_element_click", captured["json"][0]["eventCode"])

    def test_buddy_enter_click_uses_boolean_is_first_page(self):
        module = load_functions("desktop_buddy5_sequence")
        events = module["desktop_buddy5_sequence"](
            "uid-1", "nick", "buddy-1", "Buddy")
        enter = next(
            event for event in events
            if event["eventCode"] == "buddyapp_enter_click"
        )

        self.assertIs(True, enter["isFirstPage"])

    def test_buddy_app_claims_only_after_completed_readback(self):
        claims = []
        calls = {"buddy": 0}

        def prog(session, code):
            if code != "Buddy_App":
                return None, None, None
            calls["buddy"] += 1
            if calls["buddy"] == 1:
                return "accepted", 0, 1
            return "completed", 1, 1

        module = load_functions(
            "t_buddy_apps",
            prog=prog,
            desktop_buddy5_sequence=lambda *args: [],
            report_desktop_events=lambda *args: 200,
            claim=lambda session, code, log: claims.append(code),
            time=types.SimpleNamespace(sleep=lambda seconds: None),
        )
        logs = []
        module["t_buddy_apps"](object(), "uid-1", "nick", logs.append)

        self.assertEqual(["Buddy_App"], claims)
        self.assertTrue(any("回读 completed 1/1" in line for line in logs))

    def test_library_report_failure_is_visible_and_not_claimed(self):
        claims = []

        class Response:
            status_code = 400

            @staticmethod
            def json():
                return {
                    "code": 10001,
                    "msg": "10001:eventCode is empty or not string",
                }

        module = load_functions(
            "t_library",
            prog=lambda session, code: ("accepted", 0, 1),
            report_web_event=lambda *args: Response(),
            claim=lambda session, code, log: claims.append(code),
            _api_code_ok=lambda code: code in (0, "0"),
            LIB_DOC_URL="https://example.test/library",
            time=types.SimpleNamespace(sleep=lambda seconds: None),
        )
        logs = []
        module["t_library"](object(), "uid-1", "nick", logs.append)

        self.assertEqual([], claims)
        self.assertTrue(any("上报失败 HTTP 400" in line for line in logs))

    def test_library_claims_after_completed_readback(self):
        claims = []
        states = iter([
            ("accepted", 0, 1),
            ("completed", 1, 1),
        ])

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"code": 0, "msg": "OK"}

        module = load_functions(
            "t_library",
            prog=lambda session, code: next(states),
            report_web_event=lambda *args: Response(),
            claim=lambda session, code, log: claims.append(code),
            _api_code_ok=lambda code: code in (0, "0"),
            LIB_DOC_URL="https://example.test/library",
            time=types.SimpleNamespace(sleep=lambda seconds: None),
        )
        logs = []
        module["t_library"](object(), "uid-1", "nick", logs.append)

        self.assertEqual(["Library_read"], claims)
        self.assertTrue(any("completed 1/1" in line for line in logs))


class DesktopAuthSafetyTests(unittest.TestCase):
    def _auth_state(self, nickname, uid):
        return {
            "account": {
                "uid": uid,
                "nickname": nickname,
                "phoneNumber": nickname + "-phone",
                "lastLogin": True,
            },
            "auth": {
                "accessToken": nickname + "-AT",
                "refreshToken": nickname + "-RT",
                "expiresAt": 1,
            },
            "accounts": [],
        }

    def _swap_module(self, info_path):
        return load_functions(
            "swap_info", "restore_info",
            INFO_PATH=info_path,
            _SWAP_MARK="_wb_all_swapped",
            os=os, json=json, shutil=shutil, base64=base64,
        )

    def test_swap_and_restore_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            info_path = os.path.join(tmp, "workbuddy-desktop.info")
            original = self._auth_state("REAL", "real-uid")
            Path(info_path).write_text(json.dumps(original), encoding="utf-8")
            module = self._swap_module(info_path)
            token = jwt_token({"sub": "script-uid", "exp": 2000000000})

            self.assertTrue(module["swap_info"](token))
            swapped = json.loads(Path(info_path).read_text(encoding="utf-8"))
            self.assertTrue(swapped["_wb_all_swapped"])
            self.assertEqual("script-uid", swapped["account"]["uid"])
            self.assertEqual(token, swapped["auth"]["accessToken"])
            self.assertTrue(os.path.exists(info_path + ".wb_all_bak"))

            self.assertTrue(module["restore_info"]())
            restored = json.loads(Path(info_path).read_text(encoding="utf-8"))
            self.assertEqual(original, restored)
            self.assertFalse(os.path.exists(info_path + ".wb_all_bak"))

    def test_stale_backup_does_not_overwrite_user_relogin(self):
        with tempfile.TemporaryDirectory() as tmp:
            info_path = os.path.join(tmp, "workbuddy-desktop.info")
            user_a = self._auth_state("USER_A", "uid-a")
            user_b = self._auth_state("USER_B", "uid-b")
            Path(info_path).write_text(json.dumps(user_a), encoding="utf-8")
            module = self._swap_module(info_path)
            token = jwt_token({"sub": "script-uid", "exp": 2000000000})

            self.assertTrue(module["swap_info"](token))
            Path(info_path).write_text(json.dumps(user_b), encoding="utf-8")
            self.assertTrue(module["swap_info"](token))
            backup = json.loads(Path(info_path + ".wb_all_bak").read_text(encoding="utf-8"))
            self.assertEqual(user_b, backup)

            self.assertTrue(module["restore_info"]())
            restored = json.loads(Path(info_path).read_text(encoding="utf-8"))
            self.assertEqual(user_b, restored)

    def test_restore_refuses_to_overwrite_unmarked_user_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            info_path = os.path.join(tmp, "workbuddy-desktop.info")
            current = self._auth_state("CURRENT", "uid-current")
            stale = self._auth_state("STALE", "uid-stale")
            Path(info_path).write_text(json.dumps(current), encoding="utf-8")
            Path(info_path + ".wb_all_bak").write_text(json.dumps(stale), encoding="utf-8")
            module = self._swap_module(info_path)

            self.assertTrue(module["restore_info"]())
            restored = json.loads(Path(info_path).read_text(encoding="utf-8"))
            self.assertEqual(current, restored)
            self.assertFalse(os.path.exists(info_path + ".wb_all_bak"))

    def test_swap_returns_false_without_desktop_auth_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = self._swap_module(os.path.join(tmp, "missing.info"))
            self.assertFalse(module["swap_info"](
                jwt_token({"sub": "script-uid", "exp": 2000000000})))

    def test_workstation_skips_daemon_when_restart_returns_none(self):
        daemon_calls = []
        restore_calls = []

        class Proc:
            @staticmethod
            def run(*_args, **_kwargs):
                return None

            @staticmethod
            def Popen(*_args, **_kwargs):
                return None

        module = load_functions(
            "t_workstation",
            sys=types.SimpleNamespace(platform="win32"),
            time=types.SimpleNamespace(sleep=lambda _seconds: None),
            subprocess=Proc,
            prog=lambda _session, _code: ("accepted", 0, 1),
            swap_info=lambda _token: True,
            restore_info=lambda: restore_calls.append(True) or True,
            restart_desktop=lambda: None,
            daemon_chat=lambda *args, **kwargs: daemon_calls.append(args) or "txt",
        )
        logs = []
        module["t_workstation"](object(), "uid", "nick", logs.append, "token")
        self.assertEqual([], daemon_calls)
        self.assertEqual([True], restore_calls)
        self.assertTrue(any("守护进程未就绪" in line for line in logs))

    def test_desktop_exception_still_restores_auth(self):
        restore_calls = []

        class Proc:
            @staticmethod
            def run(*_args, **_kwargs):
                return None

            @staticmethod
            def Popen(*_args, **_kwargs):
                return None

        module = load_functions(
            "t_desktop_tasks",
            sys=types.SimpleNamespace(platform="win32"),
            time=types.SimpleNamespace(sleep=lambda _seconds: None),
            subprocess=Proc,
            ensure_local_skill=lambda: None,
            swap_info=lambda _token: True,
            restore_info=lambda: restore_calls.append(True) or True,
            restart_desktop=lambda: "http://daemon",
            daemon_chat=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
            cdp_ui_send=lambda _prompt: False,
            _desktop_fingerprint_fallback=lambda *args, **kwargs: None,
            SKILL_NAME="algorithmic-trading",
            claim=lambda *args, **kwargs: None,
            prog=lambda _session, _code: ("accepted", 0, 1),
        )
        with self.assertRaises(RuntimeError):
            module["t_desktop_tasks"](
                object(), "uid", "nick", "token", lambda _message: None, True, False)
        self.assertEqual([True], restore_calls)

    def test_restore_failure_does_not_restart_desktop(self):
        process_calls = []

        class Proc:
            @staticmethod
            def run(*args, **kwargs):
                process_calls.append(("run", args, kwargs))

            @staticmethod
            def Popen(*args, **kwargs):
                process_calls.append(("Popen", args, kwargs))

        module = load_functions(
            "t_desktop_tasks",
            sys=types.SimpleNamespace(platform="win32"),
            time=types.SimpleNamespace(sleep=lambda _seconds: None),
            subprocess=Proc,
            ensure_local_skill=lambda: None,
            swap_info=lambda _token: True,
            restore_info=lambda: False,
            restart_desktop=lambda: "http://daemon",
            daemon_chat=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
            cdp_ui_send=lambda _prompt: False,
            _desktop_fingerprint_fallback=lambda *args, **kwargs: None,
            SKILL_NAME="algorithmic-trading",
            claim=lambda *args, **kwargs: None,
            prog=lambda _session, _code: ("accepted", 0, 1),
        )
        logs = []
        with self.assertRaises(RuntimeError):
            module["t_desktop_tasks"](
                object(), "uid", "nick", "token", logs.append, True, False)
        self.assertEqual([], process_calls)
        self.assertTrue(any("认证还原失败" in line for line in logs))

    def test_cdp_missing_websocket_dependency_is_a_visible_skip(self):
        module = load_functions(
            "cdp_ui_send", requests=requests, time=time, json=json,
            uuid=uuid,
        )
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name == "websocket":
                raise ImportError("missing websocket-client")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded_import):
            self.assertFalse(module["cdp_ui_send"]("hello"))


class SkillShareTaskTests(unittest.TestCase):
    def _namespace(
        self, row, channel_payload, progress_values, report_status=200,
        market_skills=None,
    ):
        responses = []
        reports = []
        claims = []
        sleeps = []
        progress = iter(progress_values)
        if market_skills is None:
            market_skills = [{
                "skill_id": "skill_preferred",
                "name": "Preferred Skill",
                "description": "description",
                "source": "builtin-market",
            }]

        class Session:
            headers = {"Authorization": "Bearer test-token"}

            def __init__(self):
                self.responses = []

            def post(self, url, **kwargs):
                responses.append((url, kwargs))
                self.responses.append((url, kwargs))
                if "market/skill/list" in url:
                    return FakeResponse(payload={
                        "code": 0,
                        "data": {"skills": market_skills},
                    })
                return FakeResponse(payload=channel_payload)

        session = Session()
        fake_time = types.SimpleNamespace(
            time=lambda: 1_700_000_000,
            sleep=sleeps.append,
        )
        module = load_functions(
            "_skill_share_market_skill",
            "_create_skill_share_channel",
            "_skill_share_channel_events",
            "t_skill_share",
            api_retry=lambda *args, **kwargs: session.post(
                kwargs.get("url") or args[2], **kwargs),
            CBDOMAIN="https://example.test",
            SKILL_SHARE_CHANNEL_URL="https://share.test/channels",
            SKILL_FALLBACK=("skill_preferred", "Preferred Skill"),
            _api_code_ok=lambda code: code in (0, "0"),
            json=json,
            DESKTOP_UA="WorkBuddy/5.9.3",
            _as_int=lambda value: int(value or 0),
            _task_snapshot_row=lambda snapshot, code: None,
            _growth_task_row=lambda session, code: dict(row),
            _accept_with_verify=lambda *args, **kwargs: True,
            report_desktop_events=lambda session, uid, nick, events: (
                reports.append(events) or report_status
            ),
            prog=lambda session, code: next(progress),
            claim=lambda session, code, log: claims.append(code) or True,
            time=fake_time,
        )
        return module, session, reports, claims, sleeps

    def test_real_channel_and_progress_readback_gate_claim(self):
        module, session, reports, claims, _ = self._namespace(
            {
                "task_code": "skill_share",
                "accept_status": "accepted",
                "progress": {"current": 0, "target": 1},
            },
            {
                "code": 0,
                "data": {
                    "channel_id": "channel-1",
                    "share_code": "share-1",
                    "ways": [{
                        "share_way": "link",
                        "resource_ref": "https://share.example/skill",
                    }],
                },
            },
            [
                ("accepted", 0, 1),
                ("completed", 1, 1),
            ],
        )
        logs = []
        module["t_skill_share"](
            session, "uid-1", "nick", logs.append, snapshot=None)

        self.assertEqual(["skill_share"], claims)
        self.assertEqual(1, len(reports))
        self.assertEqual(
            ["skill_share_channel_action", "skill_share_channel_action"],
            [event["eventCode"] for event in reports[0]],
        )
        self.assertEqual(
            ["click", "success"],
            [event["panelAction"] for event in reports[0]],
        )
        self.assertTrue(any("✅" in line for line in logs))

    def test_channel_without_progress_never_claims(self):
        module, session, reports, claims, sleeps = self._namespace(
            {
                "task_code": "skill_share",
                "accept_status": "accepted",
                "progress": {"current": 0, "target": 1},
            },
            {
                "code": 0,
                "data": {
                    "channel_id": "channel-1",
                    "share_code": "share-1",
                    "ways": [{
                        "share_way": "link",
                        "resource_ref": "https://share.example/skill",
                    }],
                },
            },
            [("accepted", 0, 1)] * 5,
        )
        logs = []
        module["t_skill_share"](
            session, "uid-1", "nick", logs.append, snapshot=None)

        self.assertEqual(1, len(reports))
        self.assertEqual([], claims)
        self.assertEqual(4, len(sleeps))
        self.assertTrue(any("进度未点亮" in line for line in logs))

    def test_channel_failure_does_not_report_or_claim(self):
        module, session, reports, claims, _ = self._namespace(
            {
                "task_code": "skill_share",
                "accept_status": "accepted",
                "progress": {"current": 0, "target": 1},
            },
            {"code": 110002, "msg": "not shareable", "data": {}},
            [],
        )
        module["t_skill_share"](
            session, "uid-1", "nick", lambda message: None, snapshot=None)

        self.assertEqual([], reports)
        self.assertEqual([], claims)

    def test_locked_and_claimed_rows_are_noops(self):
        for row in (
            {
                "task_code": "skill_share",
                "accept_status": "accepted",
                "locked": True,
                "progress": {"current": 0, "target": 1},
            },
            {
                "task_code": "skill_share",
                "accept_status": "claimed",
                "progress": {"current": 1, "target": 1},
            },
        ):
            with self.subTest(row=row):
                module, session, reports, claims, _ = self._namespace(
                    row, {"code": 0, "data": {}}, []
                )
                module["t_skill_share"](
                    session, "uid-1", "nick",
                    lambda message: None, snapshot=None,
                )
                self.assertEqual([], reports)
                self.assertEqual([], claims)

    def test_empty_market_list_does_not_create_or_claim(self):
        module, session, reports, claims, _ = self._namespace(
            {
                "task_code": "skill_share",
                "accept_status": "accepted",
                "progress": {"current": 0, "target": 1},
            },
            {"code": 0, "data": {}},
            [],
            market_skills=[],
        )
        logs = []
        module["t_skill_share"](
            session, "uid-1", "nick", logs.append, snapshot=None)

        self.assertEqual([], reports)
        self.assertEqual([], claims)
        self.assertEqual(1, len(session.responses))
        self.assertIn("市场列表未返回可分享技能", " ".join(logs))


class CampaignTaskTests(unittest.TestCase):
    """小程序活动任务口径：埋点推进度 → completed → claim 才入账。

    实测（2026-11-12，星巴克/记者节）：活动任务不在成长中心列表里，
    `/portal/activity/tasks` 才是判据来源；completed_at 之后必须再调
    `POST /portal/activity/tasks/<code>/claim`，raw 回读 claimed_at 才算到账。
    """

    def _clock(self):
        class _Clock:
            @staticmethod
            def time():
                return 1700000000.0

            @staticmethod
            def sleep(_seconds):
                return None
        return _Clock

    def _harness(self, state, claims, fired, advance=True, fail_kinds=()):
        """state: {campaign: {task: row}}；_fire 按调用推进度并写 completed_at。"""
        import copy

        def cam_tasks(_s, _uid, camp, log=None):
            return copy.deepcopy(state.get(camp) or {})

        def fire(_s, _uid, _nick, spec, task, _log):
            fired.append((spec["code"], task["task"]))
            if task.get("kind") in fail_kinds:
                # 触发失败（网络异常/会话建不起来）：本任务不推进度
                return False
            row = state[spec["code"]][task["task"]]
            if not advance:
                # 判据延迟：服务端暂时不推进度
                return True
            row["progress"] = min(row["target"], row["progress"] + 1)
            if row["progress"] >= row["target"]:
                row["completed_at"] = "2026-11-12T11:00:00+08:00"
            return True

        return load_functions(
            "_campaign_poll", "t_campaigns", time=self._clock(),
            CAMPAIGN_SPECS=assignment_value("CAMPAIGN_SPECS"),
            CAMPAIGN_POLL=assignment_value("CAMPAIGN_POLL"),
            CAMPAIGN_GAP=assignment_value("CAMPAIGN_GAP"),
            CAMPAIGN_SLOW_POLL=assignment_value("CAMPAIGN_SLOW_POLL"),
            CAMPAIGN_SLOW_GAP=assignment_value("CAMPAIGN_SLOW_GAP"),
            CAMPAIGN_DESKTOP_WAIT=assignment_value("CAMPAIGN_DESKTOP_WAIT"),
            CAMPAIGN_DESKTOP_POLL=assignment_value("CAMPAIGN_DESKTOP_POLL"),
            CAMPAIGN_APP_WAIT=assignment_value("CAMPAIGN_APP_WAIT"),
            CAMPAIGN_APP_POLL=assignment_value("CAMPAIGN_APP_POLL"),
            want=lambda *codes: True,
            campaign_tasks=cam_tasks,
            _campaign_fire=fire,
            _campaign_view=lambda *a, **k: None,
            _campaign_claim=lambda _s, _uid, camp, task, _log, credit=0: (
                claims.append((camp, task)) or True),
        )

    def _blank_state(self):
        return {
            "campaign_starbucks_2026": {
                "ai_chat": {"target": 3, "progress": 0, "completed_at": None,
                            "claimed_at": None, "need_view": True, "credit": 100},
                "expert": {"target": 1, "progress": 0, "completed_at": None,
                           "claimed_at": None, "need_view": True, "credit": 100},
                "app": {"target": 1, "progress": 0, "completed_at": None,
                        "claimed_at": None, "need_view": True, "credit": 100},
            },
            "campaign_reporter_2026": {
                "expert_chat": {"target": 1, "progress": 0, "completed_at": None,
                                "claimed_at": None, "need_view": True, "credit": 50},
                "skill_use": {"target": 1, "progress": 0, "completed_at": None,
                              "claimed_at": None, "need_view": True, "credit": 50},
                "desktop_same": {"target": 1, "progress": 0, "completed_at": None,
                                 "claimed_at": None, "need_view": True, "credit": 100},
            },
        }

    def test_skips_when_campaign_not_dispatched(self):
        claims, fired, logs = [], [], []
        module = self._harness({}, claims, fired)
        module["t_campaigns"](object(), "uid", "nick", logs.append)
        self.assertEqual([], fired)
        self.assertEqual([], claims)
        self.assertTrue(any("未下发" in line for line in logs))

    def test_fires_until_target_then_claims(self):
        claims, fired, logs = [], [], []
        module = self._harness(self._blank_state(), claims, fired)
        module["t_campaigns"](object(), "uid", "nick", logs.append)
        chat_hits = [f for f in fired if f == ("campaign_starbucks_2026", "ai_chat")]
        self.assertEqual(3, len(chat_hits))          # target=3 → 三次对话
        self.assertIn(("campaign_starbucks_2026", "expert"), fired)
        self.assertIn(("campaign_reporter_2026", "expert_chat"), fired)
        self.assertIn(("campaign_reporter_2026", "skill_use"), fired)
        self.assertEqual(
            [("campaign_starbucks_2026", "ai_chat"), ("campaign_starbucks_2026", "expert"),
             ("campaign_starbucks_2026", "app"),
             ("campaign_reporter_2026", "expert_chat"), ("campaign_reporter_2026", "skill_use"),
             ("campaign_reporter_2026", "desktop_same")],
            claims)

    def test_completed_task_claims_without_refire(self):
        state = self._blank_state()
        for camp_rows in state.values():
            for row in camp_rows.values():
                row["progress"] = row["target"]
                row["completed_at"] = "2026-11-12T10:41:58+08:00"
        claims, fired, logs = [], [], []
        module = self._harness(state, claims, fired)
        module["t_campaigns"](object(), "uid", "nick", logs.append)
        self.assertEqual([], fired)
        self.assertEqual(6, len(claims))

    def test_desktop_and_app_tasks_are_automated(self):
        """desktop_same 一条事件点亮，app 真跑一轮云端对话——两者都能自动领取。"""
        claims, fired, logs = [], [], []
        module = self._harness(self._blank_state(), claims, fired)
        module["t_campaigns"](object(), "uid", "nick", logs.append)
        self.assertIn(("campaign_reporter_2026", "desktop_same"), fired)
        self.assertIn(("campaign_reporter_2026", "desktop_same"), claims)
        self.assertIn(("campaign_starbucks_2026", "app"), fired)
        self.assertIn(("campaign_starbucks_2026", "app"), claims)

    def test_app_trigger_failure_stays_pending(self):
        """app 会话建不起来时只提示，不假装成功、不领奖。"""
        claims, fired, logs = [], [], []
        module = self._harness(self._blank_state(), claims, fired, fail_kinds=("app",))
        module["t_campaigns"](object(), "uid", "nick", logs.append)
        self.assertIn(("campaign_starbucks_2026", "app"), fired)
        self.assertNotIn(("campaign_starbucks_2026", "app"), claims)
        self.assertTrue(any("触发失败" in line and "App" in line for line in logs))

    def test_claimed_task_is_skipped(self):
        state = self._blank_state()
        for camp_rows in state.values():
            for row in camp_rows.values():
                row["progress"] = row["target"]
                row["completed_at"] = "2026-11-12T10:41:58+08:00"
                row["claimed_at"] = "2026-11-12T10:56:28+08:00"
        claims, fired, logs = [], [], []
        module = self._harness(state, claims, fired)
        module["t_campaigns"](object(), "uid", "nick", logs.append)
        self.assertEqual([], fired)
        self.assertEqual([], claims)

    def test_missing_task_row_is_not_fired(self):
        """活动只下发部分任务时，未下发的那些不能按本地 spec 硬发事件。"""
        state = {"campaign_starbucks_2026": {
            "ai_chat": {"target": 1, "progress": 0, "completed_at": None,
                        "claimed_at": None, "need_view": True, "credit": 100}}}
        claims, fired, logs = [], [], []
        module = self._harness(state, claims, fired)
        module["t_campaigns"](object(), "uid", "nick", logs.append)
        self.assertEqual([("campaign_starbucks_2026", "ai_chat")], fired)
        self.assertTrue(any("未下发" in line and "解锁 App" in line for line in logs))

    def test_refire_is_capped_at_remaining_count(self):
        """判据延迟时不额外多发：重发次数上限 = target - cur。"""
        state = self._blank_state()
        claims, fired, logs = [], [], []
        module = self._harness(state, claims, fired, advance=False)
        module["t_campaigns"](object(), "uid", "nick", logs.append)
        ai_chat = [f for f in fired if f[1] == "ai_chat"]
        self.assertEqual(3, len(ai_chat))          # target=3，不再 +2
        self.assertEqual([], claims)
        self.assertTrue(any("判据延迟未落库" in line for line in logs))


class CampaignClaimTests(unittest.TestCase):
    """领奖口径：claim 成功 ≠ 到账，必须回读到 claimed_at 才算成功。"""

    def _clock(self):
        class _Clock:
            @staticmethod
            def time():
                return 1700000000.0

            @staticmethod
            def sleep(_seconds):
                return None
        return _Clock

    def _claim(self, rows, http=200, code=0):
        calls = {"read": 0}

        def cam_tasks(_s, _uid, camp, log=None):
            calls["read"] += 1
            return rows

        def call(_s, _uid, method, path, body=None):
            return http, {"code": code, "msg": "OK"}

        module = load_functions(
            "_campaign_claim", time=self._clock(), campaign_tasks=cam_tasks,
            _campaign_call=call,
            CAMPAIGN_CLAIM_GAP=assignment_value("CAMPAIGN_CLAIM_GAP"),
            CAMPAIGN_CLAIM_POLL=assignment_value("CAMPAIGN_CLAIM_POLL"),
        )
        return module["_campaign_claim"], calls

    def test_claim_confirmed_by_claimed_at(self):
        claim, calls = self._claim({"desktop_same": {"claimed_at": "2026-11-12T15:39:27+08:00"}})
        logs = []
        self.assertTrue(claim(object(), "uid", "campaign_reporter_2026",
                              "desktop_same", logs.append, 100))
        self.assertEqual(1, calls["read"])
        self.assertTrue(any("+100积分" in line for line in logs))

    def test_claim_without_readback_is_not_success(self):
        """claim 返回 code==0 但没落库：不能记 +积分，留给下一轮。"""
        claim, calls = self._claim({"desktop_same": {"claimed_at": None}})
        logs = []
        self.assertFalse(claim(object(), "uid", "campaign_reporter_2026",
                               "desktop_same", logs.append, 100))
        self.assertGreater(calls["read"], 1)          # 反复回读而不是一次就放弃
        self.assertFalse(any("+100积分" in line for line in logs))
        self.assertTrue(any("未回读到 claimed_at" in line for line in logs))

    def test_claim_http_error_is_reported(self):
        claim, calls = self._claim({}, http=500, code=199999)
        logs = []
        self.assertFalse(claim(object(), "uid", "campaign_reporter_2026",
                               "desktop_same", logs.append, 100))
        self.assertEqual(0, calls["read"])            # 没成功就不回读
        self.assertTrue(any("HTTP 500" in line for line in logs))


class CampaignReadTests(unittest.TestCase):
    """活动任务读取：失败要能在日志里看见，不能静默当成「活动结束」。"""

    def _tasks(self, http, payload):
        module = load_functions(
            "campaign_tasks", _campaign_call=lambda *a, **k: (http, payload))
        return module["campaign_tasks"]

    def test_success_parses_rows(self):
        tasks = self._tasks(200, {"code": 0, "data": {
            "tasks": [{"task_code": "ai_chat", "target_count": 3, "reward_credit": 100,
                       "need_view": True}],
            "progress": [{"task_code": "ai_chat", "progress": 1}]}})
        rows = tasks(object(), "uid", "camp")
        self.assertEqual(1, rows["ai_chat"]["progress"])
        self.assertEqual(3, rows["ai_chat"]["target"])
        self.assertEqual(100, rows["ai_chat"]["credit"])

    def test_http_error_is_logged(self):
        tasks = self._tasks(500, {"code": 199999, "msg": "internal"})
        logs = []
        self.assertEqual({}, tasks(object(), "uid", "camp", logs.append))
        self.assertTrue(any("读取失败" in line and "HTTP 500" in line for line in logs))

    def test_business_error_is_logged(self):
        tasks = self._tasks(200, {"code": 100002, "msg": "unauthorized"})
        logs = []
        self.assertEqual({}, tasks(object(), "uid", "camp", logs.append))
        self.assertTrue(any("读取失败" in line and "100002" in line for line in logs))


class CampaignEventShapeTests(unittest.TestCase):
    """活动事件的形状：专家必须带指定 expert_id，技能事件要带 skillId 与对话。"""

    def _fire(self, reports, expert_stub=None):
        def report(_s, _uid, _nick, events, **kw):
            reports.append({"events": list(events), "env": kw.get("env_extra")})
            return 200

        def pick(_s):
            raise AssertionError("指定专家的活动任务不该回落到通用专家列表")

        def expert_events(uid, nick, eid, ename, conv):
            return [{"eventCode": "expert_summon_click", "id": eid,
                     "expertTitle": ename, "conversationId": conv},
                    {"eventCode": "chat_request_send", "expertId": eid}]

        return load_functions(
            "_campaign_fire", mp_report=report,
            mp_chat_event=lambda uid, nick, conv: {"eventCode": "chat_request_send",
                                                   "conversationId": conv},
            mp_expert_use_events=expert_stub or expert_events,
            _campaign_skill_events=lambda sid, sname: [
                {"eventCode": "skill_info", "id": sname, "skillId": sid,
                 "skillName": sname}],
            _mp_pick_expert=pick,
            _new_conversation_id=lambda: "conv-fixed",
        )

    def test_reporter_expert_chat_uses_specified_expert(self):
        reports = []
        module = self._fire(reports)
        task = {"task": "expert_chat", "label": "媒体工作助理",
                "kind": "expert", "expert_id": "ex_NaiDbO06Adf3",
                "expert_name": "媒体工作助理"}
        self.assertTrue(module["_campaign_fire"](
            object(), "uid", "nick", {"code": "campaign_reporter_2026"}, task,
            lambda _m: None))
        events = reports[0]["events"]
        self.assertEqual("ex_NaiDbO06Adf3", events[0]["id"])
        self.assertEqual("媒体工作助理", events[0]["expertTitle"])
        self.assertEqual("conv-fixed", events[0]["conversationId"])

    def test_skill_task_reports_slug_name(self):
        reports = []
        module = self._fire(reports)
        task = {"task": "skill_use", "kind": "skill",
                "skill_id": "skill_2117491159283752960",
                "skill_name": "interview-material-organizer"}
        module["_campaign_fire"](object(), "uid", "nick",
                                 {"code": "campaign_reporter_2026"}, task, lambda _m: None)
        events = reports[0]["events"]
        self.assertEqual("skill_info", events[0]["eventCode"])
        self.assertEqual("skill_2117491159283752960", events[0]["skillId"])
        # 判据按技能 slug 认人：传中文显示名不计数
        self.assertEqual("interview-material-organizer", events[0]["id"])
        self.assertEqual("interview-material-organizer", events[0]["skillName"])

    def test_page_context_is_forwarded(self):
        reports = []
        module = self._fire(reports)
        task = {"task": "ai_chat", "kind": "chat", "times": 1}
        spec = {"code": "campaign_starbucks_2026",
                "page": {"pageURL": "subpackages/secondary/starbucks-activity-428/index",
                         "pageName": "starbucks_activity"}}
        module["_campaign_fire"](object(), "uid", "nick", spec, task, lambda _m: None)
        self.assertEqual("starbucks_activity", reports[0]["env"]["pageName"])
        self.assertTrue(reports[0]["env"]["pageURL"].endswith("starbucks-activity-428/index"))

    def test_skill_events_carry_skill_id_and_conversation(self):
        module = load_functions(
            "_campaign_skill_events",
            _new_conversation_id=lambda: "conv-skill",
            _new_message_id=lambda: "a" * 32,
        )
        info, chat = module["_campaign_skill_events"](
            "skill_2117491159283752960", "interview-material-organizer")
        self.assertEqual("skill_info", info["eventCode"])
        self.assertEqual("skill_2117491159283752960", info["skillId"])
        self.assertEqual("market", info["skillType"])
        self.assertEqual("chat_request_send", chat["eventCode"])
        self.assertEqual("skill_2117491159283752960", chat["skillId"])
        self.assertEqual(1, chat["skillCount"])
        self.assertEqual("conv-skill", chat["conversationId"])

    def test_desktop_same_reports_cnb_case_id(self):
        """desktop_same 只发一条桌面域 playbook_prompt_send，id 用产物目录名。"""
        cases = assignment_value("CAMPAIGN_CASES")
        module = load_functions("_campaign_desktop_events", CAMPAIGN_CASES=cases, uuid=uuid)
        case = cases["content-calendar"]
        events = module["_campaign_desktop_events"](case)
        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual("playbook_prompt_send", event["eventCode"])
        # 判据按产物目录名认案例：content-calendar / 中文名都不计数
        self.assertEqual("cnb-content-calendar", event["id"])
        self.assertEqual("内容发布排期表", event["name"])
        self.assertEqual("table", event["type"])
        self.assertEqual(1, event["isOfficial"])
        self.assertEqual("discover", event["source"])
        self.assertTrue(event["requestId"])
        self.assertTrue(event["conversationId"])

    def test_headers_keep_mp_identity(self):
        module = load_functions(
            "_campaign_headers", MP_REPORT_HEADERS=assignment_value("MP_REPORT_HEADERS"))

        class _S:
            headers = {"Authorization": "Bearer tok"}

        headers = module["_campaign_headers"](_S, "uid-1")
        self.assertEqual("Bearer tok", headers["Authorization"])
        self.assertEqual("uid-1", headers["X-User-Id"])
        self.assertEqual("mp-weixin", headers["X-Client-Platform"])
        self.assertEqual("workbuddy-mp", headers["X-Client-Product"])

    def test_app_headers_use_app_identity(self):
        """app 任务必须用 App 身份头：服务端按 x-client-product 判来源。"""
        module = load_functions("_campaign_app_headers")
        headers = module["_campaign_app_headers"]("tok-123", "uid-9", "text/event-stream")
        self.assertEqual("Bearer tok-123", headers["Authorization"])
        self.assertEqual("uid-9", headers["X-User-Id"])
        self.assertEqual("workbuddy-app", headers["X-Client-Product"])
        self.assertEqual("android", headers["X-Client-Platform"])
        self.assertEqual("text/event-stream", headers["Accept"])
        self.assertIn("WorkBuddyApp/", headers["User-Agent"])

    def test_app_machine_id_is_derived_not_hardcoded(self):
        """设备号必须按账号派生：仓库里不留真实设备指纹，也不跨账号复用。"""
        module = load_functions("_campaign_app_machine", hashlib=__import__("hashlib"))
        machine = module["_campaign_app_machine"]
        first = machine("uid-9")
        self.assertEqual(first, machine("uid-9"))
        self.assertNotEqual(first, machine("uid-10"))
        self.assertEqual(8, len(first))
        self.assertTrue(all(c in "0123456789abcdef" for c in first))
        # 空账号也不能炸，且依然是 8 位
        self.assertEqual(8, len(machine(None)))

    def test_docs_contain_no_real_phone_numbers(self):
        """公开文档/脚本里只允许示例号，不得出现接入过的真实账号。"""
        import re as _re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        allowed = {"13800000000", "13800138000"}
        pattern = _re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
        for name in ("CHANGELOG.md", "README.md", "workbuddy_daily.py"):
            path = os.path.join(root, name)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            found = set(pattern.findall(text)) - allowed
            self.assertEqual(set(), found, "%s 含未脱敏号码: %s" % (name, sorted(found)))


if __name__ == "__main__":
    unittest.main()
