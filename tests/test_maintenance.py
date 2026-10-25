"""Offline regressions for maintenance helpers and the installed provider SDKs."""
import ast
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_conclave import APP, load_function, literal_assignments, source_tree


class MaintenanceTests(unittest.TestCase):
    def test_model_migration_covers_all_pickers_without_touching_transcripts(self):
        tree = source_tree()
        ns = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                name = getattr(node.targets[0], "id", "")
                if name.endswith("_MODELS") or name in ("MODEL_PICKER_SPECS", "_RETIRED_MODEL_LABELS"):
                    exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP), "exec"), ns)
        migrate = load_function("migrate_model_labels", ns)
        specs = ns["MODEL_PICKER_SPECS"]
        state = {"thread": [{"model": "retired-model"}]}
        defaults = {}
        for provider, (key, suffix, options) in specs.items():
            default = list(options)[-1]
            for k in [key] + [f"{p}_{suffix}" for p in ("k_syn", "k_fu", "k_adj", "k_res")]:
                state[k] = "removed label"
                defaults[k] = default
        state["k_cla_model"] = "Fable 5"
        state["k_meta_model"] = "Spark 1.2 (flagship)"
        state["k_gem"] = "Flash (3.7)"
        state["k_glm_model"] = "GLM-5 Turbo (balanced)"
        state["k_syn_gpt_model"] = "Sol (flagship)"
        migrate(state, defaults, specs, ns["_RETIRED_MODEL_LABELS"])
        self.assertEqual(state["k_cla_model"], "Fable 5.1")
        self.assertEqual(state["k_meta_model"], "Spark 1.3 (balanced)")
        self.assertEqual(state["k_gem"], "Flash (3.8)")
        self.assertEqual(state["k_glm_model"], "GLM-5.3 Flash (economy)")
        self.assertEqual(state["k_syn_gpt_model"], "Sol (advanced)")
        for _, (key, suffix, options) in specs.items():
            for k in [key] + [f"{p}_{suffix}" for p in ("k_syn", "k_fu", "k_adj", "k_res")]:
                self.assertIn(state[k], options)
        before = dict(state)
        migrate(state, defaults, specs, ns["_RETIRED_MODEL_LABELS"])
        self.assertEqual(state, before)
        self.assertEqual(state["thread"], [{"model": "retired-model"}])
        # A removed default and stale alias still recover to an available model.
        migrate(state, {}, {"Test": ("unknown", "unknown", {"available": "id"})}, {})
        self.assertEqual(state["unknown"], "available")

    def test_phase_accounting_preserves_order_costs_and_quota_semantics(self):
        state = {"quota_err_Claude": True}
        costs = []
        ns = {"st": SimpleNamespace(session_state=state), "add_cost": costs.append}
        account = load_function("commit_result_accounting", ns)
        phase = load_function("commit_background_phase", ns)
        account({"Claude": {"err": True}}, fallback_to_label=True)
        self.assertTrue(state["quota_err_Claude"])
        job = {"phase2": {"result": {"err": False, "cost": 1}},
               "phase3": {"result": {"provider": "Claude", "err": True, "credit_err": True, "cost": 2}},
               "phase4": {"result": {"provider": "Claude", "err": False, "cost": 3}}}
        plan = {p: {"provider": "Claude", "label_key": label}
                for p, label in (("phase2", "Synthesizer"), ("phase3", "Adjudicator"), ("phase4", "Revision"))}
        phase(job, plan, "phase2")
        self.assertNotIn("quota_err_Claude", state)
        phase(job, plan, "phase3")
        self.assertTrue(state["quota_err_Claude"])
        phase(job, plan, "phase4")
        self.assertNotIn("quota_err_Claude", state)
        before = len(costs)
        self.assertEqual(phase(job, plan, "missing"), (None, None))
        self.assertEqual(len(costs), before)
        self.assertEqual([next(iter(c)) for c in costs],
                         ["Claude", "Synthesizer", "Adjudicator", "Revision"])
        self.assertEqual(sum(r.get("cost", 0) for c in costs for r in c.values()), 6)

    def test_evaluator_and_memo_use_supplied_key_snapshot_even_when_empty(self):
        ns = literal_assignments("KEY_MAP", "CLAUDE_MODELS", "CLAUDE_EFFORT", "NO_WEB_PROVIDERS")
        read_keys = Mock(return_value={"anthropic": "fallback-fixture"})
        ns.update(st=SimpleNamespace(session_state={}), eval_provider_of=lambda _: "Claude",
                  load_keys=read_keys, CFG={"Claude": {"model_id": "source-model"}},
                  build_independent_memo_prompt=lambda *args: "fixture")
        evaluate = load_function("evaluator_job", ns)
        memo = load_function("build_memo_job", ns)
        key_name = ns["KEY_MAP"]["Claude"]
        for keys in ({key_name: "snapshot-fixture"}, {}):
            expected = keys.get(key_name, "")
            self.assertEqual(evaluate("k_syn", [], False, keys_now=keys)[3]["key"], expected)
            self.assertEqual(memo("k_syn", "fixture", "", False, keys_now=keys)["key"], expected)
        read_keys.assert_not_called()
        evaluate("k_syn", [], False)
        read_keys.assert_called_once()


