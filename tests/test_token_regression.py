import base64
import contextlib
from concurrent.futures import ThreadPoolExecutor
import threading
import io
import json
import os
from pathlib import Path
import tempfile
import types
import time
import unittest
from unittest import mock

from test_workbuddy_core import jwt_token, load_functions


class TokenRegressionTests(unittest.TestCase):
    def test_token_file_rebuild_matches_jwt_identity_when_store_key_is_alias(self):
        old = jwt_token({"sub": "one", "preferred_username": "actual-user", "version": "old"})
        new = jwt_token({"sub": "one", "preferred_username": "actual-user", "version": "new"})
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "access.txt"
            path.write_text(old, encoding="utf-8")
            module = load_functions("_rebuild_token_file", "jwt_user", os=os, json=json,
                                    base64=__import__("base64"), TOKEN_FILE=str(path))
            module["_rebuild_token_file"]({"alias": {"access_token": new}})
            self.assertEqual(new, path.read_text(encoding="utf-8"))

    def test_manual_refresh_preserves_account_keys_and_other_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tokens.json"
            original = {"alias": {"access_token": "old-at", "refresh_token": "old-rt", "extra": "keep"},
                        "actual-user": {"access_token": "other-at", "refresh_token": "other-rt"}}
            path.write_text(json.dumps(original), encoding="utf-8")
            module = load_functions("refresh_all", os=os, json=json, time=types.SimpleNamespace(strftime=time.strftime, sleep=lambda _: None), REFRESH_STORE=str(path),
                refresh_one=lambda rt, at: ("new-at", "new-rt") if rt == "old-rt" else (None, "failed"),
                jwt_user=lambda at: "actual-user", _rebuild_token_file=lambda store: None)
            with contextlib.redirect_stdout(io.StringIO()):
                module["refresh_all"](verbose=False)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("new-rt", saved["alias"]["refresh_token"])
            self.assertEqual("keep", saved["alias"]["extra"])
            self.assertEqual(original["actual-user"], saved["actual-user"])

    def test_six_accounts_repeated_simultaneous_refresh(self):
        for iteration in range(25):
            with self.subTest(iteration=iteration), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                path = root / "tokens.json"
                token_file = root / "access.txt"
                def at(name, version):
                    return jwt_token({"sub": name, "preferred_username": "actual-" + name, "version": version})
                names = ["user" + str(i) for i in range(6)]
                original = {"alias-" + name: {"access_token": at(name, "old"),
                    "refresh_token": name + "-old", "extra": "keep-" + name} for name in names}
                path.write_text(json.dumps(original), encoding="utf-8")
                token_file.write_text("@".join(at(name, "old") for name in names), encoding="utf-8")
                gate = threading.Barrier(len(names))
                def refresh(rt, old_at):
                    name = rt.rsplit("-", 1)[0]
                    time.sleep(.001)
                    return at(name, "new"), name + "-new"
                module = load_functions("_refresh_account_once", "_rebuild_token_file", "jwt_user", os=os, json=json,
                    base64=base64, time=time, REFRESH_STORE=str(path), TOKEN_FILE=str(token_file), refresh_one=refresh)
                accounts = [{"note": "alias-" + name, **original["alias-" + name]} for name in names]
                def call(account):
                    gate.wait(timeout=5)
                    return module["_refresh_account_once"](account, lambda message: None)
                with ThreadPoolExecutor(max_workers=6) as executor:
                    self.assertEqual([True] * 6, list(executor.map(call, accounts)))
                saved = json.loads(path.read_text(encoding="utf-8"))
                for name in names:
                    self.assertEqual(name + "-new", saved["alias-" + name]["refresh_token"])
                    self.assertEqual("keep-" + name, saved["alias-" + name]["extra"])
                self.assertEqual({at(name, "new") for name in names},
                                 set(token_file.read_text(encoding="utf-8").split("@")))

    def test_same_rt_with_concurrent_at_change_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "tokens.json"
            path.write_text(json.dumps({"user": {"access_token": "old-at", "refresh_token": "old-rt"}}), encoding="utf-8")
            def refresh(rt, at):
                path.write_text(json.dumps({"user": {"access_token": "external-at", "refresh_token": "old-rt"}}), encoding="utf-8")
                return "new-at", "new-rt"
            module = load_functions("_refresh_account_once", os=os, json=json, time=time, REFRESH_STORE=str(path),
                                    refresh_one=refresh, _rebuild_token_file=mock.Mock())
            account = {"note": "user", "access_token": "old-at", "refresh_token": "old-rt"}
            with self.assertRaises(RuntimeError):
                module["_refresh_account_once"](account, lambda message: None)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("external-at", saved["user"]["access_token"])
            copies = list(root.glob("tokens.json.tmp-*"))
            self.assertEqual(1, len(copies))
            self.assertEqual("new-rt", json.loads(copies[0].read_text(encoding="utf-8"))["user"]["refresh_token"])

    def test_invalid_latest_store_keeps_recovery_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "tokens.json"
            path.write_text(json.dumps({"user": {"access_token": "old-at", "refresh_token": "old-rt"}}), encoding="utf-8")
            def refresh(rt, at):
                path.write_text("{broken", encoding="utf-8")
                return "new-at", "new-rt"
            module = load_functions("_refresh_account_once", os=os, json=json, time=time, REFRESH_STORE=str(path),
                                    refresh_one=refresh, _rebuild_token_file=mock.Mock())
            account = {"note": "user", "access_token": "old-at", "refresh_token": "old-rt"}
            with self.assertRaises(RuntimeError):
                module["_refresh_account_once"](account, lambda message: None)
            self.assertEqual("{broken", path.read_text(encoding="utf-8"))
            self.assertEqual("old-rt", account["refresh_token"])
            copies = list(root.glob("tokens.json.tmp-*"))
            self.assertEqual(1, len(copies))
            self.assertEqual("new-rt", json.loads(copies[0].read_text(encoding="utf-8"))["user"]["refresh_token"])

    def test_run_account_retries_401_using_the_reloaded_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tokens.json"
            path.write_text(json.dumps({"user": {"access_token": "new-at", "refresh_token": "new-rt"}}), encoding="utf-8")
            seen = []
            class Response:
                def __init__(self, code):
                    self.status_code, self.text = code, "denied"
            class Session:
                def __init__(self, token):
                    self.token = token
                def get(self, *args, **kwargs):
                    return Response(401 if self.token == "old-at" else 403)
            def new_api(token):
                seen.append(token)
                return Session(token)
            module = load_functions("_refresh_account_once", "run_account", os=os, json=json, time=time,
                REFRESH_STORE=str(path), refresh_one=mock.Mock(), _rebuild_token_file=mock.Mock(),
                BASE="https://example.invalid", new_api=new_api, uid_of=lambda token: "uid",
                nickname_of=lambda token: "nick", mask_phone=lambda value: value,
                _json_or_empty=lambda response: {"code": "denied"},
                is_account_restricted=lambda *args: False, is_enterprise_denial=lambda *args: True)
            account = {"note": "user", "access_token": "old-at", "refresh_token": "old-rt"}
            with contextlib.redirect_stdout(io.StringIO()):
                _, summary = module["run_account"](1, account, False)
            self.assertEqual(["old-at", "new-at"], seen)
            self.assertTrue(summary["enterprise"])
            module["refresh_one"].assert_not_called()

    def test_same_rt_with_newer_at_is_loaded_without_another_refresh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tokens.json"
            path.write_text(json.dumps({"user": {"access_token": "new-at", "refresh_token": "same-rt"}}), encoding="utf-8")
            refresh = mock.Mock()
            module = load_functions("_refresh_account_once", os=os, json=json, time=time, REFRESH_STORE=str(path),
                                    refresh_one=refresh, _rebuild_token_file=mock.Mock())
            account = {"note": "user", "access_token": "old-at", "refresh_token": "same-rt"}
            self.assertTrue(module["_refresh_account_once"](account, lambda message: None))
            self.assertEqual("new-at", account["access_token"])
            refresh.assert_not_called()

    def test_manual_refresh_saves_each_rotation_before_next_network_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tokens.json"
            path.write_text(json.dumps({name: {"access_token": name + "-at", "refresh_token": name + "-rt"}
                                        for name in ("a", "b")}), encoding="utf-8")
            def refresh(rt, at):
                if rt == "b-rt":
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual("a-new-rt", saved["a"]["refresh_token"])
                    raise KeyboardInterrupt("simulate process interruption")
                return "a-new-at", "a-new-rt"
            module = load_functions("refresh_all", os=os, json=json,
                time=types.SimpleNamespace(strftime=time.strftime, sleep=lambda _: None), REFRESH_STORE=str(path),
                refresh_one=refresh, jwt_user=lambda at: "?", _rebuild_token_file=lambda store: None)
            with self.assertRaises(KeyboardInterrupt):
                module["refresh_all"](verbose=False)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("a-new-rt", saved["a"]["refresh_token"])
            self.assertEqual("b-rt", saved["b"]["refresh_token"])

    def test_failed_rebuild_keeps_saved_rt_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tokens.json"
            path.write_text(json.dumps({"user": {"access_token": "old-at", "refresh_token": "old-rt"}}), encoding="utf-8")
            logs = []
            module = load_functions("_refresh_account_once", os=os, json=json, time=time, REFRESH_STORE=str(path),
                refresh_one=lambda rt, at: ("new-at", "new-rt"),
                _rebuild_token_file=mock.Mock(side_effect=OSError("AT failure")))
            first = {"note": "user", "access_token": "old-at", "refresh_token": "old-rt"}
            self.assertTrue(module["_refresh_account_once"](first, logs.append))
            self.assertEqual("new-rt", json.loads(path.read_text(encoding="utf-8"))["user"]["refresh_token"])
            self.assertTrue(any("AT 文件重建失败" in text for text in logs))
            second = {"note": "user", "access_token": "old-at", "refresh_token": "old-rt"}
            self.assertTrue(module["_refresh_account_once"](second, logs.append))
            self.assertEqual("new-rt", second["refresh_token"])

    def test_bootstrap_does_not_replace_unreadable_store_with_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tokens.json"
            path.write_text("{broken", encoding="utf-8")
            module = load_functions("_bootstrap_store", os=os, json=json, time=time, REFRESH_STORE=str(path),
                _parse_env_tokens=lambda raw: [("new", "at", "rt")], _token_user=lambda at: "")
            with mock.patch.dict(os.environ, {"WORKBUDDY_REFRESH_TOKEN": "new:at:rt"}):
                with contextlib.redirect_stdout(io.StringIO()):
                    module["_bootstrap_store"]()
            self.assertEqual("{broken", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

