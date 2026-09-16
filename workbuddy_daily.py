#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🌱 WorkBuddy Daily - 全能签到脚本 v2.0
════════════════════════════════════════════════════════════════

📌 这是什么
   一个脚本搞定 WorkBuddy 成长中心全部自动化：Token 自动续期、积分/用量查询、
   18 项成长任务、8 项互动玩法、自动领奖，全部无人值守。

✨ 特性
   🔐 Token 永续     只配一个刷新令牌变量，脚本自动续期（90 天滚动，永不过期）
   ✅ 18 项任务      14 项纯 API（云端直接跑）+ 2 项桌面任务（自动换血，非 Windows 跳过）
   🎮 8 项互动玩法   抽奖、盲盒、Buddy、派猫猫旅行、连签兑换、补签卡、礼包补偿、徽章
   💰 三类查询       积分套餐（剩余/总量/已用）、用量统计、成长数据（等级/连签/能量）
   🎁 自动领奖       扫描全部已完成任务，自动领取积分与能量
   📢 推送通知       可选 PUSHPLUS_TOKEN，运行结果推送到微信
   🧩 幂等安全       重复运行只补缺口，不会重复领取或重复操作
   🐧 青龙友好       非 Windows 自动跳过桌面任务，纯 API 部分直接跑

🚀 使用方法（青龙面板三步）
   1. 上传脚本     workbuddy_daily.py
   2. 设置变量     WORKBUDDY_REFRESH_TOKEN = 每行一个 "手机号:AT:RT"（多账号换行分隔）
   3. 定时任务     0 7,12 * * *    日常全流程
                  30 23 * * *     夜猫子活动窗口（23:00-08:00）

⌨️ 命令行参数
   python workbuddy_daily.py               全流程：续期 → 查询 → 任务 → 领奖
   python workbuddy_daily.py --refresh     仅刷新所有账号 Token
   python workbuddy_daily.py --query       仅查询积分/用量/成长
   python workbuddy_daily.py --no-desktop  跳过桌面任务（非 Windows 自动生效）
   python workbuddy_daily.py --only 3      只跑第 3 个账号

🔑 环境变量
   WORKBUDDY_REFRESH_TOKEN   【必填】多账号刷新令牌，换行分隔
   PUSHPLUS_TOKEN            【可选】推送通知

获取变量值（首次必看）
   第一步：在电脑上安装并登录 WorkBuddy 桌面端
   第二步：登录成功后，用记事本打开下面的文件：
      C:/Users/你的用户名/AppData/Local/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info
      (AppData 是隐藏文件夹，文件管理器地址栏直接粘贴上面的路径即可)
   第三步：在文件里搜索 accessToken 和 refreshToken，后面各跟一串很长的
      eyJ 开头的字符串，那就是 AT 和 RT
   第四步：按下面的格式拼一行，多个账号就写多行：

      手机号:AT那串:RT那串

   示例（1个账号写一行，换行分隔）：
      1XXXXXXXXXX:eyJhbGciOiJSUzI1NiIs...很长...:eyJhbGciOiJIUzUxMiIs...也很长...
      1XXXXXXXXXX:eyJhbGciOiJSUzI1NiIs...:eyJhbGciOiJIUzUxMiIs...

   ⚠️ 注意：AT 和 RT 之间用英文冒号 : 分隔，等号后面的引号不要带
   ⚠️ RT 是你唯一的续期凭据，泄露了别人就能操作你的账号

📦 任务清单
   ☁️ 云端任务（14 项，纯 API）
      每日签到 · 设计创意模式 · 探索优秀灵感 · 召唤3次专家团 · 发现应用
      企鹅教师助手 · 和平精英主题 · 体验资料库 · 设置自动化任务 · 召唤5次专家
      使用5个模板 · GLM-5.2模型对话 · 和AI聊天5次 · 夜猫子活动
   🖥️ 桌面任务（2 项，需 Windows 桌面端，每号一次即永久有效）
      桌面端对话1次 · 尝鲜热门技能
   🎮 互动玩法
      抽奖 · 盲盒 · Buddy信息 · 派猫猫旅行 · 连签兑换 · 补签卡 · 礼包补偿 · 徽章

⚙️ 特别之处
   · 续期节奏：距上次刷新 >10 天 或 AT 7 天内过期，自动刷新（离线会话 30 天失效）
   · RT 轮换：每次刷新都会换发新令牌并立即保存，形成永续循环
   · 桌面任务：自动备份并切换桌面端认证文件，跑完还原，全程无需人工
   · 数据文件：wb_refresh_tokens.json 自动生成与维护，无需手动管理
   · 新增账号：变量值末尾追加一行 "手机号:AT:RT" 即可，下次运行自动并入

📄 依赖：requests（pip3 install requests）

🔒 隐私说明
   脚本不含任何账号、手机号、Token 或设备信息，所有凭据均由环境变量注入。
