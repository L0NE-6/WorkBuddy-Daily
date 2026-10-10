#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy AI（国际版）每日活跃 - 青龙脚本
────────────────────────────────────────────────────────────
国际版的每日奖励不是普通签到，而是一条「活跃任务」链：

  1. 探测   POST /billing/meter/checkin-activity-status
            → data.active（活动是否开放）/ data.today_checked_in（今日是否已领）
  2. 领取   POST /v2/billing/meter/daily-checkin（仅当活动开放且今日未领）
  3. 保活   POST /v2/chat/completions（发一次最小流式对话）
            活动要求账号当天有过一次模型调用；免费模型不消耗积分。

口径说明：
  - 活动未开放是**正常状态**，不会当成失败；
  - 保活成功不等于签到成功：两者分开报告；
  - 一个账号只需要跑一轮，结果推送企业微信 / PushPlus。

环境变量：
  WORKBUDDY_INTL_TOKENS  多账号，换行或 & 分隔，每条：`token` / `备注|token` / `备注|token|uid` / `备注|token|uid|refresh_token`（带 RT 可自动续期）
                         （token 是 www.workbuddy.ai 的登录态，与国内版不通用）
  WORKBUDDY_TOKEN        单账号兜底（国际版登录态）
  WORKBUDDY_INTL_MODELS  可选，保活模型链，逗号分隔；默认官方免费的四个
  QYWX_TOKEN             企业微信机器人（也兼容 WECHAT_WEBHOOK）
  PLUSPLUS_TOKEN         PushPlus
  NO_NOTIFY              设 1 关闭推送

用法：
  python workbuddy_intl_daily.py
  python workbuddy_intl_daily.py --only 1
  python workbuddy_intl_daily.py --no-keepalive   # 只探测与领取，不做保活
"""

import base64
import json
import os
import sys
import time
import uuid

import requests
import urllib3

urllib3.disable_warnings()

BASE = "https://www.workbuddy.ai"
STATUS_PATH = "/billing/meter/checkin-activity-status"
CLAIM_PATH = "/v2/billing/meter/daily-checkin"
CHAT_PATH = "/v2/chat/completions"
REFRESH_PATH = "/v2/plugin/auth/token/refresh"
TOKEN_STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wb_intl_tokens.json")

CLIENT_VERSION = "5.5.2"
CLI_VERSION = "2.137.1"
UA = "WorkBuddy/%s WorkBuddy AI/%s CLI/%s" % (CLIENT_VERSION, CLIENT_VERSION, CLI_VERSION)
DEFAULT_MODELS = ("deepseek-v4.1-flash", "hy3", "hy4-preview", "hy4-preview-f")

ONLY = None
NO_KEEPALIVE = False


def log(msg):
    print("%s %s" % (time.strftime("[%H:%M:%S]"), msg), flush=True)


def mask(text, keep=6):
    text = str(text or "")
    if len(text) <= keep + 4:
        return text
    return text[:keep] + "****" + text[-4:]


def uid_of(token):
    """从 JWT 的 sub 取账号标识；取不到返回空串。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload)).get("sub") or "")
    except Exception:
        return ""


def api_headers(token, uid, accept=None):
    headers = {
        "Accept": accept or "application/json",
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % token,
        "User-Agent": UA,
        "X-IDE-Type": "WorkBuddy",
        "X-IDE-Name": "WorkBuddy AI",
        "X-IDE-Version": CLIENT_VERSION,
        "X-Product": "WorkBuddy AI",
    }
    if uid:
        headers["X-User-Id"] = uid
    return headers


def chat_headers(token, uid, request_id):
    headers = api_headers(token, uid, accept="text/event-stream")
    headers.pop("X-Product", None)
    headers.update({
        "X-Product": "WorkBuddy AI",
        "X-Request-ID": request_id,
        "X-Conversation-Request-ID": request_id,
        "X-Conversation-ID": request_id,
        "X-Session-ID": request_id,
        "X-Agent-Purpose": "conversation",
        "X-Conversation-Message-ID": request_id,
        "X-Root-Request-ID": request_id,
        "Origin": BASE,
        "Referer": BASE + "/",
        "X-Requested-With": "XMLHttpRequest",
    })
    return headers


def post_json(path, token, uid, body=None, timeout=25, retries=3):
    url = BASE + path
    last = None
    for attempt in range(1, retries + 1):
        try:
            session = requests.Session()
            session.trust_env = False
            response = session.post(url, headers=api_headers(token, uid), json=body if body is not None else {},
                                    timeout=timeout, verify=False)
            if 500 <= response.status_code < 600 and attempt < retries:
                last = RuntimeError("http %s" % response.status_code)
                time.sleep(1.0 * attempt)
                continue
            return response
        except Exception as error:
            last = error
            if attempt < retries:
                time.sleep(1.0 * attempt)
    raise RuntimeError("请求失败: %s" % last)