class InstalledSDKTests(unittest.TestCase):
    """Exercise real SDK serialization/parsing with in-memory HTTP transports."""

    def _namespace(self):
        ns = literal_assignments("CHATGPT_SPEED", "CLAUDE_NO_DIALS", "CLAUDE_ALWAYS_THINK",
                                 "CLAUDE_OLD_SEARCH")
        ns.update(threading=threading, _request_timeout=lambda *args: 30.0,
                  claude_thinking_forced=lambda *args: False)
        for name in ("_inline_text_files", "_binary_files", "_oai_chat_messages",
                     "_oai_responses_input", "_claude_messages", "_gemini_interactions_input",
                     "_content_text", "_chat_completion_text", "_responses_call", "call_model"):
            load_function(name, ns)
        return ns

    @staticmethod
    def _transport_module(sdk):
        # v3 OpenAI/v1 Anthropic switched their default transport to HTTPX2.
        major = int(sdk.__version__.split(".")[0])
        use_v2 = major >= (3 if sdk.__name__ == "openai" else 1)
        if use_v2:
            import httpx2
            return httpx2
        import httpx
        return httpx

    def test_openai_chat_and_responses_round_trip(self):
        import openai
        http = self._transport_module(openai)
        captured = []
        def handler(request):
            body = json.loads(request.content)
            captured.append(body)
            if str(request.url).endswith("/chat/completions"):
                response = {"id": "fixture", "object": "chat.completion", "created": 0,
                            "model": body["model"], "choices": [{"index": 0, "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "fixture answer"}}],
                            "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7}}
            else:
                response = {"id": "fixture", "object": "response", "created_at": 0, "status": "completed",
                            "model": body["model"], "output": [{"type": "message", "id": "m",
                            "status": "completed", "role": "assistant", "content": [
                            {"type": "output_text", "text": "fixture answer", "annotations": []}]}],
                            "usage": {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7}}
            return http.Response(200, json=response)
        with http.Client(transport=http.MockTransport(handler)) as transport:
            client = openai.OpenAI(api_key="offline-fixture", http_client=transport, max_retries=0)
            ns = self._namespace()
            ns["OpenAI"] = lambda **kwargs: client
            with patch("socket.create_connection", side_effect=AssertionError("Network forbidden")):
                for web in (False, True):
                    result = ns["call_model"]("ChatGPT", "offline-fixture",
                        [{"role": "user", "content": "fixture"}],
                        {"model_id": "gpt-5.6-luna", "effort": "low", "speed": "Fast"}, web)
                    self.assertEqual(result, ("fixture answer", 4, 3))
        self.assertEqual(captured[0]["reasoning"], {"effort": "low", "summary": "auto"})
        self.assertNotIn("tools", captured[0])
        self.assertEqual(captured[1]["tools"][0]["type"], "web_search")
        self.assertTrue(all(b["service_tier"] == "fast" for b in captured))

    def test_anthropic_native_dials_and_returned_thinking(self):
        import anthropic
        http = self._transport_module(anthropic)
        captured = []
        def handler(request):
            captured.append(json.loads(request.content))
            return http.Response(200, json={"id": "fixture", "type": "message", "role": "assistant",
                "model": "claude-sonnet-5", "stop_reason": "end_turn", "stop_sequence": None,
                "content": [{"type": "thinking", "thinking": "fixture reasoning", "signature": "fixture"},
                            {"type": "text", "text": "fixture answer"}],
                "usage": {"input_tokens": 4, "output_tokens": 3}})
        with http.Client(transport=http.MockTransport(handler)) as transport:
            client = anthropic.Anthropic(api_key="offline-fixture", http_client=transport,
                                         max_retries=0, timeout=480.0)
            ns = self._namespace()
            ns["Anthropic"] = lambda **kwargs: client
            ns["_take_reasoning"]()
            with patch("socket.create_connection", side_effect=AssertionError("Network forbidden")):
                result = ns["call_model"]("Claude", "offline-fixture",
                    [{"role": "user", "content": "fixture"}],
                    {"model_id": "claude-sonnet-5", "effort": "high", "thinking": True, "max_searches": 2}, True)
            self.assertEqual(result, ("fixture answer", 4, 3))
            self.assertIn("fixture reasoning", ns["_take_reasoning"]())
        self.assertEqual(captured[0]["thinking"], {"type": "adaptive", "display": "summarized"})
        self.assertEqual(captured[0]["output_config"], {"effort": "high"})
        self.assertEqual(captured[0]["tools"][0]["max_uses"], 2)

    def test_google_stateless_interactions_round_trip(self):
        import httpx
        from google import genai
        from google.genai import types
        captured = []
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "fixture", "status": "completed",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": "fixture answer"}]}],
                "usage": {"total_input_tokens": 4, "total_output_tokens": 3}})
        with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
            client = genai.Client(api_key="offline-fixture",
                                  http_options=types.HttpOptions(httpx_client=transport))
            ns = self._namespace()
            ns.update(genai=SimpleNamespace(Client=lambda **kwargs: client), types=types)
            with patch("socket.create_connection", side_effect=AssertionError("Network forbidden")):
                result = ns["call_model"]("Gemini", "offline-fixture",
                    [{"role": "user", "content": "fixture"}],
                    {"model_id": "gemini-3.7-pro", "level": "high"}, True)
            self.assertEqual(result, ("fixture answer", 4, 3))
            client.close()
        self.assertFalse(captured[0]["store"])
        self.assertEqual(captured[0]["tools"], [{"type": "google_search"}])
        self.assertEqual(captured[0]["generation_config"]["thinking_level"], "high")
        self.assertEqual(captured[0]["generation_config"]["thinking_summaries"], "auto")