"""
import sys, os, json, time, uuid, base64, glob, glob as _glob, shutil, subprocess, threading, queue
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

BASE = "https://www.workbuddy.cn"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) WorkBuddy/5.5.4 Chrome/138.0.7204.251 Electron/37.10.3 Safari/537.36"

# ---------- 任务代码 → 中文名映射 ----------
TASK_NAME_CN = {
    "create_canvas": "设计创意模式",
    "playbook_prompt": "探索优秀灵感",
    "RichMeow_Chat": "桌面端对话",
    "Library_read": "体验资料库",
    "Expert_lighthouse": "腾讯轻量云专家",
    "Expert_Philanthropy": "公益专家",
    "Hp_Appearance": "和平精英主题",
    "Buddy_App": "发现应用",
    "Buddy_App_QQ": "企鹅教师助手",
    "Model_chat_GLM5.2": "GLM-5.2模型对话",
    "black_cat": "夜猫子活动",
    "Expert_team_use_3": "召唤3次专家团",
    "first_buddy": "领取Buddy",
    "chat_5": "和AI聊天5次",
    "skill_1": "尝鲜热门技能",
    "expert_5": "召唤5次专家",
    "template_5": "使用5个模板",
    "automation_1": "设置自动化任务",
    "workstation_expert": "工作台搭建师",
}

def task_cn(code):
    return TASK_NAME_CN.get(code, code)


QQ_TPL = "cb_y5Dy46tPQGGWtueMxXbe"          # 企鹅教师助手模板


# ---------- 专家市场数据（内联，无需外部模块） ----------
EXPERT_MARKETPLACE_URL = "https://acc-1258344699.cos.accelerate.myqcloud.com/workbuddy/expert-marketplace/expert_center.json"
_expert_cache = None


def _extract_name(val):
    if isinstance(val, dict):
        return val.get("zh", val.get("en", str(val)))
    return str(val)


def fetch_expert_marketplace():
    """拉取专家市场配置（带缓存）"""
    global _expert_cache
    if _expert_cache is not None:
        return _expert_cache
    try:
        s = requests.Session()
        s.trust_env = False
        s.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        r = s.get(EXPERT_MARKETPLACE_URL, timeout=15, verify=False)
        if r.status_code == 200:
            _expert_cache = r.json()
            return _expert_cache
    except Exception:
        pass
    return None


def get_team_experts(count=5):
    """专家团列表"""
    data = fetch_expert_marketplace()
    if not data:
        return []
    team = []
    for e in data.get("experts", []):
        meta = e.get("_meta", {})
        if meta.get("expertType") == "team" or e.get("expertType") == "team":
            team.append({"id": e["id"], "name": _extract_name(e.get("displayName", e.get("name", {}))),
                         "industryId": meta.get("industryId", e.get("industryId", "")),
                         "profession": _extract_name(e.get("profession", "")),
                         "defaultInitPrompt": _extract_name(e.get("defaultInitPrompt", ""))})
    return team[:count]


def get_normal_experts(count=10):
    """普通专家列表"""
    data = fetch_expert_marketplace()
    if not data:
        return []
    normal = []
    for e in data.get("experts", []):
        meta = e.get("_meta", {})
        if meta.get("expertType", e.get("expertType", "")) == "agent":
            normal.append({"id": e["id"], "name": _extract_name(e.get("displayName", e.get("name", {}))),
                           "industryId": meta.get("industryId", e.get("industryId", "")),
                           "profession": _extract_name(e.get("profession", "")),
                           "defaultInitPrompt": _extract_name(e.get("defaultInitPrompt", ""))})
    return normal[:count]


def get_template_scenes(count=5):
    """模板场景列表（失败时用内置兜底）"""
    data = fetch_expert_marketplace()
    fallback = [{"id": "01-ProductDesign", "name": "产品设计"}, {"id": "02-Marketing", "name": "营销文案"},
                {"id": "03-DataAnalysis", "name": "数据分析"}, {"id": "04-CodeReview", "name": "代码审查"},
                {"id": "05-Report", "name": "报告撰写"}]
    if not data:
        return fallback[:count]
    scenes = [{"id": c["id"], "name": _extract_name(c.get("name", {}))} for c in data.get("categories", [])[:count]]
    return scenes or fallback[:count]

THEME_KEY = "theme-tkmw7j"                   # 和平精英激战金秋
LIB_DOC_URL = "https://www.workbuddy.cn/space/d/o0KWYeynteVv06UnAZqIFm"
SKILL_NAME = "algorithmic-trading"
INFO_BACKUP = os.path.join(os.path.expanduser("~"), "AppData", "Local", "CodeBuddyExtension", "Data", "Public", "auth", "workbuddy-desktop.info")

# ---------- 账号 ----------
# ---------- Token 自动续期（内联实现，无需外部文件） ----------
REFRESH_URL = "https://copilot.tencent.com/v2/plugin/auth/token/refresh"
REFRESH_STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wb_refresh_tokens.json")
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "WORKBUDDY_ACCESS_TOKEN.txt")



def _parse_env_tokens(raw):
    """解析环境变量：支持换行或 @ 分隔；每项格式 "手机号:AT:RT" 或 "手机号:RT" 或 纯token"""
    items = []
    if not raw:
        return items
    for line in raw.replace("@", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) >= 3 and not parts[0].startswith("eyJ"):
            items.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
        elif len(parts) == 2 and not parts[0].startswith("eyJ"):
            items.append((parts[0].strip(), "", parts[1].strip()))
        else:
            items.append(("", line, ""))
    return items


def _bootstrap_store():
    """自举：json不存在时从环境变量 WORKBUDDY_REFRESH_TOKEN 生成（唯一变量）"""
    if os.path.exists(REFRESH_STORE):
        try:
            if json.load(open(REFRESH_STORE, encoding="utf-8")):
                return
        except Exception:
            pass
    store = {}
    for user, at, rt in _parse_env_tokens(os.environ.get("WORKBUDDY_REFRESH_TOKEN", "")):
        if not rt:
            continue
        key = user or (jwt_user(at) if at else "acct-%d" % (len(store) + 1))
        store[key] = {"refresh_token": rt, "access_token": at}
    if store:
        json.dump(store, open(REFRESH_STORE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("📝 已自动生成 wb_refresh_tokens.json（%d 个账号）" % len(store))


_bootstrap_store()


def jwt_user(tok):
    try:
        pay = tok.split(".")[1]; pay += "=" * (4 - len(pay) % 4)
        return json.loads(base64.urlsafe_b64decode(pay)).get("preferred_username", "?")
    except Exception:
        return "?"


def refresh_one(rt):
    """刷新单个RT，返回 (新AT, 新RT) 或 (None, 错误信息)"""
    s = requests.Session(); s.trust_env = False
    r = s.post(REFRESH_URL, json={}, timeout=20, verify=False,
               headers={"X-Refresh-Token": rt, "X-Auth-Refresh-Source": "plugin",
                        "Content-Type": "application/json"})
    d = r.json()
    inner = d.get("data") or {}
    if d.get("code") == 0 and inner.get("accessToken"):
        return inner["accessToken"], inner.get("refreshToken") or rt
    return None, str(d.get("msg", ""))[:120]


def refresh_all(verbose=True):
    """刷新 wb_refresh_tokens.json 中所有账号，重建 token 文件。返回 {user: at}"""
    store = {}
    if os.path.exists(REFRESH_STORE):
        try:
            store = json.load(open(REFRESH_STORE, encoding="utf-8"))
        except Exception:
            store = {}
    updated = {}
    for user, ent in list(store.items()):
        rt = ent.get("refresh_token")
        if not rt:
            continue
        try:
            at, nrt = refresh_one(rt)
        except Exception as e:
            at, nrt = None, str(e)[:60]
        if at:
            real_user = jwt_user(at)
            if real_user and real_user != "?" and real_user != user:
                store.pop(user, None)
                user = real_user
            store[user] = {"refresh_token": nrt, "access_token": at,
                           "updated": time.strftime("%Y-%m-%d %H:%M")}
            updated[user] = at
            if verbose:
                print("   🔄 %s token已续期" % user)
        elif verbose:
            print("   ⚠️ %s 续期失败: %s" % (user, nrt))
        time.sleep(1)
    if updated:
        json.dump(store, open(REFRESH_STORE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        _rebuild_token_file(store)
    return updated


def _rebuild_token_file(store):
    """按现有 token 文件的顺序替换为新 AT；文件不存在则从 store 全新构建"""
    toks, seen = [], set()
    src = ""
    if os.path.exists(TOKEN_FILE):
        src = open(TOKEN_FILE, encoding="utf-8").read().strip()
    if src:
        for t in src.split("@"):
            t = t.strip()
            if not t:
                continue
            u = jwt_user(t)
            if u in seen:
                continue
            seen.add(u)
            toks.append(store.get(u, {}).get("access_token", t))
    else:
        # 全新构建：直接用 store 里所有有效 AT
        for u, ent in store.items():
            at = ent.get("access_token", "")
            if at and u not in seen:
                seen.add(u)
                toks.append(at)
    if toks:
        open(TOKEN_FILE, "w", encoding="utf-8").write("@".join(toks))


def auto_refresh():
    """启动前自动续期：距上次刷新超10天(或AT快过期)的账号自动刷新"""
    # 环境变量里的新RT合并进 store（支持随时用变量补充账号）
    env_rt = os.environ.get("WORKBUDDY_REFRESH_TOKEN", "").strip()
    if env_rt:
        try:
            store = json.load(open(REFRESH_STORE, encoding="utf-8")) if os.path.exists(REFRESH_STORE) else {}
        except Exception:
            store = {}
        existing_rt = {v.get("refresh_token") for v in store.values()}
        added = 0
        for user, at, rt in _parse_env_tokens(env_rt):
            if not rt:
                continue
            key = user or (jwt_user(at) if at else "env-%d" % (len(store) + 1))
            if rt not in existing_rt:
                store[key] = {"refresh_token": rt, "access_token": at or store.get(key, {}).get("access_token", "")}
                existing_rt.add(rt)
                added += 1
        if added:
            json.dump(store, open(REFRESH_STORE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("📝 从 WORKBUDDY_REFRESH_TOKEN 新增 %d 个账号的刷新令牌" % added)
    if not os.path.exists(REFRESH_STORE):
        return
    try:
        store = json.load(open(REFRESH_STORE, encoding="utf-8"))
    except Exception:
        return
    now = time.time()
    due = {}
    for user, ent in list(store.items()):
        at = ent.get("access_token", "")
        if not at:
            # 只有RT没有AT（首次自举）：立即刷新
            due[user] = ent
            continue
        try:
            pay = at.split(".")[1]; pay += "=" * (4 - len(pay) % 4)
            exp = json.loads(base64.urlsafe_b64decode(pay)).get("exp", 0)
        except Exception:
            exp = 0
        try:
            lt = time.mktime(time.strptime(ent.get("updated", ""), "%Y-%m-%d %H:%M"))
        except Exception:
            lt = 0
        # 触发条件: 距上次刷新>10天(离线会话30天失效) 或 AT 7天内过期
        if (now - lt > 10 * 86400) or (exp and exp - now < 7 * 86400):
            due[user] = ent
    if not due:
        return
    print("🔑 检查到 %d 个账号需要续期..." % len(due))
    updated = {}
    for user, ent in due.items():
        try:
            at, nrt = refresh_one(ent.get("refresh_token", ""))
        except Exception:
            at, nrt = None, "err"
        if at:
            store[user] = {"refresh_token": nrt, "access_token": at,
                           "updated": time.strftime("%Y-%m-%d %H:%M")}
            updated[user] = at
            print("   🔄 %s token已自动续期(新有效期90天)" % user)
        else:
            print("   ⚠️ %s 续期失败: %s" % (user, nrt))
        time.sleep(1)
    if updated:
        json.dump(store, open(REFRESH_STORE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        _rebuild_token_file(store)


def load_accounts():
    auto_refresh()  # 依据 RT 变量/json 续期，并同步 token 文件
    # 续期池优先：与变量实时同步、以手机号为名；本地 token 文件仅作回退
    # (否则新加账号在下次续期前会被旧 token 文件吞掉)
    if os.path.exists(REFRESH_STORE):
        try:
            store = json.load(open(REFRESH_STORE, encoding="utf-8"))
            accs = [{"note": user, "access_token": v.get("access_token", "")}
                    for user, v in store.items() if v.get("access_token")]
            if accs:
                return accs
        except Exception:
            pass
    env = ""
    if os.path.exists(TOKEN_FILE):  # 回退：本地文件(续期后最新)
        env = open(TOKEN_FILE, encoding="utf-8").read().strip()
    if env:
        items = [x.strip() for x in env.replace("@", "\n").splitlines() if x.strip()]
        return [{"note": "账号%d" % (i + 1), "access_token": t}
                for i, t in enumerate(items)]
    print("未找到账号：请设置环境变量 WORKBUDDY_REFRESH_TOKEN（每行 手机号:AT:RT）")
    sys.exit(1)


ACCOUNTS = load_accounts()
ONLY = int(sys.argv[sys.argv.index("--only") + 1]) - 1 if "--only" in sys.argv else None
if ONLY is not None:
    ACCOUNTS = [ACCOUNTS[ONLY]]
QUERY_ONLY = "--query" in sys.argv
NO_DESKTOP = "--no-desktop" in sys.argv

# ---------- 基础 ----------
def new_api(tok):
    s = requests.Session(); s.trust_env = False
    s.headers.update({"Authorization": "Bearer " + tok, "Content-Type": "application/json",
                      "Accept": "application/json, text/plain, */*", "Origin": BASE,
                      "Referer": BASE + "/profile/growth-center", "User-Agent": UA})
    return s


def uid_of(tok):
    try:
        return json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + "==")).get("sub", "")
    except Exception:
        return ""


def nickname_of(tok):
    try:
        return json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + "==")).get("nickname", "") or "用户"
    except Exception:
        return "用户"


def prog(s, code):
    try:
        r = s.get(BASE + "/v2/activity/growth/tasks", timeout=25, verify=False).json()
        for t in r.get("data", {}).get("tasks", []):
            if t.get("task_code") == code:
                pr = t.get("progress") or {}
                return t.get("accept_status", ""), pr.get("current"), pr.get("target")
    except Exception:
        pass
    return None, None, None


def claim(s, code, log):
    r = s.post(BASE + "/v2/activity/growth/tasks/%s/claim" % code, json={}, timeout=20, verify=False)
    try:
        d = r.json().get("data", {})
        log("   🎁领奖[%s]: %s" % (code, "已领过" if d.get("already_claimed") else "+%s积分+%s能量" % (d.get("credit"), d.get("energy"))))
    except Exception:
        log("   领奖[%s]: HTTP %s" % (code, r.status_code))


def report(s, uid, nick, events):
    """events: list of dict；自动补全信封"""
    UA_SHORT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 WorkBuddy/5.5.4"
    out = []
    for e in events:
        env = {"timestamp": int(time.time() * 1000), "reportDelay": 0, "userId": uid, "userNickname": nick,
               "ideName": "web-Agents", "ideType": "web-Agents", "machineId": str(uuid.uuid4()),
               "mode": "CLOUD", "userAgent": UA_SHORT, "os": "Win32", "timezone": "Asia/Shanghai"}
        env.update(e)
        out.append(env)
    try:
        r = s.post(BASE + "/v2/report", json=out, timeout=15, verify=False)
        return r.status_code
    except Exception:
        return 0


def webchat(s, conv_name, prompt, meta=None, model="glm-5.2"):
    conv = s.post(BASE + "/console/webchat/conversations", json={"name": conv_name + "-" + str(uuid.uuid4())[:8]},
                  timeout=20, verify=False).json()
    conv_id = conv.get("data", {}).get("conversationId", "")
    payload = {"messages": [{"role": "user", "content": prompt}], "model": model, "stream": True,
               "conversationId": conv_id}
    if meta:
        payload["_meta"] = meta
    headers = dict(s.headers); headers["Accept"] = "text/event-stream"
    txt = ""
    try:
        with s.post(BASE + "/console/chat/completions", json=payload, timeout=90, verify=False,
                    stream=True, headers=headers) as r:
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data: "):
                    d = line[6:]
                    if d.strip() in ("[DONE]", "[完成]", "[✅完成]"):
                        break
                    try:
                        jj = json.loads(d)
                        for c in jj.get("choices", []):
                            cp = c.get("delta", {}).get("content", "")
                            if cp:
                                txt += cp
                    except Exception:
                        pass
    except Exception:
        pass
    return conv_id, txt


def chat_request_events(uid, nick, conv_id, prompt, txt):
    now = int(time.time() * 1000)
    rid = "cmb-" + str(uuid.uuid4())
    common = {"userId": uid, "userNickname": nick, "ideName": "web-Agents", "ideType": "web-Agents",
              "machineId": str(uuid.uuid4()), "mode": "CLOUD", "userAgent": UA, "os": "Win32",
              "timezone": "Asia/Shanghai"}
    return [
        {"eventCode": "chat_request_send", "timestamp": now, "reportDelay": 0, **common,
         "conversationId": conv_id, "requestId": rid, "requestModelId": "glm-5.2",
         "requestModelName": "GLM-5.2", "inputLength": len(prompt), "customAgentName": ""},
        {"eventCode": "chat_request_response", "timestamp": now + 100, "reportDelay": 0, **common,
         "conversationId": conv_id, "requestId": rid, "requestModelId": "glm-5.2",
         "requestModelName": "GLM-5.2", "toolCallCount": 0, "inputToken": max(1, len(prompt) // 4),
         "outputToken": max(1, len(txt) // 4), "totalToken": max(2, (len(prompt) + len(txt)) // 4)},
        {"eventCode": "chat_message_send", "timestamp": now + 50, "reportDelay": 0, **common,
         "conversationId": conv_id, "requestId": rid, "messageId": "cmb-" + str(uuid.uuid4()),
         "requestModelId": "glm-5.2", "requestModelName": "GLM-5.2", "historyCount": 1,
         "isContextTruncated": False, "currentStepCount": 1, "traceId": rid, "rootRequestId": rid,
         "parentConversationId": conv_id, "agentName": "cli", "agentType": "main"}], rid


# ---------- 查询 ----------
def queryCredits(s):
    """积分查询：套餐总量/剩余/已用"""
    try:
        r = s.post(BASE + "/billing/meter/get-user-resource-summary", json={}, timeout=20, verify=False).json()
        pkgs = r.get("data", {}).get("Packages", [])
        paid = r.get("data", {}).get("IsPaidUser")
        out = []
        for i, p in enumerate(pkgs):
            remain = p.get("CycleRemainCapacity", "0")
            total = p.get("CycleTotalCapacity", "0")
            used = p.get("CycleUsedCapacity", "0")
            # 清理小数尾巴
            remain = remain.rstrip("0").rstrip(".") if "." in remain else remain
            used = used.rstrip("0").rstrip(".") if "." in used else used
            total = total.rstrip("0").rstrip(".") if "." in total else total
            pkg_name = "主套餐" if i == 0 else "加量包%d" % i
            out.append("%s剩余%s积分(共%s,已用%s)" % (pkg_name, remain, total, used))
        return ("；".join(out) if out else "暂无套餐"), paid
    except Exception as e:
        return "查询失败:" + str(e)[:40], False


def queryUsage(s):
    """用量查询：资源总数/总用量"""
    try:
        r = s.post(BASE + "/billing/meter/get-user-resource", json={}, timeout=20, verify=False).json()
        resp = r.get("data", {}).get("Response", {}).get("Data", {}) or {}
        return "共%d类资源，本月已使用%s次" % (resp.get("TotalCount", "?"), resp.get("TotalDosage", "?"))
    except Exception:
        return "用量数据延迟2-3小时"


# ---------- 各任务配方（全部经过实测） ----------
def t_sign(s, uid, nick, log):
    r = s.post(BASE + "/v2/billing/meter/daily-checkin", json={}, timeout=20, verify=False)
    try:
        d = r.json()
        if d.get("code") in (0, 200):
            log("   ✅签到成功 +%s积分 连签%s天" % (d.get("data", {}).get("credit", "?"), d.get("data", {}).get("streak_days", "?")))
        else:
            log("   ✅签到: %s" % (d.get("msg", "")[:40] or "已签到"))
    except Exception:
        log("   签到请求失败")


def t_accept_all(s, uid, nick, log):
    r = s.get(BASE + "/v2/activity/growth/tasks", timeout=25, verify=False).json()
    todo = [t.get("task_code") for t in r.get("data", {}).get("tasks", [])
            if isinstance(t, dict) and t.get("accept_status") == "not_accepted"]
    if todo:
        resp = s.post(BASE + "/v2/activity/growth/tasks/accept", json={"task_codes": todo},
                      timeout=20, verify=False)
        try:
            accepted = resp.json().get("code") == 0
        except Exception:
            accepted = False
        if not accepted:
            log("   📋接受任务失败，继续执行已有状态")
            return

        # The endpoint returns before the task list is updated.  New accounts
        # otherwise remain visible as `not_accepted`, causing every task
        # handler below to skip them in the same run.  Poll briefly until the
        # server reflects the accepted state.
        pending = set(todo)
        deadline = time.time() + 15
        while pending and time.time() < deadline:
            time.sleep(2)
            try:
                check = s.get(BASE + "/v2/activity/growth/tasks", timeout=25, verify=False).json()
                current = {t.get("task_code"): t.get("accept_status")
                           for t in check.get("data", {}).get("tasks", []) if isinstance(t, dict)}
                pending = {code for code in pending if current.get(code) == "not_accepted"}
            except Exception:
                pass
        if pending:
            log("   📋已提交接受任务，仍待同步: %s" % ",".join(sorted(pending)))
        else:
            log("   📋已接受任务: %s" % ",".join(todo))


def t_team_3(s, uid, nick, log):
    """召唤3次专家团：真实团队对话+全字段遥测"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    teams = get_team_experts(10)
    if not teams:
        return
    for rd in range(3):
        st, cur, tgt = prog(s, "Expert_team_use_3")
        if st in ("completed", "claimed") or (cur or 0) >= (tgt or 3):
            break
        team = teams[rd % len(teams)]
        prompt = "你好，请简单介绍一下你们团队能帮我做什么，回答OK即可"
        req_id = str(uuid.uuid4()); msg_id = "cmb-" + str(uuid.uuid4())
        ge = [{"eventCode": "ExpertActualUse", "id": team["id"],
               "extra": {"name": team["name"], "expertTitle": team.get("profession", ""),
                         "type": team.get("industryId", "") or "", "expertType": "team",
                         "source": "builtin", "version": "", "cost": 8, "characterCount": len(prompt),
                         "requestId": req_id, "messageId": msg_id,
                         "requestModelId": "glm-5.2", "requestModelName": "GLM-5.2"},
               "expertType": "team"}]
        meta = {"codebuddy.ai": {"growthEvent": json.dumps(ge, ensure_ascii=False), "promptRequestId": req_id,
                                 "clientSendTime": int(time.time() * 1000), "userId": uid,
                                 "mode": "craft", "model": "glm-5.2", "expertId": team["id"],
                                 "expert": {"id": team["id"], "name": team["name"],
                                            "profession": team.get("profession", ""), "prompt": prompt[:50]},
                                 "tags": ["expert:" + team["id"]]}}
        conv_id, txt = webchat(s, "team", prompt, meta)
        report(s, uid, nick, [{"eventCode": "expert_actual_use", "id": team["id"], "name": team["name"],
                               "expertTitle": team.get("profession", ""), "type": team.get("industryId", "") or "",
                               "expertType": "team", "source": "builtin", "version": "", "cost": 8,
                               "characterCount": len(prompt), "conversationId": conv_id, "requestId": req_id,
                               "messageId": msg_id, "requestModelId": "glm-5.2", "requestModelName": "GLM-5.2"}])
        time.sleep(5)
    st, cur, tgt = prog(s, "Expert_team_use_3")
    log("   召唤3次专家团: %s %s/%s" % (st, cur, tgt))


