import ast
import datetime
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


def load_functions(*names, **globals_):
    namespace = {"__builtins__": __builtins__, **globals_}
    body = [FUNCTIONS[name] for name in names]
    module = ast.Module(body=body, type_ignores=[])
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


class FakeResponse:
    status_code = 200

    def __init__(self, payload=None):
        self.payload = payload or {}

    def json(self):
        return self.payload


class ExpiryReminderRegressionTests(unittest.TestCase):
    def reminder_namespace(self):
        return load_functions(
            "_api_code_ok",
            "_claim_response_ok",
            "_claim_receipt_is_confirmed",
            "_task_code_of",
            "_task_status_of",
            "_task_is_done",
            "_log_task_expiry_reminders",
            CAMPAIGN_TASK_CODES={"campaign", "campaign_reporter_2026"},
            beijing_today=lambda: datetime.date(2026, 11, 13),
        )

    def test_scheduling_snapshot_does_not_drive_expiry_reporting(self):
        class Session:
            def get(self, url, **kwargs):
                return FakeResponse({"code": 0, "data": {"tasks": []}})

        module = load_functions(
            "t_unknown_tasks",
            BASE="https://example.test",
            MP_HEADER={"X-Client-Platform": "miniprogram"},
            LEVEL_TASK_CODES=set(),
            beijing_today=lambda: datetime.date(2026, 11, 13),
        )
        scheduling_snapshot = {
            "rows": {
                "skill_share": {
                    "task_code": "skill_share",
                    "accept_status": "accepted",
                    "progress": {"current": 0, "target": 1},
                    "valid_end": "2026-11-18",
                },
            },
            "errors": [],
        }

        logs = []
        module["t_unknown_tasks"](
            Session(), "u1", "nick", logs.append, snapshot=scheduling_snapshot)

        self.assertFalse(
            any("任务有效期提醒" in line for line in logs),
            "调度快照只用于开跑前决策，不能生成终态到期提醒",
        )

    def test_terminal_states_use_one_claimed_or_receipt_definition(self):
        module = self.reminder_namespace()
        rows = [
            {
                "task_code": "claimed_status",
                "accept_status": "claimed",
                "valid_end": "2026-11-18",
            },
            {
                "task_code": "claimed_flag",
                "accept_status": "accepted",
                "claimed": True,
                "valid_end": "2026-11-18",
            },
            {
                "task_code": "receipt_confirmed",
                "accept_status": "accepted",
                "valid_end": "2026-11-18",
            },
            {
                "task_code": "receipt_empty",
                "accept_status": "accepted",
                "valid_end": "2026-11-18",
            },
            {
                "task_code": "already_claimed",
                "accept_status": "completed",
                "valid_end": "2026-11-18",
            },
            {
                "task_code": "completed_only",
                "accept_status": "completed",
                "progress": {"current": 1, "target": 1},
                "valid_end": "2026-11-18",
            },
            {
                "task_code": "claimed_false",
                "accept_status": "accepted",
                "claimed": "false",
                "valid_end": "2026-11-18",
            },
            {
                "code": "active_only",
                "status": "accepted",
                "valid_end": "2026-11-18",
            },
            {
                "task_code": "campaign_reporter_2026",
                "accept_status": "accepted",
                "valid_end": "2026-11-18",
            },
            {
                "task_code": "locked_task",
                "accept_status": "accepted",
                "locked": True,
                "valid_end": "2026-11-18",
            },
            {
                "task_code": "far_task",
                "accept_status": "accepted",
                "valid_end": "2026-12-01",
            },
        ]
        receipts = {
            "receipt_confirmed": {"readback_claimed": True},
            "receipt_empty": {},
            "already_claimed": {"already_claimed": True},
        }

        logs = []
        soon = module["_log_task_expiry_reminders"](
            rows, logs.append, receipts=receipts)

        self.assertEqual(
            ["receipt_empty", "completed_only", "claimed_false", "active_only"],
            [item[0] for item in soon],
        )
        text = "\n".join(logs)
        self.assertIn("任务有效期提醒（未完成/未领奖）", text)
        self.assertIn("receipt_empty", text)
        self.assertIn("completed_only", text)
        self.assertIn("claimed_false", text)
        self.assertIn("active_only", text)
        self.assertNotIn("claimed_status", text)
        self.assertNotIn("claimed_flag", text)
        self.assertNotIn("receipt_confirmed", text)
        self.assertNotIn("already_claimed", text)
        self.assertNotIn("campaign_reporter_2026", text)
        self.assertNotIn("locked_task", text)
        self.assertNotIn("far_task", text)

    def test_no_task_rows_emit_no_expiry_reminder(self):
        module = self.reminder_namespace()
        logs = []

        soon = module["_log_task_expiry_reminders"]([], logs.append)

        self.assertEqual([], soon)
        self.assertEqual([], logs)


if __name__ == "__main__":
    unittest.main()