def json_of(response):
    try:
        return response.json()
    except Exception:
        return {}


def first_text(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def claim_label(code, msg):
    mapping = {
        41000: "活动未开始或已结束",
        40901: "任务暂不可领取",
        401: "登录状态失效，请重新登录",
        40100: "登录状态失效，请重新登录",
    }
    return mapping.get(code) or msg or ("code=%s" % code)


def detect_activity(token, uid):
    """返回 (status_available, active, today_checked_in, note)。"""
    response = post_json(STATUS_PATH, token, uid)
    payload = json_of(response)
    code = payload.get("code")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    available = response.status_code == 200 and code == 0
    active = bool(data.get("active"))
    checked = bool(data.get("today_checked_in", data.get("todayCheckedIn")))
    if not available:
        note = "活动未开放（%s）" % claim_label(code, first_text(payload.get("msg"), payload.get("message")) or "HTTP %s" % response.status_code)
    elif checked:
        note = "今日已领取"
    elif active:
        note = "活动开放，今日未领"
    else:
        note = "活动已关闭"
    return available, active, checked, note


def claim_daily(token, uid):
    """领取每日奖励；重复领取（400/409 + 已签文案）算已完成。"""
    response = post_json(CLAIM_PATH, token, uid)
    payload = json_of(response)
    code = payload.get("code")
    message = first_text(payload.get("msg"), payload.get("message"))
    if response.status_code == 200 and code == 0:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        gained = data.get("credits", data.get("reward", data.get("amount")))
        return True, ("领取成功 +%s" % gained) if gained else "领取成功"
    duplicate = ("已签" in message) or ("已领" in message) or ("already" in message.lower())
    if response.status_code in (400, 409) and duplicate:
        return True, "今日已领取"
    return False, claim_label(code, message) or ("HTTP %s" % response.status_code)


def keepalive(token, uid):
    """发一次最小流式对话，让上游把今天记成「活跃」；逐个尝试免费模型。"""
    models = [m.strip() for m in (os.environ.get("WORKBUDDY_INTL_MODELS") or "").split(",") if m.strip()]
    if not models:
        models = list(DEFAULT_MODELS)
    last = ""
    for model in models:
        request_id = str(uuid.uuid4())
        body = {
            "model": model,
            "stream": True,
            "max_tokens": 16,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "hi"},
            ],
        }
        try:
            session = requests.Session()
            session.trust_env = False
            response = session.post(BASE + CHAT_PATH, headers=chat_headers(token, uid, request_id),
                                    json=body, timeout=60, verify=False, stream=True)
            ok = response.status_code == 200
            try:
                response.close()
            except Exception:
                pass
            if ok:
                return True, model
            last = "HTTP %s" % response.status_code
        except Exception as error:
            last = str(error)
    return False, last or "未知错误"


# ---------- 令牌续期（可选） ----------
# 需要一条 refresh_token（RT）才能续期；只给 AT 时按短期令牌使用，
# 到期后重新获取即可。续期结果写回 wb_intl_tokens.json。