def t_buddy_apps(s, uid, nick, log):
    common1 = {"userId": uid, "userNickname": nick, "ideName": "web-Agents", "ideType": "web-Agents",
               "machineId": str(uuid.uuid4()), "mode": "CLOUD", "userAgent": UA, "os": "Win32",
               "timezone": "Asia/Shanghai"}
    for task in ("Buddy_App", "Buddy_App_QQ"):
        st, cur, tgt = prog(s, task)
        if st in ("completed", "claimed"):
            continue
        now = int(time.time() * 1000)
        report(s, uid, nick, [
            {"eventCode": "buddyapp_discover_click", "timestamp": now},
            {"eventCode": "buddyapp_enter_click", "timestamp": now + 60, "elementId": QQ_TPL,
             "elementName": "企鹅教师助手", "position": "sidebar-switcher-trigger", "isFirstPage": "1"},
            {"eventCode": "buddyapp_show", "timestamp": now + 120, "elementId": QQ_TPL, "elementName": "企鹅教师助手"}])
        time.sleep(6)
    log("   发现应用/企鹅教师助手: %s / %s" % (prog(s, "Buddy_App")[0], prog(s, "Buddy_App_QQ")[0]))


def t_theme(s, uid, nick, log):
    st, cur, tgt = prog(s, "Hp_Appearance")
    if st in ("completed", "claimed"):
        return
    r = s.post(BASE + "/portal/user-asset/appearance/set", json={"kind": "theme", "resource_key": THEME_KEY},
               timeout=20, verify=False)
    if r.json().get("code") == 0:
        time.sleep(2)
        report(s, uid, nick, [{"eventCode": "appearance_skin_apply", "action": "apply",
                               "source": "settings_close", "id": THEME_KEY, "vipLevel": "free",
                               "series": "craft", "type": "personal"}])
        time.sleep(6)
    log("   和平精英主题: %s" % prog(s, "Hp_Appearance")[0])


