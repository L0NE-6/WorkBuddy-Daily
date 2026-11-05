import importlib.util
import json
from pathlib import Path
from unittest import mock

import unittest

from test_workbuddy_core import load_functions


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CN = load_functions(
    "is_unapproved_channel",
    "is_account_restricted",
    "interpret_failure",
    json=json,
)
INTL = load_module("workbuddy_channel_limit_intl", "workbuddy_intl_daily.py")


class ChannelFailureTests(unittest.TestCase):
    def payload(self):
        return {
            "error": {
                "message": "Illegal API invocation from an unapproved channel",
                "upstream_code": "11-128",
            }
        }

    def test_both_scripts_detect_nested_11_128(self):
        payload = self.payload()
        self.assertTrue(CN["is_unapproved_channel"](400, payload))
        self.assertTrue(INTL.is_unapproved_channel(400, payload))
        self.assertFalse(CN["is_unapproved_channel"](403, {"code": 11140}))
        self.assertFalse(INTL.is_unapproved_channel(403, {"code": 11140}))

    def test_cn_classifies_channel_limit_before_account_restriction(self):
        text, skip = CN["interpret_failure"](400, self.payload())
        self.assertIn("11-128", text)
        self.assertIn("非登录失效", text)
        self.assertTrue(skip)

    def test_intl_preflight_raises_channel_limit_without_refresh(self):
        response = mock.Mock(status_code=400, text=json.dumps(self.payload()))
        with mock.patch.object(INTL, "post_json", return_value=response), \
                mock.patch.object(INTL, "json_of", return_value=self.payload()):
            with self.assertRaises(INTL.UnapprovedChannel):
                INTL.preflight_restricted("token", "uid")

    def test_intl_keepalive_stops_model_chain_on_channel_limit(self):
        response = mock.Mock(status_code=400)
        with mock.patch.object(INTL, "model_chain", return_value=["model-a", "model-b"]), \
                mock.patch.object(INTL, "json_of", return_value=self.payload()), \
                mock.patch.object(INTL.requests, "Session") as session_cls:
            session_cls.return_value.post.return_value = response
            with self.assertRaises(INTL.UnapprovedChannel):
                INTL.keepalive("token", "uid")
        self.assertEqual(1, session_cls.return_value.post.call_count)


if __name__ == "__main__":
    unittest.main()
