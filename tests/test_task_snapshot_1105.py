import ast
import types
import unittest
from pathlib import Path


SOURCE_PATH = Path(__file__).resolve().parents[1] / "workbuddy_daily.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
FUNCTIONS = {
    node.name: node
    for node in TREE.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}

BASE = "https://example.test"
MP_HEADER = {"X-Client-Platform": "miniprogram"}


def load_functions(*names, **globals_):
    namespace = {"__builtins__": __builtins__, **globals_}
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


def task_payload(*rows):
    return {
        "code": 0,
        "msg": "OK",
        "data": {"tasks": [dict(row) for row in rows]},
    }


class ThreeSourceSession:
    def __init__(self, desktop, mp, level=None, mp_error=None):
        self.desktop = desktop
        self.mp = mp
        self.level = level or task_payload()
        self.mp_error = mp_error
        self.calls = []

    def get(self, url, **kwargs):
        headers = kwargs.get("headers") or {}
        self.calls.append((url, dict(headers)))
        if url.endswith("/activity/growth/tasks") and "/v2/" not in url:
            return FakeResponse(payload=self.level)
        if headers.get("X-Client-Platform") == "miniprogram":
            if self.mp_error:
                raise self.mp_error
            return FakeResponse(payload=self.mp)
        return FakeResponse(payload=self.desktop)


def snapshot_namespace():
    names = {
        "missing": "missing",
        "active": "active",
        "locked": "locked",
        "done": "done",
        "claimed": "claimed",
        "ready": "ready",
        "mp_only": "mp_only",
        "mixed_locked": "mixed_locked",
        "cross_source": "cross_source",
        "claimed_cross": "claimed_cross",
        "inconsistent_completed": "inconsistent_completed",
    }
    return load_functions(
        "_api_code_ok",
        "_as_int",
        "_task_code_of",
        "_task_status_of",
        "_task_is_done",
        "_task_is_ready",
        "_task_row_needs_action",
        "_task_state_needs_action",
        "_merge_task_snapshot_row",
        "_build_task_snapshot",
        "_task_snapshot_row",
        "_task_snapshot_prog",
        "_is_growth_task_code",
        "_task_snapshot_skip_reason",
        "_task_snapshot_group_skip_reason",
        "_task_snapshot_needs_action",
        "_growth_task_rows",
        "_dynamic_expert_task_codes",
        "_run_cloud_task",
        BASE=BASE,
        MP_HEADER=MP_HEADER,
        TASK_NAME_CN=names,
        REPORT_GAP=1.05,
        time=types.SimpleNamespace(sleep=lambda seconds: None),
    )