def t_library(s, uid, nick, log):
    st, cur, tgt = prog(s, "Library_read")
    if st in ("completed", "claimed"):
        return
    report(s, uid, nick, [{"eventCode": "web_element_click", "pageURL": LIB_DOC_URL,
                           "elementId": "library_doc_intro_click", "elementName": "WorkBuddy资料库介绍",
                           "enterpriseId": ""}])
    time.sleep(6)
    log("   体验资料库: %s" % prog(s, "Library_read")[0])


def t_chat_n(s, uid, nick, log, code, n, prompts):
    """通用聊天任务: chat_5 / Model_chat_GLM5.2"""
    for i in range(n):
        st, cur, tgt = prog(s, code)
        if st in ("completed", "claimed") or (cur or 0) >= (tgt or n):
            break
        conv_id, txt = webchat(s, code, prompts[i % len(prompts)])
        if txt:
            evs, _ = chat_request_events(uid, nick, conv_id, prompts[i % len(prompts)], txt)
            report(s, uid, nick, evs)
        time.sleep(4)
    st, cur, tgt = prog(s, code)
    log("   %s: %s %s/%s" % (code, st, cur, tgt))


def t_black_cat(s, uid, nick, log):
    hour = time.localtime().tm_hour
    st, cur, tgt = prog(s, "black_cat")
    if st in ("completed", "claimed"):
        return
    if not (hour >= 23 or hour < 8):
        log("   夜猫子: 仅23:00-08:00计数，当前%d点，跳过" % hour)
        return
    need = (tgt or 3) - (cur or 0)
    for i in range(max(0, need)):
        conv_id, txt = webchat(s, "night", ["今天天气怎么样？", "1+1等于几？", "讲个笑话"][i % 3])
        if txt:
            evs, _ = chat_request_events(uid, nick, conv_id, "聊天", txt)
            report(s, uid, nick, evs)
        time.sleep(5)
        st, cur, tgt = prog(s, "black_cat")
        if st in ("completed", "claimed"):
            break
    log("   夜猫子: %s %s/%s" % (prog(s, "black_cat")[0], prog(s, "black_cat")[1], prog(s, "black_cat")[2]))


