#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""📦 归档：开学季活动（school_open_day_2026）+ 校园日 mp 任务 school_season

归档时间：2026-10-07
归档原因：
  · 服务端 `GET /portal/activity/school/tasks` 返回 in_period=false（活动已结束，
    2026 开学季于 9 月下旬收尾），校园日 mp 任务 school_season 也已不下发；
  · 主脚本为保持精简，把这段代码整体移出（不再参与日常运行）。

说明：
  · 本文件是**存档**，不随主脚本更新，也不保证能单独运行 —— 它依赖主脚本里的
    api_retry / desktop_chat_sequence / desktop_fingerprint / derive_id /
    beijing_now 等工具函数；
  · 若官方以后重开同类活动，把本文件里的实现按新版接口核对后搬回主脚本即可；
  · 日常运行已不需要本文件（主脚本 v3.4+ 不再包含开学季逻辑）。

──── 以下为归档代码原文 ────
"""
# ---------- 开学季活动（school_open_day_2026） ----------
SCHOOL_DOMAIN = "https://www.codebuddy.cn"
SCHOOL_BASE = SCHOOL_DOMAIN + "/portal/activity/school"
SCHOOL_FRESHMAN = SCHOOL_DOMAIN + "/portal/activity/freshman"
SCHOOL_TEACHER = SCHOOL_DOMAIN + "/portal/activity/teacher"
SCHOOL_ACTIVITY_ID = "school_open_day_2026"
MP_UA = ("Mozilla/5.0 (Linux; Android 14; MicroMessenger/8.0.49 WeChat/0.8.0 "
         "MiniProgramEnv/android; wkbrowser xweb)")
SCHOOL_EXPERT_CATEGORY = "16-BackToSchool"
SCHOOL_EXPERT_FALLBACK = {"expert-school-01": {"id": "expert-school-01", "name": "开学季助手", "profession": "教育"}}
LOTTERY_PRIZE_LABELS = {
    "school_credit_6": "6积分", "school_credit_66": "66积分",
    "school_voucher_luckin": "瑞幸咖啡15元券", "school_voucher_kfc_ok": "肯德基OK餐券",
    "school_voucher_kfc_ice": "肯德基冰淇淋券", "school_voucher_kugou": "酷狗会员月卡券",
}
# 学校活动任务：manual=人工跳过, report=遥测点亮, share=share-complete
SCHOOL_TASK_MODES = {
    "task_student_verify": {"mode": "manual", "note": "微信学生认证（人工）"},
    "share_invite": {"mode": "share", "note": "分享活动给好友"},
    "chat_3_times": {"mode": "report", "note": "与AI对话3次", "kind": "mini_chat"},
    "desktop_chat_1_time": {"mode": "report", "note": "桌面端对话1次", "kind": "desktop_seq"},
    "expert_use": {"mode": "report", "note": "召唤开学季专家并对话", "kind": "expert"},
}


def _school_session(at):
    """用现有 AT 创建 school 活动会话（小程序 UA + codebuddy 域）。"""
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"Authorization": "Bearer " + at, "Accept": "application/json",
                      "Content-Type": "application/json", "User-Agent": MP_UA,
                      "Referer": "https://www.codebuddy.cn/"})
    return s


def _school_get(s, url):
    return api_retry(s, "GET", url)


def _school_post(s, url, body=None):
    return api_retry(s, "POST", url, body=body)


def _school_report(s, uid, nick, events, host=None, desktop=False):
    """开学季事件上报。host 默认 codebuddy.cn；desktop=True 时走 copilot 域（桌面任务判据）。"""
    target = host or SCHOOL_DOMAIN
    if desktop:
        out = {"common": {"userId": uid, "userNickname": nick, "ideName": "WorkBuddy",
                          "ideType": "WorkBuddy", "machineId": derive_id(uid, "machine"),
                          "mode": "LOCAL", "userAgent": UA, "os": "win32",
                          "timezone": "Asia/Shanghai"},
               "events": events}
        return api_retry(s, "POST", "https://copilot.tencent.com/v2/report", body=out,
                         headers={"X-Product": "SaaS"})
    out = {"common": {"userId": uid, "userNickname": nick, "ideName": "web-Agents",
                      "ideType": "web-Agents", "machineId": derive_id(uid, "machine"), "mode": "CLOUD",
                      "userAgent": MP_UA, "os": "Android", "timezone": "Asia/Shanghai"},
           "events": events}
    return api_retry(s, "POST", target + "/v2/report", body=out)


def _school_fetch_expert(s):
    """拉取 BackToSchool 分类的真实专家（字段名对齐上游 school.fetch_school_expert）。"""
    body = {"edition_mode": "all,domestic", "page": 1, "page_size": 20,
            "sort_by": "use_count", "sort_order": "desc",
            "categories": [SCHOOL_EXPERT_CATEGORY], "expert_type": "agent"}
    try:
        r = _school_post(s, SCHOOL_DOMAIN + "/v2/operation-platform/market/expert/list", body)
        d = r.json()
        experts = (d.get("data") or {}).get("experts") or []
        for e in experts:
            eid = e.get("expert_id")          # ← 正确字段名（不是 "id"）
            if not eid:
                continue
            dn = e.get("display_name_zh") or {}
            name = (dn.get("zh") if isinstance(dn, dict) else dn) or eid
            return eid, name
    except Exception:
        pass
    for eid, info in SCHOOL_EXPERT_FALLBACK.items():
        return eid, info["name"]
    return "", ""


def _school_desktop_seq_event(uid, nick, conv_id):
    """模拟一次桌面对话的 6 连指纹事件 + activityId（走 desktop_chat_sequence 同款形状）。"""
    evs = desktop_chat_sequence(uid, nick, conv_id, conv_id, conv_id)
    fp = desktop_fingerprint(uid, nick)
    out = []
    for e in evs:
        m = dict(e)
        m.update(fp)
        m["activityId"] = SCHOOL_ACTIVITY_ID
        out.append(m)
    return out


def _school_expert_event(uid, nick, expert_id, expert_name, conv_id):
    """专家 4 事件链（含 activityId，开学季 expert_use 判据）。"""
    return mp_expert_use_events(uid, nick, expert_id, expert_name, conv_id,
                                activity_id=SCHOOL_ACTIVITY_ID)


def _school_fetch_tasks(s):
    r = _school_get(s, SCHOOL_BASE + "/tasks")
    d = r.json()
    if d.get("code") != 0:
        return [], False
    data = d.get("data") or {}
    return data.get("tasks") or [], data.get("in_period", False)


def _school_viewed(s, code):
    r = _school_post(s, SCHOOL_BASE + "/tasks/%s/viewed" % code)
    return r.json().get("code") == 0


def _school_share_complete(s):
    r = _school_post(s, SCHOOL_BASE + "/tasks/share-complete", {"channel": "wechat"})
    return r.json().get("code") == 0


def _school_claim(s, code):
    r = _school_post(s, SCHOOL_BASE + "/tasks/%s/claim" % code)
    return r.json().get("code") == 0


def school_run_tasks(s, uid, nick, log):
    """执行开学季任务的完整流程：viewed → 判据 → 轮询 → claim → 抽奖。"""
    tasks, in_period = _school_fetch_tasks(s)
    if not in_period:
        log("  🏫 开学季活动非进行期，跳过")
        return
    log("  🏫 ── 开学季活动（%d 个任务）──" % len(tasks))
    for t in tasks:
        code = t.get("task_code", "")
        status = t.get("status", "")
        spec = SCHOOL_TASK_MODES.get(code)
        if not code:
            continue
        if status == "claimed":
            log("   %s: 已领取，跳过" % code)
            continue
        if status == "completed":
            # 已完成未领奖 → 补领（此前误判为"跳过"，导致奖励漏领）
            if _school_claim(s, code):
                log("   %s: 🎁 补领奖成功" % code)
            else:
                log("   %s: 补领奖失败（可稍后重试）" % code)
            time.sleep(WRITE_GAP)
            continue
        if spec is None:
            log("   %s: 未知任务类型，跳过" % code)
            continue
        mode = spec["mode"]
        if mode == "manual":
            log("   %s: 人工环节（%s），跳过" % (code, spec.get("note", "")))
            continue
        # viewed 激活
        try:
            if _school_viewed(s, code):
                log("   %s: viewed 激活" % code)
                time.sleep(WRITE_GAP)
        except Exception as e:
            log("   %s: viewed 失败 %s" % (code, str(e)[:60]))
            continue
        # 判据
        ok = False
        if mode == "share":
            try:
                ok = _school_share_complete(s)
                log("   %s: share-complete %s" % (code, "✅" if ok else "❌"))
                time.sleep(WRITE_GAP)
            except Exception as e:
                log("   %s: share 失败 %s" % (code, str(e)[:60]))
        elif mode == "report":
            kind = spec.get("kind", "")
            conv_id = "conv-" + str(uuid.uuid4())
            if kind == "mini_chat":
                for i in range(3):
                    try:
                        mp_report(s, uid, nick, [mp_chat_event(
                            uid, nick, "wbsc-" + str(uuid.uuid4()),
                            activity_id=SCHOOL_ACTIVITY_ID)])
                        log("   %s: chat #%d/3 ✅" % (code, i + 1))
                        time.sleep(WRITE_GAP)
                    except Exception as e:
                        log("   %s: chat #%d 失败 %s" % (code, i + 1, str(e)[:60]))
            elif kind == "desktop_seq":
                evs = _school_desktop_seq_event(uid, nick, conv_id)
                try:
                    _school_report(s, uid, nick, evs, desktop=True)
                    log("   %s: desktop_seq (copilot域) ✅" % code)
                    time.sleep(WRITE_GAP)
                except Exception as e:
                    log("   %s: desktop 失败 %s" % (code, str(e)[:60]))
            elif kind == "expert":
                eid, ename = _school_fetch_expert(s)
                if not eid:
                    log("   %s: 未取到专家，跳过" % code)
                else:
                    try:
                        evs = _school_expert_event(uid, nick, eid, ename, conv_id)
                        mp_report(s, uid, nick, evs)
                        log("   %s: expert 4事件链 ✅ (%s)" % (code, ename))
                        time.sleep(WRITE_GAP)
                    except Exception as e:
                        log("   %s: expert 失败 %s" % (code, str(e)[:60]))
        # 轮询等待完成
        for _ in range(5):
            time.sleep(2)
            try:
                ts2, _ = _school_fetch_tasks(s)
                after = next((x for x in ts2 if x.get("task_code") == code), None)
                if after and after.get("status") in ("completed", "claimed"):
                    log("   %s: ✅ 已完成" % code)
                    break
            except Exception:
                pass
        # claim
        try:
            ts3, _ = _school_fetch_tasks(s)
            after = next((x for x in ts3 if x.get("task_code") == code), None)
            if after and after.get("status") == "completed":
                if _school_claim(s, code):
                    log("   %s: 🎁 已领奖" % code)
                    time.sleep(WRITE_GAP)
        except Exception as e:
            log("   %s: claim 失败 %s" % (code, str(e)[:60]))


def school_lottery(s, uid, nick, log):
    """开学季幸运大转盘：查余额 → 循环抽到 0。"""
    try:
        r = _school_get(s, SCHOOL_BASE + "/config")
        d = r.json()
        if d.get("code") != 0:
            log("  🏫 lottery config 失败")
            return
        chance = (d.get("data") or {}).get("chance") or {}
        bal = chance.get("balance", 0)
        if not bal or bal <= 0:
            log("  🏫 lottery 余额=0，无需抽奖")
            return
        log("  🏫 lottery 余额=%s，开始抽奖..." % bal)
        results = []
        while bal > 0:
            time.sleep(WRITE_GAP)
            draw_uuid = str(uuid.uuid4())
            try:
                r2 = _school_post(s, SCHOOL_BASE + "/wheel/draw", {"draw_uuid": draw_uuid})
                d2 = r2.json()
                if d2.get("code") == 40900:
                    log("  🏫 lottery 次数耗尽")
                    break
                if d2.get("code") != 0:
                    log("  🏫 lottery draw 失败: %s" % str(d2.get("msg", ""))[:60])
                    break
                prize = (d2.get("data") or {}).get("prize_code", "")
                credit = (d2.get("data") or {}).get("credit_amount", 0)
                label = LOTTERY_PRIZE_LABELS.get(prize, prize or "未知")
                results.append(label)
                bal -= 1
                log("  🏫 lottery → %s（余 %s）" % (label, bal))
            except Exception as e:
                log("  🏫 lottery draw 异常 %s" % str(e)[:60])
                break
        if results:
            log("  🏫 lottery 汇总: %d 抽，奖品: %s" % (len(results), ", ".join(results)))
    except Exception as e:
        log("  🏫 lottery 异常 %s" % str(e)[:80])
