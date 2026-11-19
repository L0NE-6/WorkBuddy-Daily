import base64
import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "workbuddy_login", ROOT / "workbuddy_login.py"
)
LOGIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOGIN)


def make_jwt(typ, issuer="https://copilot.tencent.com/auth/realms/copilot"):
    def enc(value):
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return "%s.%s.sig" % (
        enc({"alg": "HS512", "typ": "JWT"}),
        enc({
            "typ": typ,
            "iss": issuer,
            "sub": "uid-test",
            "preferred_username": "13800000000",
            "exp": 2_000_000_000,
        }),
    )


class LoginTokenValidationTests(unittest.TestCase):
    def test_valid_cn_tokens_pass_structure_check(self):
        at = make_jwt("Bearer")
        rt = make_jwt("Offline")
        self.assertEqual([], LOGIN.validate_credentials(at, rt))
        self.assertTrue(LOGIN._token_report(at)["jwt"])
        self.assertEqual("Bearer", LOGIN._token_report(at)["typ"])
        self.assertEqual("Offline", LOGIN._token_report(rt)["typ"])

    def test_opaque_refresh_token_is_rejected_before_output(self):
        errors = LOGIN.validate_credentials(make_jwt("Bearer"), "x" * 2713)
        self.assertTrue(any("RT" in row and "2713" in row for row in errors))

    def test_swapped_at_rt_are_rejected(self):
        errors = LOGIN.validate_credentials(
            make_jwt("Offline"),
            make_jwt("Bearer"),
        )
        self.assertTrue(any("AT 的 typ=Offline" in row for row in errors))
        self.assertTrue(any("RT 的 typ=Bearer" in row for row in errors))

    def test_verify_returns_rotated_tokens(self):
        at = make_jwt("Bearer")
        rt = make_jwt("Offline")
        new_at = make_jwt("Bearer")
        new_rt = make_jwt("Offline")

        class Response:
            def json(self):
                return {
                    "code": 0,
                    "data": {
                        "accessToken": new_at,
                        "refreshToken": new_rt,
                    },
                }

        with mock.patch.object(LOGIN.requests, "post", return_value=Response()) as post:
            ok, got_at, got_rt = LOGIN.verify_rt(rt, at)

        self.assertTrue(ok)
        self.assertEqual(new_at, got_at)
        self.assertEqual(new_rt, got_rt)
        headers = post.call_args.kwargs["headers"]
        self.assertEqual("Bearer " + at, headers["Authorization"])
        self.assertEqual("uid-test", headers["X-User-Id"])


if __name__ == "__main__":
    unittest.main()
