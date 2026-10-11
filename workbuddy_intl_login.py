#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔐 WorkBuddy AI 国际版 · 设备授权登录工具
════════════════════════════════════════════════════════════════
无需手机号，专为纯 OAuth（Google / GitHub / X）注册的国际版账号设计。

流程（WorkBuddy 客户端 ExternalLinkAuthenticationProvider）:
  1. POST /v2/plugin/auth/state?platform=workbuddy-ai  → {state, authUrl}
  2. 浏览器打开 authUrl，用 Google / GitHub / X 登录并授权
  3. 脚本每秒轮询 GET /v2/plugin/auth/token?state=…    → accessToken + refreshToken

用法:
  python workbuddy_intl_login.py                            # 打印链接 → 浏览器授权 → 自动拿令牌
  python workbuddy_intl_login.py --no-open                  # 只打印链接，不尝试自动打开浏览器
  python workbuddy_intl_login.py --timeout 600              # 自定义等待授权秒数（默认 300）
  python workbuddy_intl_login.py --refresh '备注|AT|uid|RT'  # 用已有 RT 刷新令牌

输出格式（直接填入 WORKBUDDY_INTL_TOKENS 环境变量 / 仓库 secret）:
  备注|AT|uid|RT

说明:
  · 国际版 www.workbuddy.ai 与国内版 www.workbuddy.cn 的登录态互不通用。
  · 国际版没有手机号绑定，短信登录接口对纯 OAuth 账号不可用，因此走本设备授权流程。
  · 令牌自带 refresh_token（约 1 年有效），workbuddy_intl_daily.py 会自动续期。
  · 国内版登录工具请用 workbuddy_login.py（短信验证码）；国际版无手机号绑定，故单独提供本工具。
  · 仅依赖 requests。