def t_expert_5(s, uid, nick, log):
    """召唤5次专家：普通专家遥测"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    experts = get_normal_experts(20)
    for i in range(5):
        st, cur, tgt = prog(s, "expert_5")
        if st in ("completed", "claimed") or (cur or 0) >= (tgt or 5):
            break
        e = experts[i % len(experts)] if experts else {"id": "expert-" + str(uuid.uuid4())[:8], "name": "Expert", "profession": ""}
        report(s, uid, nick, [
            {"eventCode": "expert_summoned", "id": e["id"], "name": e["name"], "type": "agent",
             "expertTitle": e.get("profession", ""), "expertType": "agent"},
            {"eventCode": "expert_actual_use", "id": e["id"], "name": e["name"], "type": e.get("industryId", "") or "",
             "expertType": "agent", "source": "builtin", "version": "", "cost": 5, "characterCount": 30,
             "requestId": str(uuid.uuid4()), "messageId": "cmb-" + str(uuid.uuid4()),
             "requestModelId": "glm-5.2", "requestModelName": "GLM-5.2"}])
        time.sleep(3)
    st, cur, tgt = prog(s, "expert_5")
    log("   召唤5次专家: %s %s/%s" % (st, cur, tgt))


def t_template_5(s, uid, nick, log):
    """使用5个模板：批量遥测"""
    scenes = [{"id": "01-ProductDesign", "name": "产品设计"}, {"id": "02-Marketing", "name": "营销文案"},
              {"id": "03-DataAnalysis", "name": "数据分析"}, {"id": "04-CodeReview", "name": "代码审查"},
              {"id": "05-Report", "name": "报告撰写"}]
    for i, sc in enumerate(scenes):
        st, cur, tgt = prog(s, "template_5")
        if st in ("completed", "claimed") or (cur or 0) >= (tgt or 5):
            break
        tid = sc["id"]
        report(s, uid, nick, [
            {"eventCode": "agent_task_created", "source": "CLOUD", "name": "", "mode": "craft",
             "requestModelId": "default", "action": tid, "has_template": True, "template_id": tid,
             "template_name": sc["name"]},
            {"eventCode": "agent_task_created_with_template", "templateId": tid, "templateName": sc["name"],
             "isCustomModel": True, "id": tid, "name": sc["name"]},
            {"eventCode": "playbook_prompt_send", "ext1": str(uuid.uuid4()), "requestId": str(uuid.uuid4()),
             "id": tid, "name": sc["name"], "type": "other", "promptLength": 30, "isOfficial": 1,
             "source": "growth-center"}])
        time.sleep(2)
    st, cur, tgt = prog(s, "template_5")
    log("   使用5个模板: %s %s/%s" % (st, cur, tgt))


def t_canvas_automation(s, uid, nick, log):
    """设计创意模式 + 自动化任务 + 优秀灵感"""
    st, cur, tgt = prog(s, "create_canvas")
    if st not in ("completed", "claimed"):
        report(s, uid, nick, [{"eventCode": "agent_task_created", "source": "CLOUD", "name": "", "mode": "craft",
                               "requestModelId": "default", "task_mode": "design"},
                              {"eventCode": "wbx_design_canvas_task_create"}])
        time.sleep(3)
    st, cur, tgt = prog(s, "automation_1")
    if st not in ("completed", "claimed"):
        report(s, uid, nick, [{"eventCode": "agent_task_created", "source": "CLOUD", "name": "", "mode": "craft",
                               "requestModelId": "default", "task_mode": "automation",
                               "isAutomationBackground": True},
                              {"eventCode": "automated_task_create_suc", "action": "create"},
                              {"eventCode": "automated_task_execute", "action": "execute"}])
        time.sleep(3)
    st, cur, tgt = prog(s, "playbook_prompt")
    if st not in ("completed", "claimed"):
        report(s, uid, nick, [{"eventCode": "playbook_prompt_send", "ext1": str(uuid.uuid4()),
                               "requestId": str(uuid.uuid4()), "id": "01-ProductDesign", "name": "产品设计",
                               "type": "other", "promptLength": 30, "isOfficial": 1, "source": "growth-center"}])
        time.sleep(3)
    log("   设计/自动化/灵感: %s / %s / %s" % (prog(s, "create_canvas")[0], prog(s, "automation_1")[0], prog(s, "playbook_prompt")[0]))


def t_glm52(s, uid, nick, log):
    t_chat_n(s, uid, nick, log, "Model_chat_GLM5.2", 1, ["你好，请介绍一下你自己"])
    t_chat_n(s, uid, nick, log, "chat_5", 5, ["你好", "今天天气怎么样？", "1+1等于几？", "Python是什么？", "推荐一本好书"])




# ---------- 互动玩法（移植自 workbuddy_checkin.py 验证代码） ----------
def t_lottery(s, uid, nick, log):
    """抽奖：查剩余次数并全部抽完"""
    try:
        r = s.get(BASE + "/v2/activity/growth/lottery/chances", timeout=20, verify=False).json()
        cd = r.get("data", {})
        chances = cd.get("balance", cd.get("chances", cd.get("remaining", 0)))
        if not chances or chances <= 0:
            log("   🎰抽奖: 无次数")
            return
        won = []
        for i in range(int(chances)):
            if i > 0:
                time.sleep(2)
            rr = s.post(BASE + "/v2/activity/growth/lottery/draw",
                        json={"client_token": "draw-" + str(uuid.uuid4())}, timeout=20, verify=False).json()
            if rr.get("code") == 0:
                prize = rr.get("data", {}).get("prize_name", rr.get("data", {}).get("name", "?"))
                won.append(str(prize))
            else:
                log("   🎰抽奖失败: %s" % str(rr.get("msg", ""))[:40])
                break
        log("   🎰抽奖: %s" % ("、".join(won) if won else "无结果"))
    except Exception as e:
        log("   🎰抽奖异常: %s" % str(e)[:50])


def t_blindbox(s, uid, nick, log):
    """盲盒：能量足够就开（每次10能量，最多开5次）"""
    try:
        q = s.get(BASE + "/v2/activity/growth/buddy/quota", timeout=20, verify=False).json()
        qd = q.get("data", {})
        affordable = qd.get("affordable", 0)
        if not affordable or affordable <= 0:
            log("   📦盲盒: 能量不足 (%s/10)" % qd.get("balance", "?"))
            return
        n = min(affordable, 5)
        got = []
        for _ in range(n):
            rr = s.post(BASE + "/v2/activity/growth/buddy/open", json={"count": 1}, timeout=20, verify=False).json()
            if rr.get("code") == 0:
                results = rr.get("data", {}).get("results", [])
                if results:
                    it = results[0]
                    ins = it.get("instance", {}); tpl = it.get("template", {})
                    got.append("%s(%s)" % (ins.get("name", tpl.get("name", "?")), ins.get("rarity", tpl.get("rarity", ""))))
            else:
                break
            time.sleep(1.5)
        log("   📦盲盒: %s" % ("、".join(got) if got else "开启失败"))
    except Exception as e:
        log("   📦盲盒异常: %s" % str(e)[:50])


def t_buddy_info(s, uid, nick, log):
    """Buddy 信息"""
    try:
        r = s.get(BASE + "/v2/activity/growth/buddy/info", timeout=20, verify=False).json()
        if r.get("code") == 0:
            b = r.get("data", {}).get("buddy", r.get("data", {}))
            log("   🐱Buddy: %s (%s)%s" % (b.get("name", "?"), b.get("rarity", ""),
                                           ", " + b.get("personality") if b.get("personality") else ""))
    except Exception:
        pass


def t_travel(s, uid, nick, log):
    """派猫猫旅行：到达领礼物 / 旅行中等待 / 空闲出发（幂等状态机）"""
    try:
        vis = s.get(BASE + "/v2/activity/growth/buddy/visible", timeout=20, verify=False).json()
        if vis.get("code") == 0:
            vd = vis.get("data", {})
            if not vd.get("buddy_visible", True) or not vd.get("has_buddy", True):
                log("   🐾旅行: 无Buddy，跳过")
                return
        st = s.get(BASE + "/v2/activity/growth/buddy/travel/status", timeout=20, verify=False).json()
        if not (st.get("code") == 0):
            log("   🐾旅行: 状态获取失败")
            return
        sd = st.get("data", {})
        state = sd.get("state", "idle")
        if state == "arrived":
            rr = s.post(BASE + "/v2/activity/growth/buddy/travel/claim", json={}, timeout=20, verify=False).json()
            if rr.get("code") == 0:
                log("   🐾旅行: 🎉领取礼物 +%s积分" % rr.get("data", {}).get("reward_credit", 0))
            else:
                log("   🐾旅行: 领取失败 %s" % str(rr.get("msg", ""))[:40])
            return
        if state == "traveling":
            remain = max(0, (sd.get("arrive_at", 0) - sd.get("server_now", 0)) // 60)
            log("   🐾旅行: 旅行中，约%s分钟后到达" % remain)
            return
        if sd.get("daily_limit_reached"):
            log("   🐾旅行: 今日次数已用尽")
            return
        cfg = s.get(BASE + "/v2/activity/growth/buddy/travel/config", timeout=20, verify=False).json()
        locs = cfg.get("data", {}).get("locations", [])
        if not locs:
            log("   🐾旅行: 无目的地")
            return
        rr = s.post(BASE + "/v2/activity/growth/buddy/travel/depart",
                    json={"location_id": locs[0].get("id")}, timeout=20, verify=False).json()
        if rr.get("code") == 0:
            remain = max(0, (rr.get("data", {}).get("arrive_at", 0) - rr.get("data", {}).get("server_now", 0)) // 3600)
            log("   🐾旅行: ✅已出发，约%s小时后到达（下次运行自动领取）" % remain)
        else:
            log("   🐾旅行: 出发失败 %s" % str(rr.get("msg", ""))[:40])
    except Exception as e:
        log("   🐾旅行异常: %s" % str(e)[:50])


def t_redeem(s, uid, nick, log, streak_days=None):
    """兑换奖励：按连签档位（7d/14d/28d）"""
    tiers = [("7d", 7, "入门"), ("14d", 14, "进阶"), ("28d", 28, "巅峰")]
    for tier, need, label in tiers:
        if streak_days is not None and streak_days < need:
            continue
        rr = s.post(BASE + "/v2/activity/growth/redeem",
                    json={"tier": tier, "client_token": "redeem-" + tier + "-" + str(uuid.uuid4())},
                    timeout=20, verify=False).json()
        code = rr.get("code", -1)
        if code == 0:
            d = rr.get("data", {})
            log("   🎁兑换%s档: +%s积分 +%s能量 +%s抽奖" % (label, d.get("credit_granted", 0),
                                                        d.get("energy_granted", 0), d.get("chances_granted", 0)))
        elif code == 409:
            log("   🎁兑换%s档: 已兑换过" % label)
        # 403=天数不足，静默


def t_badges(s, uid, nick, log):
    try:
        r = s.get(BASE + "/v2/activity/growth/badges", timeout=20, verify=False).json()
        badges = r.get("data", {}).get("badges", r.get("data", {}).get("list", []))
        earned = sum(1 for b in badges if isinstance(b, dict) and b.get("earned"))
        log("   🏅徽章: %s个" % earned)
    except Exception:
        pass


# ---------- 桌面端换血任务（RichMeow / skill_1） ----------
# 2026-09 桌面端升级为 -ai 变体：认证文件与数据目录都换了名字
_INFO_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "CodeBuddyExtension", "Data", "Public", "auth")
INFO_PATH = os.path.join(_INFO_DIR, "workbuddy-desktop-ai.info")
if not os.path.exists(INFO_PATH):  # 旧版桌面端回落
    INFO_PATH = os.path.join(_INFO_DIR, "workbuddy-desktop.info")
SESSION_DIRS = [os.path.join(os.path.expanduser("~"), ".workbuddy-ai", "sessions"),
                os.path.join(os.path.expanduser("~"), ".workbuddy", "sessions")]


def swap_info(tok):
    """把 .info 认证换成目标账号（自动备份）"""
    bak = INFO_PATH + ".wb_all_bak"
    if not os.path.exists(bak):
        shutil.copy(INFO_PATH, bak)
    d = json.load(open(INFO_PATH, encoding="utf-8"))
    pay = tok.split(".")[1]; pay += "=" * (4 - len(pay) % 4)
    j = json.loads(base64.urlsafe_b64decode(pay))
    d["auth"]["accessToken"] = tok
    d["auth"]["refreshToken"] = ""
    d["auth"]["expiresAt"] = j.get("exp", 0) * 1000

    def fix(a, last):
        if isinstance(a, dict):
            a["uid"] = j.get("sub")
            for k in ("nickname", "phoneNumber"):
                if k in a: a[k] = "脚本账号"
            if "lastLogin" in a: a["lastLogin"] = "True" if last else "False"
        return a
    fix(d.get("account", {}), True)
    for k in ("accounts", "allAccounts"):
        if isinstance(d.get(k), list):
            for a in d[k]: fix(a, False)
    json.dump(d, open(INFO_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def restore_info():
    bak = INFO_PATH + ".wb_all_bak"
    if os.path.exists(bak):
        shutil.copy(bak, INFO_PATH)
        os.remove(bak)


def ensure_local_skill():
    """确保本机技能目录有 algorithmic-trading（运行时扫描 ~/.workbuddy/skills）"""
    d = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills", SKILL_NAME + "__skillhub")
    if os.path.exists(os.path.join(d, "SKILL.md")):
        return
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8").write(
        "---\nname: %s\ndescription: 算法交易技能：量化策略开发、回测、信号生成与风险管理辅助。\n---\n\n# Algorithmic Trading\n为用户提供量化策略编写、回测与风险分析。\n" % SKILL_NAME)
    json.dump({"ownerId": "wb_all", "slug": SKILL_NAME, "version": "1.0.0", "publishedAt": int(time.time() * 1000)},
              open(os.path.join(d, "_meta.json"), "w", encoding="utf-8"))
    json.dump({"slug": SKILL_NAME, "name": "Algorithmic Trading", "version": "1.0.0",
               "installedAt": int(time.time() * 1000), "source": "skillhub"},
              open(os.path.join(d, "_skillhub_meta.json"), "w", encoding="utf-8"))


def daemon_chat(prompt, blocks=None, meta_extra=None, deadline=280):
    """连接本机守护进程 ACP 发一次对话，返回回复文本"""
    import websocket
    s = requests.Session(); s.trust_env = False
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "acp-connection-id": str(uuid.uuid4())}
    BASE_D = None
    r = None
    fs = sorted(_glob.glob(os.path.join(os.path.expanduser("~"), ".workbuddy", "sessions", "*.json")),
                key=os.path.getmtime, reverse=True)
    cands = []
    for f in fs[:6]:
        try:
            u = (json.load(open(f, encoding="utf-8")).get("url") or "").rstrip("/")
            if u: cands.append(u)
        except Exception:
            pass
    for base in cands:
        try:
            r = s.post(base + "/api/v1/acp/connect", headers=headers, timeout=6, stream=True)
            if r.status_code == 200:
                BASE_D = base
                break
        except Exception:
            continue
    if not BASE_D:
        return ""
    for data in (l.strip() for l in r.iter_lines(decode_unicode=True)):
        if not data:
            continue
        p = data[5:].strip() if data.startswith("data:") else data
        try:
            d = json.loads(p)
        except Exception:
            continue
        if d.get("connectionId") and d.get("sessionToken"):
            headers["acp-connection-id"] = d["connectionId"]
            headers["acp-session-token"] = d["sessionToken"]
            break

    def read_rpc(rr, dl, state):
        while time.time() < dl:
            try:
                raw = rr.raw.readline()
            except Exception:
                break
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            p = line[5:].strip() if line.startswith("data:") else line
            try:
                d = json.loads(p)
            except Exception:
                continue
            upd = d.get("params", {}).get("update", {}) if isinstance(d.get("params"), dict) else {}
            if upd.get("sessionUpdate") in ("agent_message_chunk", "agent_thought_chunk"):
                c = upd.get("content", {})
                if isinstance(c, dict) and c.get("type") == "text":
                    state["text"] += c.get("text", "")
            if d.get("id") is not None:
                return d
            if d.get("method") == "session/endTurn":
                return d
        return None

    st = {"text": ""}
    d = read_rpc(s.post(BASE_D + "/api/v1/acp", headers=headers, timeout=(10, 20), stream=True,
                        json={"jsonrpc": "2.0", "method": "initialize",
                              "params": {"protocolVersion": 1, "capabilities": {},
                                         "clientInfo": {"name": "wb_all", "version": "1.0"}}, "id": 1}),
                 time.time() + 20, st)
    if not (d and "result" in d):
        return ""
    d = read_rpc(s.post(BASE_D + "/api/v1/acp", headers=headers, timeout=(10, 20), stream=True,
                        json={"jsonrpc": "2.0", "method": "session/new",
                              "params": {"cwd": os.path.expanduser("~"), "mcpServers": []}, "id": 2}),
                 time.time() + 30, st)
    sid = (d or {}).get("result", {}).get("sessionId") if d else None
    if not sid:
        return ""
    blocks = blocks or [{"type": "text", "text": prompt}]
    meta = {"codebuddy.ai": {"promptRequestId": str(uuid.uuid4()), "clientSendTime": int(time.time() * 1000),
                             "conversationId": sid, "mode": "craft", "model": "glm-5.2"}}
    if meta_extra:
        meta["codebuddy.ai"].update(meta_extra)
    read_rpc(s.post(BASE_D + "/api/v1/acp", headers=headers, timeout=(10, 20), stream=True,
                    json={"jsonrpc": "2.0", "method": "session/prompt",
                          "params": {"sessionId": sid, "prompt": blocks, "_meta": meta}, "id": 3}),
             time.time() + deadline, st)
    return st["text"]


def restart_desktop():
    subprocess.run(["taskkill", "/F", "/IM", "WorkBuddy.exe"], capture_output=True)
    time.sleep(3)
    for d_ in SESSION_DIRS:
        for f in _glob.glob(os.path.join(d_, "*.json")):
            try:
                os.remove(f)
            except Exception:
                pass
    subprocess.Popen(["cmd", "/c", "start", "", r"C:\Program Files\WorkBuddy\WorkBuddy.exe", "--remote-debugging-port=9222"],
                     creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
    # 等守护进程会话出现（新版 .workbuddy-ai/sessions，updatedAt 可能是字符串）
    for _ in range(20):
        time.sleep(3)
        fs = []
        for d_ in SESSION_DIRS:
            fs += _glob.glob(os.path.join(d_, "*.json"))
        fs = sorted(fs, key=os.path.getmtime, reverse=True)
        for f in fs[:3]:
            try:
                d = json.load(open(f, encoding="utf-8"))
                if time.time() * 1000 - int(d.get("updatedAt") or 0) < 60000 and d.get("url"):
                    return d["url"]
            except Exception:
                pass
    return None




def cdp_ui_send(prompt):
    """通过 CDP 在桌面端 UI 用 Slate beforeinput 发送聊天（新账号兜底）"""
    import websocket
    try:
        r = requests.get("http://127.0.0.1:9222/json", timeout=5, proxies={"http": None, "https": None})
        pt = [x for x in r.json() if x.get("type") == "page"][0]
        ws = websocket.create_connection(pt["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
        state = {"i": 0}

        def cmd(m, p=None):
            state["i"] += 1
            ws.send(json.dumps({"id": state["i"], "method": m, "params": p or {}}))
            return state["i"]

        def wait_id(rid):
            t0 = time.time()
            while time.time() - t0 < 10:
                try:
                    ws.settimeout(1.0); raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception:
                    return None
                m = json.loads(raw)
                if m.get("id") == rid:
                    return m
            return None

        def ev(expr):
            m = wait_id(cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True}))
            return (m or {}).get("result", {}).get("result", {}).get("value")

        def click(x, y):
            for t in ("mousePressed", "mouseReleased"):
                wait_id(cmd("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1}))
        pos = ev(r'(function(){ const e=document.querySelector("[contenteditable=\"true\"]"); if(!e) return null; const r=e.getBoundingClientRect(); return JSON.stringify({x:r.x+150,y:r.y+r.height/2}); })()')
        if not pos:
            return False
        p = json.loads(pos)
        click(p["x"], p["y"])
        time.sleep(0.5)
        ev(r'(function(){ const e=document.querySelector("[contenteditable=\"true\"]"); e.focus(); const dt=new DataTransfer(); dt.setData("text/plain", %s); e.dispatchEvent(new ClipboardEvent("paste", {clipboardData: dt, bubbles: true, cancelable: true})); e.dispatchEvent(new InputEvent("beforeinput", {inputType: "insertText", data: %s, bubbles: true, cancelable: true})); return e.textContent.slice(0,30); })()' % (json.dumps(prompt), json.dumps(prompt)))
        time.sleep(1)
        btn = ev(r'(function(){ const b=document.querySelector(".cr-send-button"); if(!b) return null; const r=b.getBoundingClientRect(); return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2,disabled:!!b.disabled}); })()')
        if btn and '"disabled":false' in btn:
            p2 = json.loads(btn)
            click(p2["x"], p2["y"])
            time.sleep(10)
            return True
        return False
    except Exception:
        return False


def t_desktop_tasks(s, uid, nick, tok, log, need_rich, need_skill):
    """桌面端换血任务：RichMeow(桌面对话) / skill_1(安装并使用技能)"""
    log("   🖥️ 桌面换血流程启动（结束后自动还原认证并重启桌面端）...")
    # 技能安装走 API（服务端记录）
    if need_skill:
        try:
            src = s.get(BASE + "/console/as/marketplace/sources", timeout=20, verify=False).json()
            srcs = src.get("data", {}).get("sources") or []
            mid = srcs[0].get("id") if srcs else None
            if mid:
                s.post(BASE + "/console/as/user/plugins/install",
                       json={"plugin_name": SKILL_NAME, "marketplace_id": mid, "version": "latest"},
                       timeout=30, verify=False)
        except Exception:
            pass
    ensure_local_skill()
    swap_info(tok)
    url = restart_desktop()
    if not url:
        log("   ❌桌面端守护进程未就绪，跳过桌面任务")
        restore_info()
        subprocess.Popen(["cmd", "/c", "start", "", r"C:\Program Files\WorkBuddy\WorkBuddy.exe"])
        return
    time.sleep(8)
    if need_rich:
        txt = daemon_chat("你好，请用一句话介绍你自己")
        if not txt:
            log("   守护进程会话未就绪，改用 CDP UI 发送...")
            cdp_ui_send("你好，请用一句话介绍你自己")
        log("   桌面对话: %s字" % len(txt))
        time.sleep(8)
    if need_skill:
        blocks = [{"type": "resource_link", "uri": "skill://" + SKILL_NAME, "title": SKILL_NAME,
                   "name": SKILL_NAME, "_meta": {"mentionType": "skill", "skillName": SKILL_NAME}},
                  {"type": "text", "text": "你必须通过技能系统正式加载（load）该技能，加载成功后回答：技能已加载"}]
        txt = daemon_chat("", blocks=blocks)
        if not txt:
            log("   守护进程不可用，改用 CDP UI 技能调用...")
            cdp_ui_send("/" + SKILL_NAME + " 请按技能说明回答OK")
        log("   技能加载: %s" % ("成功" if "加载" in txt else "回复%d字" % len(txt)))
        time.sleep(8)
    restore_info()
    subprocess.run(["taskkill", "/F", "/IM", "WorkBuddy.exe"], capture_output=True)
    time.sleep(3)
    subprocess.Popen(["cmd", "/c", "start", "", r"C:\Program Files\WorkBuddy\WorkBuddy.exe"])
    time.sleep(5)
    if need_rich:
        st, cur, tgt = prog(s, "RichMeow_Chat")
        log("   桌面端对话1次: %s %s/%s" % (st, cur, tgt))
        if st == "completed":
            claim(s, "RichMeow_Chat", log)
    if need_skill:
        st, cur, tgt = prog(s, "skill_1")
        log("   尝鲜热门技能: %s %s/%s" % (st, cur, tgt))
        if st == "completed":
            claim(s, "skill_1", log)




def t_gift_compensation(s, uid, nick, log):
    """礼包(每号一次) + 补偿领取(活动开启时)"""
    try:
        r = s.post(BASE + "/billing/meter/claim-gift", json={}, timeout=15, verify=False).json()
        if r.get("code") == 0:
            log("   🎊新手礼包: +%s积分" % r.get("data", {}).get("credit", "?"))
    except Exception:
        pass
    try:
        r = s.post(BASE + "/billing/meter/claim-compensation", json={}, timeout=15, verify=False).json()
        if r.get("code") == 0:
            log("   🎊补偿领取: +%s积分" % r.get("data", {}).get("credit", "?"))
    except Exception:
        pass


def t_makeup(s, uid, nick, log):
    """补签卡：热力图检测昨日漏签则自动补签（保住连签）"""
    import datetime
    try:
        hm = s.get(BASE + "/v2/activity/growth/heatmap", timeout=20, verify=False).json()
        cells = hm.get("data", {}).get("cells", [])
        bal = s.get(BASE + "/v2/activity/growth/streak", timeout=20, verify=False).json().get("data", {}).get("makeup_cards", {}).get("balance", 0)
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        missed = None
        for c in cells:
            d = str(c.get("date", ""))[:10]
            if d == yesterday and not c.get("score", 0):
                missed = yesterday
                break
        if missed and bal > 0:
            r = s.post(BASE + "/v2/activity/growth/makeup-cards/use", json={"target_date": missed},
                       timeout=20, verify=False).json()
            log("   🩹补签%s: %s" % (missed, "成功，连签保住" if r.get("code") == 0 else str(r.get("msg", ""))[:40]))
        elif missed:
            log("   🩹昨日(%s)漏签但无补签卡" % missed)
        else:
            log("   🩹无漏签，无需补签")
    except Exception as e:
        log("   🩹补签检查异常: %s" % str(e)[:50])


def t_first_buddy(s, uid, nick, log):
    """新账号：领取第一只Buddy"""
    st, cur, tgt = prog(s, "first_buddy")
    if st in ("completed", "claimed"):
        return
    try:
        r = s.post(BASE + "/v2/activity/growth/buddy/first", json={}, timeout=20, verify=False).json()
        log("   🐱首只Buddy: %s" % ("成功" if r.get("code") == 0 else str(r.get("msg", ""))[:40]))
    except Exception as e:
        log("   🐱首只Buddy异常: %s" % str(e)[:40])


def t_workstation(s, uid, nick, log, tok):
    """工作台搭建师专家（若任务出现）：优先桌面换血对话，回退遥测"""
    st, cur, tgt = prog(s, "workstation_expert")
    if st in ("completed", "claimed") or st is None:
        return
    log("   检测到工作台搭建师任务，尝试桌面换血对话...")
    # 桌面换血对话（复用 desktop 流程）
    swap_info(tok)
    restart_desktop()
    time.sleep(8)
    blocks = [{"type": "resource_link", "uri": "expert://WorkspaceBuilder", "title": "工作台搭建师",
               "name": "工作台搭建师", "_meta": {"mentionType": "expert", "expertId": "WorkspaceBuilder"}},
              {"type": "text", "text": "你好，请介绍你能帮我搭建什么工作台，回答OK即可"}]
    txt = daemon_chat("", blocks=blocks)
    log("   工作台搭建师对话: %s字" % len(txt))
    restore_info()
    subprocess.run(["taskkill", "/F", "/IM", "WorkBuddy.exe"], capture_output=True)
    time.sleep(3)
    subprocess.Popen(["cmd", "/c", "start", "", r"C:\Program Files\WorkBuddy\WorkBuddy.exe"])
    time.sleep(5)
    log("   工作台搭建师: %s %s/%s" % prog(s, "workstation_expert"))


def t_unknown_tasks(s, uid, nick, log):
    """检测脚本未覆盖的新任务，明确提示"""
    known = {"create_canvas", "playbook_prompt", "RichMeow_Chat", "Library_read", "Expert_lighthouse",
             "Expert_Philanthropy", "Hp_Appearance", "Buddy_App", "Buddy_App_QQ", "Model_chat_GLM5.2",
             "black_cat", "Expert_team_use_3", "first_buddy", "chat_5", "skill_1", "expert_5",
             "template_5", "automation_1", "workstation_expert"}
    r = s.get(BASE + "/v2/activity/growth/tasks", timeout=25, verify=False).json()
    for t in r.get("data", {}).get("tasks", []):
        if not isinstance(t, dict):
            continue
        code = t.get("task_code", "")
        st = t.get("accept_status", "")
        if code in known or st in ("claimed", "completed"):
            continue
        desc = t.get("task_desc", "")[:50]
        if "subscribe" in code.lower() or "公众号" in (t.get("title", "") + desc):
            log("   ⚠️新任务需手动: %s %s (%s) — 需微信扫码关注公众号" % (code, t.get("title", ""), desc))
        elif "donat" in code.lower() or "捐款" in desc or "公益" in t.get("title", ""):
            log("   ⚠️新任务需手动: %s %s (%s) — 涉及真实捐款" % (code, t.get("title", ""), desc))
        else:
            log("   ⚠️未覆盖新任务: %s %s (%s) — 请反馈更新脚本" % (code, t.get("title", ""), desc))


# ---------- 单账号全流程 ----------
def run_account(idx, acc, do_desktop):
    tok = acc["access_token"]
    uid = uid_of(tok)
    nick = nickname_of(tok)
    s = new_api(tok)
    msgs = []
    summary = {"idx": idx, "note": acc.get("note", ""), "credits": "", "usage": "", "growth": "",
               "done": 0, "total": 0, "rest": [], "level": "?", "energy": "?"}

    tag = "账号%d" % idx
    def log(m):
        ts = time.strftime("%H:%M:%S")
        line = "[%s][%s] %s" % (ts, tag, m)
        print(line)
        msgs.append(line)

    log("")
    log("╭─ 👤 账号%d  %s" % (idx, acc.get("note", "")))
    # 查询
    credits, paid = queryCredits(s)
    usage = queryUsage(s)
    prof = s.get(BASE + "/v2/activity/growth/profile", timeout=25, verify=False).json().get("data", {})
    energy = s.get(BASE + "/v2/activity/growth/energy", timeout=25, verify=False).json().get("data", {}).get("balance")
    streak = s.get(BASE + "/v2/activity/growth/streak", timeout=25, verify=False).json().get("data", {}).get("streak", {})
    summary["credits"] = credits
    summary["usage"] = usage
    summary["streak"] = streak.get("days", "?")
    summary["signed"] = "✅" if "已签到" in (credits + "") or True else "❌"
    log("💰 积分: %s" % credits)
    log("📊 用量: %s" % usage)
    try:
        hm = s.get(BASE + "/v2/activity/growth/heatmap", timeout=20, verify=False).json()
        cells = hm.get("data", {}).get("cells", [])
        signed = sum(1 for c in cells if isinstance(c, dict) and c.get("score", 0) > 0)
    except Exception:
        signed = "?"
    log("🌱 成长: 等级%s 连签%s天 能量%s 累签%s天" % (prof.get("level", "?"), streak.get("days", "?"), energy, signed))
    if QUERY_ONLY:
        return msgs, {"idx": idx, "note": acc.get("note", ""), "done": 0, "total": 0, "rest": [], "level": "?", "energy": "?"}
    # 桌面任务先做（新账号必须先有真实桌面会话，否则接受会被回滚、遥测不计数）
    need_rich = prog(s, "RichMeow_Chat")[0] not in ("completed", "claimed")
    need_skill = prog(s, "skill_1")[0] not in ("completed", "claimed")
    if do_desktop and (need_rich or need_skill):
        log("  🖥️ ── 桌面任务（引导优先） ──")
        t_desktop_tasks(s, uid, nick, tok, log, need_rich, need_skill)
    elif need_rich or need_skill:
        log("── 桌面任务跳过(--no-desktop): RichMeow=%s skill_1=%s ──" % (need_rich, need_skill))
    # 任务
    log("  ☁️ ── 云端任务 ──")
    t_accept_all(s, uid, nick, log)
    t_sign(s, uid, nick, log)
    t_team_3(s, uid, nick, log)
    t_buddy_apps(s, uid, nick, log)
    t_theme(s, uid, nick, log)
    t_library(s, uid, nick, log)
    t_canvas_automation(s, uid, nick, log)
    t_expert_5(s, uid, nick, log)
    t_template_5(s, uid, nick, log)
    t_glm52(s, uid, nick, log)
    t_black_cat(s, uid, nick, log)
    t_badges(s, uid, nick, log)
    t_lottery(s, uid, nick, log)
    t_blindbox(s, uid, nick, log)
    t_buddy_info(s, uid, nick, log)
    t_travel(s, uid, nick, log)
    t_redeem(s, uid, nick, log, streak.get("days"))
    t_gift_compensation(s, uid, nick, log)
    t_first_buddy(s, uid, nick, log)
    t_makeup(s, uid, nick, log)
    t_workstation(s, uid, nick, log, tok)
    t_unknown_tasks(s, uid, nick, log)
    # 领奖
    log("  🎁 ── 领奖 ──")
    r = s.get(BASE + "/v2/activity/growth/tasks", timeout=25, verify=False).json()
    n = 0
    for t in r.get("data", {}).get("tasks", []):
        if isinstance(t, dict) and t.get("accept_status") == "completed":
            claim(s, t.get("task_code", ""), log)
            n += 1
            time.sleep(1)
    if n == 0:
        log("   无待领奖励")
    # 终态
    st_all = s.get(BASE + "/v2/activity/growth/tasks", timeout=25, verify=False).json().get("data", {}).get("tasks", [])
    done = sum(1 for t in st_all if isinstance(t, dict) and t.get("accept_status") in ("claimed", "completed"))
    rest = [task_cn(t.get("task_code","")) for t in st_all if isinstance(t, dict) and t.get("accept_status") not in ("claimed", "completed")]
    prof2 = s.get(BASE + "/v2/activity/growth/profile", timeout=25, verify=False).json().get("data", {})
    summary.update({"done": done, "total": len(st_all), "rest": rest,
                    "level": prof2.get("level", "?"), "energy": energy})
    log("🏁 %s: 完成%s/%s 等级%s 剩余: %s" % (acc.get("note", ""), done, len(st_all), prof2.get("level", "?"),
                                             ", ".join(rest) if rest else "无"))
    return msgs, summary



# ---------- 推送摘要（精简版，避免超长截断） ----------
def build_summary(summaries):
    """每个账号详细中文报告 + 总计统计"""
    summaries.sort(key=lambda x: x.get("idx", 0))
    total_done = total_tasks = 0
    lines = []
    lines.append("📊 各账号运行报告")
    lines.append("")

    for sm in summaries:
        idx = sm.get("idx", 0)
        done = sm.get("done", 0)
        total = sm.get("total", 0)
        total_done += done
        total_tasks += total
        credits = sm.get("credits", "")
        usage = sm.get("usage", "")
        energy = sm.get("energy", "?")
        level = sm.get("level", "?")
        streak = sm.get("streak", "?")
        note = sm.get("note", "")[:16]
        rest = sm.get("rest") or []
        # 翻译任务代码为中文
        rest_cn = [task_cn(r) for r in rest]

        lines.append("👤 账号%d  %s" % (idx, note))
        lines.append("   💰 %s" % (credits if credits else "暂无数据"))
        lines.append("   📊 %s" % (usage if usage else "暂无数据"))
        lines.append("   🌱 等级%s | 连签%s天 | 能量%s" % (level, streak, energy))
        if rest_cn:
            lines.append("   ⏳ 未完成: %s" % "、".join(rest_cn))
        else:
            lines.append("   ✅ 全部完成！")
        lines.append("")

    lines.append("📊 ══ 总计 ══")
    lines.append("👥 共%d个账号，任务完成 %d/%d 项" % (len(summaries), total_done, total_tasks))

    # 汇总待办分布（中文）
    all_rest = {}
    for sm in summaries:
        for r in (sm.get("rest") or []):
            cn = task_cn(r)
            all_rest[cn] = all_rest.get(cn, 0) + 1
    if all_rest:
        lines.append("")
        for cn, cnt in sorted(all_rest.items(), key=lambda x: -x[1]):
            lines.append("   · %s（%d个账号待完成）" % (cn, cnt))

    lines.append("")
    lines.append("🕐 %s" % time.strftime("%Y-%m-%d %H:%M"))
    return chr(10).join(lines)


# ---------- 推送通知（内置 PushPlus，无需外部模块） ----------
def send_notify(title, content):
    """PushPlus 推送；未配置 PUSHPLUS_TOKEN 则跳过"""
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token:
        return False
    # 内容过长时截断（PushPlus 上限约 2 万字）
    if len(content) > 18000:
        content = content[:18000] + "\n...(内容过长已截断)"
    try:
        s = requests.Session(); s.trust_env = False
        r = s.post("https://www.pushplus.plus/send",
                   json={"token": token, "title": title, "content": content, "template": "txt"},
                   timeout=20, verify=False)
        d = r.json()
        if d.get("code") == 200:
            print("📢 推送成功")
            return True
        print("📢 推送失败: %s" % str(d.get("msg", ""))[:80])
    except Exception as e:
        print("📢 推送异常: %s" % str(e)[:80])
    return False


def main():
    if "--refresh" in sys.argv:
        print("╔═══════════════════════════════════╗")
        print("║ 🔐 WorkBuddy Token 续期工具       ║")
        print("╚═══════════════════════════════════╝")
        updated = refresh_all()
        if updated:
            print("✅ 共续期 %d 个账号: %s" % (len(updated), ", ".join(updated.keys())))
        else:
            print("⚠️ 无账号续期（检查 wb_refresh_tokens.json 是否存在）")
        print("📄 WORKBUDDY_ACCESS_TOKEN.txt 已同步更新")
        return
    print("╔════════════════════════════════════════╗")
    print("║ 🌱 WorkBuddy 全能脚本                  ║")
    print("║ 🔐续期 💰积分 📊用量 🌱成长            ║")
    print("║ ✅任务 🎮玩法 🎁领奖 📢推送            ║")
    print("╚════════════════════════════════════════╝")
    print("👥 账号数: %d" % len(ACCOUNTS))
    do_desktop = not NO_DESKTOP
    if do_desktop and sys.platform != "win32":
        print("⚠️ 非Windows环境(青龙/云服务器)，自动跳过桌面任务(RichMeow/技能需一次性在Windows桌面端完成)")
        do_desktop = False
    all_msgs = []
    summaries = []
    if QUERY_ONLY:
        with ThreadPoolExecutor(max_workers=min(6, len(ACCOUNTS))) as ex:
            futs = {ex.submit(run_account, i + 1, acc, False): i for i, acc in enumerate(ACCOUNTS)}
            for f in as_completed(futs):
                msgs_part, sm = f.result()
                all_msgs.extend(msgs_part)
                summaries.append(sm)
    else:
        # 云端任务并发，桌面任务串行
        for i, acc in enumerate(ACCOUNTS):
            msgs_part, sm = run_account(i + 1, acc, do_desktop)
            all_msgs.extend(msgs_part)
            summaries.append(sm)
            time.sleep(2)
    # 推送摘要（完整日志见青龙日志/控制台）
    send_notify("🌱 WorkBuddy 签到报告", build_summary(summaries))


from concurrent.futures import ThreadPoolExecutor, as_completed

if __name__ == "__main__":
    main()
