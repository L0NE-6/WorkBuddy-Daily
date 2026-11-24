import importlib.util
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path


SOURCE_PATH = Path(__file__).resolve().parents[1] / "workbuddy_intl_daily.py"
SPEC = importlib.util.spec_from_file_location("workbuddy_intl_daily_test", SOURCE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Response:
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self.payload = payload
        self.text = text

    def json(self):
        return self.payload

    def close(self):
        return None


class StreamResponse(Response):
    def __init__(self, status_code, payload, lines, text=""):
        super().__init__(status_code, payload, text)
        self.lines = lines

    def iter_lines(self, decode_unicode=False):
        for line in self.lines:
            yield line


class InternationalBehaviorTests(unittest.TestCase):
    def test_keepalive_403_is_model_permission_not_account_restriction(self):
        response = Response(403, {"code": 403, "msg": "model forbidden"})

        class FakeSession:
            def __init__(self):
                self.trust_env = True

            def post(self, *args, **kwargs):
                return response

        old_session = MODULE.requests.Session
        old_chain = MODULE.model_chain
        MODULE.requests.Session = FakeSession
        MODULE.model_chain = lambda: ["free-model"]
        try:
            ok, note = MODULE.keepalive("token", "uid")
        finally:
            MODULE.requests.Session = old_session
            MODULE.model_chain = old_chain
        self.assertFalse(ok)
        self.assertIn("非账号风控", note)

    def test_keepalive_11140_still_raises_account_restricted(self):
        response = Response(403, {"code": 11140, "msg": "request illegal"})

        class FakeSession:
            def __init__(self):
                self.trust_env = True

            def post(self, *args, **kwargs):
                return response

        old_session = MODULE.requests.Session
        old_chain = MODULE.model_chain
        MODULE.requests.Session = FakeSession
        MODULE.model_chain = lambda: ["free-model"]
        try:
            with self.assertRaises(MODULE.AccountRestricted):
                MODULE.keepalive("token", "uid")
        finally:
            MODULE.requests.Session = old_session
            MODULE.model_chain = old_chain

    def test_preflight_stops_on_11140_before_refresh_or_writes(self):
        MODULE.post_json = lambda *args, **kwargs: Response(
            403, {"code": 11140, "msg": "request illegal"})
        with self.assertRaises(MODULE.AccountRestricted):
            MODULE.preflight_restricted("token", "uid")

    def test_plain_401_is_left_for_normal_refresh_path(self):
        MODULE.post_json = lambda *args, **kwargs: Response(
            401, {"code": 401, "msg": "token expired"})
        response, payload = MODULE.preflight_restricted("token", "uid")
        self.assertEqual(401, response.status_code)
        self.assertEqual(401, payload["code"])

    def test_claim_label_distinguishes_restricted_from_expired(self):
        self.assertIn("账号受限", MODULE.claim_label(11140, ""))
        self.assertIn("登录状态失效", MODULE.claim_label(401, ""))

    def test_growth_task_parser_accepts_level_family_code_field(self):
        row = {"code": "first_chat", "status": "available"}

        self.assertEqual("first_chat", MODULE.growth_task_code(row))
        self.assertEqual("available", MODULE.growth_task_status(row))
        self.assertFalse(MODULE.growth_task_is_claimed(row))
        self.assertFalse(MODULE.growth_task_is_ready(row))

    def test_growth_success_code_accepts_numeric_and_string_200(self):
        for code in (0, 200, "0", "200", None):
            self.assertTrue(MODULE._growth_code_ok(code))
        self.assertFalse(MODULE._growth_code_ok(500))
        self.assertFalse(MODULE._growth_code_ok("500"))

    def test_growth_task_ready_requires_server_completion(self):
        self.assertTrue(MODULE.growth_task_is_ready({"status": "completed"}))
        self.assertTrue(MODULE.growth_task_is_ready({
            "status": "accepted",
            "progress": {"current": 1, "target": 1},
        }))
        self.assertFalse(MODULE.growth_task_is_ready({
            "status": "available",
            "progress": {"current": 0, "target": 1},
        }))
        self.assertFalse(MODULE.growth_task_is_ready({
            "status": "not_accepted",
            "progress": {"current": 1, "target": 1},
        }))
        self.assertFalse(MODULE.growth_task_is_ready({
            "status": "in_progress",
            "progress": {"current": 1, "target": 1},
        }))
        self.assertFalse(MODULE.growth_task_is_ready({
            "status": "completed",
            "locked": True,
        }))

    def test_wechat_linked_is_detection_only(self):
        output = io.StringIO()
        rows = {"wechat_linked": {"code": "wechat_linked", "status": "available"}}
        with mock.patch.object(MODULE, "fetch_growth_tasks", return_value=(rows, [])), \
                mock.patch.object(MODULE, "perform_growth_task_action") as action, \
                mock.patch.object(MODULE, "claim_growth_task") as claim, \
                redirect_stdout(output):
            result = MODULE.run_growth_tasks("token", "uid", experimental=True)

        self.assertEqual([("wechat_linked", "需客户端绑定微信")], result)
        action.assert_not_called()
        claim.assert_not_called()
        self.assertIn("不伪造完成", output.getvalue())

    def test_completed_wechat_linked_is_not_claimed(self):
        output = io.StringIO()
        rows = {"wechat_linked": {"code": "wechat_linked", "status": "completed"}}
        with mock.patch.object(MODULE, "fetch_growth_tasks", return_value=(rows, [])), \
                mock.patch.object(MODULE, "claim_growth_task") as claim, \
                redirect_stdout(output):
            result = MODULE.run_growth_tasks("token", "uid", experimental=True)

        claim.assert_not_called()
        self.assertEqual([("wechat_linked", "服务端已达标，需官方客户端确认绑定")], result)

    def test_dual_source_merge_never_downgrades_claimed(self):
        responses = [
            Response(200, {"code": 0, "data": {"tasks": [
                {"code": "first_chat", "status": "claimed"},
            ]}}),
            Response(200, {"code": 0, "data": {"tasks": [
                {"code": "first_chat", "status": "available"},
            ]}}),
            Response(200, {"code": 0, "data": {"tasks": []}}),
        ]

        class FakeSession:
            def __init__(self, response):
                self.response = response
                self.trust_env = True

            def get(self, *args, **kwargs):
                return self.response

        sessions = [FakeSession(response) for response in responses]
        with mock.patch.object(MODULE.requests, "Session", side_effect=sessions):
            rows, errors = MODULE.fetch_growth_tasks("token", "uid")

        self.assertEqual([], errors)
        self.assertEqual("claimed", MODULE.growth_task_status(rows["first_chat"]))
        self.assertTrue(MODULE.growth_task_is_claimed(rows["first_chat"]))

    def test_accept_uses_v2_endpoint_and_requires_settled_readback(self):
        response = Response(200, {
            "code": 200,
            "data": {"results": [
                {"task_code": "other", "status": "error"},
                {"task_code": "first_chat", "status": "pending"},
            ]},
        })
        pending = {"code": "first_chat", "accept_status": "not_accepted"}
        accepted = {"code": "first_chat", "accept_status": "accepted"}

        class FakeSession:
            def __init__(self):
                self.trust_env = True
                self.post_calls = []

            def post(self, url, **kwargs):
                self.post_calls.append((url, kwargs))
                return response

        session = FakeSession()
        with mock.patch.object(MODULE.requests, "Session", return_value=session), \
                mock.patch.object(MODULE, "fetch_growth_tasks", side_effect=[
                    ({"first_chat": pending}, []),
                    ({"first_chat": accepted}, []),
                ]), mock.patch.object(MODULE.time, "sleep"):
            ok, note = MODULE.accept_growth_task("token", "uid", "first_chat")

        self.assertTrue(ok)
        self.assertIn("accepted", note)
        self.assertEqual("/v2/activity/growth/tasks/accept",
                         session.post_calls[0][0][len(MODULE.BASE):])

    def test_claim_retries_async_readback(self):
        response = Response(200, {"code": 0, "data": {"credit": 30}})

        class FakeSession:
            def __init__(self):
                self.trust_env = True

            def post(self, *args, **kwargs):
                return response

        with mock.patch.object(MODULE.requests, "Session", FakeSession), \
                mock.patch.object(MODULE, "fetch_growth_tasks", side_effect=[
                    ({"first_chat": {"code": "first_chat", "status": "completed"}}, []),
                    ({"first_chat": {"code": "first_chat", "status": "claimed"}}, []),
                ]) as fetch, mock.patch.object(MODULE.time, "sleep"):
            ok, note = MODULE.claim_growth_task("token", "uid", "first_chat")

        self.assertTrue(ok)
        self.assertIn("回读确认", note)
        self.assertEqual(2, fetch.call_count)

    def test_action_without_server_progress_never_claims(self):
        output = io.StringIO()
        rows = {"first_chat": {"code": "first_chat", "status": "available"}}
        unchanged = {"code": "first_chat", "status": "available"}
        with mock.patch.object(MODULE, "fetch_growth_tasks", return_value=(rows, [])), \
                mock.patch.object(MODULE, "perform_growth_task_action",
                                  return_value=(True, "真实对话已创建")), \
                mock.patch.object(MODULE, "_wait_growth_task", return_value=unchanged), \
                mock.patch.object(MODULE, "claim_growth_task") as claim, \
                redirect_stdout(output):
            result = MODULE.run_growth_tasks("token", "uid", experimental=True)

        claim.assert_not_called()
        self.assertIn("状态未变", result[0][1])
        self.assertIn("不冒领", output.getvalue())

    def test_international_growth_tasks_are_read_only_by_default(self):
        output = io.StringIO()
        rows = {"first_chat": {"code": "first_chat", "status": "available"}}
        with mock.patch.object(MODULE, "fetch_growth_tasks", return_value=(rows, [])), \
                mock.patch.object(MODULE, "perform_growth_task_action") as action, \
                mock.patch.object(MODULE, "claim_growth_task") as claim, \
                redirect_stdout(output):
            result = MODULE.run_growth_tasks("token", "uid")

        action.assert_not_called()
        claim.assert_not_called()
        self.assertIn("只读", result[0][1])
        self.assertIn("未开放自动任务事件", output.getvalue())

    def test_report_growth_events_requires_explicit_zero_code(self):
        class FakeSession:
            def __init__(self):
                self.trust_env = True

            def post(self, *args, **kwargs):
                return Response(200, {})

        with mock.patch.object(MODULE.requests, "Session", FakeSession):
            ok, note = MODULE.report_growth_events(
                "token", "uid", [{"eventCode": "first_chat"}])

        self.assertFalse(ok)
        self.assertIn("code=None", note)

    def test_credit_total_sums_precise_and_legacy_fields(self):
        class FakeSession:
            def __init__(self):
                self.trust_env = True

            def post(self, *args, **kwargs):
                return Response(200, {
                    "code": 0,
                    "data": {"Packages": [
                        {"CycleRemainCapacity": 10},
                        {"CycleRemainCapacityPrecise": "2.5"},
                    ]},
                })

        with mock.patch.object(MODULE.requests, "Session", FakeSession):
            total, error = MODULE.query_credit_total("token", "uid")

        self.assertEqual(12.5, total)
        self.assertEqual("", error)

    def test_inactive_activity_does_not_promise_credits(self):
        text = MODULE.day_completed_note(
            "活动已关闭", True, False, False, False, True, True)

        self.assertIn("积分未确认", text)
        self.assertNotIn("异步发放", text)

    def test_claim_daily_marks_delayed_credit_without_claiming_arrival(self):
        response = Response(200, {
            "code": 0,
            "data": {"credit": 30, "credit_delayed": True},
        })
        with mock.patch.object(MODULE, "post_json", return_value=response):
            ok, note = MODULE.claim_daily("token", "uid")

        self.assertTrue(ok)
        self.assertIn("待入账", note)

    def test_completed_task_is_claimed_only_after_readback(self):
        output = io.StringIO()
        rows = {"template_used": {"code": "template_used", "status": "available"}}
        completed = {"code": "template_used", "status": "completed"}
        with mock.patch.object(MODULE, "fetch_growth_tasks", return_value=(rows, [])), \
                mock.patch.object(MODULE, "perform_growth_task_action",
                                  return_value=(True, "模板事件已上报")), \
                mock.patch.object(MODULE, "_wait_growth_task", return_value=completed), \
                mock.patch.object(MODULE, "claim_growth_task",
                                  return_value=(True, "已领取（回读确认）")) as claim, \
                redirect_stdout(output):
            result = MODULE.run_growth_tasks("token", "uid", experimental=True)

        claim.assert_called_once_with("token", "uid", "template_used")
        self.assertEqual([("template_used", "已领取（回读确认）")], result)

    def test_claim_requires_claimed_readback(self):
        response = Response(200, {"code": 0, "data": {"credit": 30}})

        class FakeSession:
            def __init__(self):
                self.trust_env = True

            def post(self, *args, **kwargs):
                return response

        with mock.patch.object(MODULE.requests, "Session", FakeSession), \
                mock.patch.object(MODULE, "fetch_growth_tasks", return_value=(
                    {"first_chat": {"code": "first_chat", "status": "available"}}, [])):
            ok, note = MODULE.claim_growth_task("token", "uid", "first_chat")

        self.assertFalse(ok)
        self.assertIn("回读仍为", note)

    def test_claim_confirms_only_claimed_status(self):
        response = Response(200, {"code": 0, "data": {"credit": 30}})

        class FakeSession:
            def __init__(self):
                self.trust_env = True

            def post(self, *args, **kwargs):
                return response

        with mock.patch.object(MODULE.requests, "Session", FakeSession), \
                mock.patch.object(MODULE, "fetch_growth_tasks", return_value=(
                    {"first_chat": {"code": "first_chat", "status": "claimed"}}, [])):
            ok, note = MODULE.claim_growth_task("token", "uid", "first_chat")

        self.assertTrue(ok)
        self.assertIn("回读确认", note)

    def test_first_chat_uses_real_conversation_id(self):
        captured = {}
        with mock.patch.object(MODULE, "web_task_conversation_session",
                               return_value=(True, "ok", {
                                   "id": "conv-real", "messageId": "cmb-real",
                               }, "model-x")), \
                mock.patch.object(MODULE, "report_growth_events",
                                  side_effect=lambda token, uid, events: (
                                      captured.setdefault("events", events), (True, "reported"))[1]):
            ok, note = MODULE.perform_growth_task_action("token", "uid", "first_chat")

        self.assertTrue(ok)
        self.assertIn("reported", note)
        self.assertEqual("conv-real", captured["events"][0]["conversationId"])
        self.assertEqual("cmb-real", captured["events"][0]["messageId"])
        self.assertEqual("cmb-real", captured["events"][2]["rootRequestId"])
        self.assertTrue(all(event["conversationId"] == "conv-real"
                            for event in captured["events"] if "conversationId" in event))

    def test_skill_install_failure_stops_before_fake_event(self):
        with mock.patch.object(MODULE, "fetch_task_skill",
                               return_value=({"id": "skill-1", "name": "demo"}, "")), \
                mock.patch.object(MODULE, "install_task_skill",
                                  return_value=(False, "install failed")), \
                mock.patch.object(MODULE, "web_task_conversation_session") as web, \
                mock.patch.object(MODULE, "report_growth_events") as report:
            ok, note = MODULE.perform_growth_task_action(
                "token", "uid", "skill_installed")

        self.assertFalse(ok)
        self.assertEqual("install failed", note)
        web.assert_not_called()
        report.assert_not_called()

    def test_task_specific_event_codes_are_not_aliased(self):
        template_events = MODULE._template_task_events(
            {"id": "1", "name": "Research"}, "conv", "req")
        expert_events = MODULE._expert_task_events(
            {"id": "ex", "name": "Expert", "profession": "Expert",
             "expertType": "agent", "version": "1.0.0"},
            "conv", "cmb-req", "cmb-req", "hello")
        skill_events = MODULE._skill_task_events(
            {"id": "skill-1", "slug": "skill-slug", "name": "Skill"},
            "conv", "cmb-req", "cmb-req")

        self.assertIn("agent_task_created_with_template",
                      [event["eventCode"] for event in template_events])
        self.assertIn("template_used", [event["eventCode"] for event in template_events])
        self.assertIn("expert_summoned", [event["eventCode"] for event in expert_events])
        self.assertIn("expert_actual_use", [event["eventCode"] for event in expert_events])
        self.assertIn("skill_info", [event["eventCode"] for event in skill_events])
        self.assertNotIn("skill_installed", [event["eventCode"] for event in skill_events])
        template_created = next(
            event for event in template_events if event["eventCode"] == "agent_task_created")
        self.assertTrue(template_created["has_template"])
        self.assertEqual("CLOUD", template_created["source"])
        self.assertIn("playbook_prompt_send",
                      [event["eventCode"] for event in template_events])
        expert_use = next(
            event for event in expert_events if event["eventCode"] == "expert_actual_use")
        self.assertEqual("LOCAL", expert_use["mode"])
        self.assertEqual("cmb-req", expert_use["messageId"])
        skill_info = next(event for event in skill_events if event["eventCode"] == "skill_info")
        self.assertEqual("cmb-req", skill_info["messageId"])
        self.assertEqual("skill-slug", skill_info["id"])

    def test_task_headers_split_level_web_and_mp_sources(self):
        level = MODULE.level_task_headers("token", "uid")
        web = MODULE.growth_headers("token", "uid", platform="web")
        mp = MODULE.growth_headers("token", "uid", platform="miniprogram")

        self.assertNotIn("X-Client-Platform", level)
        self.assertEqual("web", web["X-Client-Platform"])
        self.assertEqual("miniprogram", mp["X-Client-Platform"])
        self.assertEqual("www.workbuddy.ai", level["X-Domain"])

    def test_fetch_growth_tasks_uses_source_specific_platform_header(self):
        class FakeSession:
            def __init__(self):
                self.trust_env = True
                self.get_calls = []

            def get(self, url, **kwargs):
                self.get_calls.append((url, kwargs))
                return Response(200, {"code": 0, "data": {"tasks": []}})

        sessions = [FakeSession(), FakeSession(), FakeSession()]
        with mock.patch.object(MODULE.requests, "Session", side_effect=sessions):
            MODULE.fetch_growth_tasks("token", "uid")

        level_headers = sessions[0].get_calls[0][1]["headers"]
        desktop_headers = sessions[1].get_calls[0][1]["headers"]
        mp_headers = sessions[2].get_calls[0][1]["headers"]
        self.assertNotIn("X-Client-Platform", level_headers)
        self.assertNotIn("X-Client-Platform", desktop_headers)
        self.assertEqual("miniprogram", mp_headers["X-Client-Platform"])

    def test_web_task_conversation_requires_real_sse_message_id(self):
        lines = [
            'data: {"id":"cmb-real","choices":[{"delta":{"content":"OK"}}]}',
            "data: [DONE]",
        ]
        create_response = Response(
            200, {"code": 0, "data": {"conversationId": "conv-real"}})
        chat_response = StreamResponse(200, {}, lines)

        class FakeSession:
            def __init__(self):
                self.trust_env = True
                self.post_calls = []

            def post(self, url, **kwargs):
                self.post_calls.append((url, kwargs))
                return create_response if len(self.post_calls) == 1 else chat_response

        session = FakeSession()
        with mock.patch.object(MODULE.requests, "Session", return_value=session), \
                mock.patch.object(MODULE, "model_chain", return_value=["model-x"]):
            ok, note, data, model = MODULE.web_task_conversation_session(
                "token", "uid", "hello", metadata={"userId": "uid"})

        self.assertTrue(ok)
        self.assertIn("已完成", note)
        self.assertEqual("conv-real", data["id"])
        self.assertEqual("cmb-real", data["messageId"])
        self.assertEqual("model-x", model)
        self.assertTrue(session.post_calls[0][0].endswith("/console/webchat/conversations"))
        self.assertIn("WorkBuddy task-", session.post_calls[0][1]["json"]["name"])
        self.assertTrue(session.post_calls[1][0].endswith("/console/chat/completions"))
        self.assertEqual("conv-real", session.post_calls[1][1]["json"]["conversationId"])
        self.assertEqual(
            [
                {"role": "system", "content": MODULE.SYSTEM_PROMPT},
                {"role": "user", "content": "hello"},
            ],
            session.post_calls[1][1]["json"]["messages"],
        )
        self.assertEqual("text/event-stream", session.post_calls[1][1]["headers"]["Accept"])
        self.assertEqual(MODULE.BASE + "/", session.post_calls[1][1]["headers"]["Referer"])
        self.assertEqual(
            "web", session.post_calls[1][1]["headers"]["X-Client-Platform"])
        self.assertEqual("uid", session.post_calls[1][1]["json"]["_meta"]["userId"])

    def test_web_task_conversation_400_includes_server_reason(self):
        create_response = Response(
            200, {"code": 0, "data": {"conversationId": "conv-real"}})
        bad_response = Response(
            400,
            {"code": 400001, "msg": "request data invalid"},
            text='{"code":400001,"msg":"request data invalid"}',
        )

        class FakeSession:
            def __init__(self):
                self.trust_env = True
                self.post_calls = []

            def post(self, url, **kwargs):
                self.post_calls.append((url, kwargs))
                return create_response if len(self.post_calls) == 1 else bad_response

        with mock.patch.object(MODULE.requests, "Session", return_value=FakeSession()), \
                mock.patch.object(MODULE, "model_chain", return_value=["model-x"]):
            ok, note, _data, _model = MODULE.web_task_conversation_session(
                "token", "uid", "hello")

        self.assertFalse(ok)
        self.assertIn("HTTP 400", note)
        self.assertIn("code=400001", note)
        self.assertIn("request data invalid", note)

    def test_web_task_conversation_without_message_id_fails(self):
        create_response = Response(
            200, {"code": 0, "data": {"conversationId": "conv-real"}})
        chat_response = StreamResponse(200, {}, [
            'data: {"choices":[{"delta":{"content":"OK"}}]}',
            "data: [DONE]",
        ])

        class FakeSession:
            def __init__(self):
                self.trust_env = True
                self.count = 0

            def post(self, *args, **kwargs):
                self.count += 1
                return create_response if self.count == 1 else chat_response

        with mock.patch.object(MODULE.requests, "Session", FakeSession), \
                mock.patch.object(MODULE, "model_chain", return_value=["model-x"]):
            ok, note, _data, _model = MODULE.web_task_conversation_session(
                "token", "uid", "hello")

        self.assertFalse(ok)
        self.assertIn("messageId", note)

    def test_fetch_task_scene_uses_public_english_endpoint(self):
        class FakeSession:
            def __init__(self):
                self.trust_env = True
                self.get_calls = []

            def get(self, url, **kwargs):
                self.get_calls.append((url, kwargs))
                return Response(200, {
                    "code": 0,
                    "data": {"scenes": [{"id": 1, "name": "Slides Creation"}]},
                })

        session = FakeSession()
        with mock.patch.object(MODULE.requests, "Session", return_value=session):
            scene, error = MODULE.fetch_task_scene("token", "uid")

        self.assertEqual("", error)
        self.assertEqual("1", scene["id"])
        self.assertTrue(session.get_calls[0][0].endswith(
            "/v2/as/support/scenes?locale=en"))
        self.assertNotIn("Authorization", session.get_calls[0][1]["headers"])

    def test_install_skill_requires_installed_readback(self):
        sources = Response(200, {
            "code": 0, "data": {"sources": [{"id": "market-1"}]},
        })
        installed_missing = Response(200, {"code": 0, "data": {"plugins": []}})
        installed_present = Response(200, {
            "code": 0,
            "data": {"plugins": [{"plugin_name": "demo-slug", "enabled": True}]},
        })

        class FakeSession:
            def __init__(self):
                self.trust_env = True
                self.get_responses = [
                    installed_missing,
                    sources,
                    installed_missing,
                    installed_present,
                ]

            def get(self, *args, **kwargs):
                return self.get_responses.pop(0)

            def post(self, *args, **kwargs):
                return Response(200, {"code": 0})

        with mock.patch.object(MODULE.requests, "Session", FakeSession), \
                mock.patch.object(MODULE.time, "sleep"):
            ok, note = MODULE.install_task_skill(
                "token", "uid", {
                    "id": "skill-1", "slug": "demo-slug", "name": "Demo Skill",
                })

        self.assertTrue(ok)
        self.assertIn("回读确认", note)

    def test_report_growth_events_adds_desktop_envelope(self):
        captured = {}

        class FakeSession:
            def __init__(self):
                self.trust_env = True

            def post(self, url, **kwargs):
                captured["url"] = url
                captured["headers"] = kwargs["headers"]
                captured["json"] = kwargs["json"]
                return Response(200, {"code": 0})

        with mock.patch.object(MODULE.requests, "Session", FakeSession):
            ok, note = MODULE.report_growth_events(
                "token", "uid", [{"eventCode": "first_chat"}])

        self.assertTrue(ok)
        self.assertIn("已上报", note)
        self.assertEqual("SaaS", captured["headers"]["X-Product"])
        self.assertEqual("www.workbuddy.ai", captured["headers"]["X-Domain"])
        event = captured["json"][0]
        self.assertEqual("uid", event["userId"])
        self.assertTrue(event["machineId"])
        self.assertTrue(event["sessionId"])
        self.assertTrue(event["qimei36"])
        self.assertEqual(1789036585355, event["releaseDate"])
        self.assertEqual("5f9692923c93033111c51ad7b003eb80204a9b75", event["commit"])
        self.assertEqual("win32", event["os"])
        self.assertEqual(20, event["cpuCores"])
        self.assertEqual(24, event["memorySize"])
        self.assertEqual("WorkBuddy", event["ideName"])

    def test_no_keepalive_skips_web_task_action(self):
        rows = {"first_chat": {
            "code": "first_chat", "status": "available", "_source": "level",
        }}
        output = io.StringIO()
        with mock.patch.object(MODULE, "fetch_growth_tasks", return_value=(rows, [])), \
                mock.patch.object(MODULE, "perform_growth_task_action") as action, \
                redirect_stdout(output):
            result = MODULE.run_growth_tasks(
                "token", "uid", allow_web=False, experimental=True)

        action.assert_not_called()
        self.assertEqual([("first_chat", "已跳过（--no-keepalive）")], result)


if __name__ == "__main__":
    unittest.main()