"""
import argparse
import base64
import json
import os
import sys
import time

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
try:
    from requests.packages.urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except Exception:
    pass

ENDPOINT = "https://www.workbuddy.ai"
PLATFORM = "workbuddy-ai"
DOMAIN = "www.workbuddy.ai"
UA = "WorkBuddy/5.4.2"

STATE_PATH = "/v2/plugin/auth/state"
TOKEN_PATH = "/v2/plugin/auth/token"
REFRESH_PATH = "/v2/plugin/auth/token/refresh"
ACCOUNT_PATH = "/v2/plugin/login/account"

RETRY_TOKEN = 11217       # 服务端「仍在等待授权」的应答码
POLL_INTERVAL = 1.0


def anon_headers():
    """匿名请求头：告诉网关本次调用不带任何既有凭证。"""
    return {
        "User-Agent": UA,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Domain": DOMAIN,
        "X-No-Authorization": "true",
        "X-No-User-Id": "true",
        "X-No-Enterprise-Id": "true",
        "X-No-Department-Info": "true",
    }


def _uid_of(tok):
    """从 JWT 的 sub 取账号标识；与 workbuddy_intl_daily.py 的规则保持一致。"""
    try:
        payload = str(tok).split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload)).get("sub") or "")
    except Exception:
        return ""


def start_login():
    """第一步：申请一次授权会话，返回 {state, authUrl}。"""
    url = ENDPOINT + STATE_PATH + "?platform=" + PLATFORM
    r = requests.post(url, headers=anon_headers(), json={}, timeout=25, verify=False)
    d = r.json()
    data = d.get("data") if isinstance(d, dict) else None
    if not isinstance(data, dict) or not data.get("state") or not data.get("authUrl"):
        raise RuntimeError("auth/state 未返回 state/authUrl：%s"
                           % json.dumps(d, ensure_ascii=False)[:300])
    return data


def poll_token(state, timeout_s=300, on_tick=None):
    """第二步：轮询拿令牌，直到用户在浏览器完成授权。"""
    url = ENDPOINT + TOKEN_PATH + "?state=" + state
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(url, headers=anon_headers(), timeout=20, verify=False)
            d = r.json()
        except Exception:
            time.sleep(POLL_INTERVAL)
            continue
        data = d.get("data") if isinstance(d, dict) else None
        if isinstance(data, dict) and data.get("accessToken"):
            return data
        code = d.get("code") if isinstance(d, dict) else None
        if code not in (RETRY_TOKEN, 0, None):
            msg = str(d.get("msg", ""))[:120]
            if msg:
                raise RuntimeError("轮询失败：%s" % msg)
        if on_tick:
            on_tick()
        time.sleep(POLL_INTERVAL)
    raise TimeoutError("等待授权超时（%d 秒）。请重新运行并尽快在浏览器完成登录。" % timeout_s)


def fetch_account(state, at):
    """第三步：用新令牌取账号信息（拿 uid / 昵称）。"""
    url = ENDPOINT + ACCOUNT_PATH + "?state=" + state
    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Authorization": "Bearer " + str(at),
        "X-Domain": DOMAIN,
        "X-No-User-Id": "true",
        "X-No-Enterprise-Id": "true",
        "X-No-Department-Info": "true",
    }
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            r = requests.get(url, headers=headers, timeout=15, verify=False)
            d = r.json()
        except Exception:
            time.sleep(POLL_INTERVAL)
            continue
        data = d.get("data") if isinstance(d, dict) else None
        if isinstance(data, dict) and data.get("uid"):
            return data
        time.sleep(POLL_INTERVAL)
    return {}


def refresh_token(rt, at=""):
    """用 refresh_token 换一套新令牌。"""
    if not rt:
        raise RuntimeError("未提供 refresh token")
    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Refresh-Token": str(rt),
        "X-Auth-Refresh-Source": "plugin",
        "X-Domain": DOMAIN,
        "Authorization": "Bearer " + str(at or ""),
    }
    r = requests.post(ENDPOINT + REFRESH_PATH, headers=headers, json={}, timeout=20, verify=False)
    d = r.json()
    data = d.get("data") if isinstance(d, dict) else None
    if not isinstance(data, dict) or not data.get("accessToken"):
        raise RuntimeError("刷新失败：%s" % json.dumps(d, ensure_ascii=False)[:200])
    return data


def _emit(note, at, uid, rt, auth=None):
    """输出 env 行并落盘到 wb_intl_login_result.json。"""
    line = "%s|%s|%s|%s" % (note, at, uid, rt)
    print()
    print("=" * 64)
    print("✅ 将下面的值填入 WORKBUDDY_INTL_TOKENS（多账号换行分隔）")
    print("=" * 64)
    print(line)
    print("=" * 64)

    record = {"note": note, "uid": uid, "env_line": line,
              "login_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if isinstance(auth, dict):
        record["expires_in"] = auth.get("expiresIn")
        record["refresh_expires_in"] = auth.get("refreshExpiresIn")
    save_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "wb_intl_login_result.json")
    items = []
    if os.path.exists(save_file):
        try:
            items = json.load(open(save_file, encoding="utf-8"))
        except Exception:
            items = []
    items.append(record)
    with open(save_file, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=1)
    print("📁 结果已保存到: %s" % save_file)


def main():
    parser = argparse.ArgumentParser(description="WorkBuddy AI 国际版设备授权登录")
    parser.add_argument("--no-open", action="store_true", help="不尝试自动打开浏览器")
    parser.add_argument("--timeout", type=int, default=300, help="等待授权的秒数（默认 300）")
    parser.add_argument("--refresh", metavar="ENTRY",
                        help="用已有 RT 刷新令牌，格式：备注|AT|uid|RT")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════╗")
    print("║ 🔐 WorkBuddy AI 国际版 · 设备授权登录工具        ║")
    print("╚══════════════════════════════════════════════════╝")

    # 模式二：刷新已有令牌
    if args.refresh:
        parts = args.refresh.split("|")
        note = parts[0] if len(parts) > 0 and parts[0] else "账号1"
        old_at = parts[1] if len(parts) > 1 else ""
        uid = parts[2] if len(parts) > 2 else ""
        rt = parts[3] if len(parts) > 3 else ""
        print("🔁 正在用 refresh_token 刷新…")
        data = refresh_token(rt, old_at)
        new_at = data["accessToken"]
        new_rt = data.get("refreshToken") or rt
        uid = uid or _uid_of(new_at)
        print("  ✅ 刷新成功")
        _emit(note, new_at, uid, new_rt, data)
        return 0

    # 模式一：设备授权登录
    print("[1/3] 申请授权会话…")
    started = start_login()
    state = started["state"]
    auth_url = started["authUrl"]
    print("  ✅ 已生成授权链接")
    print()
    print("=" * 64)
    print("👉 请在浏览器打开下面的链接，用 Google / GitHub / X 登录并授权：")
    print()
    print("   " + auth_url)
    print("=" * 64)
    print()
    if not args.no_open:
        try:
            import webbrowser
            webbrowser.open(auth_url, new=2)
            print("（已尝试自动打开浏览器）")
        except Exception:
            pass

    print("[2/3] 等待你在浏览器完成授权", end="", flush=True)

    def tick():
        sys.stdout.write(".")
        sys.stdout.flush()

    auth = poll_token(state, args.timeout, tick)
    print("\n  ✅ 已获取令牌")

    at = auth["accessToken"]
    rt = auth.get("refreshToken") or ""
    uid = _uid_of(at)

    print("[3/3] 获取账号信息…")
    account = fetch_account(state, at)
    if account.get("uid"):
        uid = account["uid"]
    note = account.get("nickname") or (uid[:8] if uid else "账号1")
    print("  ✅ %s（uid=%s）" % (note, uid or "?"))

    _emit(note, at, uid, rt, auth)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)
    except Exception as error:
        print("\n❌ %s" % error)
        sys.exit(1)
