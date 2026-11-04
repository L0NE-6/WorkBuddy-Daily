import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from test_workbuddy_core import jwt_token


SOURCE = Path(__file__).resolve().parents[1] / "workbuddy_daily.py"
WORKER = r'''
import base64, json, os, runpy, sys, time, types
from pathlib import Path
source, directory, operation = sys.argv[1:]
root = Path(directory)
os.environ["WORKBUDDY_STATE_DIR"] = directory
os.environ.pop("WORKBUDDY_REFRESH_TOKEN", None)
import requests
requests.Session = lambda: (_ for _ in ()).throw(RuntimeError("network forbidden"))
module = runpy.run_path(source, run_name="workbuddy_test")
g = module["_refresh_account_once"].__globals__
def wait_file(name):
    deadline = time.monotonic() + 15
    while not (root / name).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("timeout waiting for " + name)
        time.sleep(.01)
def token(name, version):
    payload = json.dumps({"sub": name, "preferred_username": "actual-" + name,
                          "exp": int(time.time()) + 86400 * 30, "version": version}, separators=(",", ":"))
    return "eyJ." + base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + ".sig"
def refresh(rt, at):
    with (root / (operation + ".calls")).open("a", encoding="utf-8") as f:
        f.write(rt + "\n")
    (root / (operation + ".entered")).touch()
    if operation == "a":
        wait_file("release")
    name = rt.split("-")[0]
    return token(name, operation), name + "-rt-" + operation
g["refresh_one"] = refresh
(root / (operation + ".ready")).touch()
wait_file(operation + ".go")
if operation == "a":
    account = {"note": "alias-a", **json.loads((root / "wb_refresh_tokens.json").read_text(encoding="utf-8"))["alias-a"]}
    if not g["_refresh_account_once"](account, lambda msg: None):
        raise RuntimeError("401 refresh failed")
elif operation == "b":
    g["refresh_all"](verbose=False)
else:
    g["time"] = types.SimpleNamespace(time=lambda: time.time() + 11 * 86400,
        strftime=time.strftime, strptime=time.strptime, mktime=time.mktime, sleep=time.sleep)
    g["auto_refresh"]()
'''


def wait_for(path, process, timeout=15):
    end = time.monotonic() + timeout
    while not path.exists():
        if process.poll() is not None:
            out, err = process.communicate()
            raise AssertionError("worker exited %s: %s %s" % (process.returncode, out, err))
        if time.monotonic() > end:
            raise AssertionError("worker did not reach " + str(path))
        time.sleep(.01)


class FullProcessIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "wb_refresh_tokens.json"
        self.token_file = self.root / "WORKBUDDY_ACCESS_TOKEN.txt"
        updated = time.strftime("%Y-%m-%d %H:%M")
        def token(name):
            return jwt_token({"sub": name, "preferred_username": "actual-" + name,
                              "exp": int(time.time()) + 86400 * 30, "version": "old"})
        self.path.write_text(json.dumps({"alias-" + name: {"access_token": token(name),
            "refresh_token": name + "-rt-old", "updated": updated, "extra": "keep-" + name}
            for name in ("a", "b")}), encoding="utf-8")
        self.token_file.write_text(token("a") + "@" + token("b"), encoding="utf-8")
        self.processes = []
        self.addCleanup(self.stop_workers)

    def stop_workers(self):
        (self.root / "release").touch()
        for p in self.processes:
            if p.poll() is None:
                p.kill()
            p.communicate()

    def start(self, operation):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        process = subprocess.Popen([sys.executable, "-c", WORKER, str(SOURCE), str(self.root), operation],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   encoding="utf-8", errors="replace", env=env)
        self.processes.append(process)
        return process

    def test_real_module_401_and_manual_refresh_serialize_and_keep_all_tokens(self):
        first, second = self.start("a"), self.start("b")
        wait_for(self.root / "a.ready", first)
        wait_for(self.root / "b.ready", second)
        (self.root / "a.go").touch()
        wait_for(self.root / "a.entered", first)
        (self.root / "b.go").touch()
        time.sleep(.3)
        self.assertIsNone(second.poll())
        self.assertFalse((self.root / "b.entered").exists())
        (self.root / "release").touch()
        for process in (first, second):
            out, err = process.communicate(timeout=15)
            self.assertEqual(0, process.returncode, out + err)
        self.assertEqual(["a-rt-a", "b-rt-old"], (self.root / "b.calls").read_text().splitlines())
        store = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual({"alias-a", "alias-b"}, set(store))
        self.assertEqual("a-rt-b", store["alias-a"]["refresh_token"])
        self.assertEqual("b-rt-b", store["alias-b"]["refresh_token"])
        self.assertEqual("keep-a", store["alias-a"]["extra"])
        self.assertEqual("keep-b", store["alias-b"]["extra"])
        tokens = self.token_file.read_text(encoding="utf-8").split("@")
        self.assertEqual([store["alias-a"]["access_token"], store["alias-b"]["access_token"]], tokens)

    def test_real_module_401_and_automatic_refresh_serialize(self):
        first, second = self.start("a"), self.start("c")
        wait_for(self.root / "a.ready", first)
        wait_for(self.root / "c.ready", second)
        (self.root / "a.go").touch()
        wait_for(self.root / "a.entered", first)
        (self.root / "c.go").touch()
        time.sleep(.3)
        self.assertIsNone(second.poll())
        self.assertFalse((self.root / "c.entered").exists())
        (self.root / "release").touch()
        for process in (first, second):
            out, err = process.communicate(timeout=15)
            self.assertEqual(0, process.returncode, out + err)
        self.assertEqual(["a-rt-a", "b-rt-old"], (self.root / "c.calls").read_text().splitlines())
        store = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual("a-rt-c", store["alias-a"]["refresh_token"])
        self.assertEqual("b-rt-c", store["alias-b"]["refresh_token"])
        self.assertEqual("keep-a", store["alias-a"]["extra"])
        self.assertEqual("keep-b", store["alias-b"]["extra"])
        self.assertEqual([store["alias-a"]["access_token"], store["alias-b"]["access_token"]],
                         self.token_file.read_text(encoding="utf-8").split("@"))

    def test_process_death_releases_system_lock(self):
        first, second = self.start("a"), self.start("b")
        wait_for(self.root / "a.ready", first)
        wait_for(self.root / "b.ready", second)
        (self.root / "a.go").touch()
        wait_for(self.root / "a.entered", first)
        (self.root / "b.go").touch()
        time.sleep(.2)
        self.assertFalse((self.root / "b.entered").exists())
        first.kill()
        first.communicate(timeout=5)
        out, err = second.communicate(timeout=15)
        self.assertEqual(0, second.returncode, out + err)
        self.assertTrue((self.root / "b.entered").exists())


if __name__ == "__main__":
    unittest.main()