def claim_namespace(sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return load_functions(
        "_api_code_ok",
        "_as_int",
        "_claim_readback",
        "_claim_response_ok",
        "claim",
        "_mp_claim",
        "claim_ready_tasks",
        BASE=BASE,
        MP_HEADER=MP_HEADER,
        MP_TASK_CODES=set(),
        DESKTOP_ONLY_TASK_CODES=set(),
        time=types.SimpleNamespace(sleep=sleeps.append),
    )


class TaskSnapshotTests(unittest.TestCase):
    def test_snapshot_reads_each_source_once_and_classifies_states(self):
        desktop = task_payload(
            {
                "task_code": "done",
                "accept_status": "completed",
                "progress": {"current": 1, "target": 1},
            },
            {
                "task_code": "claimed",
                "accept_status": "claimed",
                "claimed": True,
                "progress": {"current": 1, "target": 1},
            },
            {
                "task_code": "ready",
                "accept_status": "accepted",
                "progress": {"current": 2, "target": 2},
            },
            {
                "task_code": "locked",
                "accept_status": "accepted",
                "locked": True,
                "valid_start": "2026-11-08T00:00:00+08:00",
            },
            {
                "task_code": "active",
                "accept_status": "accepted",
                "progress": {"current": 0, "target": 2},
            },
            {
                "task_code": "mixed_locked",
                "accept_status": "accepted",
                "locked": False,
                "progress": {"current": 0, "target": 1},
            },
            {
                "task_code": "cross_source",
                "accept_status": "accepted",
                "progress": {"current": 0, "target": 1},
            },
            {
                "task_code": "claimed_cross",
                "accept_status": "accepted",
                "progress": {"current": 0, "target": 1},
            },
            {
                "task_code": "inconsistent_completed",
                "accept_status": "completed",
                "progress": {"current": 1, "target": 2},
            },
        )
        mp = task_payload(
            {
                "task_code": "mp_only",
                "accept_status": "accepted",
                "locked": True,
                "valid_start": "2026-11-09",
            },
            {
                "task_code": "ready",
                "accept_status": "accepted",
                "progress": {"current": 2, "target": 2},
            },
            {
                "task_code": "mixed_locked",
                "accept_status": "not_accepted",
                "locked": True,
                "valid_start": "2026-11-10",
            },
            {
                "task_code": "cross_source",
                "accept_status": "completed",
                "progress": {"current": 1, "target": 1},
            },
            {
                "task_code": "claimed_cross",
                "accept_status": "accepted",
                "claimed": True,
                "progress": {"current": 0, "target": 1},
            },
        )
        session = ThreeSourceSession(desktop, mp)
        module = snapshot_namespace()

        snapshot = module["_build_task_snapshot"](session)

        self.assertEqual(3, len(session.calls))
        self.assertTrue(snapshot["complete"])
        self.assertEqual(
            {"done", "claimed", "ready", "locked", "active", "mixed_locked",
             "cross_source", "claimed_cross", "inconsistent_completed",
             "mp_only"},
            snapshot["have"],
        )
        self.assertIn("claimed", snapshot["done"])
        self.assertNotIn("done", snapshot["done"])
        self.assertIn("done", snapshot["ready"])
        self.assertIn("ready", snapshot["ready"])
        self.assertIn("locked", snapshot["locked"])
        self.assertIn("mp_only", snapshot["locked"])
        self.assertEqual(
            "已完成/已领奖",
            module["_task_snapshot_skip_reason"](snapshot, "claimed"),
        )
        self.assertEqual(
            "已达标待领奖",
            module["_task_snapshot_skip_reason"](snapshot, "done"),
        )
        self.assertEqual(
            "已达标待领奖",
            module["_task_snapshot_skip_reason"](snapshot, "ready"),
        )
        self.assertIn(
            "未到上线时间",
            module["_task_snapshot_skip_reason"](snapshot, "locked"),
        )
        self.assertEqual(
            "该账号未下发此任务",
            module["_task_snapshot_skip_reason"](snapshot, "missing"),
        )
        self.assertEqual(
            "",
            module["_task_snapshot_skip_reason"](snapshot, "active"),
        )
        self.assertEqual(
            "",
            module["_task_snapshot_skip_reason"](snapshot, "mixed_locked"),
        )
        self.assertEqual(
            "已达标待领奖",
            module["_task_snapshot_skip_reason"](snapshot, "cross_source"),
        )
        self.assertEqual(
            "桌面",
            module["_task_snapshot_row"](snapshot, "cross_source")["_source"],
        )
        self.assertEqual(
            "已达标待领奖",
            module["_task_snapshot_skip_reason"](
                snapshot, "inconsistent_completed"),
        )
        self.assertIn("claimed_cross", snapshot["done"])
        self.assertNotIn("claimed_cross", snapshot["actionable"])
        self.assertEqual(
            "已完成/已领奖",
            module["_task_snapshot_skip_reason"](snapshot, "claimed_cross"),
        )
        inconsistent_row = module["_task_snapshot_row"](
            snapshot, "inconsistent_completed")
        self.assertFalse(module["_task_row_needs_action"](inconsistent_row))
        self.assertFalse(module["_task_state_needs_action"](
            "completed", 1, 2))
        self.assertFalse(module["_task_state_needs_action"](
            "completed", 2, 2))
        self.assertTrue(module["_task_state_needs_action"](None, None, None))
        self.assertTrue(module["_task_state_needs_action"]("", 0, 1))
        self.assertFalse(module["_task_state_needs_action"](
            "claimed", 1, 1))
        self.assertTrue(
            module["_task_snapshot_group_skip_reason"](
                snapshot, ["done", "locked", "missing"]))
        self.assertEqual(
            "",
            module["_task_snapshot_group_skip_reason"](
                snapshot, ["active", "done"]),
        )

    def test_not_accepted_pending_and_empty_full_progress_require_accept(self):
        module = snapshot_namespace()
        for status in ("not_accepted", "pending", ""):
            with self.subTest(status=status):
                row = {
                    "task_code": "needs_accept",
                    "accept_status": status,
                    "progress": {"current": 1, "target": 1},
                }
                self.assertFalse(module["_task_is_ready"](row))
                self.assertTrue(module["_task_row_needs_action"](row))

        class Session:
            def __init__(self):
                self.posts = []

            def get(self, url, **kwargs):
                if kwargs.get("headers"):
                    return FakeResponse(payload=task_payload())
                return FakeResponse(payload=task_payload(
                    {
                        "task_code": "not_accepted",
                        "accept_status": "not_accepted",
                        "progress": {"current": 1, "target": 1},
                    },
                    {
                        "task_code": "pending",
                        "accept_status": "pending",
                        "progress": {"current": 1, "target": 1},
                    },
                    {
                        "task_code": "empty",
                        "accept_status": "",
                        "progress": {"current": 1, "target": 1},
                    },
                ))

            def post(self, url, **kwargs):
                self.posts.append(url)
                return FakeResponse(payload={"code": 0, "data": {}})

        session = Session()
        logs = []
        claim_namespace()["claim_ready_tasks"](session, logs.append)
        self.assertEqual([], session.posts)
        self.assertIn("无达标未领奖励", " ".join(logs))

    def test_claimed_source_beats_actionable_and_blocks_claim(self):
        desktop = task_payload({
            "task_code": "RichMeow_Chat",
            "accept_status": "accepted",
            "progress": {"current": 1, "target": 1},
        })
        mp = task_payload({
            "task_code": "RichMeow_Chat",
            "accept_status": "accepted",
            "claimed": True,
            "progress": {"current": 0, "target": 1},
        })
        snapshot_session = ThreeSourceSession(desktop, mp)
        module = snapshot_namespace()
        module["TASK_NAME_CN"]["RichMeow_Chat"] = "RichMeow_Chat"
        snapshot = module["_build_task_snapshot"](snapshot_session)
        self.assertIn("RichMeow_Chat", snapshot["done"])
        self.assertNotIn("RichMeow_Chat", snapshot["actionable"])
        self.assertEqual(
            "已完成/已领奖",
            module["_task_snapshot_skip_reason"](snapshot, "RichMeow_Chat"),
        )
        desktop_row = module["_task_snapshot_row"](
            snapshot, "RichMeow_Chat")
        self.assertFalse(module["_task_snapshot_needs_action"](
            snapshot, "RichMeow_Chat", row=desktop_row))

        class ClaimSession:
            def __init__(self):
                self.posts = []

            def get(self, url, **kwargs):
                payload = mp if kwargs.get("headers") else desktop
                return FakeResponse(payload=payload)

            def post(self, url, **kwargs):
                self.posts.append(url)
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"credit": 10},
                })

        session = ClaimSession()
        logs = []
        claim_namespace()["claim_ready_tasks"](session, logs.append)
        self.assertEqual([], session.posts)

    def test_empty_claim_receipt_not_counted_and_not_repeated(self):
        class Session:
            def __init__(self):
                self.posts = []
                self.get_calls = 0

            def get(self, url, **kwargs):
                self.get_calls += 1
                if kwargs.get("headers"):
                    return FakeResponse(payload=task_payload())
                return FakeResponse(payload=task_payload({
                    "task_code": "empty_receipt",
                    "accept_status": "accepted",
                    "progress": {"current": 1, "target": 1},
                    "reward_credit": 100,
                    "reward_energy": 5,
                }))

            def post(self, url, **kwargs):
                self.posts.append(url)
                return FakeResponse(payload={"code": 0, "data": {}})

        session = Session()
        logs = []
        module = claim_namespace()
        self.assertFalse(module["_claim_response_ok"](
            FakeResponse(payload={"code": 0, "data": {}}),
            {"code": 0, "data": {}},
        )[0])
        module["claim_ready_tasks"](session, logs.append)
        first_posts = len(session.posts)
        module["claim_ready_tasks"](session, logs.append)

        self.assertEqual(1, first_posts)
        self.assertEqual(1, len(session.posts))
        self.assertNotIn("达标补领完成", " ".join(logs))
        self.assertIn("跳过重复空 claim", " ".join(logs))

    def test_claim_receipt_requires_nonempty_reward_or_claimed(self):
        module = claim_namespace()
        response = FakeResponse(payload={"code": 0, "data": {}})
        invalid_payloads = (
            {"rewards": []},
            {"reward": {}},
            {"credit": 0},
            {"credit": "0"},
            {"credit": "0.0"},
            {"credit": "0.00"},
            {"energy": "0"},
            {"energy": "0.0"},
            {"energy": "0.00"},
            {"reward": "{}"},
            {"rewards": "[]"},
            {"reward": ""},
            {"claimed": False, "credit": 0},
        )
        for data in invalid_payloads:
            with self.subTest(data=data):
                ok, _, _ = module["_claim_response_ok"](
                    response, {"code": 0, "data": data})
                self.assertFalse(ok)
        for data in ({"credit": 1}, {"credit": "10"}, {"claimed": True}):
            with self.subTest(data=data):
                ok, _, _ = module["_claim_response_ok"](
                    response, {"code": 0, "data": data})
                self.assertTrue(ok)

    def test_confirmed_receipt_blocks_later_claim_post_despite_lag(self):
        class Session:
            def __init__(self):
                self.posts = []

            def get(self, url, **kwargs):
                if kwargs.get("headers"):
                    return FakeResponse(payload=task_payload())
                return FakeResponse(payload=task_payload({
                    "task_code": "receipt_lag",
                    "accept_status": "completed",
                }))

            def post(self, url, **kwargs):
                self.posts.append(url)
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"credit": 10},
                })

        session = Session()
        logs = []
        module = claim_namespace()
        module["claim_ready_tasks"](session, logs.append)
        module["claim_ready_tasks"](session, logs.append)

        self.assertEqual(1, len(session.posts))
        self.assertIn("已有确认回执，跳过重复 POST", " ".join(logs))
        self.assertIn("达标补领完成: 1 项", " ".join(logs))

    def test_claim_readback_rejects_http_or_business_error(self):
        class Session:
            def __init__(self, first):
                self.calls = 0
                self.first = first

            def get(self, url, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return self.first
                return FakeResponse(payload=task_payload())

        module = claim_namespace()
        claimed_row = {
            "task_code": "readback_task",
            "accept_status": "claimed",
        }
        http_error = FakeResponse(
            status_code=500,
            payload=task_payload(claimed_row),
        )
        business_error = FakeResponse(payload={
            "code": 500,
            "data": {"tasks": [claimed_row]},
        })
        valid = FakeResponse(payload=task_payload(claimed_row))
        self.assertIsNone(module["_claim_readback"](
            Session(http_error), "readback_task"))
        self.assertIsNone(module["_claim_readback"](
            Session(business_error), "readback_task"))
        self.assertEqual(
            claimed_row["task_code"],
            module["_claim_readback"](Session(valid), "readback_task")[
                "task_code"],
        )

    def test_empty_claim_receipt_counts_after_realtime_claimed_readback(self):
        class Session:
            def __init__(self):
                self.claimed = False
                self.posts = []

            def get(self, url, **kwargs):
                status = "claimed" if self.claimed else "accepted"
                if kwargs.get("headers"):
                    return FakeResponse(payload=task_payload())
                return FakeResponse(payload=task_payload({
                    "task_code": "readback_ok",
                    "accept_status": status,
                    "progress": {"current": 1, "target": 1},
                }))

            def post(self, url, **kwargs):
                self.posts.append(url)
                self.claimed = True
                return FakeResponse(payload={"code": 0, "data": {}})

        session = Session()
        logs = []
        claim_namespace()["claim_ready_tasks"](session, logs.append)
        self.assertEqual(1, len(session.posts))
        self.assertIn("回读确认已领取", " ".join(logs))
        self.assertIn("达标补领完成: 1 项", " ".join(logs))

    def test_first_buddy_missing_snapshot_still_checks_and_repairs_instance(self):
        checks = []
        repairs = []
        module = load_functions(
            "t_first_buddy",
            has_buddy=lambda session: checks.append(session) or False,
            ensure_first_buddy=lambda session, uid, nick, log: (
                repairs.append((uid, nick)) or True),
            prog=lambda session, code: (None, None, None),
        )
        module["t_first_buddy"](
            object(), "u1", "nick", lambda message: None,
            initial_state=(None, None, None),
        )
        self.assertEqual(1, len(checks))
        self.assertEqual([("u1", "nick")], repairs)

    def test_report_gap_only_after_actual_cloud_task(self):
        sleeps = []
        module = snapshot_namespace()
        module["time"] = types.SimpleNamespace(sleep=sleeps.append)
        module["REPORT_GAP"] = 1.05
        module["want"] = lambda *codes: True
        session = ThreeSourceSession(
            task_payload(
                {
                    "task_code": "skip_done",
                    "accept_status": "claimed",
                    "claimed": True,
                },
                {
                    "task_code": "run_active",
                    "accept_status": "accepted",
                    "progress": {"current": 0, "target": 1},
                },
            ),
            task_payload(),
        )
        module["TASK_NAME_CN"].update({
            "skip_done": "skip_done",
            "run_active": "run_active",
        })
        snapshot = module["_build_task_snapshot"](session)
        actions = []
        module["_run_cloud_task"](
            "skip", ["skip_done"], lambda: actions.append("skip"),
            lambda message: None, snapshot)
        self.assertEqual([], actions)
        self.assertEqual([], sleeps)
        module["_run_cloud_task"](
            "run", ["run_active"], lambda: actions.append("run"),
            lambda message: None, snapshot)
        self.assertEqual(["run"], actions)
        self.assertEqual([1.05], sleeps)

        def explode():
            raise RuntimeError("task boom")

        module["_run_cloud_task"](
            "explode", ["run_active"], explode,
            lambda message: None, snapshot)
        self.assertEqual([1.05], sleeps)

    def test_push_summary_excludes_completed_without_confirmation(self):
        rows = [
            {"task_code": "explicit_claimed", "accept_status": "claimed"},
            {"task_code": "receipt_confirmed", "accept_status": "completed"},
            {"task_code": "completed_only", "accept_status": "completed"},
            {"task_code": "empty_receipt", "accept_status": "completed"},
        ]
        receipts = {
            "receipt_confirmed": {"credit": 10},
            "empty_receipt": {},
        }
        module = load_functions(
            "_api_code_ok",
            "_claim_response_ok",
            "_summarize_task_completion",
            "task_cn",
            "build_summary",
            TASK_NAME_CN={
                "explicit_claimed": "明确已领",
                "receipt_confirmed": "回执确认",
                "completed_only": "仅完成",
                "empty_receipt": "空回执",
            },
            mask_phone=lambda value: value or "",
            _clean_num=lambda value: str(value),
            time=types.SimpleNamespace(
                strftime=lambda fmt: "2026-11-05 18:00"),
        )
        done, rest = module["_summarize_task_completion"](rows, receipts)
        self.assertEqual(2, done)
        self.assertEqual(["completed_only", "empty_receipt"], rest)

        summary = {
            "idx": 1,
            "note": "用户",
            "done": done,
            "total": len(rows),
            "rest": rest,
            "credits": "100",
            "usage": "1M",
            "energy": 5,
            "level": 6,
            "streak": 2,
            "checkin_streak": 3,
        }
        text = module["build_summary"]([summary])
        self.assertIn("任务完成 2/4 项", text)
        self.assertIn("仅完成", text)
        self.assertIn("空回执", text)

    def test_dynamic_expert_scan_falls_back_when_snapshot_source_fails(self):
        for failed_source in ("desktop", "mp"):
            with self.subTest(failed_source=failed_source):
                class Session:
                    def __init__(self):
                        self.failed_calls = 0

                    def get(self, url, **kwargs):
                        is_level = (url.endswith("/activity/growth/tasks")
                                    and "/v2/" not in url)
                        is_mp = bool(kwargs.get("headers"))
                        if is_level:
                            return FakeResponse(payload=task_payload())
                        if (failed_source == "mp") == is_mp:
                            self.failed_calls += 1
                            if self.failed_calls == 1:
                                raise RuntimeError("%s snapshot failed" % failed_source)
                            return FakeResponse(payload=task_payload({
                                "task_code": "expert_use_dynamic",
                                "accept_status": "accepted",
                            }))
                        return FakeResponse(payload=task_payload())

                module = snapshot_namespace()
                session = Session()
                snapshot = module["_build_task_snapshot"](session)
                self.assertFalse(snapshot["sources"][failed_source]["ok"])
                self.assertEqual(
                    ["expert_use_dynamic"],
                    module["_dynamic_expert_task_codes"](session, snapshot),
                )
                self.assertEqual(2, session.failed_calls)

    def test_seventeen_terminal_tasks_do_not_add_scan_or_action_calls(self):
        codes = ["done_%d" % i for i in range(6)]
        codes += ["locked_%d" % i for i in range(6)]
        codes += ["missing_%d" % i for i in range(5)]
        desktop_rows = []
        for code in codes[:6]:
            desktop_rows.append({
                "task_code": code,
                "accept_status": "claimed",
                "claimed": True,
                "progress": {"current": 1, "target": 1},
            })
        for code in codes[6:12]:
            desktop_rows.append({
                "task_code": code,
                "accept_status": "accepted",
                "locked": True,
            })
        session = ThreeSourceSession(task_payload(*desktop_rows), task_payload())
        module = snapshot_namespace()
        module["TASK_NAME_CN"].update({code: code for code in codes})
        logs = []
        actions = []
        snapshot = module["_build_task_snapshot"](session, logs.append)

        want = lambda *unused: True
        runner = load_functions(
            "_api_code_ok",
            "_as_int",
            "_task_code_of",
            "_task_status_of",
            "_task_is_done",
            "_task_is_ready",
            "_task_row_needs_action",
            "_task_state_needs_action",
            "_merge_task_snapshot_row",
            "_task_snapshot_row",
            "_task_snapshot_prog",
            "_is_growth_task_code",
            "_task_snapshot_skip_reason",
            "_task_snapshot_group_skip_reason",
            "_task_snapshot_needs_action",
            "_run_cloud_task",
            BASE=BASE,
            MP_HEADER=MP_HEADER,
            TASK_NAME_CN={code: code for code in codes},
            want=want,
            REPORT_GAP=1.05,
            time=types.SimpleNamespace(sleep=lambda seconds: None),
        )
        for code in codes:
            runner["_run_cloud_task"](
                code, [code], lambda: actions.append(code), logs.append, snapshot)

        self.assertEqual(3, len(session.calls))
        self.assertEqual([], actions)
        self.assertEqual(17, sum("快照跳过" in line for line in logs))

    def test_snapshot_error_is_visible_and_does_not_skip_unknown_codes(self):
        desktop = task_payload({
            "task_code": "active",
            "accept_status": "accepted",
            "progress": {"current": 0, "target": 1},
        })
        session = ThreeSourceSession(
            desktop, task_payload(), mp_error=RuntimeError("mp boom"))
        module = snapshot_namespace()
        logs = []

        snapshot = module["_build_task_snapshot"](session, logs.append)
        self.assertFalse(snapshot["complete"])
        self.assertTrue(any("mp boom" in error for error in snapshot["errors"]))
        self.assertTrue(any("任务快照部分口径失败" in line for line in logs))

        actions = []
        runner = load_functions(
            "_api_code_ok",
            "_as_int",
            "_task_code_of",
            "_task_status_of",
            "_task_is_done",
            "_task_is_ready",
            "_task_row_needs_action",
            "_task_state_needs_action",
            "_merge_task_snapshot_row",
            "_task_snapshot_row",
            "_task_snapshot_prog",
            "_is_growth_task_code",
            "_task_snapshot_skip_reason",
            "_task_snapshot_group_skip_reason",
            "_task_snapshot_needs_action",
            "_run_cloud_task",
            BASE=BASE,
            MP_HEADER=MP_HEADER,
            TASK_NAME_CN={"missing": "missing"},
            want=lambda *unused: True,
            REPORT_GAP=1.05,
            time=types.SimpleNamespace(sleep=lambda seconds: None),
        )
        runner["_run_cloud_task"](
            "unknown", ["missing"], lambda: actions.append("ran"), logs.append,
            snapshot,
        )
        self.assertEqual(["ran"], actions)

        def explode():
            raise RuntimeError("task boom")

        runner["_run_cloud_task"](
            "explode", ["missing"], explode, logs.append, snapshot)
        self.assertTrue(any("task boom" in line for line in logs))

    def test_accept_batch_uses_snapshot_rows_and_keeps_state_readback(self):
        class Session:
            def __init__(self):
                self.get_calls = 0
                self.post_calls = []

            def get(self, url, **kwargs):
                self.get_calls += 1
                return FakeResponse(payload=task_payload({
                    "task_code": "active",
                    "accept_status": "accepted",
                    "progress": {"current": 0, "target": 1},
                }))

            def post(self, url, **kwargs):
                self.post_calls.append((url, kwargs.get("json")))
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"results": [{
                        "task_code": "active",
                        "status": "accepted",
                    }]},
                })

        sleeps = []
        module = load_functions(
            "_api_code_ok",
            "_task_code_of",
            "_task_status_of",
            "_task_is_done",
            "prog",
            "t_accept_all",
            BASE=BASE,
            ACCEPT_BATCH_SETTLE=0,
            ACCEPT_BATCH_TRIES=3,
            time=types.SimpleNamespace(sleep=sleeps.append),
            _accept_with_verify=lambda *args: True,
            ensure_first_buddy=lambda *args: True,
        )
        session = Session()
        logs = []
        module["t_accept_all"](
            session, "u1", "nick", logs.append,
            rows=[
                {"task_code": "active", "accept_status": "not_accepted"},
                {
                    "task_code": "locked",
                    "accept_status": "not_accepted",
                    "locked": True,
                },
                {
                    "task_code": "claimed",
                    "accept_status": "not_accepted",
                    "claimed": True,
                },
                {
                    "task_code": "mp_claimed",
                    "accept_status": "not_accepted",
                },
            ],
            done_codes={"mp_claimed"},
        )

        self.assertEqual(1, session.get_calls)
        self.assertEqual(1, len(session.post_calls))
        self.assertEqual(
            {"task_codes": ["active"]}, session.post_calls[0][1])
        self.assertTrue(any("全部登记生效" in line for line in logs))

    def test_final_claim_scan_still_claims_and_reports_real_reward(self):
        class Session:
            def __init__(self):
                self.get_calls = 0
                self.posts = []

            def get(self, url, **kwargs):
                self.get_calls += 1
                headers = kwargs.get("headers") or {}
                if headers.get("X-Client-Platform") == "miniprogram":
                    return FakeResponse(payload=task_payload())
                return FakeResponse(payload=task_payload({
                    "task_code": "reward_task",
                    "accept_status": "accepted",
                    "progress": {"current": 3, "target": 3},
                }))

            def post(self, url, **kwargs):
                self.posts.append((url, kwargs.get("json")))
                return FakeResponse(payload={
                    "code": 0,
                    "data": {"credit": 30, "energy": 5},
                })

        sleeps = []
        module = load_functions(
            "_api_code_ok",
            "_as_int",
            "_claim_response_ok",
            "claim",
            "_mp_claim",
            "claim_ready_tasks",
            BASE=BASE,
            MP_HEADER=MP_HEADER,
            MP_TASK_CODES=set(),
            DESKTOP_ONLY_TASK_CODES=set(),
            time=types.SimpleNamespace(sleep=sleeps.append),
        )
        session = Session()
        logs = []
        module["claim_ready_tasks"](session, logs.append)

        self.assertEqual(2, session.get_calls)
        self.assertEqual(1, len(session.posts))
        self.assertTrue(session.posts[0][0].endswith(
            "/activity/growth/tasks/reward_task/claim"))
        self.assertTrue(any("+30积分+5能量" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
