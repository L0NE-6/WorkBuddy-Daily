"""Credit expiry regressions for the main WorkBuddy script."""
import ast
import datetime
import math
import types
from pathlib import Path
from unittest import mock


SOURCE_PATH = Path(__file__).resolve().parents[1] / "workbuddy_daily.py"
TREE = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
FUNCTIONS = {
    node.name: node for node in TREE.body if isinstance(node, ast.FunctionDef)
}
NAMESPACE = {
    "__builtins__": __builtins__,
    "datetime": datetime,
    "math": math,
    "BASE": "https://www.workbuddy.cn",
    "CREDITS_PAGE_SIZE": 100,
    "CREDITS_PRODUCT_CODE": "p_tcaca",
    "CREDITS_PACKAGE_STATUS": (0, 3),
    "CREDITS_EXPIRY_WINDOW_DAYS": 365 * 101,
    "api_retry": None,
}
NAMES = (
    "_clean_num",
    "_credit_parse_end",
    "_credit_precise_num",
    "_credit_account_remain",
    "_credit_is_monthly",
    "_credit_account_end",
    "_credit_expiry_line",
    "_credit_extract_accounts",
    "queryCreditExpiry",
    "queryCredits",
)
exec(compile(ast.Module(
    body=[FUNCTIONS[name] for name in NAMES], type_ignores=[]),
    str(SOURCE_PATH), "exec"), NAMESPACE)
NAMESPACE["beijing_now"] = lambda: datetime.datetime(2026, 11, 24, 10, 0, 0)
MODULE = types.SimpleNamespace(**NAMESPACE)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def accounts_payload():
    return {
        "data": {
            "Response": {
                "Data": {
                    "Accounts": [
                        {
                            "CapacityType": 4,
                            "CycleCapacityRemainPrecise": 500,
                            "CycleEndTime": "2026-11-30 23:59:59",
                        },
                        {
                            "CapacityType": 1,
                            "CapacityRemainPrecise": 120,
                            "ExpiredTime": "2026-12-01 23:59:59",
                        },
                    ]
                }
            }
        }
    }


def test_credit_expiry_line_separates_monthly_and_promotional_credit():
    line, warning = MODULE._credit_expiry_line(
        accounts_payload()["data"]["Response"]["Data"]["Accounts"],
        now=datetime.datetime(2026, 11, 24, 10, 0, 0),
    )

    assert "11-30 月包清零 500" in line
    assert "12-01 到期 120" in line
    assert "积分到期预警" in warning


def test_query_credits_appends_expiry_and_keeps_meta():
    summary = Response({
        "data": {
            "IsPaidUser": True,
            "Packages": [{"CycleRemainCapacity": 620}],
        }
    })
    retry = mock.Mock(side_effect=[summary, Response(accounts_payload())])
    with mock.patch.dict(NAMESPACE, {"api_retry": retry}):
        meta = {}
        text, paid = MODULE.queryCredits(object(), meta)

    assert paid is True
    assert "剩余积分合计620" in text
    assert "⏰" in text
    assert meta["expiry_line"] == text.split("\n   ", 1)[1]