def load_store():
    try:
        with open(TOKEN_STORE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_store(store):
    try:
        with open(TOKEN_STORE, "w", encoding="utf-8") as fh:
            json.dump(store, fh, ensure_ascii=False, indent=2)
    except Exception as error:
        log("⚠️ 令牌缓存写入失败: %s" % error)


def refresh_token(at, rt):
    """用 RT 换新令牌；返回 (新AT, 新RT) 或 (None, 原因)。"""
    if not rt:
        return None, "未提供 refresh token"
    try:
        session = requests.Session()
        session.trust_env = False
        response = session.post(BASE + REFRESH_PATH, json={}, timeout=20, verify=False,
                                headers={"X-Refresh-Token": rt,
                                         "X-Auth-Refresh-Source": "plugin",
                                         "Content-Type": "application/json"})
        payload = response.json()
    except Exception as error:
        return None, str(error)
    if response.status_code != 200:
        return None, "HTTP %s" % response.status_code
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    token = data.get("accessToken") or data.get("access_token")
    if payload.get("code") == 0 and token:
        return token, (data.get("refreshToken") or data.get("refresh_token") or rt)
    return None, first_text(payload.get("msg"), payload.get("message")) or "code=%s" % payload.get("code")



def parse_accounts(raw):
    accounts = []
    for index, line in enumerate((raw or "").replace("&", "\n").splitlines(), 1):
        line = line.strip().strip('"\'')
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        note, token, uid, rt = "账号%d" % index, "", "", ""
        if len(parts) == 1:
            token = parts[0]
        elif len(parts) == 2:
            note, token = parts
        elif len(parts) == 3:
            note, token, uid = parts
        else:
            note, token, uid, rt = parts[0], parts[1], parts[2], parts[3]
        if not token:
            continue
        accounts.append({"note": note, "token": token, "uid": uid or uid_of(token), "refresh_token": rt})
    return accounts


def notify(text, title="WorkBuddy AI 每日活跃"):
    raw = text.encode("utf-8")[:1800].decode("utf-8", "ignore")
    key = os.getenv("QYWX_TOKEN", "").strip() or os.getenv("WECHAT_WEBHOOK", "").strip()
    if key:
        key = key.split("key=")[-1]
        try:
            requests.post("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + key,
                          json={"msgtype": "text", "text": {"content": title + "\n" + raw}}, timeout=15)
        except Exception as error:
            log("⚠️ 企业微信推送失败: %s" % error)
    token = os.getenv("PLUSPLUS_TOKEN", "").strip()
    if token:
        try:
            requests.post("http://www.pushplus.plus/send",
                          json={"token": token, "title": title, "content": raw, "template": "txt"}, timeout=15)
        except Exception as error:
            log("⚠️ PushPlus 推送失败: %s" % error)


def main():
    global ONLY, NO_KEEPALIVE
    args = sys.argv[1:]
    if "--no-keepalive" in args:
        NO_KEEPALIVE = True
    if "--only" in args:
        try:
            ONLY = int(args[args.index("--only") + 1])
        except Exception:
            ONLY = None

    raw = os.environ.get("WORKBUDDY_INTL_TOKENS", "").strip()
    if not raw:
        single = os.environ.get("WORKBUDDY_TOKEN", "").strip()
        raw = single
    accounts = parse_accounts(raw)
    if not accounts:
        log("❌ 未配置 WORKBUDDY_INTL_TOKENS（或 WORKBUDDY_TOKEN）。")
        log("   国际版登录态与国内版不通用，请从 www.workbuddy.ai 获取。")
        return 1

    log("╔══════════════════════════════════════════╗")
    log("║ 🤖 WorkBuddy AI 国际版 每日活跃          ║")
    log("╚══════════════════════════════════════════╝")
    log("👥 账号数: %d" % len(accounts))

    lines = []
    for index, account in enumerate(accounts, 1):
        if ONLY and index != ONLY:
            continue
        log("👤 [%d] %s  token=%s" % (index, account["note"], mask(account["token"])))
        try:
            if account.get("refresh_token"):
                new_at, new_rt = refresh_token(account["token"], account["refresh_token"])
                if new_at:
                    account["token"], account["refresh_token"] = new_at, new_rt
                    log("   🔑 令牌已续期（%s）" % mask(new_at))
                    store = load_store()
                    store[account["uid"] or account["note"]] = {"access_token": new_at, "refresh_token": new_rt}
                    save_store(store)
                else:
                    log("   ⚠️ 令牌续期失败: %s（改用现有令牌）" % new_rt)
            available, active, checked, note = detect_activity(account["token"], account["uid"])
            log("   探测: %s" % note)
            if available and active and not checked:
                ok, message = claim_daily(account["token"], account["uid"])
                log("   %s 领取: %s" % ("✅" if ok else "❌", message))
            else:
                message = note
            if NO_KEEPALIVE:
                keep_ok, keep_note = False, "已跳过（--no-keepalive）"
            else:
                keep_ok, keep_note = keepalive(account["token"], account["uid"])
                log("   %s 保活: %s" % ("✅" if keep_ok else "⚠️", keep_note))
            lines.append("账号%d %s：%s；保活%s" % (
                index, account["note"], message,
                ("成功（%s）" % keep_note) if keep_ok else ("未成功（%s）" % keep_note)))
        except Exception as error:
            log("   ❌ 异常: %s" % error)
            lines.append("账号%d %s：❌ %s" % (index, account["note"], error))
        time.sleep(2)

    summary = "🤖 WorkBuddy AI 国际版每日活跃\n" + "\n".join(lines) + "\n🕐 " + time.strftime("%Y-%m-%d %H:%M")
    log(summary)
    if os.getenv("NO_NOTIFY", "").strip() != "1" and lines:
        notify(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())