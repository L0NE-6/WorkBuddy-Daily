import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "workbuddy_daily.py"
WORKER = r'''
import ast, contextlib, errno, json, os, sys, threading, time, uuid
from pathlib import Path
source, directory, name = sys.argv[1:]
root = Path(directory)
tree = ast.parse(Path(source).read_text(encoding="utf-8"))
names = {"_token_store_lock", "_write_refresh_recovery", "_save_refresh_store", "_refresh_account_once"}
body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
namespace = dict(os=os, json=json, time=time, uuid=uuid, errno=errno,
                 contextmanager=contextlib.contextmanager, _TOKEN_STORE_THREAD_LOCK=threading.Lock(),
                 REFRESH_STORE=str(root / "tokens.json"), _rebuild_token_file=lambda store: None)
def refresh(rt, at):
    (root / (name + ".entered")).touch()
    if name == "first":
        deadline = time.monotonic() + 10
        while not (root / "release").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("release timeout")
            time.sleep(.01)
    return name + "-new-at", name + "-new-rt"
namespace["refresh_one"] = refresh
exec(compile(ast.Module(body=body, type_ignores=[]), source, "exec"), namespace)
account = {"note": name, "access_token": name + "-old-at", "refresh_token": name + "-old-rt"}
(root / (name + ".ready")).touch()
if not namespace["_refresh_account_once"](account, lambda message: None):
    raise RuntimeError("refresh failed")
'''


class TokenProcessConcurrencyTests(unittest.TestCase):
    def test_separate_processes_serialize_refreshes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "tokens.json"
            path.write_text(json.dumps({name: {"access_token": name + "-old-at", "refresh_token": name + "-old-rt"}
                                        for name in ("first", "second")}), encoding="utf-8")
            def start(name):
                return subprocess.Popen([sys.executable, "-c", WORKER, str(SOURCE), temp_dir, name],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
            first = start("first")
            second = None
            try:
                deadline = time.monotonic() + 10
                while not (root / "first.entered").exists() and time.monotonic() < deadline:
                    if first.poll() is not None:
                        self.fail("first process failed: " + first.communicate()[1])
                    time.sleep(.01)
                self.assertTrue((root / "first.entered").exists())
                second = start("second")
                deadline = time.monotonic() + 10
                while not (root / "second.ready").exists() and time.monotonic() < deadline:
                    if second.poll() is not None:
                        self.fail("second process failed: " + second.communicate()[1])
                    time.sleep(.01)
                self.assertTrue((root / "second.ready").exists())
                # The second process has entered the call but must remain blocked.
                time.sleep(.2)
                self.assertIsNone(second.poll())
                self.assertFalse((root / "second.entered").exists())
                (root / "release").touch()
                for process in (first, second):
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(0, process.returncode, stdout + stderr)
                self.assertTrue((root / "second.entered").exists())
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual("first-new-rt", saved["first"]["refresh_token"])
                self.assertEqual("second-new-rt", saved["second"]["refresh_token"])
            finally:
                (root / "release").touch()
                for process in (first, second):
                    if process and process.poll() is None:
                        process.kill()
                        process.communicate()


if __name__ == "__main__":
    unittest.main()
