import ast
import base64
import html
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from copy import deepcopy
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import msvcrt
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "conclave.py"
README = ROOT / "README.md"
WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
LAUNCHER = ROOT / "launch_conclave.bat"


def source_tree():
    return ast.parse(APP.read_text(encoding="utf-8"), filename=str(APP))


def literal_assignments(*names):
    wanted = set(names)
    values = {}
    derived = {"NO_WEB_PROVIDERS", "_NAME_TO_EN_KEY", "_EN_KEY_TO_NAME",
               "_EN_KEYS_ALL", "_SRC_WEB_KEY", "_SOURCE_WEB_KEYS", "_NO_WEB_EN_KEYS",
               "ADJ_PRESETS"}
    if wanted & derived:
        env = {}
        dependencies = derived | {"PROVIDERS", "SYNTH_PRESETS", "WEB_TIER_FLOOR"}
        for node in source_tree().body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in dependencies):
                exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP), "exec"), env)
        values.update({name: env[name] for name in wanted & derived})
        wanted -= derived
    for node in source_tree().body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    values[target.id] = ast.literal_eval(node.value)
    return values


def literal_dict_string_entries(name):
    """Read literal string-keyed entries while skipping derived dict entries."""
    assignment = next(
        node for node in source_tree().body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name
                for target in node.targets)
    )
    values = {}
    for key, value in zip(assignment.value.keys, assignment.value.values):
        try:
            values[ast.literal_eval(key)] = ast.literal_eval(value)
        except (TypeError, ValueError):
            continue
    return values


_TRANSPARENCY = {}


def transparency_helpers():
    if _TRANSPARENCY:
        return _TRANSPARENCY
    consts = literal_assignments("REASONING_STORE_LIMIT", "REASONING_EXPOSURE", "LEDGER_BEGIN",
                                 "LEDGER_END", "LEDGER_NOTHING", "LEDGER_TURNS_IN_CONTEXT")
    ns = {"re": re, "threading": threading, "html": __import__("html"),
          "REASONING_STORE_LIMIT": consts["REASONING_STORE_LIMIT"],
          "REASONING_EXPOSURE": consts["REASONING_EXPOSURE"],
          "LEDGER_BEGIN": consts["LEDGER_BEGIN"], "LEDGER_END": consts["LEDGER_END"],
          "LEDGER_NOTHING": consts["LEDGER_NOTHING"],
          "LEDGER_TURNS_IN_CONTEXT": consts["LEDGER_TURNS_IN_CONTEXT"]}
    ns["_REASONING_TL"] = threading.local()
    for fn in ("_clip_context_text", "_note_reasoning", "_take_reasoning",
               "_reasoning_from_choice", "_reasoning_from_inline",
               "split_contribution_ledger", "parse_contribution_ledger",
               "contribution_ledger_instruction", "contributions_context_section"):
        _load_function_raw(fn, ns)
    ns["synth_locked_provider"] = lambda: None
    _TRANSPARENCY.update(ns)
    return _TRANSPARENCY


def _load_function_raw(name, namespace):
    function = next(node for node in source_tree().body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(module, str(APP), "exec"), namespace)
    return namespace[name]


_PROVIDER_HELPER_DEFAULTS = {}


_SWEEP_HELPER_DEFAULTS = {}


def sweep_helpers():
    if _SWEEP_HELPER_DEFAULTS:
        return _SWEEP_HELPER_DEFAULTS
    ns = literal_assignments("CHATGPT_NO_NONE", "CLAUDE_THINKING_ON", "KIMI_MAX_TOKENS",
                             "MISTRAL_VISION_MODELS", "MINIMAX_SPEED", "QWEN_EFFORT",
                             "QWEN_GRADED_EFFORT_MODELS", "QWEN_WEB_MODELS",
                             "DEEPSEEK_VISION_MODELS",
                             "OUTPUT_CAPS", "GEMINI_MINIMAL_MODELS", "GLM_VISION_MODELS",
                             "NVIDIA_VISION_MODELS", "CLAUDE_HAIKU_THINKING_BUDGET",
                             "KIMI_SEARCH_ROUNDS", "KIMI_WEB_BUDGET_S", "KIMI_ALWAYS_THINK", "GLM_ALWAYS_THINK",
                             "CLAUDE_ALWAYS_THINK", "KIMI_SEARCH_TOOL", "KIMI_SEARCH_URL",
                             "CLAUDE_BETWEEN_TOOLS_OFF", "KIMI_SEARCH_CLOSED",
                             "MAX_EFFORT_REQUEST_TIMEOUT",
                             "CLAUDE_CACHE_WRITE", "CLAUDE_CACHE_READ", "CACHED_INPUT_PRICES",
                             "JEV_FRESH_THRESHOLD", "JEV_ROUTE_WAIT_S",
                             "CLAUDE_CACHE_READ_BY_MODEL", "GEMINI_NO_SEARCH_MODELS",
                             "GEMINI_SEARCH_STANDIN",
                             "CHATGPT_SPEED", "CHATGPT_ULTRAFAST_MODELS", "FLEX_PRICES",
                             "ULTRAFAST_PRICES", "LONG_PROMPT_PRICES", "MISTRAL_ID_ALTERNATES",
                             "MISTRAL_REASONING_MODELS",
                             "CLAUDE_PAUSE_CONTINUES")
    ns["time"] = time
    ns["PACK_SOURCES"] = {}
    ns["shared_search_ready"] = lambda keys=None: False
    ns["_CACHE_TL"] = threading.local()
    ns["_SPEED_TL"] = threading.local()
    ns["re"] = re
    ns["datetime"] = datetime
    ns["_stream_slot"] = threading.local()
    for fn in ("chatgpt_effort_sent", "chatgpt_version_of", "qwen_web_supported",
               "output_cap", "gemini_level_sent", "_kimi_extract_pdfs",
               "_today_line", "_with_today", "_degenerate_repetition",
               "_max_effort_call", "_request_retries",
               "_stream_emit", "_claude_create", "_responses_create",
               "_note_cache", "_take_cache", "_note_speed", "_take_speed",
               "chatgpt_speed_used", "_is_flex_busy_error", "_billed_error"):
        _load_function_raw(fn, ns)
    _SWEEP_HELPER_DEFAULTS.update(ns)
    return _SWEEP_HELPER_DEFAULTS


def load_function(name, namespace):
    if not _PROVIDER_HELPER_DEFAULTS:
        _PROVIDER_HELPER_DEFAULTS.update(literal_assignments("PROVIDERS", "_NAME_TO_EN_KEY"))
    for key, value in _PROVIDER_HELPER_DEFAULTS.items():
        namespace.setdefault(key, value)
    for key, value in sweep_helpers().items():
        namespace.setdefault(key, value)
    for key, value in transparency_helpers().items():
        namespace.setdefault(key, value)
    if name == "run_one" and "cost_with_cache" not in namespace:
        _load_function_raw("cost_with_cache", namespace)
    return _load_function_raw(name, namespace)


def load_history_helpers(history_file):
    wanted = {
        "_exclusive_file_lock",
        "_atomic_write_json",
        "_history_alert",
        "_load_history_unlocked",
        "_load_history_cached",
        "_history_signature",
        "load_history",
        "load_history_archive",
        "_history_key",
        "update_history",
        "update_history_archive",
    }
    nodes = [node for node in source_tree().body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in wanted]
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    namespace = {
        "HISTORY_FILE": str(history_file),
        "HISTORY_ARCHIVE_FILE": str(history_file.with_name("history_archive.json")),
        "HISTORY_RECENT_LIMIT": 50,
        "contextmanager": contextmanager,
        "datetime": datetime,
        "json": json,
        "msvcrt": msvcrt,
        "os": os,
        "shutil": shutil,
        "threading": threading,
        "time": time,
        "uuid": uuid,
        "st": SimpleNamespace(session_state={}),
        "deepcopy": deepcopy,
        "_history_alerts": [],
        "_history_write_blocked": {},
    }
    exec(compile(module, str(APP), "exec"), namespace)
    return namespace


class StaticContractTests(unittest.TestCase):
    def test_source_compiles(self):
        compile(APP.read_text(encoding="utf-8"), str(APP), "exec")

    def test_streamlit_base_theme_matches_dark_default(self):
        config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
        base = re.search(r'(?m)^\s*base\s*=\s*"([^"]+)"\s*$', config)
        self.assertIsNotNone(base)
        self.assertEqual(base.group(1).lower(), "dark")
        self.assertEqual(literal_assignments("DEFAULTS")["DEFAULTS"]["k_theme_mode"],
                         "Dark")
        source = APP.read_text(encoding="utf-8")
        theme_css = literal_assignments(
            "STREAMLIT_THEME_REANCHOR_CSS")["STREAMLIT_THEME_REANCHOR_CSS"]
        # The System branch scopes the light tokens + the native re-anchor
        # under html.conclave-day (CSS nesting), pins the dark tokens under
        # html.conclave-night, and ships the solar script.
        self.assertIn('html.conclave-day {{ {_day_tokens}', source)
        self.assertIn('{STREAMLIT_THEME_REANCHOR_CSS}}}', source)
        self.assertIn('html.conclave-night {{ {_night_tokens}}}', source)
        self.assertIn("st.html(SOLAR_THEME_JS, unsafe_allow_javascript=True)", source)
        solar = literal_assignments("SOLAR_THEME_JS")["SOLAR_THEME_JS"]
        for needle in ("navigator.geolocation", "conclave-day", "conclave-night",
                       "conclave_geo", "setInterval(apply, 60000)"):
            self.assertIn(needle, solar)
        self.assertTrue(theme_css)


    def test_github_foundation_is_portable_private_and_zero_cost(self):
        readme = README.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        dependabot = DEPENDABOT.read_text(encoding="utf-8")
        launcher = LAUNCHER.read_text(encoding="utf-8")
        source = APP.read_text(encoding="utf-8")

        self.assertIn("Apache License 2.0", readme)
        self.assertIn("contents: read", workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn("python -m py_compile conclave.py", workflow)
        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertNotIn("api_keys", workflow.lower())
        self.assertIn("package-ecosystem: pip", dependabot)
        self.assertIn("package-ecosystem: github-actions", dependabot)
        self.assertIn('%~dp0', launcher)
        self.assertIn('.venv\\Scripts\\python.exe', launcher)
        self.assertIn("APP_DIR      = os.path.dirname(os.path.abspath(__file__))", source)

    def test_visual_system_uses_calm_teal_and_soft_reading_text(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("--conclave-action: #246b73", source)
        self.assertIn("--conclave-accent: #55b8b3", source)
        self.assertIn("--conclave-text: #d8dee5", source)
        self.assertIn("background: var(--conclave-action);", source)
        self.assertIn("max-width: 2400px;", source)
        self.assertIn("padding-left: clamp(0.85rem, 1.5vw, 2rem);", source)
        self.assertIn("max-width: min(210ch, 100%);", source)
        self.assertIn("padding-right: clamp(2rem, 4vw, 3.25rem);", source)
        self.assertIn(".st-key-sidebar_brand_home button p {", source)
        self.assertIn("font-size: 1.5rem !important;", source)
        self.assertNotIn("--conclave-gold:", source)

    def test_source_weighing_split_is_exact_and_lossless(self):
        namespace = {"re": __import__("re")}
        load_function("_split_after_heading", namespace)
        split = load_function("split_source_weighing", namespace)
        cases = [
            ("Answer body.\n\n---\n\n**How I weighed the sources**\nUsed A and B.",
             "Answer body.\n\n---"),
            ("Answer body.\n\n## Part 2: How I weighed the sources\nUsed A and B.",
             "Answer body."),
            ("Answer body.\n\nPART II - HOW I WEIGHED THE SOURCES\nUsed A and B.",
             "Answer body."),
        ]
        for content, expected_answer in cases:
            answer, weighing = split(content)
            self.assertEqual(answer, expected_answer)
            self.assertEqual(weighing, "Used A and B.")

        ordinary = "This answer explains how I weighed the sources in ordinary prose."
        self.assertEqual(split(ordinary), (ordinary, None))

    def test_evaluator_messages_use_collapsed_source_weighing_renderer(self):
        source = APP.read_text(encoding="utf-8")
        self.assertEqual(source.count('render_synthesized_content(m["content"])'), 2)
        self.assertIn('st.expander("How I weighed the sources", expanded=False)',
                      source)

    def test_synthesis_prompt_requires_a_question_specific_title(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("specific Markdown heading tailored to this question", source)
        self.assertNotIn("PART 1 — THE ENHANCED ANSWER", source)

    def test_claude_search_recommendations_scale_with_model_and_effort(self):
        caps = literal_assignments("CLAUDE_SEARCH_CAPS")["CLAUDE_SEARCH_CAPS"]
        recommend = load_function("recommended_claude_search_cap", {
            "CLAUDE_SEARCH_CAPS": caps,
        })
        self.assertEqual(recommend("Haiku 5.5", "Low"), 2)
        self.assertEqual(recommend("Sonnet 5.5", "High"), 5)
        self.assertEqual(recommend("Opus 5.5", "High"), 7)
        self.assertEqual(recommend("Fable 5.1", "Max"), 20)

    def test_followup_settings_inherit_the_initial_synthesizer(self):
        state = {
            "k_syn_provider": "Claude", "k_syn_cla_model": "Fable 5.1",
            "k_syn_cla_eff": "Medium", "k_syn_cla_think": True,
            "k_syn_gpt_model": "Luna (economy)", "k_syn_gpt_eff": "Low",
            "k_syn_gpt_speed": "Fast", "k_syn_gem_t": "Balanced",
            "k_syn_ds_model": "V4 Pro (flagship)", "k_syn_ds_think": True,
            "k_syn_ds_effort": "Max",
            "k_syn_grok_mode": "Fast", "k_syn_web": True,
            "k_syn_maxsearch": 9,
        }
        inherit = load_function("inherit_followup_from_initial", {
            "st": SimpleNamespace(session_state=state),
        })
        inherit()
        for suffix in ("provider", "cla_model", "cla_eff", "cla_think", "gpt_model",
                       "gpt_eff", "gpt_speed", "gem_t", "ds_model", "ds_think",
                       "ds_effort", "grok_mode", "web", "maxsearch"):
            self.assertEqual(state[f"k_fu_{suffix}"], state[f"k_syn_{suffix}"])

    def test_light_defaults_and_preset_ladder_are_coherent(self):
        values = literal_assignments("PRESETS", "SYNTH_PRESETS", "DEFAULTS")
        presets = values["PRESETS"]
        synth_presets = values["SYNTH_PRESETS"]
        defaults = values["DEFAULTS"]
        self.assertTrue(all(defaults[key] == value
                            for key, value in presets["Light"].items()))
        self.assertTrue(all(defaults[key] == value
                            for key, value in synth_presets["Light"]["Claude"].items()))

        expected = {
            "k_gpt_model": ["Luna (economy)", "Sol (advanced)", "Sol (advanced)",
                            "Sol (advanced)", "Astra (flagship)"],
            "k_gpt": ["Instant", "Low", "Medium", "Extra (xhigh)", "Max"],
            "k_gpt_speed": ["Standard", "Standard", "Standard", "Standard", "Standard"],
            "k_cla_model": ["Haiku 5.5", "Sonnet 5.5", "Sonnet 5.5", "Opus 5.5", "Fable 5.1"],
            "k_cla": ["Low", "Medium", "High", "Extra (xhigh)", "Max"],
            "k_gem": ["Flash-Lite (3.5)", "Flash-Lite (3.5)", "Flash (3.8)", "Argon (4)", "Argon (4)"],
            "k_gem_t": ["Minimal", "Balanced", "Balanced", "Balanced", "Extended"],
            "k_ds_model": ["V4.1 Flash (fast · vision)"] * 5,
            "k_ds_think": [False, True, True, True, True],
            "k_ds_effort": ["High", "High", "Max", "Max", "Max"],
            "k_grok": ["Fast", "Fast", "Balanced", "High", "Expert"],
            "k_cla_maxsearch": [2, 3, 5, 10, 20],
        }
        ordered = [presets[name] for name in ("Fast", "Light", "Balanced", "Thorough", "Deep")]
        for key, sequence in expected.items():
            self.assertEqual([preset[key] for preset in ordered], sequence, key)

        self.assertTrue(all(set(synth_presets[name]) ==
                            {"Claude", "ChatGPT", "Gemini", "DeepSeek", "Grok", "Qwen",
                             "Mistral", "Kimi", "Meta", "MiniMax", "NVIDIA", "GLM", "MiMo"}
                            for name in synth_presets))
        synth_ordered = [synth_presets[name]["Claude"]
                         for name in ("Fast", "Light", "Balanced", "Thorough", "Deep")]
        self.assertEqual([preset["k_syn_cla_model"] for preset in synth_ordered],
                         ["Sonnet 5.5", "Sonnet 5.5", "Sonnet 5.5", "Opus 5.5", "Fable 5.1"])
        self.assertEqual([preset["k_syn_cla_eff"] for preset in synth_ordered],
                         ["Low", "Medium", "High", "Extra (xhigh)", "Max"])
        self.assertEqual([preset["k_syn_maxsearch"] for preset in synth_ordered],
                         [2, 3, 5, 10, 20])
        self.assertEqual(
            [synth_presets[name]["ChatGPT"]["k_syn_gpt_model"]
             for name in ("Fast", "Light", "Balanced", "Thorough", "Deep")],
            ["Sol (advanced)", "Sol (advanced)", "Sol (advanced)",
             "Sol (advanced)", "Astra (flagship)"],
        )
        self.assertEqual(
            [synth_presets[name]["ChatGPT"]["k_syn_gpt_eff"]
             for name in ("Fast", "Light", "Balanced", "Thorough", "Deep")],
            ["Low", "Low", "Medium", "Extra (xhigh)", "Max"],
        )
        self.assertEqual(
            [synth_presets[name]["Gemini"]["k_syn_gem_t"]
             for name in ("Fast", "Light", "Balanced", "Thorough", "Deep")],
            ["Standard", "Standard", "Balanced", "Balanced", "Extended"],
        )
        self.assertEqual(
            [synth_presets[name]["DeepSeek"]["k_syn_ds_think"]
             for name in ("Fast", "Light", "Balanced", "Thorough", "Deep")],
            [True, True, True, True, True],
        )
        self.assertEqual(
            [synth_presets[name]["Qwen"]["k_syn_qwen_think"]
             for name in ("Fast", "Light", "Balanced", "Thorough", "Deep")],
            [True, True, True, True, True],
        )
        self.assertEqual(
            [synth_presets[name]["DeepSeek"]["k_syn_ds_model"]
             for name in ("Fast", "Light", "Balanced", "Thorough", "Deep")],
            ["V4.1 Flash (fast · vision)"] * 5,
        )
        self.assertEqual(
            [synth_presets[name]["Grok"]["k_syn_grok_mode"]
             for name in ("Fast", "Light", "Balanced", "Thorough", "Deep")],
            ["Fast", "Fast", "Balanced", "High", "Expert"],
        )
        self.assertEqual(
            [synth_presets[name]["Gemini"]["k_syn_gem_model"]
             for name in ("Fast", "Light", "Balanced", "Thorough", "Deep")],
            ["Flash (3.8)", "Flash (3.8)", "Flash (3.8)",
             "Argon (4)", "Argon (4)"],
        )
        self.assertEqual(defaults["k_syn_gem_model"],
                         synth_presets["Light"]["Gemini"]["k_syn_gem_model"])

        self.assertEqual(
            [preset["k_qwen_model"] for preset in ordered],
            ["Flash 3.7 (cheapest)", "Flash 3.8 (economy)", "Plus 3.7 (balanced)",
             "Plus 3.7 (balanced)", "Max 3.8 (flagship)"],
        )
        self.assertEqual([preset["k_qwen_effort"] for preset in ordered], ["Default"] * 5)
        self.assertTrue(all(synth_presets[n]["Qwen"]["k_syn_qwen_effort"] == "Default"
                            for n in synth_presets))
        self.assertEqual([preset["k_mm_speed"] for preset in ordered], ["Standard"] * 5)
        self.assertEqual([preset["k_meta_eff"] for preset in ordered],
                         ["Minimal", "Low", "Medium", "High", "Max"])
        self.assertEqual([preset["k_meta_model"] for preset in ordered],
                         ["Spark 1.3 (balanced)"] * 3 + ["Wonder 1.0 (flagship · preview)"] * 2)
        self.assertEqual(synth_presets["Thorough"]["Meta"]["k_syn_meta_model"],
                         "Wonder 1.0 (flagship · preview)")
        self.assertEqual(synth_presets["Deep"]["Meta"]["k_syn_meta_eff"], "Max")
        self.assertEqual([preset["k_qwen_think"] for preset in ordered],
                         [False, True, True, True, True])
        self.assertEqual([preset["k_qwen_budget"] for preset in ordered],
                         [4000, 4000, 8000, 16000, 32000])
        self.assertEqual(
            [synth_presets[name]["Qwen"]["k_syn_qwen_model"]
             for name in ("Fast", "Light", "Balanced", "Thorough", "Deep")],
            ["Plus 3.7 (balanced)", "Plus 3.7 (balanced)", "Plus 3.7 (balanced)",
             "Plus 3.7 (balanced)", "Max 3.8 (flagship)"],
        )
        self.assertEqual(defaults["k_syn_qwen_model"],
                         synth_presets["Light"]["Qwen"]["k_syn_qwen_model"])

    def test_adjudicator_default_is_off_at_fast_light_on_elsewhere(self):
        default_for = load_function("adjudicator_default_for_preset",
                                    {"ADJUDICATION_ON_FOR_TESTING": False})
        for name in ("Thorough", "Deep"):
            self.assertTrue(default_for(name), f"{name} must default adjudication on")
        default_for = load_function("adjudicator_default_for_preset",
                                    {"ADJUDICATION_ON_FOR_TESTING": False})
        for name in ("Fast", "Light", "Balanced"):
            self.assertFalse(default_for(name), f"{name} must default adjudication off")
        if literal_assignments("ADJUDICATION_ON_FOR_TESTING")["ADJUDICATION_ON_FOR_TESTING"]:
            live_default = load_function("adjudicator_default_for_preset",
                                         {"ADJUDICATION_ON_FOR_TESTING": True})
            for name in ("Fast", "Light", "Balanced", "Thorough", "Deep"):
                self.assertTrue(live_default(name), name)
            self.assertTrue(literal_assignments("DEFAULTS")["DEFAULTS"].get("k_adjudicator_on")
                            is False)   # the literal keeps the real policy; the switch flips it live

        # Both real call sites must actually gate on the live toggle, not
        # just the helper existing in isolation.
        source = APP.read_text(encoding="utf-8")
        send_handler = source[source.index("if submitted and not bg_active():"):
                              source.index("bg_flash(\"synthesis\")")]
        self.assertIn("adjudicator_on = adjudicator_stack_active()", send_handler)
        self.assertIn("if adjudicator_on:", send_handler)
        self.assertIn('excluded = {syn_provider} | ({adj_provider} if adjudicator_on else set())',
                      send_handler)

        followup_node = next(node for node in source_tree().body
                             if isinstance(node, ast.FunctionDef)
                             and node.name == "start_council_followup")
        followup_source = ast.get_source_segment(source, followup_node)
        self.assertIn(
            "adjudicator_on = full_council and (not research_mode) and adjudicator_stack_active()",
            followup_source)
        self.assertIn("if adjudicator_on:", followup_source)

    def test_adjudicator_toggle_is_user_controllable_at_every_preset(self):
        is_active = load_function("adjudicator_stack_active", {
            "st": SimpleNamespace(session_state={"k_adjudicator_on": False}),
        })
        self.assertFalse(is_active())

        is_active_on = load_function("adjudicator_stack_active", {
            "st": SimpleNamespace(session_state={"k_adjudicator_on": True}),
        })
        self.assertTrue(is_active_on())

        source = APP.read_text(encoding="utf-8")
        apply_preset_node = next(node for node in source_tree().body
                                 if isinstance(node, ast.FunctionDef)
                                 and node.name == "_apply_preset")
        apply_preset_source = ast.get_source_segment(source, apply_preset_node)
        self.assertIn(
            'st.session_state["k_adjudicator_on"] = adjudicator_default_for_preset(preset_name)',
            apply_preset_source)

        # The Adjudicator panel renders a real, always-enabled toggle -- not
        # gated behind any preset check.
        panel_node = next(node for node in source_tree().body
                          if isinstance(node, ast.FunctionDef)
                          and node.name == "render_adjudicator_panel")
        panel_source = ast.get_source_segment(APP.read_text(encoding="utf-8"), panel_node)
        self.assertIn('c_toggle("⚖️ Adjudication active this run", "k_adjudicator_on"',
                      panel_source)

    def test_adj_presets_mirror_synth_presets_with_renamed_keys(self):
        values = literal_assignments("SYNTH_PRESETS", "ADJ_PRESETS")
        synth_presets, adj_presets = values["SYNTH_PRESETS"], values["ADJ_PRESETS"]
        self.assertEqual(set(synth_presets), set(adj_presets))
        for tier, providers in synth_presets.items():
            self.assertEqual(set(providers), set(adj_presets[tier]))
            for provider, dials in providers.items():
                renamed = {key.replace("k_syn_", "k_adj_", 1): value
                          for key, value in dials.items()}
                self.assertEqual(adj_presets[tier][provider], renamed,
                                 f"{tier}/{provider}")

    def test_adjudicator_defaults_match_its_active_provider_at_light_tier(self):
        values = literal_assignments("DEFAULTS", "ADJ_PRESETS")
        defaults, adj_presets = values["DEFAULTS"], values["ADJ_PRESETS"]
        self.assertEqual(defaults["k_adj_provider"], "ChatGPT")
        self.assertTrue(all(defaults[key] == value
                            for key, value in adj_presets["Light"]["ChatGPT"].items()))

    def test_gemini_evaluator_gets_full_model_choice_except_research(self):
        gemini_models = literal_assignments("GEMINI_MODELS")["GEMINI_MODELS"]
        gemini_think = literal_assignments("GEMINI_THINK")["GEMINI_THINK"]
        deps = {
            "eval_provider_of": lambda _prefix: "Gemini",
            "GEMINI_MODELS": gemini_models, "GEMINI_THINK": gemini_think,
            "SYNTH_GEMINI": "gemini-3.1-pro-preview",
            "NO_WEB_PROVIDERS": ("Qwen", "Mistral", "Kimi"), "deepseek_web_supported": lambda _mid: False,
            "load_keys": lambda: {"google": "fixture-key"},
            "KEY_MAP": literal_assignments("KEY_MAP")["KEY_MAP"],
        }
        for prefix in ("k_syn", "k_fu", "k_adj"):
            state = {f"{prefix}_gem_model": "Flash-Lite (3.5)", f"{prefix}_gem_t": "Standard"}
            job_fn = load_function("evaluator_job",
                                   {**deps, "st": SimpleNamespace(session_state=state)})
            _, mid, cfg, _ = job_fn(prefix, [], False)
            self.assertEqual(mid, "gemini-3.5-flash-lite", prefix)
            self.assertEqual(cfg["model_id"], "gemini-3.5-flash-lite", prefix)

        # Research: no k_res_gem_model dial exists at all -- must stay on the
        # flagship regardless.
        state = {"k_res_gem_t": "Standard"}
        job_fn = load_function("evaluator_job",
                               {**deps, "st": SimpleNamespace(session_state=state)})
        _, mid, cfg, _ = job_fn("k_res", [], False)
        self.assertEqual(mid, "gemini-3.1-pro-preview")
        self.assertEqual(cfg["model_id"], "gemini-3.1-pro-preview")

    def test_settings_str_reports_deepseek_web_reasoning_honestly(self):
        settings_str = load_function("settings_str", {})
        off_no_web = settings_str("DeepSeek", {"thinking": False, "effort": "high"}, False)
        self.assertEqual(off_no_web, "thinking off")
        self.assertEqual(settings_str("DeepSeek", {"thinking": False, "effort": "high"}, True),
                         "thinking off")
        self.assertEqual(settings_str("DeepSeek", {"thinking": True, "effort": "max"}, False),
                         "thinking on · effort max")

    def test_openai_sdk_lazy_resources_are_warmed_up_before_any_threading(self):
        source = APP.read_text(encoding="utf-8")
        warmup_idx = source.index('import openai.resources.chat')
        self.assertNotEqual(warmup_idx, -1)
        for mod in ("import openai.resources.responses", "import openai.resources.audio"):
            self.assertIn(mod, source)
            self.assertGreater(source.index(mod), warmup_idx)
        # Must precede the first place background threads actually get
        # created, not just the (harmless) ThreadPoolExecutor import line.
        first_worker_def = source.index("def _bg_worker")
        self.assertLess(warmup_idx, first_worker_def)

    def test_every_provider_client_has_a_scaled_request_timeout(self):
        source = APP.read_text(encoding="utf-8")
        values = literal_assignments("LIGHT_REQUEST_TIMEOUT", "HEAVY_REQUEST_TIMEOUT")
        light, heavy = values["LIGHT_REQUEST_TIMEOUT"], values["HEAVY_REQUEST_TIMEOUT"]
        self.assertGreater(light, 140)
        self.assertGreater(heavy, light)
        self.assertLess(heavy, 600)

        # Every OpenAI(...)/Anthropic(...) construction must call the heuristic.
        openai_or_anthropic_sites = re.findall(
            r'(?:OpenAI|Anthropic)\([^)]*\)', source)
        self.assertGreaterEqual(len(openai_or_anthropic_sites), 7,
                                "expected at least 7 client constructions (transcribe, "
                                "speak, ChatGPT, Claude, DeepSeek, Grok, Qwen)")
        scaled_sites = [s for s in openai_or_anthropic_sites if "_request_timeout(" in s]
        self.assertGreaterEqual(len(scaled_sites), 5,
                                "ChatGPT/Claude/DeepSeek/Grok/Qwen should all scale")
        fixed_sites = [s for s in openai_or_anthropic_sites if "LIGHT_REQUEST_TIMEOUT" in s]
        self.assertGreaterEqual(len(fixed_sites), 2,
                                "transcribe/speak are always light, fixed audio operations")

        self.assertIn("http_options=types.HttpOptions(", source)
        self.assertIn("timeout=int(_request_timeout(name, cfg, web) * 1000)", source)

    def test_request_timeout_scales_by_how_heavy_each_call_is_configured(self):
        # Functional check of the heuristic itself: top-tier effort or a
        # generously-capped web search counts as heavy for each provider;
        # everything else gets the tighter default. Derived from cfg/web
        # already passed into call_model -- no preset name needed, so this
        # does the right thing for side-chats and Custom mode too.
        values = literal_assignments("LIGHT_REQUEST_TIMEOUT", "HEAVY_REQUEST_TIMEOUT")
        timeout = load_function("_request_timeout", values)
        light, heavy = values["LIGHT_REQUEST_TIMEOUT"], values["HEAVY_REQUEST_TIMEOUT"]

        self.assertEqual(timeout("Claude", {"effort": "low"}, False), light)
        self.assertEqual(timeout("Claude", {"effort": "max"}, False), 900.0)
        self.assertEqual(timeout("Claude", {"effort": "high"}, False), heavy)
        self.assertEqual(timeout("Claude", {"effort": "low", "max_searches": 2}, True), light)
        self.assertEqual(timeout("Claude", {"effort": "low", "thinking": True}, True), heavy)
        self.assertEqual(timeout("Claude", {"effort": "low", "max_searches": 5}, True), heavy)

        self.assertEqual(timeout("ChatGPT", {"effort": "medium"}, False), light)
        self.assertEqual(timeout("ChatGPT", {"effort": "xhigh"}, False), heavy)
        self.assertEqual(timeout("ChatGPT", {"effort": "low"}, True), heavy)  # any web search

        self.assertEqual(timeout("Gemini", {"level": "medium"}, False), light)
        self.assertEqual(timeout("Gemini", {"level": "high"}, False), heavy)

        self.assertEqual(timeout("DeepSeek", {"effort": "high"}, False), heavy)
        self.assertEqual(timeout("DeepSeek", {"effort": "high", "thinking": False}, False), light)
        self.assertEqual(timeout("DeepSeek", {"effort": "low"}, False), light)
        self.assertEqual(timeout("DeepSeek", {"effort": "max"}, False), 900.0)
        self.assertEqual(timeout("DeepSeek", {"effort": "high"}, True), heavy)  # web

        self.assertEqual(timeout("Grok", {"mode": "Balanced"}, False), light)
        self.assertEqual(timeout("Grok", {"mode": "High"}, False), heavy)
        self.assertEqual(timeout("Grok", {"mode": "Expert"}, False), heavy)

        self.assertEqual(timeout("Qwen", {"thinking": True, "budget": 4000}, False), light)
        self.assertEqual(timeout("Qwen", {"thinking": True, "budget": 32000}, False), heavy)
        self.assertEqual(timeout("Qwen", {"thinking": False, "budget": 32000}, False), light)

    def test_a_hung_provider_call_fails_fast_not_silently_or_as_credit_error(self):
        from openai import RateLimitError as OpenAIRateLimitError
        from anthropic import APIStatusError as AnthropicAPIStatusError
        from google.genai import errors as genai_errors
        credit_consts = literal_assignments("_CREDIT_KEYWORDS")
        is_credit_error = load_function("_is_credit_error", {
            "OpenAIRateLimitError": OpenAIRateLimitError,
            "AnthropicAPIStatusError": AnthropicAPIStatusError,
            "genai_errors": genai_errors,
            **credit_consts,
        })
        run_one = load_function("run_one", {
            "time": time,
            "call_model": lambda *a, **kw: (_ for _ in ()).throw(
                TimeoutError("Request timed out.")),
            "_is_credit_error": is_credit_error,
            "cost_of": lambda *a, **kw: None,
            "model_id_of": lambda *a, **kw: "fixture-model",
        })
        result = run_one("DeepSeek", "fixture-key", [], {}, False)
        self.assertTrue(result["err"])
        self.assertFalse(result["credit_err"])
        self.assertIn("Request timed out", result["text"])
        self.assertEqual(result["provider"], "DeepSeek")

    def test_deepseek_is_first_class_and_uses_its_native_request_shape(self):
        values = literal_assignments(
            "DEEPSEEK_MODELS", "DEEPSEEK_EFFORT", "EVAL_PROVIDERS", "DEFAULTS")
        self.assertEqual(values["DEEPSEEK_MODELS"], {
            "V4.1 Flash (fast · vision)": "deepseek-flash",
        })
        self.assertEqual(literal_dict_string_entries("PRICES")["deepseek-flash"], (0.30, 1.20))
        self.assertEqual(literal_dict_string_entries("PRICES")["deepseek-v4-flash-vision-exp"],
                         (0.44, 1.32))
        self.assertEqual(literal_assignments("DEEPSEEK_VISION_MODELS")["DEEPSEEK_VISION_MODELS"],
                         {"deepseek-flash", "deepseek-v4-flash-vision-exp"})
        self.assertEqual(values["DEEPSEEK_EFFORT"],
                         {"Low": "low", "High": "high", "Max": "max"})
        self.assertIn("DeepSeek", values["EVAL_PROVIDERS"])
        self.assertTrue(values["DEFAULTS"]["k_en_ds"])
        self.assertTrue(values["DEFAULTS"]["k_en_grok"])
        self.assertFalse(values["DEFAULTS"]["k_web_ds"])

        prices = literal_dict_string_entries("PRICES")
        self.assertEqual(prices["deepseek-v4-flash"], (0.44, 1.32))
        self.assertEqual(prices["deepseek-v4-pro"], (1.32, 3.96))

        captured = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured["request"] = kwargs
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content="DeepSeek answer"),
                        finish_reason="stop")],
                    usage=SimpleNamespace(prompt_tokens=13, completion_tokens=8),
                )

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured["client"] = kwargs
                self.chat = SimpleNamespace(completions=FakeCompletions())

        def fake_responses_call(client, model, input_payload, effort, **kwargs):
            captured["responses"] = {
                "client": client, "model": model, "input": input_payload,
                "effort": effort, **kwargs,
            }
            return "DeepSeek searched answer", 21, 13

        web_supported = load_function("deepseek_web_supported", {})
        self.assertFalse(web_supported("deepseek-flash"))
        self.assertFalse(web_supported("deepseek-v4-pro"))
        self.assertIn("DeepSeek", literal_assignments("NO_WEB_PROVIDERS")["NO_WEB_PROVIDERS"])
        call = load_function("call_model", {
            "OpenAI": FakeOpenAI,
            "_oai_chat_messages": lambda messages, pdf_ok, image_ok=True: messages,
            "_oai_responses_input": lambda messages, pdf_ok, image_ok=True: messages,
            "_responses_call": fake_responses_call,
            "NO_WEB_PROVIDERS": ("Qwen", "Mistral", "Kimi"), "deepseek_web_supported": web_supported,
            "_request_timeout": lambda *a: 180.0,
        })
        result = call(
            "DeepSeek", "fixture-key", [{"role": "user", "content": "Question"}],
            {"model_id": "deepseek-v4-pro", "thinking": True, "effort": "max"},
            False,
        )
        self.assertEqual(result, ("DeepSeek answer", 13, 8))
        self.assertEqual(captured["client"]["base_url"], "https://api.deepseek.com")
        self.assertEqual(captured["request"]["reasoning_effort"], "max")
        self.assertEqual(captured["request"]["extra_body"], {
            "thinking": {"type": "enabled"},
        })
        self.assertEqual(captured["request"]["max_tokens"], 64000)

        captured.pop("responses", None)
        plain = call(
            "DeepSeek", "fixture-key", [{"role": "user", "content": "Question"}],
            {"model_id": "deepseek-flash", "thinking": False, "effort": "high"},
            True,
        )
        self.assertEqual(plain, ("DeepSeek answer", 13, 8))
        self.assertNotIn("responses", captured)
        self.assertEqual(captured["request"]["model"], "deepseek-flash")
        self.assertEqual(captured["request"]["max_tokens"], 16000)

        responses_request = {}

        class FakeResponses:
            def create(self, **kwargs):
                responses_request.update(kwargs)
                return SimpleNamespace(
                    output_text="Searched answer", status="completed",
                    usage=SimpleNamespace(input_tokens=21, output_tokens=13),
                )

        responses_call = load_function("_responses_call", {})
        result = responses_call(
            SimpleNamespace(responses=FakeResponses()), "deepseek-v4-flash",
            [{"role": "user", "content": "Question"}], "none",
            require_reasoning=True, require_search=True,
        )
        self.assertEqual(result, ("Searched answer", 21, 13))
        self.assertEqual(responses_request["tools"], [{"type": "web_search"}])
        self.assertEqual(responses_request["tool_choice"], "required")
        self.assertEqual(responses_request["reasoning"], {"effort": "none"})

    def test_qwen_is_a_real_provider_with_its_own_native_request_shape(self):
        values = literal_assignments("QWEN_MODELS", "QWEN_BUDGET", "EVAL_PROVIDERS",
                                     "MODEL_ORDER", "DEFAULTS")
        self.assertEqual(values["QWEN_MODELS"], {
            "Max 3.8 (flagship)": "qwen3.8-max",
            "Plus 3.7 (balanced)": "qwen3.7-plus",
            "Flash 3.8 (economy)": "qwen3.8-flash",
            "Flash 3.7 (cheapest)": "qwen3.7-flash",
        })
        self.assertIn("Qwen", values["EVAL_PROVIDERS"])
        self.assertIn("Qwen", values["MODEL_ORDER"])
        self.assertFalse(values["DEFAULTS"]["k_en_qwen"])
        self.assertFalse(values["DEFAULTS"]["k_web_qwen"])

        prices = literal_dict_string_entries("PRICES")
        self.assertEqual(prices["qwen3.8-max"], (2.00, 6.00))
        self.assertEqual(prices["qwen3.7-max"], (1.65, 4.951))
        self.assertEqual(prices["qwen3.7-plus"], (0.40, 1.60))
        self.assertEqual(prices["qwen3.6-flash"], (0.165, 0.99))   # retired ID, saved chats
        self.assertEqual(prices["qwen3.7-flash"], (0.030, 0.13))

        captured = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured["request"] = kwargs
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content="Qwen answer"),
                        finish_reason="stop")],
                    usage=SimpleNamespace(prompt_tokens=11, completion_tokens=6),
                )

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured["client"] = kwargs
                self.chat = SimpleNamespace(completions=FakeCompletions())

        call = load_function("call_model", {
            "OpenAI": FakeOpenAI,
            "_oai_chat_messages": lambda messages, pdf_ok, image_ok=True: messages,
            "_request_timeout": lambda *a: 180.0,
        })
        result = call(
            "Qwen", "fixture-key", [{"role": "user", "content": "Question"}],
            {"model_id": "qwen3.7-plus", "thinking": True, "budget": 8000}, False,
        )
        self.assertEqual(result, ("Qwen answer", 11, 6))
        self.assertEqual(captured["client"]["base_url"],
                         "https://dashscope-us.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(captured["request"]["model"], "qwen3.7-plus")
        self.assertEqual(captured["request"]["extra_body"],
                         {"enable_thinking": True, "thinking_budget": 8000})

        call(
            "Qwen", "fixture-key", [{"role": "user", "content": "Question"}],
            {"model_id": "qwen3.7-flash", "thinking": False, "budget": 8000}, False,
        )
        self.assertEqual(captured["request"]["extra_body"], {"enable_thinking": False})
        self.assertNotIn("effort", captured["request"])
        self.assertNotIn("reasoning_effort", captured["request"])
        call("Qwen", "fixture-key", [{"role": "user", "content": "Q"}],
             {"model_id": "qwen3.8-max", "thinking": True, "budget": 8000, "effort": "xhigh"}, False)
        self.assertEqual(captured["request"]["reasoning_effort"], "xhigh")
        self.assertEqual(captured["request"]["extra_body"], {"enable_thinking": True})
        call("Qwen", "fixture-key", [{"role": "user", "content": "Q"}],
             {"model_id": "qwen3.8-max", "thinking": True, "budget": 8000, "effort": None}, False)
        self.assertNotIn("reasoning_effort", captured["request"])
        self.assertEqual(captured["request"]["extra_body"]["thinking_budget"], 8000)
        # a 3.7 model ignores the graded effort entirely
        call("Qwen", "fixture-key", [{"role": "user", "content": "Q"}],
             {"model_id": "qwen3.7-plus", "thinking": True, "budget": 6000, "effort": "low"}, False)
        self.assertNotIn("reasoning_effort", captured["request"])
        self.assertEqual(captured["request"]["extra_body"]["thinking_budget"], 6000)

        call("Qwen", "fixture-key", [{"role": "user", "content": "Q"}],
             {"model_id": "qwen3.8-max", "thinking": True, "budget": 8000, "effort": None}, True)
        self.assertEqual(captured["request"]["extra_body"],
                         {"enable_thinking": True, "thinking_budget": 8000,
                          "enable_search": True,
                          "search_options": {"forced_search": True, "search_strategy": "max"}})
        call("Qwen", "fixture-key", [{"role": "user", "content": "Q"}],
             {"model_id": "qwen3.7-plus", "thinking": False, "budget": 8000}, True)
        self.assertNotIn("enable_search", captured["request"]["extra_body"])
        no_web = literal_assignments("NO_WEB_PROVIDERS")["NO_WEB_PROVIDERS"]
        self.assertNotIn("Qwen", no_web)
        def _job(label):
            builder = load_function("evaluator_job", {
                "st": SimpleNamespace(session_state={
                    "k_syn_provider": "Qwen", "k_syn_qwen_model": label,
                    "k_syn_qwen_think": True, "k_syn_qwen_budget": 16000,
                }),
                "eval_provider_of": lambda _prefix: "Qwen",
                "QWEN_MODELS": values["QWEN_MODELS"],
                "NO_WEB_PROVIDERS": no_web, "deepseek_web_supported": lambda _mid: False,
                "load_keys": lambda: {"qwen": "fixture-key"},
                "KEY_MAP": literal_assignments("KEY_MAP")["KEY_MAP"],
            })
            return builder("k_syn", [], True)               # web verify ON
        _, mid, cfg, job = _job("Max 3.8 (flagship)")
        self.assertEqual(mid, "qwen3.8-max"); self.assertTrue(job["web"])
        _, mid, cfg, job = _job("Plus 3.7 (balanced)")
        self.assertEqual(mid, "qwen3.7-plus"); self.assertFalse(job["web"])

    def test_responses_call_raises_on_a_billed_but_empty_answer(self):
        class FakeEmptyResponses:
            def create(self, **kwargs):
                return SimpleNamespace(
                    output_text="", output=[], status="completed",
                    usage=SimpleNamespace(input_tokens=145009, output_tokens=1737),
                )

        responses_call = load_function("_responses_call", {})
        with self.assertRaisesRegex(RuntimeError, "no answer text"):
            responses_call(
                SimpleNamespace(responses=FakeEmptyResponses()), "deepseek-v4-flash",
                [{"role": "user", "content": "Question"}], "high",
                require_reasoning=True, require_search=True,
            )

    def test_gemini_uses_stateless_interactions_with_search_and_files(self):
        from google.genai import types as genai_types

        ns = {}
        load_function("_binary_files", ns)
        load_function("_inline_text_files", ns)
        interactions_input = load_function("_gemini_interactions_input", ns)
        captured = {}

        class FakeInteractions:
            def create(self, **kwargs):
                captured["request"] = kwargs
                return SimpleNamespace(
                    output_text="Gemini answer", status="completed",
                    usage=SimpleNamespace(total_input_tokens=19,
                                          total_output_tokens=7),
                )

        class FakeClient:
            def __init__(self, **kwargs):
                captured["client"] = kwargs
                self.interactions = FakeInteractions()

        call = load_function("call_model", {
            "genai": SimpleNamespace(Client=FakeClient),
            "types": genai_types,
            "_gemini_interactions_input": interactions_input,
            "_request_timeout": lambda *a: 180.0,
            "time": time,
        })
        result = call(
            "Gemini", "fixture-key",
            [{"role": "user", "content": "Question", "files": [
                {"kind": "image", "name": "pic.png", "mime": "image/png",
                 "b64": "AAAA"},
                {"kind": "pdf", "name": "notes.pdf", "mime": "application/pdf",
                 "b64": "BBBB"},
            ]}],
            {"model_id": "gemini-3.8-flash", "level": "medium"}, True,
        )
        self.assertEqual(result, ("Gemini answer", 19, 7))
        request = captured["request"]
        self.assertEqual(request["model"], "gemini-3.8-flash")
        self.assertFalse(request["store"])
        self.assertEqual(request["generation_config"],
                         {"thinking_level": "medium", "thinking_summaries": "auto",
                          "max_output_tokens": 32000})
        self.assertEqual(request["tools"], [{"type": "google_search"}])
        self.assertEqual(request["input"][0]["type"], "user_input")
        content_types = [item["type"] for item in request["input"][0]["content"]]
        self.assertEqual(content_types, ["text", "image", "document"])

        # Private visible history is deliberately flattened instead of forging
        # model_output thought signatures that Conclave never persisted.
        flattened = interactions_input([
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Follow-up"},
        ])
        transcript = flattened[0]["content"][0]["text"]
        self.assertIn("PRIOR ASSISTANT RESPONSE", transcript)
        self.assertIn("CURRENT USER TURN", transcript)
        self.assertEqual(len(flattened), 1)

    def test_council_followups_are_independent_bounded_and_branch_aware(self):
        values = literal_assignments(
            "CONTEXT_CHAR_BUDGET", "STATE_CAPSULE_LIMIT", "STATE_BEGIN", "STATE_END")
        clip = load_function("_clip_context_text", {})
        split = load_function("split_state_capsule", {
            "re": __import__("re"), "_clip_context_text": clip,
            "STATE_BEGIN": values["STATE_BEGIN"], "STATE_END": values["STATE_END"],
            "STATE_CAPSULE_LIMIT": values["STATE_CAPSULE_LIMIT"],
        })
        visible, state = split(
            "Visible answer\n" + values["STATE_BEGIN"] + "\nDurable facts\n" +
            values["STATE_END"])
        self.assertEqual(visible, "Visible answer")
        self.assertEqual(state, "Durable facts")

        build_context = load_function("build_working_context", {
            "_clip_context_text": clip,
            "CONTEXT_CHAR_BUDGET": values["CONTEXT_CHAR_BUDGET"],
        })
        record = {
            "memory": "M" * 9000,
            "thread": [message for index in range(4) for message in (
                {"role": "user", "display": f"Question {index}"},
                {"role": "assistant", "content": "A" * 2200},
            )],
        }
        context = build_context(
            record, "Original objective", [{"status": "pending", "content": "Chosen insight"}],
            "Fast")
        self.assertLessEqual(len(context), values["CONTEXT_CHAR_BUDGET"]["Fast"])
        self.assertIn("Original objective", context)
        self.assertIn("Chosen insight", context)

        source = APP.read_text(encoding="utf-8")
        followup_node = next(node for node in source_tree().body
                             if isinstance(node, ast.FunctionDef)
                             and node.name == "start_council_followup")
        followup_source = ast.get_source_segment(source, followup_node)
        self.assertIn("name not in excluded", followup_source)
        self.assertIn("build_source_turn_message", followup_source)
        self.assertIn("continuation=True", followup_source)
        self.assertIn("research=research_mode", followup_source)
        self.assertIn("bg_start", followup_source)
        self.assertIn("prompt, continuation=False, research=False", source)
        self.assertIn("res_prompt, continuation=False, research=True", source)
        for removed_key in ("k_syn_as_source", "k_res_as_source", "k_fu_src_weigh_in"):
            self.assertNotIn(removed_key, source)

        self.assertIn("adjudicator_on = adjudicator_stack_active()", source)
        self.assertIn("sources = [n for n in sources if n not in excluded]", source)
        self.assertIn('plan["synth_memo"] = build_memo_job("k_syn", prompt, "", False, keys_now=keys_now)', source)
        self.assertIn('plan["adj_memo"] = build_memo_job("k_adj", prompt, "", False, keys_now=keys_now)', source)

        branch_keys = load_function("_private_source_state_keys", {})
        self.assertEqual(branch_keys("synthesis"),
                         ("source_chats", "source_promotions"))
        self.assertEqual(branch_keys("research"),
                         ("res_source_chats", "res_source_promotions"))
        branch_node = next(node for node in source_tree().body
                           if isinstance(node, ast.FunctionDef)
                           and node.name == "render_private_source_branch")
        branch_source = ast.get_source_segment(source, branch_node)
        self.assertIn("on_click=_promote_source_answer", branch_source)
        self.assertNotIn('elif not branch_message.get("err") and st.button(', branch_source)

        build_synth = load_function("build_synth_prompt", {
            "random": SimpleNamespace(shuffle=lambda _items: None),
            "state_capsule_instruction": lambda research=False: "STATE CAPSULE",
        })
        initial_prompt, _ = build_synth("Initial question", {"A": "Answer"}, False)
        followup_prompt, _ = build_synth(
            "Follow-up question", {"A": "Answer"}, False, "Prior facts")
        self.assertIn("answer to the original question", initial_prompt)
        self.assertIn("answer to the current user turn", followup_prompt)
        self.assertNotIn("answer to the original question", followup_prompt)
        self.assertIn("initial synthesis", initial_prompt)
        self.assertIn("current user turn as the controlling request", followup_prompt)
        self.assertIn("do not merely restate the prior synthesis", followup_prompt)
        self.assertIn("make your own independent, reasoned judgment", initial_prompt)
        self.assertIn("Take a position", initial_prompt)

        build_source = load_function("build_source_turn_message", {})
        initial_source = build_source(
            "Initial question", continuation=False, research=False)
        followup_source_prompt = build_source(
            "Follow-up question", "Prior facts", continuation=True, research=False)
        research_source = build_source(
            "Research question", continuation=False, research=True)
        self.assertIn("independent source for Conclave", initial_source)
        self.assertIn("initial turn", initial_source)
        self.assertIn("strongest useful standalone answer", initial_source)
        self.assertIn("follow-up turn", followup_source_prompt)
        self.assertIn("current user turn as the controlling request", followup_source_prompt)
        self.assertIn("do not merely repeat", followup_source_prompt)
        self.assertIn("Shared conversation context", followup_source_prompt)
        self.assertIn("rigorous research contribution", research_source)

        build_research = load_function("build_research_prompt", {
            "random": SimpleNamespace(shuffle=lambda _items: None),
            "state_capsule_instruction": lambda research=False: "STATE CAPSULE",
        })
        initial_research, _ = build_research(
            "Initial research question", {"A": "Answer"}, False)
        followup_research, _ = build_research(
            "Follow-up research question", {"A": "Answer"}, False, "Prior facts")
        self.assertIn("initial research evaluation", initial_research)
        self.assertIn("follow-up research turn", followup_research)
        self.assertIn("current question as the controlling scope", followup_research)
        self.assertIn("do not simply repeat the prior evaluation", followup_research)

    def test_synth_prompt_reflects_phase1_architecture_review(self):
        build_synth = load_function("build_synth_prompt", {
            "random": SimpleNamespace(shuffle=lambda _items: None),
            "state_capsule_instruction": lambda research=False: "STATE CAPSULE",
        })
        prompt, _ = build_synth("Initial question", {"A": "Answer"}, False)

        self.assertIn("not a comparative review", prompt)
        self.assertNotIn("NOT to referee", prompt)

        self.assertIn("not privileged ground truth", prompt)
        self.assertIn("current verifiable primary evidence", prompt)
        self.assertIn("genuinely converges across independent answers", prompt)
        self.assertIn("minority finding is not weaker for being a minority", prompt)
        self.assertNotIn("do not define the boundaries", prompt)

        self.assertIn("materially relevant, decision-useful accurate detail", prompt)
        self.assertIn(
            "material if omitting it could change the user's understanding, "
            "decision, safety, next action, or confidence", prompt)
        self.assertNotIn("nothing correct gets lost", prompt)

        self.assertIn("make your own independent, reasoned judgment", prompt)
        self.assertIn("Take a position", prompt)

    def test_synth_prompt_claim_ledger_scales_by_preset(self):
        build_synth = load_function("build_synth_prompt", {
            "random": SimpleNamespace(shuffle=lambda _items: None),
            "state_capsule_instruction": lambda research=False: "STATE CAPSULE",
        })
        structured_marker = "explicit internal claim ledger"
        prose_marker = "privately work through the claims involved"

        default_prompt, _ = build_synth("Q", {"A": "Answer"}, False)
        self.assertIn(prose_marker, default_prompt)
        self.assertNotIn(structured_marker, default_prompt)

        for preset in ("Fast", "Light", "Balanced", "Custom"):
            prompt, _ = build_synth("Q", {"A": "Answer"}, False, "", preset)
            self.assertIn(prose_marker, prompt, f"preset={preset}")
            self.assertNotIn(structured_marker, prompt, f"preset={preset}")

        for preset in ("Thorough", "Deep"):
            prompt, _ = build_synth("Q", {"A": "Answer"}, False, "", preset)
            self.assertIn(structured_marker, prompt, f"preset={preset}")
            self.assertNotIn(prose_marker, prompt, f"preset={preset}")
            self.assertIn("verdict (include, qualify, exclude, or unresolved)", prompt)
            self.assertIn("account for every answer letter", prompt)

        source = APP.read_text(encoding="utf-8")
        self.assertEqual(source.count('"preset": current_preset_name()'), 1)
        self.assertIn('"preset": current_preset_name()',
                      ast.get_source_segment(
                          source,
                          next(node for node in source_tree().body
                               if isinstance(node, ast.FunctionDef)
                               and node.name == "build_eval_phase")))
        self.assertEqual(source.count("= build_eval_phase(")
                         + source.count(": build_eval_phase("), 7)
        self.assertIn('p2.get("preset", "Light")', source)

    def test_adjudicator_prompt_and_verdict_parsing(self):
        consts = literal_assignments("ADJ_VERDICT_BEGIN", "ADJ_VERDICT_END")
        build_adj = load_function("build_adjudicator_prompt", consts)
        answers = {"ChatGPT": "The sky is blue.", "Gemini": "Water is wet."}
        legend = {"A": "ChatGPT", "B": "Gemini"}

        prose = build_adj("What color is the sky?", "It's blue.", answers, legend, "Light")
        self.assertIn("What color is the sky?", prose)
        self.assertIn("--- Answer A ---\nThe sky is blue.", prose)
        self.assertIn("--- Answer B ---\nWater is wet.", prose)
        self.assertIn("--- The Synthesizer's finished draft ---\nIt's blue.", prose)
        self.assertIn("You are the Adjudicator", prose)
        self.assertIn("never draft a competing", prose)
        self.assertIn("own independent first take", prose)
        self.assertIn("Describe what you found in plain prose", prose)
        self.assertNotIn("For every defect you find, list it explicitly", prose)
        self.assertIn(consts["ADJ_VERDICT_BEGIN"], prose)
        self.assertIn("material_defects: yes|no", prose)

        for preset in ("Fast", "Balanced", "Custom"):
            variant = build_adj("Q", "D", answers, legend, preset)
            self.assertIn("Describe what you found in plain prose", variant, preset)

        for preset in ("Thorough", "Deep"):
            structured = build_adj("Q", "D", answers, legend, preset)
            self.assertIn("For every defect you find, list it explicitly", structured, preset)
            self.assertNotIn("Describe what you found in plain prose", structured, preset)

        split_verdict = load_function("split_adjudicator_verdict", {**consts, "re": re})
        yes_report, yes_flag = split_verdict(
            f"Report text.\n{consts['ADJ_VERDICT_BEGIN']}\nmaterial_defects: yes\n"
            f"{consts['ADJ_VERDICT_END']}")
        self.assertEqual(yes_report, "Report text.")
        self.assertTrue(yes_flag)
        no_report, no_flag = split_verdict(
            f"All clear.\n{consts['ADJ_VERDICT_BEGIN']}\nmaterial_defects: no\n"
            f"{consts['ADJ_VERDICT_END']}")
        self.assertEqual(no_report, "All clear.")
        self.assertFalse(no_flag)
        unparsed_report, unknown_flag = split_verdict("Just a report, no verdict block at all.")
        self.assertEqual(unparsed_report, "Just a report, no verdict block at all.")
        self.assertIsNone(unknown_flag)

    def test_disposition_prompt_and_response_split(self):
        build_disp = load_function("build_disposition_prompt", {
            "state_capsule_instruction": lambda research=False: "STATE CAPSULE",
        })
        answers = {"ChatGPT": "The sky is blue.", "Gemini": "Water is wet."}
        legend = {"A": "ChatGPT", "B": "Gemini"}

        prose = build_disp("What color is the sky?", "It's blue.",
                           "The 'blue' claim is unsupported.", answers, legend, "Light")
        self.assertIn("What color is the sky?", prose)
        self.assertIn("--- Answer A ---\nThe sky is blue.", prose)
        self.assertIn("--- Answer B ---\nWater is wet.", prose)
        self.assertIn("--- Your own finished draft ---\nIt's blue.", prose)
        self.assertIn("--- The Adjudicator's independent review of that draft ---\n"
                      "The 'blue' claim is unsupported.", prose)
        self.assertIn("You are the Synthesizer", prose)
        self.assertIn("accept and correct", prose)
        self.assertIn("reject with evidence", prose)
        self.assertIn("qualify", prose)
        self.assertIn("escalate as unresolved", prose)
        self.assertIn("checked against the real evidence above", prose)
        self.assertIn("Response to the Adjudicator", prose)
        self.assertIn("Work through the findings in plain prose", prose)
        self.assertNotIn("For every finding the Adjudicator raised, list it explicitly", prose)
        self.assertIn("STATE CAPSULE", prose)

        for preset in ("Thorough", "Deep"):
            structured = build_disp("Q", "D", "R", answers, legend, preset)
            self.assertIn("For every finding the Adjudicator raised, list it explicitly",
                         structured, preset)
            self.assertNotIn("Work through the findings in plain prose", structured, preset)

        disp_namespace = {"re": re}
        load_function("_split_after_heading", disp_namespace)
        split_disp = load_function("split_disposition_response", disp_namespace)
        revised, disposition = split_disp(
            "# Revised Answer\nCorrected text.\n\n---\n\n**How I weighed the sources**\nUsed A.\n\n"
            "---\n\n**Response to the Adjudicator**\nAccepted the finding and fixed it."
        )
        self.assertEqual(
            revised,
            "# Revised Answer\nCorrected text.\n\n---\n\n**How I weighed the sources**\nUsed A.\n\n---")
        self.assertEqual(disposition, "Accepted the finding and fixed it.")
        # A message that never went through a revision round-trips unchanged.
        unrevised, none_disposition = split_disp("Just an ordinary answer, no disposition section.")
        self.assertEqual(unrevised, "Just an ordinary answer, no disposition section.")
        self.assertIsNone(none_disposition)

    def test_independent_memo_prompt_and_job_builder(self):
        build_memo_prompt = load_function("build_independent_memo_prompt", {})
        initial = build_memo_prompt("What is the capital of France?")
        self.assertIn("independently", initial)
        self.assertIn("fallible hypothesis", initial)
        self.assertIn("not privileged ground truth", initial)
        self.assertIn("Do not reference your future role", initial)
        self.assertIn("initial turn", initial)
        self.assertIn("What is the capital of France?", initial)

        followup = build_memo_prompt("Follow-up question", "Prior facts", True)
        self.assertIn("follow-up turn", followup)
        self.assertIn("Prior facts", followup)
        self.assertNotIn("initial turn", followup)

        source_cfg = {"model_id": "claude-haiku-4-5", "effort": "low",
                      "thinking": False, "max_searches": 2}
        build_job = load_function("build_memo_job", {
            "build_independent_memo_prompt": build_memo_prompt,
            "eval_provider_of": lambda _prefix: "Claude",
            "CFG": {"Claude": source_cfg},
            "load_keys": lambda: {"anthropic": "fixture-key"},
            "KEY_MAP": literal_assignments("KEY_MAP")["KEY_MAP"],
        })
        job = build_job("k_syn", "A question", "", False)
        self.assertEqual(job["provider"], "Claude")
        self.assertEqual(job["cfg"], source_cfg)          # source tier, not seat tier
        self.assertIsNot(job["cfg"], source_cfg)          # defensive copy
        self.assertFalse(job["web"])
        self.assertEqual(len(job["messages"]), 1)
        self.assertIn("A question", job["messages"][0]["content"])

    def test_conversation_costs_follow_actual_source_membership_per_turn(self):
        model_order = literal_assignments("MODEL_ORDER")["MODEL_ORDER"]
        assistant_for_turn = load_function("_assistant_for_turn", {})
        turn_snapshot = load_function("turn_cost_snapshot", {
            "MODEL_ORDER": model_order,
            "_assistant_for_turn": assistant_for_turn,
            "ICON": literal_assignments("ICON")["ICON"],
        })
        conversation_snapshot = load_function("conversation_cost_snapshot", {
            "turn_cost_snapshot": turn_snapshot,
        })

        threads = {
            "ChatGPT": [
                {"role": "assistant", "turn_id": 0, "cost": 0.10},
                {"role": "assistant", "turn_id": 1, "cost": 0.20},
            ],
            "Gemini": [
                {"role": "assistant", "turn_id": 0, "cost": 0.03},
            ],
            "DeepSeek": [
                {"role": "assistant", "turn_id": 1, "cost": 0.05},
            ],
        }
        evaluator_thread = [
            {"role": "assistant", "turn_id": 0, "cost": 0.30},
            {"role": "assistant", "turn_id": 1, "cost": 0.40},
        ]

        second_turn = turn_snapshot(threads, evaluator_thread[1], "Synthesizer")
        self.assertEqual(
            [row["name"] for row in second_turn["rows"]],
            ["ChatGPT", "DeepSeek", "Synthesizer"],
        )
        self.assertAlmostEqual(second_turn["source_total"], 0.25)
        self.assertAlmostEqual(second_turn["evaluator_total"], 0.40)
        self.assertAlmostEqual(second_turn["total"], 0.65)

        conversation = conversation_snapshot(
            threads, evaluator_thread, "Synthesizer")
        self.assertEqual(len(conversation["turns"]), 2)
        self.assertAlmostEqual(conversation["source_total"], 0.38)
        self.assertAlmostEqual(conversation["evaluator_total"], 0.70)
        self.assertAlmostEqual(conversation["total"], 1.08)

    def test_followup_scroll_and_spend_layout_target_the_conversation_bottom(self):
        source = APP.read_text(encoding="utf-8")
        commit_node = next(node for node in source_tree().body
                           if isinstance(node, ast.FunctionDef)
                           and node.name == "bg_commit_if_done")
        commit_source = ast.get_source_segment(source, commit_node)
        self.assertIn('_scroll_to_latest_{mode}', commit_source)

        scroll_node = next(node for node in source_tree().body
                           if isinstance(node, ast.FunctionDef)
                           and node.name == "render_response_scroll_anchor")
        scroll_source = ast.get_source_segment(source, scroll_node)
        self.assertIn("scrollIntoView", scroll_source)
        self.assertIn("block: \"start\"", scroll_source)
        self.assertIn("unsafe_allow_javascript=True", scroll_source)
        for node in source_tree().body:
            if not (isinstance(node, ast.FunctionDef) and node.name != "render_response_scroll_anchor"):
                continue
            func_source = ast.get_source_segment(source, node)
            if "st.html(" in func_source and "<script>" in func_source:
                self.assertIn("unsafe_allow_javascript=True", func_source,
                             f"{node.name} embeds a <script> in st.html() but never allows it to run")

        synth_start = source.index("if view == VIEW_SYNTH:")
        research_start = source.index("elif view == VIEW_RES:")
        synth_view = source[synth_start:research_start]
        self.assertNotIn('st.markdown("#### ⭐ Synthesis")', synth_view)
        self.assertGreater(
            synth_view.index('render_session_spend("synthesis")'),
            synth_view.index('st.segmented_control("Synthesis view"'),
        )

    def test_followup_progress_renders_below_existing_conversation_not_above(self):
        source = APP.read_text(encoding="utf-8")
        synth_start = source.index("if view == VIEW_SYNTH:")
        research_start = source.index("elif view == VIEW_RES:")
        synth_view = source[synth_start:research_start]

        top_progress_idx = synth_view.index("if _synthesis_run_active and not synth:")
        # anchored on code, not comments (comments are stripped in the public copy)
        followup_form_idx = synth_view.index('follow_up_form("fu_synth"')
        followup_progress_idx = synth_view.rindex("if _synthesis_run_active:", 0, followup_form_idx)
        self.assertLess(top_progress_idx, followup_progress_idx)
        # The composer call must be the ELSE branch of that same check --
        # not unconditional, and not rendered ahead of the progress check.
        self.assertGreater(followup_form_idx, followup_progress_idx)
        else_idx = synth_view.index("else:", followup_progress_idx)
        self.assertLess(else_idx, followup_form_idx)

    def test_gpt_56_family_efforts_speeds_and_prices_match_contract(self):
        values = literal_assignments("CHATGPT_MODELS", "CHATGPT_EFFORT", "CHATGPT_SPEED",
                                     "PRIORITY_PRICES", "CHATGPT_NO_NONE")
        self.assertEqual(values["CHATGPT_MODELS"], {
            "Astra (flagship)": "gpt-6-astra",
            "Sol (advanced)": "gpt-6.1-sol",
            "Luna (economy)": "gpt-6-luna",
        })
        self.assertEqual(values["CHATGPT_NO_NONE"], {"gpt-6-astra", "gpt-6.1-sol"})
        effort_sent = load_function("chatgpt_effort_sent", {"CHATGPT_NO_NONE": values["CHATGPT_NO_NONE"]})
        self.assertEqual(effort_sent("gpt-6-astra", "none"), "low")     # Instant -> low on Astra
        self.assertEqual(effort_sent("gpt-6-astra", "max"), "max")
        self.assertEqual(effort_sent("gpt-6-luna", "none"), "none")
        self.assertEqual(effort_sent("gpt-6.1-sol", "none"), "low")    # Instant -> low on 6.1 Sol
        self.assertEqual(list(values["CHATGPT_EFFORT"].values()),
                         ["none", "low", "medium", "high", "xhigh", "max"])
        self.assertEqual(values["CHATGPT_SPEED"], {
            "Standard": "default",
            "Fast": "fast",
            "Flex": "flex",
            "Ultrafast": "ultrafast",
        })

        prices = literal_dict_string_entries("PRICES")
        self.assertEqual({model_id: prices[model_id]
                          for model_id in values["CHATGPT_MODELS"].values()}, {
            "gpt-6-astra": (10.00, 50.00),
            "gpt-6.1-sol": (2.00, 10.00),
            "gpt-6-luna": (0.10, 0.50),
        })
        for retired in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-6-sol"):
            self.assertIn(retired, prices)          # saved chats keep costing
        for model_id, priority_rate in values["PRIORITY_PRICES"].items():
            if model_id.startswith("gpt-"):
                self.assertEqual(priority_rate,
                                 tuple(value * 2 for value in prices[model_id]), model_id)
            else:
                self.assertTrue(model_id.startswith("MiniMax-"), model_id)
                self.assertEqual(priority_rate,
                                 tuple(round(value * 1.5, 4) for value in prices[model_id]), model_id)

    def test_current_grok_and_claude_contracts_are_pinned(self):
        values = literal_assignments("MODEL_GROK", "GROK_EFFORT", "CLAUDE_MODELS")
        self.assertEqual(values["MODEL_GROK"], "grok-4.7")
        self.assertEqual(values["GROK_EFFORT"], {
            "Fast": "low", "Balanced": "medium", "High": "high", "Expert": "xhigh",
        })
        self.assertEqual(values["CLAUDE_MODELS"], {
            "Haiku 5.5": "claude-haiku-5-5",
            "Sonnet 5.5": "claude-sonnet-5-5",
            "Opus 5.5": "claude-opus-5-5",
            "Fable 5.1": "claude-fable-5-1",
        })
        self.assertEqual(prices_of := literal_dict_string_entries("PRICES")["claude-opus-5-5"],
                         (4.00, 20.00))
        self.assertIn("claude-opus-5-5",
                      literal_assignments("CLAUDE_ALWAYS_THINK")["CLAUDE_ALWAYS_THINK"])
        prices = literal_dict_string_entries("PRICES")
        self.assertEqual(prices["claude-sonnet-5"], (2.00, 10.00))

    def test_chatgpt_request_uses_selected_model_effort_and_fast_tier(self):
        captured = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
                    choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                )

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                self.chat = SimpleNamespace(completions=FakeCompletions())

        def fake_responses(_client, model, input_payload, effort, **kw):
            captured.update(model=model, effort=effort, **kw)
            return "ok", 11, 7
        namespace = {
            "OpenAI": FakeOpenAI,
            "CHATGPT_SPEED": {"Standard": "default", "Fast": "fast"},
            "_oai_responses_input": lambda messages, pdf_ok: messages,
            "_responses_call": fake_responses,
            "_request_timeout": lambda *a: 180.0,
        }
        call_model = load_function("call_model", namespace)
        answer = call_model(
            "ChatGPT", "fixture-key", [{"role": "user", "content": "test"}],
            {"model_id": "gpt-5.6-luna", "effort": "max", "speed": "Fast"}, False,
        )

        self.assertEqual(answer, ("ok", 11, 7))
        self.assertEqual(captured["model"], "gpt-5.6-luna")
        self.assertEqual(captured["effort"], "max")
        self.assertEqual(captured["service_tier"], "fast")
        self.assertFalse(captured["tools"])
        self.assertEqual(captured["reasoning_summary"], "auto")
        self.assertEqual(captured["max_out"], 64000)            # Max effort -> heavy cap

    def test_chatgpt_web_request_uses_selected_model_and_standard_tier(self):
        captured = {}

        def fake_responses(_client, model, input_payload, effort, max_out=16000,
                           service_tier=None, reasoning_summary=None, tools=True):
            captured.update(model=model, input=input_payload, effort=effort,
                            service_tier=service_tier, tools=tools)
            return "ok", 3, 2

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                pass

        call_model = load_function("call_model", {
            "OpenAI": FakeOpenAI,
            "CHATGPT_SPEED": {"Standard": "default", "Fast": "fast"},
            "_oai_responses_input": lambda messages, pdf_ok: messages,
            "_responses_call": fake_responses,
            "_request_timeout": lambda *a: 180.0,
        })
        answer = call_model(
            "ChatGPT", "fixture-key", [{"role": "user", "content": "test"}],
            {"model_id": "gpt-5.6-terra", "effort": "medium", "speed": "Standard"},
            True,
        )

        self.assertEqual(answer, ("ok", 3, 2))
        self.assertEqual(captured["model"], "gpt-5.6-terra")
        self.assertEqual(captured["effort"], "medium")
        self.assertEqual(captured["service_tier"], "default")
        self.assertTrue(captured["tools"])                       # web on -> web_search attached

    def test_background_worker_does_not_reference_streamlit(self):
        worker = next(node for node in source_tree().body
                      if isinstance(node, ast.FunctionDef) and node.name == "_bg_worker")
        streamlit_refs = [node for node in ast.walk(worker)
                          if isinstance(node, ast.Name) and node.id == "st"]
        self.assertEqual(streamlit_refs, [])

    def test_background_worker_honors_stop_before_synthesis(self):
        wanted = {"_bg_update", "_bg_cancel_requested", "_bg_mark_cancelled", "_bg_worker"}
        nodes = [node for node in source_tree().body
                 if isinstance(node, ast.FunctionDef) and node.name in wanted]
        namespace = {
            "ThreadPoolExecutor": ThreadPoolExecutor,
            "wait": wait,
            "FIRST_COMPLETED": FIRST_COMPLETED,
            "time": time,
        }
        source_started = threading.Event()
        source_release = threading.Event()
        calls = []

        def fake_run_one(provider, *_args):
            calls.append(provider)
            source_started.set()
            source_release.wait(timeout=2)
            return {"text": "late source", "secs": 0.1, "cost": 0.0,
                    "err": False, "credit_err": False, "web": False}

        namespace.update({
            "run_one": fake_run_one,
            "build_synth_prompt": lambda *_args: self.fail("Synthesizer should not start"),
            "build_research_prompt": lambda *_args: self.fail("Evaluator should not start"),
        })
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        for _k, _v in transparency_helpers().items():
            namespace.setdefault(_k, _v)
        exec(compile(module, str(APP), "exec"), namespace)

        job_id = "cancel-fixture"
        reg = {"lock": threading.Lock(), "jobs": {job_id: {
            "status": "running", "progress": [], "cancel_requested": False,
        }}}
        plan = {
            "prompt": "fixture",
            "sources": {"ChatGPT": {"provider": "ChatGPT", "key": "fixture",
                                      "messages": [], "cfg": {}, "web": False}},
            "phase2": {"kind": "synth", "provider": "Claude", "key": "fixture",
                       "cfg": {}, "web": False, "label_key": "Synthesizer"},
        }
        worker = threading.Thread(target=namespace["_bg_worker"], args=(reg, job_id, plan))
        worker.start()
        self.assertTrue(source_started.wait(timeout=1))
        with reg["lock"]:
            reg["jobs"][job_id]["cancel_requested"] = True
        worker.join(timeout=1)
        stopped_promptly = not worker.is_alive()
        source_release.set()
        worker.join(timeout=2)

        self.assertTrue(stopped_promptly)
        self.assertEqual(reg["jobs"][job_id]["status"], "cancelled")
        self.assertEqual(calls, ["ChatGPT"])

    def test_background_worker_rejects_capsule_only_evaluator_output(self):
        wanted = {"_bg_update", "_bg_cancel_requested", "_bg_mark_cancelled", "_bg_worker"}
        nodes = [node for node in source_tree().body
                 if isinstance(node, ast.FunctionDef) and node.name in wanted]
        namespace = {
            "ThreadPoolExecutor": ThreadPoolExecutor,
            "wait": wait,
            "FIRST_COMPLETED": FIRST_COMPLETED,
            "time": time,
            "run_one": lambda *_args: {
                "text": "capsule only", "secs": 0.1, "cost": 0.0,
                "err": False, "credit_err": False, "web": False,
                "provider": "Claude",
            },
            "build_synth_prompt": lambda *_args: ("fixture body", {"A": "ChatGPT"}),
            "build_research_prompt": lambda *_args: ("fixture body", {"A": "ChatGPT"}),
            "split_state_capsule": lambda _text: ("", "Durable state"),
        }
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        for _k, _v in transparency_helpers().items():
            namespace.setdefault(_k, _v)
        exec(compile(module, str(APP), "exec"), namespace)

        job_id = "capsule-only-fixture"
        reg = {"lock": threading.Lock(), "jobs": {job_id: {
            "status": "running", "progress": [], "cancel_requested": False,
        }}}
        plan = {
            "prompt": "fixture", "sources": {}, "turn_id": 0,
            "phase2": {
                "kind": "synth", "provider": "Claude", "key": "fixture",
                "cfg": {}, "web": False, "label_key": "Synthesizer",
                "files": [], "min_good": 0,
            },
        }
        namespace["_bg_worker"](reg, job_id, plan)

        result = reg["jobs"][job_id]["phase2"]["result"]
        self.assertTrue(result["err"])
        self.assertIn("returned no visible answer", result["text"])

    def test_initial_turn_user_message_displays_plain_question_not_the_full_packet(self):
        wanted = {"_bg_update", "_bg_cancel_requested", "_bg_mark_cancelled", "_bg_worker"}
        nodes = [node for node in source_tree().body
                 if isinstance(node, ast.FunctionDef) and node.name in wanted]
        namespace = {
            "ThreadPoolExecutor": ThreadPoolExecutor,
            "wait": wait,
            "FIRST_COMPLETED": FIRST_COMPLETED,
            "time": time,
            "run_one": lambda *_args: {
                "text": "A real synthesized answer.", "secs": 0.1, "cost": 0.0,
                "err": False, "credit_err": False, "web": False,
                "provider": "Claude",
            },
            "build_synth_prompt": lambda *_args: (
                "Original question:\nWhat is the real question?\n\n"
                "Below are 3 anonymized answers to that question:...",
                {"A": "ChatGPT"},
            ),
            "build_research_prompt": lambda *_args: self.fail("Synthesis fixture, not Research"),
            "split_state_capsule": lambda text: (text, None),
        }
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        for _k, _v in transparency_helpers().items():
            namespace.setdefault(_k, _v)
        exec(compile(module, str(APP), "exec"), namespace)

        job_id = "initial-turn-display-fixture"
        reg = {"lock": threading.Lock(), "jobs": {job_id: {
            "status": "running", "progress": [], "cancel_requested": False,
        }}}
        plan = {
            "prompt": "What is the real question?", "sources": {}, "turn_id": 0,
            "continuation": False,
            "phase2": {
                "kind": "synth", "provider": "Claude", "key": "fixture",
                "cfg": {}, "web": False, "label_key": "Synthesizer",
                "files": [], "min_good": 0,
            },
        }
        namespace["_bg_worker"](reg, job_id, plan)

        msg = reg["jobs"][job_id]["phase2"]["msg"]
        self.assertEqual(msg["display"], "What is the real question?")
        self.assertIn("Below are 3 anonymized answers", msg["content"])

    def test_background_worker_folds_locked_memos_into_synthesis_evidence(self):
        wanted = {"_bg_update", "_bg_cancel_requested", "_bg_mark_cancelled", "_bg_worker"}
        nodes = [node for node in source_tree().body
                 if isinstance(node, ast.FunctionDef) and node.name in wanted]
        captured_good = {}

        def fake_run_one(provider, key, messages, cfg, web):
            if provider == "ChatGPT":
                return {"text": "ChatGPT source answer", "secs": 0.1, "cost": 0.02,
                        "err": False, "credit_err": False, "web": False, "provider": provider}
            return {"text": f"{provider} memo answer", "secs": 0.1, "cost": 0.01,
                    "err": False, "credit_err": False, "web": False, "provider": provider}

        def fake_build_synth_prompt(question, answers, web_on, context, preset):
            captured_good.update(answers)
            return "fixture body", {chr(65 + i): n for i, n in enumerate(answers)}

        namespace = {
            "ThreadPoolExecutor": ThreadPoolExecutor,
            "wait": wait, "FIRST_COMPLETED": FIRST_COMPLETED, "time": time,
            "run_one": fake_run_one,
            "build_synth_prompt": fake_build_synth_prompt,
            "build_research_prompt": lambda *_a: self.fail("Research should not run"),
            "split_state_capsule": lambda text: (text, ""),
        }
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        for _k, _v in transparency_helpers().items():
            namespace.setdefault(_k, _v)
        exec(compile(module, str(APP), "exec"), namespace)

        job_id = "memo-fixture"
        reg = {"lock": threading.Lock(), "jobs": {job_id: {
            "status": "running", "progress": [], "cancel_requested": False,
        }}}
        memo_msg = [{"role": "user", "content": "memo prompt"}]
        plan = {
            "prompt": "fixture question", "turn_id": 0, "context": "",
            "sources": {
                "ChatGPT": {"provider": "ChatGPT", "key": "src-key",
                            "messages": [], "cfg": {}, "web": False},
            },
            "synth_memo": {"provider": "Claude", "key": "memo-key-1",
                          "messages": memo_msg, "cfg": {}, "web": False},
            "adj_memo": {"provider": "Gemini", "key": "memo-key-2",
                        "messages": memo_msg, "cfg": {}, "web": False},
            "phase2": {
                "kind": "synth", "provider": "Claude", "key": "fixture",
                "cfg": {}, "web": False, "label_key": "Synthesizer",
                "files": [], "min_good": 1, "preset": "Light",
            },
        }
        namespace["_bg_worker"](reg, job_id, plan)

        job = reg["jobs"][job_id]
        self.assertEqual(job["status"], "done")
        # Memos must never appear in phase1/results (the Sources-tab driver).
        self.assertEqual(set(job["phase1"].keys()), {"ChatGPT"})
        self.assertEqual(captured_good, {
            "ChatGPT": "ChatGPT source answer",
            "Claude": "Claude memo answer",
            "Gemini": "Gemini memo answer",
        })
        # Tracked separately for cost purposes, keyed by role, not provider.
        self.assertEqual(set(job["memos"].keys()), {"synth_memo", "adj_memo"})
        self.assertEqual(job["memos"]["synth_memo"]["provider"], "Claude")
        self.assertEqual(job["memos"]["adj_memo"]["provider"], "Gemini")
        # Live progress recorded honest, distinctly-labeled rows for both --
        # a real billed background call must never be silently invisible.
        progress_names = {item["name"] for item in job["progress"]}
        self.assertIn("Synthesizer memo", progress_names)
        self.assertIn("Adjudicator memo", progress_names)

    def test_commit_reveals_evaluator_memos_only_for_completed_synthesis_turns(self):
        node = next(n for n in source_tree().body
                   if isinstance(n, ast.FunctionDef) and n.name == "bg_commit_if_done")
        commit_source = ast.get_source_segment(APP.read_text(encoding="utf-8"), node)
        self.assertIn('if mode == "synthesis":', commit_source)
        self.assertIn("eval_threads", commit_source)
        self.assertIn('("synth_memo", "Synthesizer")', commit_source)
        self.assertIn('("adj_memo", "Adjudicator")', commit_source)
        # Must live INSIDE the `if p2out and p2plan:` block (only reached once
        # synthesis has a finished answer) -- not built unconditionally.
        self.assertLess(commit_source.index("if p2out and p2plan:"),
                        commit_source.index('eval_threads.setdefault'))

    def test_background_worker_runs_sequential_adjudicator_audit(self):
        wanted = {"_bg_update", "_bg_cancel_requested", "_bg_mark_cancelled",
                 "_bg_worker", "build_adjudicator_prompt", "split_adjudicator_verdict"}
        nodes = [node for node in source_tree().body
                if isinstance(node, ast.FunctionDef) and node.name in wanted]
        consts = literal_assignments("ADJ_VERDICT_BEGIN", "ADJ_VERDICT_END")
        calls = []

        def fake_run_one(provider, key, messages, cfg, web):
            calls.append(key)
            if key == "phase2-key":
                return {"text": "The finished draft.", "secs": 0.1, "cost": 0.03,
                        "err": False, "credit_err": False, "web": False, "provider": provider}
            if key == "phase3-key":
                self.assertIn("The finished draft.", messages[0]["content"])
                self.assertIn("You are the Adjudicator", messages[0]["content"])
                return {"text": ("Found one unsupported claim.\n" +
                                consts["ADJ_VERDICT_BEGIN"] + "\nmaterial_defects: yes\n" +
                                consts["ADJ_VERDICT_END"]),
                        "secs": 0.2, "cost": 0.01, "err": False, "credit_err": False,
                        "web": False, "provider": provider}
            return {"text": f"{provider} source answer", "secs": 0.1, "cost": 0.02,
                    "err": False, "credit_err": False, "web": False, "provider": provider}

        namespace = {
            "ThreadPoolExecutor": ThreadPoolExecutor,
            "wait": wait, "FIRST_COMPLETED": FIRST_COMPLETED, "time": time, "re": re,
            "run_one": fake_run_one,
            "build_synth_prompt": lambda question, answers, web_on, context, preset: (
                "fixture body", {chr(65 + i): n for i, n in enumerate(answers)}),
            "build_research_prompt": lambda *_a: self.fail("Research should not run"),
            "split_state_capsule": lambda text: (text, ""),
            **consts,
        }
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        for _k, _v in transparency_helpers().items():
            namespace.setdefault(_k, _v)
        exec(compile(module, str(APP), "exec"), namespace)

        base_plan = {
            "prompt": "fixture question", "turn_id": 0, "context": "",
            "sources": {
                "ChatGPT": {"provider": "ChatGPT", "key": "src-key",
                            "messages": [], "cfg": {}, "web": False},
            },
            "phase2": {
                "kind": "synth", "provider": "Claude", "key": "phase2-key",
                "cfg": {}, "web": False, "label_key": "Synthesizer",
                "files": [], "min_good": 1, "preset": "Light",
            },
        }
        plan = {**base_plan, "phase3": {
            "provider": "Gemini", "key": "phase3-key", "cfg": {}, "web": False,
            "label_key": "Adjudicator", "preset": "Light",
        }}
        reg = {"lock": threading.Lock(), "jobs": {"p3": {
            "status": "running", "progress": [], "cancel_requested": False,
        }}}
        namespace["_bg_worker"](reg, "p3", plan)
        job = reg["jobs"]["p3"]

        self.assertEqual(job["status"], "done")
        self.assertIn("finished", job)
        # Strictly sequential: the source, then phase2, then phase3 -- never
        # phase3 before phase2 finishes.
        self.assertEqual(calls, ["src-key", "phase2-key", "phase3-key"])
        self.assertEqual(job["phase2"]["result"]["text"], "The finished draft.")
        self.assertIsNotNone(job["phase3"])
        self.assertEqual(job["phase3"]["material_defects"], True)
        self.assertEqual(job["phase3"]["result"]["text"], "Found one unsupported claim.")
        self.assertNotIn(consts["ADJ_VERDICT_BEGIN"], job["phase3"]["result"]["text"])
        progress_names = {item["name"] for item in job["progress"]}
        self.assertIn("Adjudicator", progress_names)

        # No phase3 job (e.g. Research, or synthesis not active) -- must stay
        # completely dormant, no extra run_one call, no crash.
        calls.clear()
        reg2 = {"lock": threading.Lock(), "jobs": {"noaudit": {
            "status": "running", "progress": [], "cancel_requested": False,
        }}}
        namespace["_bg_worker"](reg2, "noaudit", dict(base_plan))
        job2 = reg2["jobs"]["noaudit"]
        self.assertEqual(job2["status"], "done")
        self.assertIsNone(job2["phase3"])
        self.assertNotIn("phase3-key", calls)

    def test_background_worker_runs_conditional_disposition_revision(self):
        wanted = {"_bg_update", "_bg_cancel_requested", "_bg_mark_cancelled",
                 "_bg_worker", "build_adjudicator_prompt", "split_adjudicator_verdict",
                 "build_disposition_prompt"}
        nodes = [node for node in source_tree().body
                if isinstance(node, ast.FunctionDef) and node.name in wanted]
        consts = literal_assignments("ADJ_VERDICT_BEGIN", "ADJ_VERDICT_END")
        calls = []

        def make_fake_run_one(verdict):
            def fake_run_one(provider, key, messages, cfg, web):
                calls.append(key)
                if key == "phase2-key":
                    return {"text": "The finished draft.", "secs": 0.1, "cost": 0.03,
                            "err": False, "credit_err": False, "web": False, "provider": provider}
                if key == "phase3-key":
                    return {"text": ("Found one unsupported claim.\n" +
                                    consts["ADJ_VERDICT_BEGIN"] +
                                    f"\nmaterial_defects: {verdict}\n" +
                                    consts["ADJ_VERDICT_END"]),
                            "secs": 0.2, "cost": 0.01, "err": False, "credit_err": False,
                            "web": False, "provider": provider}
                if key == "phase4-key":
                    self.assertEqual(len(messages), 3)
                    self.assertTrue(messages[0]["content"].endswith("fixture body"))
                    self.assertTrue(messages[0].get("cache"))
                    self.assertEqual(messages[1], {"role": "assistant",
                                                   "content": "The finished draft."})
                    self.assertIn("Found one unsupported claim.", messages[2]["content"])
                    self.assertIn("You are the Synthesizer", messages[2]["content"])
                    self.assertNotIn("fixture body", messages[2]["content"])
                    return {"text": "The corrected, revised draft.", "secs": 0.15, "cost": 0.02,
                            "err": False, "credit_err": False, "web": False, "provider": provider}
                return {"text": f"{provider} source answer", "secs": 0.1, "cost": 0.02,
                        "err": False, "credit_err": False, "web": False, "provider": provider}
            return fake_run_one

        def run(verdict, phase4_job):
            calls.clear()
            namespace = {
                "ThreadPoolExecutor": ThreadPoolExecutor,
                "wait": wait, "FIRST_COMPLETED": FIRST_COMPLETED, "time": time, "re": re,
                "run_one": make_fake_run_one(verdict),
                "build_synth_prompt": lambda question, answers, web_on, context, preset: (
                    "fixture body", {chr(65 + i): n for i, n in enumerate(answers)}),
                "build_research_prompt": lambda *_a: self.fail("Research should not run"),
                "split_state_capsule": lambda text: (text, ""),
                "state_capsule_instruction": lambda research=False: "STATE CAPSULE",
                "_with_today": sweep_helpers()["_with_today"],
                **consts,
            }
            module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
            for _k, _v in transparency_helpers().items():
                namespace.setdefault(_k, _v)
            exec(compile(module, str(APP), "exec"), namespace)
            plan = {
                "prompt": "fixture question", "turn_id": 0, "context": "",
                "sources": {
                    "ChatGPT": {"provider": "ChatGPT", "key": "src-key",
                                "messages": [], "cfg": {}, "web": False},
                },
                "phase2": {
                    "kind": "synth", "provider": "Claude", "key": "phase2-key",
                    "cfg": {}, "web": False, "label_key": "Synthesizer",
                    "files": [], "min_good": 1, "preset": "Light",
                },
                "phase3": {
                    "provider": "Gemini", "key": "phase3-key", "cfg": {}, "web": False,
                    "label_key": "Adjudicator", "preset": "Light",
                },
            }
            if phase4_job:
                plan["phase4"] = {
                    "provider": "Claude", "key": "phase4-key", "cfg": {}, "web": False,
                    "label_key": "Revision", "preset": "Light",
                }
            reg = {"lock": threading.Lock(), "jobs": {"p4": {
                "status": "running", "progress": [], "cancel_requested": False,
            }}}
            namespace["_bg_worker"](reg, "p4", plan)
            return reg["jobs"]["p4"]

        # material_defects: yes -> the revision runs, strictly after phase3,
        # and its result is captured distinctly from the original draft.
        job_yes = run("yes", phase4_job=True)
        self.assertEqual(job_yes["status"], "done", job_yes.get("error"))
        self.assertEqual(calls, ["src-key", "phase2-key", "phase3-key", "phase4-key"])
        self.assertIsNotNone(job_yes["phase4"])
        self.assertEqual(job_yes["phase4"]["result"]["text"], "The corrected, revised draft.")
        self.assertEqual(job_yes["phase2"]["result"]["text"], "The finished draft.")
        progress_names = {item["name"] for item in job_yes["progress"]}
        self.assertIn("Revision", progress_names)
        self.assertTrue(job_yes["material_defects"])

        # material_defects: no -> the revision must stay completely
        # dormant, even though a phase4 job exists in the plan -- no extra
        # call, no crash.
        job_no = run("no", phase4_job=True)
        self.assertEqual(job_no["status"], "done")
        self.assertEqual(calls, ["src-key", "phase2-key", "phase3-key"])
        self.assertIsNone(job_no["phase4"])
        self.assertFalse(job_no["material_defects"])

        # No phase4 job at all (e.g. Research, which has no Adjudicator
        # concept) -- must stay dormant regardless of the verdict.
        job_none = run("yes", phase4_job=False)
        self.assertEqual(job_none["status"], "done")
        self.assertEqual(calls, ["src-key", "phase2-key", "phase3-key"])
        self.assertIsNone(job_none["phase4"])

    def test_synthesis_contracts_stay_silent_on_search_failure(self):
        import random
        synth = load_function("build_synth_prompt",
                              {"random": random,
                               "state_capsule_instruction": lambda research: "C"})
        text, _ = synth("q", {"A": "a", "B": "b"}, True)
        self.assertIn("never mention the search problem", text)
        solo = load_function("build_solo_followup_prompt",
                             {"state_capsule_instruction": lambda research: "C"})
        self.assertIn("never mention the search problem", solo("q", "ctx", True))

    def test_solo_followup_prompt_keeps_synthesizing_without_source_pool(self):
        consts = literal_assignments("STATE_BEGIN", "STATE_END")
        build = load_function("build_solo_followup_prompt", {
            "state_capsule_instruction": load_function(
                "state_capsule_instruction", dict(consts)),
        })
        body = build("What about the second option?",
                     context="Original objective:\nPick a laptop.", web_on=False)
        self.assertIn("What about the second option?", body)
        self.assertIn("Original objective:\nPick a laptop.", body)
        self.assertIn("keep synthesizing", body)
        self.assertIn("Cross-check any new claim", body)
        self.assertIn("controlling request", body)
        # Continuity: the state-capsule contract still applies so the durable
        # memory keeps updating across solo turns too.
        self.assertIn(consts["STATE_BEGIN"], body)
        # None of the multi-source evaluation machinery belongs here.
        self.assertNotIn("anonymized answers", body)
        self.assertNotIn("Answer A", body)
        self.assertNotIn("claim ledger", body)
        self.assertNotIn("How I weighed the sources", body)
        # Web flag actually changes the instruction.
        self.assertNotIn("web search", body)
        self.assertIn("web search", build("q", context="", web_on=True))

    def test_background_worker_runs_solo_followup_with_no_sources(self):
        wanted = {"_bg_update", "_bg_cancel_requested", "_bg_mark_cancelled",
                 "_bg_worker"}
        nodes = [node for node in source_tree().body
                if isinstance(node, ast.FunctionDef) and node.name in wanted]
        calls = []

        def fake_run_one(provider, key, messages, cfg, web):
            calls.append(key)
            self.assertIn("solo contract fixture", messages[0]["content"])
            return {"text": "The solo continuation answer.", "secs": 0.1,
                    "cost": 0.004, "err": False, "credit_err": False,
                    "web": False, "provider": provider}

        namespace = {
            "ThreadPoolExecutor": ThreadPoolExecutor,
            "wait": wait, "FIRST_COMPLETED": FIRST_COMPLETED, "time": time,
            "run_one": fake_run_one,
            "build_solo_followup_prompt": lambda question, context, web_on: (
                f"solo contract fixture: {question} | ctx={context}"),
            "build_synth_prompt": lambda *_a: self.fail(
                "build_synth_prompt must not run on a solo follow-up"),
            "build_research_prompt": lambda *_a: self.fail("Research should not run"),
            "split_state_capsule": lambda text: (text, "durable state fixture"),
        }
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        for _k, _v in transparency_helpers().items():
            namespace.setdefault(_k, _v)
        exec(compile(module, str(APP), "exec"), namespace)

        plan = {
            "prompt": "a quick follow-up", "turn_id": 1,
            "context": "Original objective:\nfixture",
            "sources": {},
            "phase2": {
                "kind": "solo", "provider": "Claude", "key": "solo-key",
                "cfg": {}, "web": False, "label_key": "Synthesizer",
                "files": [], "min_good": 0, "preset": "Light",
            },
            "synth_memo": None, "adj_memo": None,
            "phase3": None, "phase4": None,
        }
        reg = {"lock": threading.Lock(), "jobs": {"solo": {
            "status": "running", "progress": [], "cancel_requested": False,
        }}}
        namespace["_bg_worker"](reg, "solo", plan)
        job = reg["jobs"]["solo"]

        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertEqual(calls, ["solo-key"])
        self.assertEqual(job["phase2"]["result"]["text"], "The solo continuation answer.")
        self.assertEqual(job["phase2"]["legend"], {})
        self.assertEqual(job["phase2"]["state"], "durable state fixture")
        self.assertIsNone(job["phase3"])
        self.assertIsNone(job["phase4"])

    def test_followup_handler_defaults_solo_and_toggle_restores_full_council(self):
        handler = next(node for node in source_tree().body
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "start_council_followup")
        src = ast.get_source_segment(APP.read_text(encoding="utf-8"), handler)
        self.assertIn(
            'full_council = research_mode or bool(st.session_state.get("k_full_council"))',
            src)
        # Solo path: no sources, min_good 0, and the Adjudicator stack never
        # builds (adjudicator_on requires full_council).
        self.assertIn("sources = []", src)
        self.assertIn("minimum = 0", src)
        self.assertIn(
            "adjudicator_on = full_council and (not research_mode) and adjudicator_stack_active()",
            src)
        self.assertIn('("synth" if full_council else "solo")', src)
        # The toggle exists as a real DEFAULTS-backed control, defaulting to
        # the cheap path, and the composer renders it for synth follow-ups.
        self.assertFalse(literal_dict_string_entries("DEFAULTS")["k_full_council"])
        form = next(node for node in source_tree().body
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "follow_up_form")
        form_src = ast.get_source_segment(APP.read_text(encoding="utf-8"), form)
        self.assertIn('key="k_full_council"', form_src)

    def test_background_progress_tracks_each_call_and_phase(self):
        progress_rows = load_function("_bg_progress_rows", {
            "ICON": {"ChatGPT": "C", "Gemini": "G", "Synthesizer": "S"},
        })
        plan = {
            "sources": {"ChatGPT": {"web": False}, "Gemini": {"web": True}},
            "src_meta": {
                "ChatGPT": {"model_id": "gpt-fixture", "settings": "effort low"},
                "Gemini": {"model_id": "gemini-fixture", "settings": "balanced"},
            },
            "phase2": {"label_key": "Synthesizer", "label": "Claude Opus 5",
                       "kind": "synth", "web": False, "model_id": "claude-fixture",
                       "settings": "effort high"},
        }
        job = {
            "plan": plan,
            "phase": 1,
            "progress": [{"name": "ChatGPT", "secs": 1.2, "cost": 0.01,
                          "err": False, "credit": False, "web": False}],
        }
        rows = progress_rows(job)
        self.assertEqual([row["state"] for row in rows], ["done", "active", "queued"])
        self.assertEqual(rows[1]["verb"], "searching & thinking")
        self.assertEqual(rows[1]["model_id"], "gemini-fixture")
        self.assertEqual(rows[2]["settings"], "effort high")

        job["phase"] = 2
        job["progress"].append({"name": "Gemini", "secs": 2.3, "cost": 0.02,
                                "err": False, "credit": False, "web": True})
        rows = progress_rows(job)
        self.assertEqual([row["state"] for row in rows], ["done", "done", "active"])
        self.assertEqual(rows[2]["verb"], "synthesizing sources")

        render_html = load_function("_bg_progress_html", {"html": html, "CONCLAVE_SEAL_SVG": "<svg fixture/>", "seal_mark_html": load_function("seal_mark_html", {"CONCLAVE_SEAL_SVG": "<svg fixture/>"})})
        markup = render_html(rows)
        self.assertIn("conclave-seal-loading", markup)
        self.assertIn("Synthesizer", markup)

    def test_progress_fraction_counts_every_tracked_row_not_just_sources_and_phase2(self):
        progress_rows = load_function("_bg_progress_rows", {
            "ICON": {"Gemini": "\U0001F537", "DeepSeek": "\U0001F30A", "Grok": "\U000026A1"},
            "model_id_of": load_function("model_id_of", {"MODEL_GROK": "grok-fixture"}),
            "settings_str": load_function("settings_str", {}),
        })
        plan = {
            "sources": {"Gemini": {"web": True}},
            "src_meta": {"Gemini": {"model_id": "gemini-fixture", "settings": "balanced"}},
            "synth_memo": {"provider": "DeepSeek",
                           "cfg": {"model_id": "deepseek-fixture", "thinking": True, "effort": "high"},
                           "web": False},
            "adj_memo": {"provider": "Grok", "cfg": {"model_id": "grok-fixture", "mode": "Fast"},
                        "web": False},
            "phase2": {"label_key": "Synthesizer", "label": "Claude Sonnet 5",
                       "kind": "synth", "web": True, "model_id": "claude-fixture",
                       "settings": "effort high · thinking on"},
            "phase3": {"label_key": "Adjudicator", "label": "ChatGPT GPT-5.6 Terra",
                       "web": False, "model_id": "gpt-fixture", "settings": "effort low"},
            "phase4": {"label_key": "Revision", "label": "Claude Sonnet 5",
                       "web": False, "model_id": "claude-fixture", "settings": "effort high"},
        }
        job = {
            "plan": plan,
            "phase": 2,
            "started": 0.0,
            "phase1_done_at": 0.0,
            "progress": [
                {"name": "Gemini", "secs": 32.6, "cost": 0.046,
                 "err": False, "credit": False, "web": True},
                {"name": "Synthesizer memo", "secs": 43.6, "cost": 0.0528,
                 "err": False, "credit": False, "web": False},
                {"name": "Adjudicator memo", "secs": 26.9, "cost": 0.0328,
                 "err": False, "credit": False, "web": False},
            ],
        }
        rows = progress_rows(job)
        self.assertEqual(len(rows), 6, "one source + 2 memos + phase2/3/4 = 6 tracked rows")
        finished = sum(1 for row in rows if row["state"] in ("done", "error", "skipped"))
        self.assertEqual(finished, 3, "Gemini + both memos are done; draft/audit/revision are not")

        # The fixed formula in bg_progress() itself must derive both numbers
        # from this same `rows` list, not the old sources-plus-one count.
        source = APP.read_text(encoding="utf-8")
        worker_start = source.index("def bg_progress():")
        worker_end = source.index("def render_pending_question():")
        bg_progress_source = source[worker_start:worker_end]
        self.assertNotIn('len(plan["sources"])', bg_progress_source)
        self.assertIn("expect = len(rows)", bg_progress_source)
        self.assertIn('row["state"] in ("done", "error", "skipped")', bg_progress_source)

    def test_pending_question_renders_above_live_progress(self):
        source = APP.read_text(encoding="utf-8")
        synth_start = source.index("if view == VIEW_SYNTH:")
        research_start = source.index("elif view == VIEW_RES:")
        synth_view = source[synth_start:research_start]
        research_view = source[research_start:]
        for label, view_source in (("Synthesis", synth_view), ("Research", research_view)):
            calls = [i for i in range(len(view_source))
                     if view_source.startswith("bg_refresh(show_progress=True)", i)]
            self.assertEqual(len(calls), 2, f"{label} should call bg_progress() twice")
            for call_idx in calls:
                preceding = view_source[:call_idx]
                last_pending = preceding.rfind("render_pending_question()")
                self.assertGreater(last_pending, -1,
                                   f"{label}: no render_pending_question() before a bg_progress() call")
                # Nothing else meaningful must sit between the two calls.
                between = preceding[last_pending + len("render_pending_question()"):]
                self.assertTrue(between.strip() == "" or between.strip().startswith("#"),
                                f"{label}: unexpected content between render_pending_question() and bg_progress()")

    def test_memo_progress_rows_show_the_actual_provider(self):
        progress_rows = load_function("_bg_progress_rows", {
            "ICON": {"Gemini": "\U0001F537", "DeepSeek": "\U0001F30A"},
            "model_id_of": load_function("model_id_of", {"MODEL_GROK": "grok-fixture"}),
            "settings_str": load_function("settings_str", {}),
        })
        plan = {
            "sources": {},
            "src_meta": {},
            "synth_memo": {"provider": "Gemini", "cfg": {"model_id": "gemini-fixture",
                                                          "level": "high"}, "web": False},
            "adj_memo": {"provider": "DeepSeek", "cfg": {"model_id": "deepseek-fixture",
                                                          "thinking": False}, "web": False},
        }
        job = {"plan": plan, "phase": 1, "progress": [], "started": time.time()}
        rows = progress_rows(job)
        synth_row = next(r for r in rows if r["name"] == "Synthesizer memo")
        adj_row = next(r for r in rows if r["name"] == "Adjudicator memo")
        self.assertEqual(synth_row["label"], "Gemini (Synthesizer)")
        self.assertEqual(synth_row["icon"], "\U0001F537")
        self.assertEqual(synth_row["model_id"], "gemini-fixture")
        self.assertEqual(adj_row["label"], "DeepSeek (Adjudicator)")
        self.assertEqual(adj_row["icon"], "\U0001F30A")
        self.assertIn("independent answer", synth_row["verb"])

        # Once done, the same provider-specific label/icon carry through.
        job["progress"] = [
            {"name": "Synthesizer memo", "secs": 5.0, "cost": 0.01,
             "err": False, "credit": False, "web": False},
            {"name": "Adjudicator memo", "secs": 4.0, "cost": 0.02,
             "err": False, "credit": False, "web": False},
        ]
        rows = progress_rows(job)
        synth_row = next(r for r in rows if r["name"] == "Synthesizer memo")
        self.assertEqual(synth_row["state"], "done")
        self.assertEqual(synth_row["label"], "Gemini (Synthesizer)")

    def test_progress_rows_show_a_live_elapsed_counter_while_active(self):
        progress_rows = load_function("_bg_progress_rows", {
            "ICON": {"ChatGPT": "C"},
        })
        now = time.time()
        plan = {
            "sources": {"ChatGPT": {"web": False}},
            "src_meta": {"ChatGPT": {"model_id": "gpt-fixture", "settings": "effort low"}},
            "phase2": {"label_key": "Synthesizer", "label": "Claude Opus 5",
                       "kind": "synth", "web": False, "model_id": "claude-fixture",
                       "settings": "effort high"},
            "phase3": {"label_key": "Adjudicator", "label": "GPT-5.6 Terra",
                       "web": False, "model_id": "gpt-fixture-adj",
                       "settings": "effort low"},
        }
        # Phase 1 (the ChatGPT source) still active -- elapsed from job start.
        job = {"plan": plan, "phase": 1, "progress": [], "started": now - 5}
        rows = progress_rows(job)
        self.assertEqual(rows[0]["state"], "active")
        self.assertAlmostEqual(now - rows[0]["started_at"], 5, delta=1)

        # Phase 2 (Synthesizer) active -- elapsed from end of Phase 1, NOT
        # from the overall job start (which would overstate it).
        job = {"plan": plan, "phase": 2, "progress": [
            {"name": "ChatGPT", "secs": 1.0, "cost": 0.01, "err": False,
             "credit": False, "web": False},
        ], "started": now - 20, "phase1_done_at": now - 8}
        rows = progress_rows(job)
        synth_row = next(r for r in rows if r["name"] == "Synthesizer")
        self.assertEqual(synth_row["state"], "active")
        self.assertAlmostEqual(now - synth_row["started_at"], 8, delta=1)

        frozen_time = SimpleNamespace(time=lambda: now)
        render_html = load_function("_bg_progress_html", {
            "html": html,
            "time": frozen_time,
            "seal_mark_html": load_function(
                "seal_mark_html", {"CONCLAVE_SEAL_SVG": "<svg fixture/>"}),
        })
        markup = render_html(rows)
        self.assertIn("8s", markup)
        self.assertIn("effort high", markup)  # settings folded into the active line too

        # Phase 3 (Adjudicator) active -- elapsed from end of Phase 2.
        job = {"plan": plan, "phase": 2, "progress": [
            {"name": "ChatGPT", "secs": 1.0, "cost": 0.01, "err": False,
             "credit": False, "web": False},
            {"name": "Synthesizer", "secs": 3.0, "cost": 0.02, "err": False,
             "credit": False, "web": False},
        ], "started": now - 30, "phase1_done_at": now - 18, "phase2_done_at": now - 3}
        rows = progress_rows(job)
        adj_row = next(r for r in rows if r["name"] == "Adjudicator")
        self.assertEqual(adj_row["state"], "active")
        self.assertAlmostEqual(now - adj_row["started_at"], 3, delta=1)
        self.assertIn("3s", render_html(rows))

    def test_history_search_covers_nested_conversation_text(self):
        matches = load_function("history_matches", {})
        entry = {
            "ts": "2026-07-31T09:00:00",
            "prompt": "Initial smoke question",
            "threads": {
                "Gemini": [{"role": "assistant", "content": "A hidden nested phrase"}],
            },
        }
        self.assertTrue(matches(entry, "smoke"))
        self.assertTrue(matches(entry, "nested phrase"))
        self.assertTrue(matches(entry, "SMOKE nested"))
        self.assertFalse(matches(entry, "missing phrase"))
        self.assertTrue(matches(entry, ""))

        research = {"ts": "2026-07-31T08:00:00", "mode": "research", "prompt": None}
        filtered = load_function("filtered_history", {"history_matches": matches})
        self.assertEqual(filtered([entry, research], mode_filter="Research"), [research])
        self.assertEqual(filtered([entry, research], sort_order="Oldest first"),
                         [research, entry])
        self.assertEqual(filtered([entry, research], sort_order="A–Z"),
                         [research, entry])

    def test_voice_fallback_is_limited_to_model_availability(self):
        helper = next(node for node in source_tree().body
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "_model_fallback_allowed")
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[])),
                     str(APP), "exec"), namespace)

        class ApiError(Exception):
            def __init__(self, message, code=None):
                super().__init__(message)
                self.code = code

        allowed = namespace["_model_fallback_allowed"]
        self.assertTrue(allowed(ApiError("missing", code="model_not_found")))
        self.assertTrue(allowed(ApiError("You do not have access to this model")))
        self.assertFalse(allowed(ApiError("insufficient_quota")))
        self.assertFalse(allowed(ApiError("connection timed out")))


class HistoryReadCacheTests(unittest.TestCase):
    def test_cache_reuses_parse_and_defends_against_caller_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            path.write_text('[{"nested": {"answer": "original"}}]', encoding="utf-8")
            h = load_history_helpers(path)
            with patch.object(json, "load", wraps=json.load) as parse:
                first = h["load_history"]()
                first[0]["nested"]["answer"] = "changed in UI"
                second = h["load_history"]()
                self.assertEqual(second[0]["nested"]["answer"], "original")
                self.assertEqual(parse.call_count, 1)

    def test_cache_detects_other_writer_deletion_recreation_and_archive_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            reader = load_history_helpers(path)
            writer = load_history_helpers(path)
            writer["update_history"](lambda _: [{"ts": "first"}])
            self.assertEqual(reader["load_history"]()[0]["ts"], "first")
            writer["update_history"](lambda _: [{"ts": "second"}])
            self.assertEqual(reader["load_history"]()[0]["ts"], "second")
            path.unlink()
            self.assertEqual(reader["load_history"](), [])
            writer["update_history"](lambda _: [{"ts": "third"}])
            self.assertEqual(reader["load_history"]()[0]["ts"], "third")
            writer["update_history_archive"](lambda _: [{"ts": "old"}])
            self.assertEqual(reader["load_history_archive"]()[0]["ts"], "old")
            writer["update_history_archive"](lambda _: [])
            self.assertEqual(reader["load_history_archive"](), [])

    def test_corruption_after_cached_read_preserves_warning_and_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            path.write_text('[{"ts": "before"}]', encoding="utf-8")
            h = load_history_helpers(path)
            h["load_history"]()
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(h["load_history"](), [])
            self.assertTrue(h["_history_alerts"])
            self.assertEqual(len(list(Path(tmp).glob("*.corrupt"))), 1)
            h["update_history"](lambda _: [{"ts": "recovered"}])
            self.assertEqual(h["load_history"]()[0]["ts"], "recovered")

    def test_cached_read_never_supplies_the_writer_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            h = load_history_helpers(path)
            h["update_history"](lambda _: [{"ts": "first"}])
            h["load_history"]()
            other = load_history_helpers(path)
            other["update_history"](lambda rows: rows + [{"ts": "other"}])
            h["update_history"](lambda rows: rows + [{"ts": "last"}])
            self.assertEqual([r["ts"] for r in h["load_history"]()],
                             ["first", "other", "last"])


class RefreshRoutingTests(unittest.TestCase):
    def test_page_registers_one_timer_with_fallback_after_rendering(self):
        source = APP.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"(?m)^@st\.fragment\(run_every=0\.6\)", source)), 1)
        self.assertEqual(source.count("_progress_refresh_rendered = True"), 4)
        self.assertIn('if st.session_state.get("bg_job_id") and not _progress_refresh_rendered:', source)
        self.assertGreater(source.rindex("        bg_refresh()"),
                           source.index("elif view == VIEW_SETTINGS:"))

    def test_refresh_requests_main_rerun_on_completion_and_renders_only_when_visible(self):
        calls = []
        state = {"bg_job_id": "fixture"}
        job = {"status": "running"}
        fake_st = SimpleNamespace(
            session_state=state, fragment=lambda **_: lambda fn: fn,
            rerun=lambda **kw: calls.append(("rerun", kw)))
        refresh = load_function("bg_refresh", {
            "st": fake_st, "_bg_get": lambda _: job,
            "bg_progress": lambda: calls.append(("progress", {}))})
        refresh()
        self.assertEqual(calls, [])
        refresh(show_progress=True)
        self.assertEqual(calls, [("progress", {})])
        for status in ("done", "error", "cancelled"):
            calls.clear()
            job["status"] = status
            refresh()
            self.assertEqual(calls, [("rerun", {"scope": "app"})])


class HistoryConcurrencyTests(unittest.TestCase):
    def test_concurrent_updates_do_not_lose_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            helpers = load_history_helpers(Path(tmp) / "history.json")
            update_history = helpers["update_history"]
            count = 24
            barrier = threading.Barrier(count)
            errors = []

            def writer(index):
                try:
                    barrier.wait(timeout=5)
                    update_history(lambda history: history + [{"index": index}])
                except Exception as exc:  # surfaced after every thread joins
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(count)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            history = helpers["load_history"]()
            self.assertEqual({item["index"] for item in history}, set(range(count)))

    def test_history_overflow_moves_to_archive_without_data_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            helpers = load_history_helpers(Path(tmp) / "history.json")
            update_history = helpers["update_history"]
            for index in range(55):
                entry = {"ts": f"fixture-{index:02d}", "mode": "synthesis",
                         "prompt": f"Prompt {index}"}
                update_history(lambda history, entry=entry: [entry, *history])

            recent = helpers["load_history"]()
            archived = helpers["load_history_archive"]()
            self.assertEqual(len(recent), 50)
            self.assertEqual(len(archived), 5)
            self.assertEqual(
                {entry["ts"] for entry in [*recent, *archived]},
                {f"fixture-{index:02d}" for index in range(55)},
            )

    def test_corrupt_history_is_quarantined_with_a_visible_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "history.json"
            history_file.write_text("{not json", encoding="utf-8")
            helpers = load_history_helpers(history_file)

            self.assertEqual(helpers["load_history"](), [])
            sidecars = list(Path(tmp).glob("history.json.*.corrupt"))
            self.assertEqual(len(sidecars), 1)
            self.assertEqual(sidecars[0].read_text(encoding="utf-8"), "{not json")
            self.assertEqual(len(helpers["_history_alerts"]), 1)
            self.assertIn(sidecars[0].name, helpers["_history_alerts"][0])
            self.assertEqual(helpers["_history_write_blocked"], {})

            # The quarantined file is out of the way, so saving works again.
            helpers["update_history"](
                lambda history: [{"ts": "fixture", "mode": "synthesis"}, *history])
            self.assertEqual(len(helpers["load_history"]()), 1)

    def test_unpreservable_corrupt_history_blocks_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "history.json"
            history_file.write_text("{not json", encoding="utf-8")
            helpers = load_history_helpers(history_file)

            def _refuse(*_args, **_kwargs):
                raise OSError("file is held open by another process")

            real_os = helpers["os"]
            helpers["os"] = SimpleNamespace(
                path=real_os.path, replace=_refuse, remove=real_os.remove,
                fsync=real_os.fsync, getpid=real_os.getpid, stat=real_os.stat)
            helpers["shutil"] = SimpleNamespace(copyfile=_refuse)
            try:
                self.assertEqual(helpers["load_history"](), [])
                self.assertIn(str(history_file), helpers["_history_write_blocked"])
                self.assertTrue(helpers["_history_alerts"])
                with self.assertRaises(RuntimeError):
                    helpers["update_history"](lambda history: history)
            finally:
                helpers["os"] = real_os
                helpers["shutil"] = shutil
            # The unreadable file was never renamed, copied, or overwritten.
            self.assertEqual(history_file.read_text(encoding="utf-8"), "{not json")
            self.assertEqual(list(Path(tmp).glob("history.json.*.corrupt")), [])


def fake_st(state=None):
    return SimpleNamespace(session_state={} if state is None else state)


def load_credit_error(namespace=None):
    """Load the real _is_credit_error with stand-in provider exception types."""
    class FakeOpenAIRateLimitError(Exception):
        def __init__(self, message="", code=None):
            super().__init__(message)
            self.code = code

    class FakeAnthropicAPIStatusError(Exception):
        def __init__(self, message="", type=None):
            super().__init__(message)
            self.type = type

    class FakeGenaiAPIError(Exception):
        def __init__(self, message="", code=None, status=None):
            super().__init__(message)
            self.code = code
            self.status = status

    ns = namespace if namespace is not None else {}
    ns.update(literal_assignments("_CREDIT_KEYWORDS"))
    ns.update({
        "OpenAIRateLimitError": FakeOpenAIRateLimitError,
        "AnthropicAPIStatusError": FakeAnthropicAPIStatusError,
        "genai_errors": SimpleNamespace(APIError=FakeGenaiAPIError),
    })
    load_function("_is_credit_error", ns)
    return ns


def make_fake_openai(transcription_create=None, speech_create=None):
    class FakeOpenAI:
        def __init__(self, api_key=None, timeout=None):
            self.audio = SimpleNamespace(
                transcriptions=SimpleNamespace(create=transcription_create),
                speech=SimpleNamespace(create=speech_create),
            )
    return FakeOpenAI


class AttachmentHelperTests(unittest.TestCase):
    def test_pack_files_classifies_uploads_and_enforces_caps(self):
        ns = literal_assignments("IMG_MIME", "MAX_FILE_MB", "MAX_TEXT_CHARS")
        ns["base64"] = base64
        ns["_prepare_image"] = lambda raw, ext: (raw, ns["IMG_MIME"][ext], None)
        pack_files = load_function("pack_files", ns)

        big = SimpleNamespace(name="big.bin",
                              getvalue=lambda: bytes(ns["MAX_FILE_MB"] * 1024 * 1024 + 1))
        image = SimpleNamespace(name="chart.png", getvalue=lambda: b"imgbytes")
        pdf = SimpleNamespace(name="spec.pdf", getvalue=lambda: b"pdfbytes")
        long_text = SimpleNamespace(name="long.txt",
                                    getvalue=lambda: b"x" * (ns["MAX_TEXT_CHARS"] + 10))
        no_ext = SimpleNamespace(name="README", getvalue=lambda: b"plain body")

        packed = pack_files([big, image, pdf, long_text, no_ext])

        self.assertEqual(packed[0]["kind"], "text")
        self.assertIn("skipped", packed[0]["text"])
        self.assertIn(f"{ns['MAX_FILE_MB']} MB", packed[0]["text"])

        self.assertEqual(packed[1]["kind"], "image")
        self.assertEqual(packed[1]["mime"], "image/png")
        self.assertEqual(base64.b64decode(packed[1]["b64"]), b"imgbytes")

        self.assertEqual(packed[2]["kind"], "pdf")
        self.assertEqual(packed[2]["mime"], "application/pdf")
        self.assertEqual(base64.b64decode(packed[2]["b64"]), b"pdfbytes")

        self.assertEqual(packed[3]["kind"], "text")
        self.assertTrue(packed[3]["text"].endswith("[truncated — file exceeds inline limit]"))

        self.assertEqual(packed[4]["kind"], "text")
        self.assertEqual(packed[4]["text"], "plain body")

        self.assertEqual(pack_files(None), [])

    def test_inline_text_files_folds_notes_and_binary_filter(self):
        ns = {}
        inline = load_function("_inline_text_files", ns)
        binary = load_function("_binary_files", ns)

        self.assertEqual(inline(None, None), "")
        self.assertEqual(inline("Body", []), "Body")

        files = [
            {"name": "old.txt", "kind": "text", "stripped": True},
            {"name": "notes.txt", "kind": "text", "text": "line one"},
            {"name": "chart.png", "kind": "image", "b64": "aW1n"},
        ]
        folded = inline("Body", files)
        self.assertIn("'old.txt' from an earlier session is no longer available", folded)
        self.assertIn("[Attached file: notes.txt]", folded)
        self.assertIn("line one", folded)
        self.assertTrue(folded.endswith("Body"))

        message = {"files": [
            {"name": "a.png", "kind": "image"},
            {"name": "b.pdf", "kind": "pdf"},
            {"name": "c.txt", "kind": "text"},
            {"name": "d.png", "kind": "image", "stripped": True},
        ]}
        kept = binary(message)
        self.assertEqual([f["name"] for f in kept], ["a.png", "b.pdf"])
        self.assertEqual(binary({}), [])

    def _builder_namespace(self):
        ns = {}
        load_function("_inline_text_files", ns)
        load_function("_binary_files", ns)
        load_function("_image_text_only_note", ns)
        load_function("_pdf_unsupported_note", ns)
        return ns

    def _messages(self):
        image = {"name": "chart.png", "kind": "image", "mime": "image/png", "b64": "aW1n"}
        pdf = {"name": "spec.pdf", "kind": "pdf", "mime": "application/pdf", "b64": "cGRm"}
        return [
            {"role": "user", "content": "Question", "files": [image, pdf]},
            {"role": "assistant", "content": "Answer", "files": [image]},
        ]

    def test_openai_chat_builder_shapes_native_parts(self):
        ns = self._builder_namespace()
        build = load_function("_oai_chat_messages", ns)

        out = build(self._messages(), pdf_ok=True)
        parts = out[0]["content"]
        self.assertEqual([p["type"] for p in parts], ["image_url", "file", "text"])
        self.assertEqual(parts[0]["image_url"]["url"], "data:image/png;base64,aW1n")
        self.assertEqual(parts[1]["file"]["filename"], "spec.pdf")
        self.assertEqual(parts[1]["file"]["file_data"], "data:application/pdf;base64,cGRm")
        self.assertEqual(parts[2]["text"], "Question")
        self.assertEqual(out[1]["content"], "Answer")

        out = build(self._messages(), pdf_ok=False, image_ok=False)
        text = out[0]["content"]
        self.assertIsInstance(text, str)
        self.assertIn("[Image 'chart.png' attached", text)
        self.assertIn("[PDF 'spec.pdf' attached", text)
        self.assertTrue(text.endswith("Question"))

    def test_openai_responses_builder_shapes_native_parts(self):
        ns = self._builder_namespace()
        build = load_function("_oai_responses_input", ns)

        out = build(self._messages(), pdf_ok=True)
        parts = out[0]["content"]
        self.assertEqual([p["type"] for p in parts], ["input_image", "input_file", "input_text"])
        self.assertEqual(parts[0]["image_url"], "data:image/png;base64,aW1n")
        self.assertEqual(parts[1]["filename"], "spec.pdf")
        self.assertEqual(parts[1]["file_data"], "data:application/pdf;base64,cGRm")
        self.assertEqual(parts[2]["text"], "Question")
        self.assertEqual(out[1]["content"], "Answer")

        out = build(self._messages(), pdf_ok=False, image_ok=False)
        text = out[0]["content"]
        self.assertIsInstance(text, str)
        self.assertIn("[Image 'chart.png' attached", text)
        self.assertIn("[PDF 'spec.pdf' attached", text)

    def test_claude_builder_uses_native_blocks(self):
        ns = self._builder_namespace()
        build = load_function("_claude_messages", ns)

        out = build(self._messages())
        blocks = out[0]["content"]
        self.assertEqual([b["type"] for b in blocks], ["image", "document", "text"])
        self.assertEqual(blocks[0]["source"],
                         {"type": "base64", "media_type": "image/png", "data": "aW1n"})
        self.assertEqual(blocks[1]["source"]["media_type"], "application/pdf")
        self.assertEqual(blocks[2]["text"], "Question")
        self.assertEqual(out[1]["content"], "Answer")


class ClaudeWebNarrationTests(unittest.TestCase):

    class _Block:
        def __init__(self, type, text=None):
            self.type = type
            if text is not None:
                self.text = text

    class _Usage:
        input_tokens = 10
        output_tokens = 20

    def _call(self, blocks, web):
        captured = {}
        outer = self

        class _Messages:
            def create(self, **kw):
                captured.update(kw)
                return type("R", (), {"content": blocks, "stop_reason": "end_turn",
                                      "usage": outer._Usage()})()

        class _Client:
            def __init__(self, api_key=None, timeout=None, **kw):
                self.messages = _Messages()

        call_model = load_function("call_model", {
            "OpenAI": None, "Anthropic": _Client, "genai": None,
            "_request_timeout": lambda *a: 60.0,
            "_claude_messages": lambda m: m,
            "CLAUDE_NO_DIALS": set(), "CLAUDE_ALWAYS_THINK": set(),
            "CLAUDE_OLD_SEARCH": set(),
            "claude_thinking_forced": lambda m, e: False,
        })
        return call_model("Claude", "k", [], {"model_id": "claude-sonnet-5",
                                              "effort": "low", "thinking": False,
                                              "max_searches": 2}, web)

    def test_web_run_drops_interstitial_narration_before_the_last_tool(self):
        B = self._Block
        blocks = [B("text", "I'll search for current facts first."),
                  B("server_tool_use"),
                  B("web_search_tool_result"),
                  B("text", "Good -- got the data I need. Searching once more."),
                  B("server_tool_use"),
                  B("web_search_tool_result"),
                  B("text", "# The Real Answer\n\nSubstance here.")]
        text, _, _ = self._call(blocks, web=True)
        self.assertEqual(text, "# The Real Answer\n\nSubstance here.")

    def test_web_run_falls_back_to_full_join_if_nothing_follows_the_tools(self):
        B = self._Block
        blocks = [B("text", "Everything I wrote came before the search."),
                  B("server_tool_use"), B("web_search_tool_result")]
        text, _, _ = self._call(blocks, web=True)
        self.assertEqual(text, "Everything I wrote came before the search.")

    def test_web_off_keeps_the_plain_full_join(self):
        B = self._Block
        blocks = [B("thinking"), B("text", "Part one. "), B("text", "Part two.")]
        text, _, _ = self._call(blocks, web=False)
        self.assertEqual(text, "Part one. Part two.")

    def test_opening_a_saved_chat_lands_on_its_newest_answer(self):
        state = {}
        land = load_function("_land_on_latest_answer",
                             {"st": type("S", (), {"session_state": state})()})
        land({"thread": [
            {"role": "user"},
            {"role": "assistant", "turn_id": 1},
            {"role": "user"},
            {"role": "assistant", "turn_id": 3},
        ]}, "synthesis")
        self.assertEqual(state.get("_scroll_to_latest_synthesis"), 3)
        state.clear()
        land({"thread": [{"role": "assistant"}]}, "synthesis")
        land(None, "research")
        self.assertEqual(state, {})


class GracefulWebFallbackTests(unittest.TestCase):

    def _run_one(self, calls, fail_with):
        seen = []
        def fake_call_model(name, key, messages, cfg, web):
            seen.append(web)
            if web:
                raise fail_with
            return "answered without web", 10, 20
        ns = {"call_model": fake_call_model, "time": time,
              "_is_credit_error": lambda n, e: False,
              "cost_of": lambda *a, **k: 0.001, "model_id_of": lambda n, c: "m"}
        load_function("_is_timeout_error", ns)
        load_function("_is_web_unavailable_error", ns)
        run_one = load_function("run_one", ns)
        return run_one("Claude", "k", [], {"model_id": "m"}, True), seen

    def test_search_switched_off_on_the_account_retries_without_web(self):
        r, seen = self._run_one([], RuntimeError(
            "Error code: 400 - {'error': {'code': '400', 'message': 'Param Incorrect', "
            "'param': 'web search tool found in the request body, but webSearchEnabled "
            "is false', 'type': ''}}"))
        self.assertEqual(seen, [True, False])
        self.assertFalse(r["err"]); self.assertFalse(r["web"])
        self.assertEqual(r["web_fallback_reason"], "unavailable")
        msg = load_function("assistant_msg", {})(r, "m", "low")
        cap = load_function("meta_caption", {})(msg)
        self.assertIn("web search isn't switched on for this account", cap)
        r, seen = self._run_one([], RuntimeError("Error code: 400 - invalid model"))
        self.assertEqual(seen, [True]); self.assertTrue(r["err"])

    def test_live_cost_meter_climbs_with_finished_calls(self):
        fn = load_function("live_cost_so_far", {})
        job = {"progress": [{"name": "A", "cost": 0.01, "err": False},
                            {"name": "B", "cost": None, "err": True},
                            {"name": "C", "cost": 0.0, "err": False}],
               "preview_calls": [{"cost": 0.001}], "jev": {"calls": [{"cost": 0.0005}]},
               "fresh_calls": [{"cost": 0.016}, {"cost": 0.005}]}
        out = fn(job)
        self.assertAlmostEqual(out["models"], 0.01)
        self.assertAlmostEqual(out["helpers"], 0.0225)
        self.assertAlmostEqual(out["total"], 0.0325)
        self.assertEqual(out["finished"], 3)
        self.assertEqual(fn({})["total"], 0.0)

    def test_timeout_on_web_retries_once_without_web_and_says_so(self):
        class APITimeoutError(Exception):
            pass
        r, seen = self._run_one([], APITimeoutError("Request timed out."))
        self.assertEqual(seen, [True, False])
        self.assertFalse(r["err"])
        self.assertFalse(r["web"])
        self.assertTrue(r["web_fallback"])
        self.assertEqual(r["text"], "answered without web")
        msg = load_function("assistant_msg", {})(r, "m", "low")
        self.assertTrue(msg["web_fallback"])
        cap = load_function("meta_caption", {})(msg)
        self.assertIn("search timed out", cap)

    def test_non_timeout_failures_do_not_trigger_the_fallback(self):
        r, seen = self._run_one([], RuntimeError("invalid api key"))
        self.assertEqual(seen, [True])
        self.assertTrue(r["err"])
        self.assertFalse(r["web_fallback"])

    def test_timeout_detection_by_class_name_and_message(self):
        is_timeout = load_function("_is_timeout_error", {})
        class ReadTimeout(Exception):
            pass
        self.assertTrue(is_timeout(ReadTimeout("x")))
        self.assertTrue(is_timeout(RuntimeError("upstream request timed out")))
        self.assertFalse(is_timeout(RuntimeError("billing_error: insufficient quota")))


class MistralAndKimiProviderTests(unittest.TestCase):

    def _ns(self, captured):
        class FakeCompletions:
            def __init__(self, content):
                self.content = content
            def create(self, **kwargs):
                captured["request"] = kwargs
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=self.content),
                                             finish_reason="stop")],
                    usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7))
        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured["client"] = kwargs
                content = captured.get("reply", "answer")
                self.chat = SimpleNamespace(completions=FakeCompletions(content))
        ns = {"OpenAI": FakeOpenAI,
              "_oai_chat_messages": lambda messages, pdf_ok, image_ok=True: messages,
              "_request_timeout": lambda *a: 150.0,
              "MISTRAL_REASONING_MODELS": {"mistral-medium-latest", "mistral-small-latest"},
              "KIMI_ALWAYS_THINK": {"kimi-k3"}}
        ns["_oai_chat_messages"] = (lambda messages, pdf_ok, image_ok=True, image_shape="object",
                                    pdf_shape="file", skip_pdf=False:
                                    captured.update(image_ok=image_ok, image_shape=image_shape,
                                                    pdf_ok=pdf_ok, pdf_shape=pdf_shape) or messages)
        load_function("_content_text", ns)
        load_function("_mistral_call", ns)
        load_function("_kimi_call", ns)
        return ns

    def test_tables_prices_and_membership(self):
        v = literal_assignments("MISTRAL_MODELS", "KIMI_MODELS", "MISTRAL_EFFORT",
                                "KIMI_EFFORT", "EVAL_PROVIDERS", "MODEL_ORDER",
                                "NO_WEB_PROVIDERS", "DEFAULTS")
        self.assertEqual(v["MISTRAL_MODELS"], {
            "Large 4 (flagship · preview)": "mistral-large-4",
            "Medium 3.5 (advanced)": "mistral-medium-latest",
            "Large 3 (balanced)": "mistral-large-2512",
            "Small 4 (economy)": "mistral-small-latest"})
        self.assertEqual(v["KIMI_MODELS"], {"K3.1 Pro (flagship)": "kimi-k3.1-pro",
                                            "K3 (prior)": "kimi-k3",
                                            "K2.6 (balanced)": "kimi-k2.6"})
        self.assertEqual(list(v["MISTRAL_EFFORT"].values()),
                         ["none", "minimal", "low", "medium", "high", "xhigh"])
        self.assertEqual(list(v["KIMI_EFFORT"].values()), ["low", "high", "max"])
        for prov in ("Mistral", "Kimi"):
            self.assertIn(prov, v["EVAL_PROVIDERS"])
            self.assertIn(prov, v["MODEL_ORDER"])
        self.assertIn("Mistral", v["NO_WEB_PROVIDERS"])
        self.assertNotIn("Kimi", v["NO_WEB_PROVIDERS"])
        self.assertFalse(v["DEFAULTS"]["k_web_kimi"])
        self.assertFalse(v["DEFAULTS"]["k_en_mistral"])
        self.assertFalse(v["DEFAULTS"]["k_en_kimi"])
        prices = literal_dict_string_entries("PRICES")
        self.assertEqual(prices["mistral-medium-latest"], (1.50, 7.50))
        self.assertEqual(prices["mistral-large-latest"], (0.50, 1.50))
        self.assertEqual(prices["mistral-small-latest"], (0.15, 0.60))
        self.assertEqual(prices["kimi-k3"], (3.00, 15.00))
        self.assertEqual(prices["kimi-k2.6"], (0.95, 4.00))
        self.assertEqual(list(literal_assignments("MISTRAL_EFFORT")["MISTRAL_EFFORT"].values()),
                         ["none", "minimal", "low", "medium", "high", "xhigh"])

    def test_mistral_request_shape_and_chunked_content(self):
        captured = {"reply": [{"type": "thinking", "text": "hmm"},
                              {"type": "text", "text": "Bonjour, answer."}]}
        ns = self._ns(captured)
        text, itok, otok = ns["_mistral_call"]("Mistral", "k", [], {
            "model_id": "mistral-medium-latest", "effort": "high"}, False)
        self.assertEqual(captured["client"]["base_url"], "https://api.mistral.ai/v1")
        self.assertEqual(captured["client"]["max_retries"], 1)
        req = captured["request"]
        self.assertEqual(req["model"], "mistral-medium-latest")
        self.assertTrue(captured["image_ok"])
        self.assertEqual(captured["image_shape"], "string")
        self.assertTrue(captured["pdf_ok"]); self.assertEqual(captured["pdf_shape"], "document_url")
        self.assertEqual(req["reasoning_effort"], "high")
        self.assertNotIn("temperature", req)
        self.assertEqual(text, "Bonjour, answer.")   # thinking chunk dropped
        self.assertEqual((itok, otok), (12, 7))
        # Large 3 has no reasoning dial -- the parameter is never sent
        captured = {"reply": "plain"}
        ns = self._ns(captured)
        ns["_mistral_call"]("Mistral", "k", [], {"model_id": "mistral-large-latest",
                                                 "effort": "high"}, False)
        self.assertNotIn("reasoning_effort", captured["request"])
        self.assertTrue(captured["image_ok"])

    def test_kimi_request_shape_k3_vs_k26(self):
        captured = {"reply": "k3 answer"}
        ns = self._ns(captured)
        ns["_kimi_call"]("Kimi", "k", [], {"model_id": "kimi-k3", "effort": "max",
                                           "thinking": True}, False)
        self.assertEqual(captured["client"]["base_url"], "https://api.moonshot.ai/v1")
        req = captured["request"]
        self.assertEqual(req["reasoning_effort"], "max")
        self.assertNotIn("extra_body", req)
        self.assertNotIn("temperature", req)          # docs: not modifiable
        self.assertEqual(req["max_completion_tokens"], 64000)
        self.assertNotIn("max_tokens", req)
        captured = {"reply": "k2.6 answer"}
        ns = self._ns(captured)
        ns["_kimi_call"]("Kimi", "k", [], {"model_id": "kimi-k2.6", "effort": "high",
                                           "thinking": False}, False)
        req = captured["request"]
        self.assertNotIn("reasoning_effort", req)
        self.assertEqual(req["extra_body"], {"thinking": {"type": "disabled"}})


class MetaMiniMaxNvidiaProviderTests(unittest.TestCase):

    def _ns(self, captured):
        class FakeCompletions:
            def __init__(self, content):
                self.content = content
            def create(self, **kwargs):
                captured["request"] = kwargs
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=self.content),
                                             finish_reason="stop")],
                    usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7))
        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured["client"] = kwargs
                self.chat = SimpleNamespace(completions=FakeCompletions(captured.get("reply", "answer")))
        ns = {"OpenAI": FakeOpenAI, "re": re,
              "_oai_chat_messages": lambda messages, pdf_ok, image_ok=True: messages,
              "_oai_responses_input": lambda messages, pdf_ok, image_ok=True: messages,
              "_responses_call": lambda *a, **k: captured.setdefault("responses", (a, k)) and ("web", 1, 1),
              "_request_timeout": lambda *a: 150.0,
              "MINIMAX_THINK_OFF_OK": {"MiniMax-M3"}}
        load_function("_content_text", ns)
        load_function("_chat_completion_text", ns)
        load_function("_meta_call", ns)
        load_function("_minimax_call", ns)
        load_function("_nvidia_call", ns)
        return ns

    def test_tables_prices_and_membership(self):
        v = literal_assignments("META_MODELS", "MINIMAX_MODELS", "NVIDIA_MODELS", "META_EFFORT",
                                "EVAL_PROVIDERS", "MODEL_ORDER", "NO_WEB_PROVIDERS", "DEFAULTS",
                                "PROVIDER_ORIGIN", "KEY_MAP")
        self.assertEqual(v["META_MODELS"], {"Wonder 1.0 (flagship · preview)": "muse-wonder-1.0",
                                            "Spark 1.3 (balanced)": "muse-spark-1.3"})
        self.assertNotIn("contributor", str(v["META_MODELS"]))   # trains on prompts -- excluded
        self.assertEqual(list(v["META_EFFORT"].values()),
                         ["minimal", "low", "medium", "high", "xhigh", "max"])
        self.assertEqual(v["MINIMAX_MODELS"]["M3 (flagship)"], "MiniMax-M3")
        self.assertEqual(v["NVIDIA_MODELS"]["Nemotron 3 Ultra (flagship)"],
                         "nvidia/nemotron-3-ultra-550b-a55b")
        for prov in ("Meta", "MiniMax", "NVIDIA"):
            self.assertIn(prov, v["EVAL_PROVIDERS"]); self.assertIn(prov, v["MODEL_ORDER"])
            self.assertIn(prov, v["PROVIDER_ORIGIN"]); self.assertIn(prov, v["KEY_MAP"])
        self.assertNotIn("Meta", v["NO_WEB_PROVIDERS"])      # Responses-path web search
        self.assertNotIn("MiniMax", v["NO_WEB_PROVIDERS"])
        self.assertIn("NVIDIA", v["NO_WEB_PROVIDERS"]); self.assertIn("DeepSeek", v["NO_WEB_PROVIDERS"])
        self.assertFalse(v["DEFAULTS"]["k_web_mm"])
        for k in ("k_en_meta", "k_en_mm", "k_en_nv", "k_web_meta"):
            self.assertFalse(v["DEFAULTS"][k])
        prices = literal_dict_string_entries("PRICES")
        self.assertEqual(prices["muse-spark-1.3"], (1.25, 4.25))
        self.assertEqual(prices["muse-wonder-1.0"], (4.00, 20.00))
        self.assertEqual(prices["muse-spark-1.2"], (1.25, 4.25))
        self.assertEqual(prices["MiniMax-M3"], (0.30, 1.20))
        self.assertEqual(prices["MiniMax-M2.7-highspeed"], (0.60, 2.40))
        self.assertEqual(prices["nvidia/nemotron-3-ultra-550b-a55b"], (0.0, 0.0))

    def test_meta_request_shape_and_web_path(self):
        captured = {"reply": "spark"}
        ns = self._ns(captured)
        text, *_ = ns["_meta_call"]("Meta", "k", [], {"model_id": "muse-spark-1.2", "effort": "low"}, False)
        self.assertEqual(captured["client"]["base_url"], "https://api.meta.ai/v1")
        req = captured["request"]
        self.assertEqual(req["reasoning_effort"], "low")
        self.assertNotIn("temperature", req)            # docs: leave unset
        self.assertEqual(text, "spark")
        captured = {"reply": "x"}
        ns = self._ns(captured)
        text, *_ = ns["_meta_call"]("Meta", "k", [], {"model_id": "muse-spark-1.3", "effort": "max"}, True)
        self.assertIn("responses", captured)            # web-on rides the Responses helper
        self.assertEqual(text, "web")
        self.assertEqual(captured["responses"][1].get("reasoning_summary"), "auto")

    def test_minimax_thinking_shape_and_think_tag_strip(self):
        captured = {"reply": "<think>hmm</think>The answer."}
        ns = self._ns(captured)
        text, *_ = ns["_minimax_call"]("MiniMax", "k", [], {"model_id": "MiniMax-M3", "thinking": False}, False)
        self.assertEqual(captured["client"]["base_url"], "https://api.minimax.io/v1")
        req = captured["request"]
        self.assertEqual(req["extra_body"], {"thinking": {"type": "disabled"}, "reasoning_split": True})
        self.assertIn("max_completion_tokens", req); self.assertNotIn("max_tokens", req)
        self.assertNotIn("service_tier", req)           # Standard sends no tier
        self.assertEqual(text, "The answer.")
        captured = {"reply": "fast"}
        ns = self._ns(captured)
        ns["_minimax_call"]("MiniMax", "k", [], {"model_id": "MiniMax-M3", "thinking": True,
                                                 "speed": "Priority"}, False)
        self.assertEqual(captured["request"]["service_tier"], "priority")
        captured = {"reply": "ok"}
        ns = self._ns(captured)
        ns["_minimax_call"]("MiniMax", "k", [], {"model_id": "MiniMax-M2.7", "thinking": False}, False)
        # M2.x cannot disable thinking -- adaptive is sent regardless of the toggle
        self.assertEqual(captured["request"]["extra_body"]["thinking"], {"type": "adaptive"})

    def test_nvidia_thinking_budget_shape(self):
        captured = {"reply": "nemo"}
        ns = self._ns(captured)
        ns["_nvidia_call"]("NVIDIA", "k", [], {"model_id": "nvidia/nemotron-3-ultra-550b-a55b",
                                              "thinking": True, "budget": 16000}, False)
        self.assertEqual(captured["client"]["base_url"], "https://integrate.api.nvidia.com/v1")
        req = captured["request"]
        self.assertEqual(req["extra_body"]["chat_template_kwargs"],
                         {"enable_thinking": True, "reasoning_budget": 16000})
        self.assertEqual(req["extra_body"]["reasoning_budget"], 16000)
        self.assertLessEqual(req["max_tokens"], 32768)
        captured = {"reply": "nemo"}
        ns = self._ns(captured)
        ns["_nvidia_call"]("NVIDIA", "k", [], {"model_id": "nvidia/nemotron-3-nano-30b-a3b",
                                              "thinking": False, "budget": 4000}, False)
        self.assertEqual(captured["request"]["extra_body"], {"chat_template_kwargs": {"enable_thinking": False}})


class GlmProviderTests(unittest.TestCase):

    def _ns(self, captured):
        class FakeCompletions:
            def __init__(self, content):
                self.content = content
            def create(self, **kwargs):
                captured["request"] = kwargs
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=self.content),
                                             finish_reason="stop")],
                    usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7))
        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured["client"] = kwargs
                self.chat = SimpleNamespace(completions=FakeCompletions(captured.get("reply", "answer")))
        ns = {"OpenAI": FakeOpenAI, "re": re,
              "_oai_chat_messages": lambda messages, pdf_ok, image_ok=True: messages,
              "_request_timeout": lambda *a: 150.0,
              "GLM_ALWAYS_THINK": {"glm-5.3"}}
        load_function("_content_text", ns)
        load_function("_glm_call", ns)
        return ns

    def test_tables_prices_and_membership(self):
        v = literal_assignments("GLM_MODELS", "GLM_EFFORT", "EVAL_PROVIDERS", "MODEL_ORDER",
                                "NO_WEB_PROVIDERS", "DEFAULTS", "PROVIDER_ORIGIN", "KEY_MAP")
        self.assertEqual(v["GLM_MODELS"], {"GLM-5.3 (flagship)": "glm-5.3",
                                           "GLM-5.3 Flash (economy)": "glm-5.3-flash"})
        self.assertEqual(list(v["GLM_EFFORT"].values()), ["low", "high", "max"])
        self.assertIn("GLM", v["EVAL_PROVIDERS"]); self.assertIn("GLM", v["MODEL_ORDER"])
        self.assertIn("GLM", v["PROVIDER_ORIGIN"]); self.assertEqual(v["KEY_MAP"]["GLM"], "zai")
        self.assertNotIn("GLM", v["NO_WEB_PROVIDERS"])   # hosted search on its own endpoint
        self.assertFalse(v["DEFAULTS"]["k_en_glm"]); self.assertFalse(v["DEFAULTS"]["k_web_glm"])
        prices = literal_dict_string_entries("PRICES")
        self.assertEqual(prices["glm-5.3"], (1.40, 4.40))
        self.assertEqual(prices["glm-5-turbo"], (1.20, 4.00))
        self.assertEqual(prices["glm-4.7"], (0.60, 2.20))
        self.assertEqual(prices["glm-5.3-flash"], (0.15, 0.50))

    def test_glm_request_shapes(self):
        captured = {"reply": "<think>x</think>The answer."}
        ns = self._ns(captured)
        text, *_ = ns["_glm_call"]("GLM", "k", [], {"model_id": "glm-5.3", "effort": "low",
                                                    "thinking": False}, False)
        self.assertEqual(captured["client"]["base_url"], "https://api.z.ai/api/paas/v4/")
        self.assertEqual(captured["client"]["max_retries"], 1)
        req = captured["request"]
        self.assertEqual(req["extra_body"]["thinking"], {"type": "enabled"})   # cannot disable
        self.assertEqual(req["extra_body"]["reasoning_effort"], "low")
        self.assertNotIn("tools", req)
        self.assertEqual(text, "The answer.")
        # 4.7: thinking toggles, no effort sent below 5.2
        captured = {"reply": "ok"}
        ns = self._ns(captured)
        ns["_glm_call"]("GLM", "k", [], {"model_id": "glm-4.7", "effort": "high",
                                         "thinking": False}, False)
        req = captured["request"]
        self.assertEqual(req["extra_body"], {"thinking": {"type": "disabled"}})
        # web on: the hosted tool with the minimal documented shape, engine omitted
        captured = {"reply": "webby"}
        ns = self._ns(captured)
        ns["_glm_call"]("GLM", "k", [], {"model_id": "glm-5.3", "effort": "high",
                                         "thinking": True}, True)
        req = captured["request"]
        self.assertEqual(req["tools"], [{"type": "web_search",
                                        "web_search": {"enable": True, "search_engine": "search-prime", "search_result": True}}])


class SharedSourcesV380Tests(unittest.TestCase):

    def _consts(self, *names):
        return literal_assignments(*names)

    def _search_ns(self):
        ns = {"json": json, "time": time, "re": re, "html": html,
              "ThreadPoolExecutor": ThreadPoolExecutor}
        ns.update(literal_assignments(
            "TAVILY_URL", "TAVILY_DEPTH", "TAVILY_CREDIT_PRICE", "TAVILY_MAX_RESULTS",
            "BRAVE_URL", "BRAVE_PRICE", "BRAVE_COUNT", "SEARCH_TIMEOUT_S",
            "FRESH_SOURCES_CHAR_BUDGET", "FRESH_SOURCE_EXCERPT_CHARS", "SEARCH_QUERY_CHARS"))
        for fn in ("_search_request", "_site_of", "_plain_snippet", "tavily_search",
                   "brave_search", "_url_key", "merge_search_results",
                   "fetch_fresh_sources", "search_query_for", "fresh_pages_given",
                   "format_fresh_sources", "_with_fresh_sources"):
            load_function(fn, ns)
        return ns

    @staticmethod
    def _fake_urlopen(routes, seen):
        class _Resp:
            def __init__(self, body): self.body = body
            def read(self): return json.dumps(self.body).encode("utf-8")
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def urlopen(req, timeout=None):
            seen.append(req)
            for prefix, body in routes.items():
                if req.full_url.startswith(prefix):
                    if isinstance(body, Exception):
                        raise body
                    return _Resp(body)
            raise AssertionError(req.full_url)
        return urlopen

    def test_both_engines_at_once_merged_without_duplicates(self):
        import urllib.error
        ns = self._search_ns()
        seen = []
        tavily = {"results": [
            {"title": "Strait update", "url": "https://www.reuters.com/x?utm_source=feed",
             "content": "Ships moved through the strait today, officials said.",
             "published_date": "2026-10-02T09:00"},
            {"title": "Oil", "url": "https://ft.com/oil", "content": "Prices rose."}],
            "usage": {"credits": 2}}
        brave = {"web": {"results": [
            {"title": "<strong>Hormuz</strong> news", "url": "https://apnews.com/y",
             "description": "Navy <strong>ships</strong> &amp; more",
             "extra_snippets": ["Second bit."], "page_age": "2026-10-01T00"},
            {"title": "Reuters", "url": "https://reuters.com/x/", "description": "Short."}]}}
        with patch("urllib.request.urlopen", self._fake_urlopen(
                {"https://api.tavily.com": tavily, "https://api.search.brave.com": brave}, seen)):
            pack = ns["fetch_fresh_sources"]({"Tavily": "tk", "Brave": "bk"}, "hormuz", "advanced")
        tav = next(r for r in seen if r.full_url.startswith("https://api.tavily.com"))
        brv = next(r for r in seen if r.full_url.startswith("https://api.search.brave.com"))
        body = json.loads(tav.data.decode("utf-8"))
        self.assertEqual(tav.get_header("Authorization"), "Bearer tk")
        self.assertEqual((body["search_depth"], body["max_results"], body["chunks_per_source"]),
                         ("advanced", 8, 3))
        self.assertEqual(brv.get_header("X-subscription-token"), "bk")
        self.assertIn("extra_snippets=true", brv.full_url)
        self.assertEqual(pack["service"], "Tavily + Brave")
        self.assertEqual(pack["counts"], {"Tavily": 2, "Brave": 2})
        self.assertEqual(pack["both"], 1)                         # reuters.com/x found twice
        self.assertEqual([r["site"] for r in pack["results"]],
                         ["reuters.com", "apnews.com", "ft.com"])  # interleaved, de-duplicated
        self.assertEqual(pack["results"][0]["found_by"], ["Tavily", "Brave"])
        self.assertIn("officials said", pack["results"][0]["text"])   # longer excerpt kept
        self.assertEqual(pack["results"][1]["text"], "Navy ships & more … Second bit.")
        self.assertAlmostEqual(pack["cost"], 0.016 + 0.005)
        self.assertEqual(len(pack["calls"]), 2)

        seen.clear()
        err = urllib.error.HTTPError("https://api.tavily.com/search", 432, "x", {}, None)
        with patch("urllib.request.urlopen", self._fake_urlopen(
                {"https://api.tavily.com": err, "https://api.search.brave.com": brave}, seen)):
            pack = ns["fetch_fresh_sources"]({"Tavily": "tk", "Brave": "bk"}, "hormuz", "basic")
        self.assertEqual(pack["service"], "Brave")
        self.assertEqual(pack["errors"], ["Tavily returned HTTP 432"])
        self.assertEqual([c["model"] for c in pack["calls"]], ["Brave"])   # the failed call billed nothing
        tav = next(r for r in seen if r.full_url.startswith("https://api.tavily.com"))
        self.assertNotIn("chunks_per_source", json.loads(tav.data.decode("utf-8")))  # basic depth

        none = ns["fetch_fresh_sources"]({}, "q")
        self.assertEqual((none["service"], none["results"]), (None, []))

    def test_pack_text_query_and_message_injection(self):
        ns = self._search_ns()
        pack = {"service": "Tavily", "results": [
            {"title": "T", "url": "https://a.com/1", "site": "a.com", "date": "2026-10-02",
             "text": "x" * 5000}] * 20}
        text = ns["format_fresh_sources"](pack)
        self.assertIn("[1] T — a.com — 2026-10-02", text)
        self.assertIn("name its site in parentheses", text)
        self.assertLess(len(text), 40_000 + 1_000)
        msgs = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "a"},
                {"role": "user", "content": "now"}]
        out = ns["_with_fresh_sources"](msgs, "PACK")
        self.assertEqual(out[2]["content"], "now\n\n---\nPACK")
        self.assertEqual(msgs[2]["content"], "now")                       # history untouched
        self.assertEqual(out[0]["content"], "first")
        q = ns["search_query_for"]
        self.assertEqual(q("And the submarines?", "US navy in the Strait of Hormuz"),
                         "US navy in the Strait of Hormuz — And the submarines?")
        self.assertEqual(q("x" * 1000), "x" * 380)

    def _pack_worker(self, mode, needs_web=None):
        sent = {}
        fetched = []

        def fake_run_one(provider, key, messages, cfg, web):
            if key == "synth":
                return SpeedLayerV378Tests._ok(provider, "draft")
            sent[provider] = (messages[0]["content"] if messages else "", web)
            return SpeedLayerV378Tests._ok(provider, f"{provider} answer")

        extra = {}
        names = {"_with_fresh_sources"}
        if needs_web is not None:
            extra["jev_route"] = lambda *a: {"needs_web": needs_web, "preset": "Light"}
            extra["jev_check_answer"] = lambda *a: {"verdict": "answers", "confidence": 0.9}
            extra["jev_claim_check"] = lambda *a: {"claims": [], "clean": None}
            extra.update(self._consts("JEV_HOLD_VERDICTS", "JEV_HOLD_CONFIDENCE"))
            names.add("jev_should_hold")
        extra.update(self._consts("JEV_FRESH_THRESHOLD", "JEV_ROUTE_WAIT_S"))

        def fake_fetch(keys, query, *_depth):
            fetched.append(query)
            return {"service": "Tavily",
                    "results": [{"title": "t", "url": "u", "site": "s", "date": "", "text": "x"}],
                    "calls": [{"model": "Tavily", "secs": 0.2, "cost": 0.016}], "errors": []}

        ns = SpeedLayerV378Tests._worker_ns(
            self, fake_run_one, extra=names,
            build_synth_prompt=lambda q, good, w, c, p: ("pool", {"A": "A"}),
            fetch_fresh_sources=fake_fetch,
            format_fresh_sources=lambda pack: "FRESH",
            fresh_pages_given=lambda pack: [dict(r, block="b") for r in pack["results"]],
            **extra)
        plan = SpeedLayerV378Tests._plan(["Claude", "DeepSeek", "Kimi"],
                                        fresh={"keys": {"Tavily": "t"}, "mode": mode,
                                               "query": "q"})
        for n in ("Claude", "DeepSeek", "Kimi"):
            plan["sources"][n]["messages"] = [{"role": "user", "content": "turn"}]
        plan["sources"]["Claude"]["web"] = True                          # primary: own search
        plan["sources"]["DeepSeek"].update(pack=True, pack_web=False)
        plan["sources"]["Kimi"].update(pack=True, pack_web=True)
        if needs_web is not None:
            plan["jev"] = {"key": "k", "mode": "shadow", "today": ""}
        reg = SpeedLayerV378Tests._reg("j")
        ns["_bg_worker"](reg, "j", plan)
        return reg["jobs"]["j"], sent, fetched

    def test_web_mode_gives_the_pages_only_to_sources_whose_web_is_on(self):
        job, sent, fetched = self._pack_worker("web")
        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertEqual(fetched, ["q"])
        self.assertEqual(sent["Kimi"], ("turn\n\n---\nFRESH", False))   # own search stays off
        self.assertEqual(sent["DeepSeek"][0], "turn")
        self.assertEqual(sent["Claude"], ("turn", True))                 # primary untouched
        self.assertEqual(job["fresh"]["for"], ["Kimi"])
        self.assertEqual(job["fresh_calls"][0]["cost"], 0.016)
        self.assertEqual(job["fresh"]["results"][0]["text"], "x")
        self.assertEqual(job["fresh"]["found"], 1)

    def test_pages_given_match_the_prompt_and_overlap_stats(self):
        ns = self._search_ns()
        pack = {"service": "Tavily + Brave", "results": [
            {"title": f"T{i}", "url": f"https://a.com/{i}", "site": "a.com", "date": "",
             "found_by": ["Tavily", "Brave"] if i == 0 else ["Brave"], "text": "y" * 9000}
            for i in range(30)]}
        given = ns["fresh_pages_given"](pack)
        text = ns["format_fresh_sources"](pack)
        self.assertLess(len(given), 30)                        # the budget cut is recorded
        self.assertTrue(all(g["block"] in text for g in given))
        self.assertEqual(len(given[0]["text"]), 3000)          # the excerpt as read
        self.assertIn("found by both engines", given[0]["block"])
        stats = load_function("fresh_source_stats", {})([
            {"synth": {"thread": [
                {"role": "assistant", "fresh_sources": {"status": "done", "secs": 2.0,
                 "counts": {"Tavily": 8, "Brave": 10}, "both": 3},
                 "helpers": [{"name": "Fresh sources · Tavily + Brave", "cost": 0.021}]},
                {"role": "assistant", "fresh_sources": {"status": "skipped"}}]}},
            {"research": {"thread": [
                {"role": "assistant", "fresh_sources": {"status": "done", "secs": 1.0,
                 "counts": {"Brave": 9}, "both": 0}}]}}])
        self.assertEqual((stats["turns"], stats["both_turns"]), (2, 1))
        self.assertEqual(stats["found"], {"Tavily": 8, "Brave": 19})
        self.assertAlmostEqual(stats["cost"], 0.021)
        line = load_function("fresh_stats_line", {})(stats)
        self.assertIn("3.0 pages found by both per turn", line)
        self.assertIn("38% of the smaller engine's results", line)

    def test_jev_gate_and_always(self):
        job, sent, _ = self._pack_worker("jev", needs_web=0.8)
        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertEqual(job["fresh"]["for"], ["DeepSeek", "Kimi"])
        self.assertIn("Jev: current facts needed (0.80)", job["fresh"]["reason"])
        self.assertTrue(sent["DeepSeek"][0].endswith("FRESH"))
        job, sent, _ = self._pack_worker("jev", needs_web=0.1)
        self.assertEqual(job["fresh"]["for"], ["Kimi"])                  # web-on source still gets it
        self.assertEqual(sent["DeepSeek"][0], "turn")
        job, sent, _ = self._pack_worker("always")
        self.assertEqual(job["fresh"]["for"], ["DeepSeek", "Kimi"])

    def test_uses_shared_sources_and_parking_release(self):
        ns = {"st": fake_st({"k_pack_grok": True, "k_pack_cla": False})}
        ns.update(literal_assignments("PACK_KEYS"))
        ns["shared_search_ready"] = lambda keys=None: True
        uses = load_function("uses_shared_sources", ns)
        self.assertTrue(uses("Grok")); self.assertFalse(uses("Claude"))
        self.assertFalse(uses("DeepSeek"))                           # 🔎 not switched on
        self.assertFalse(uses("Grok", ready=False))
        no_web = ("DeepSeek", "Qwen", "Mistral", "NVIDIA")
        pbd = load_function("pages_by_default",
                            {**literal_assignments("WEBSRC_SHARED_DEFAULT"),
                             "NO_WEB_PROVIDERS": no_web})
        self.assertEqual([p for p in ("Claude", "ChatGPT", "Gemini", "Grok", "Meta", "DeepSeek",
                                      "Kimi", "Qwen", "MiMo", "GLM", "MiniMax", "Mistral",
                                      "NVIDIA") if pbd(p)],
                         ["Grok", "DeepSeek", "Kimi", "Qwen", "MiniMax", "Mistral", "NVIDIA"])
        defaults = literal_assignments("DEFAULTS")["DEFAULTS"]
        keys = literal_assignments("PACK_KEYS")["PACK_KEYS"]
        self.assertEqual(set(keys), set(literal_assignments("PROVIDERS")["PROVIDERS"]))
        self.assertTrue(all(defaults[k] is False for k in keys.values()))
        def parked_with(ready, pack):
            pns = {"st": fake_st({"k_pack_ds": pack, "k_fresh_mode": "web"}),
                   "any_source_web_on": lambda: True, "NO_WEB_PROVIDERS": ("DeepSeek",),
                   "shared_search_ready": lambda: ready, "FRESH_MODE_CODES": {}}
            pns.update(literal_assignments("PACK_KEYS"))
            load_function("_no_web_gets_pages", pns)
            return load_function("source_parked", pns)("DeepSeek")
        self.assertFalse(parked_with(True, True))
        self.assertTrue(parked_with(True, False))
        self.assertTrue(parked_with(False, True))

    def test_global_web_switch_hands_out_pages_and_own_search(self):
        providers = literal_assignments("PROVIDERS")["PROVIDERS"]
        state = {"k_web_all": True, "k_syn_provider": "Claude", "k_fu_provider": "Claude",
                 "k_adj_provider": "ChatGPT", "k_qwen_model": "q"}
        ns = {"st": fake_st(state), "shared_search_ready": lambda keys=None: True,
              "_SRC_WEB_KEY": {p: s["web_key"] for p, s in providers.items() if s["web_key"]},
              "NO_WEB_PROVIDERS": ("DeepSeek", "Qwen", "Mistral", "NVIDIA"),
              "WEB_TIER_FLOOR": {"Grok": "Balanced"}, "_note_web_floor_crossed": lambda p: None,
              "_source_qwen_web_supported": lambda: True,
              "_evaluator_qwen_web_supported": lambda prefix: True}
        ns.update(literal_assignments("PACK_KEYS", "WEBSRC_SHARED_DEFAULT"))
        load_function("pages_by_default", ns)
        flip = load_function("_set_all_source_web", ns)
        flip()
        self.assertTrue(state["k_pack_grok"]); self.assertFalse(state["k_web_grok"])
        self.assertTrue(state["k_pack_ds"]); self.assertTrue(state["k_pack_nv"])
        self.assertTrue(state["k_pack_kimi"]); self.assertFalse(state["k_web_kimi"])
        self.assertTrue(state["k_web_cla"]); self.assertFalse(state["k_pack_cla"])
        self.assertTrue(state["k_web_mimo"]); self.assertTrue(state["k_web_glm"])
        self.assertTrue(state["k_syn_web"]); self.assertTrue(state["k_adj_web"])
        state["k_web_all"] = False
        flip()
        self.assertFalse(any(state[k] for k in ns["PACK_KEYS"].values()))
        self.assertFalse(state["k_web_cla"]); self.assertFalse(state["k_syn_web"])
        # Without a search key the old behaviour stands: own search for all who have one.
        ns["shared_search_ready"] = lambda keys=None: False
        flip = load_function("_set_all_source_web", ns)
        state["k_web_all"] = True
        flip()
        self.assertTrue(state["k_web_grok"]); self.assertFalse(state["k_pack_grok"])

    def test_prompt_cache_marking_and_pricing(self):
        ns = {"_inline_text_files": lambda c, f: c, "_binary_files": lambda m: []}
        build = load_function("_claude_messages", ns)
        out = build([{"role": "user", "content": "evidence", "cache": True},
                     {"role": "assistant", "content": "draft"},
                     {"role": "user", "content": "review"}])
        self.assertEqual(out[0]["content"], [{"type": "text", "text": "evidence",
                                              "cache_control": {"type": "ephemeral"}}])
        self.assertEqual(out[2]["content"], "review")
        ns = {"PRICES": {"claude-x": (4.0, 20.0), "gpt-6.1-sol": (2.0, 10.0), "other": (1.0, 1.0)},
              "PRIORITY_PRICES": {}}
        ns.update(literal_assignments("CLAUDE_CACHE_WRITE", "CLAUDE_CACHE_READ",
                                      "CACHED_INPUT_PRICES", "CLAUDE_CACHE_READ_BY_MODEL"))
        load_function("cost_of", ns)
        cwc = load_function("cost_with_cache", ns)
        base = cwc("claude-x", 120_000, 0)
        self.assertAlmostEqual(base, 0.48)
        # Claude: 100k tokens read from cache at 0.1x, 10k written at 1.25x.
        self.assertAlmostEqual(cwc("claude-x", 120_000, 0, cache={"read": 100_000, "write": 10_000}),
                               0.48 - 100_000 * 0.9 * 4 / 1e6 + 10_000 * 0.25 * 4 / 1e6)
        self.assertAlmostEqual(cwc("gpt-6.1-sol", 100_000, 0, cache={"read": 80_000}),
                               0.2 - 80_000 * 1.9 / 1e6)
        self.assertAlmostEqual(cwc("other", 100_000, 0, cache={"read": 80_000}), 0.1)  # unknown rate

    def test_disposition_follow_on_and_fresh_cost_row(self):
        disp = load_function("build_disposition_prompt", {
            "state_capsule_instruction": lambda research=False: "STATE CAPSULE"})
        text = disp("Q", "DRAFT-TEXT", "REPORT", {"A": "EVIDENCE-TEXT"}, {"A": "A"}, "Light",
                    follow_on=True)
        self.assertIn("REPORT", text)
        self.assertNotIn("DRAFT-TEXT", text); self.assertNotIn("EVIDENCE-TEXT", text)
        self.assertIn("COMPLETE revised final answer", text)
        rows = load_function("helper_cost_rows", literal_assignments("CEREBRAS_PREVIEW_MODEL"))({
            "fresh_calls": [{"model": "Tavily", "secs": 0.5, "cost": 0.016, "in_tok": None,
                             "out_tok": None}]})
        self.assertEqual(rows[0]["name"], "Fresh sources · Tavily")
        self.assertAlmostEqual(rows[0]["cost"], 0.016)


class MiMoProviderTests(unittest.TestCase):

    def _ns(self, captured):
        class FakeCompletions:
            def create(self, **kwargs):
                captured["request"] = kwargs
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(
                        content=captured.get("reply", "answer"),
                        reasoning_content=captured.get("reasoning")),
                        finish_reason=captured.get("finish", "stop"))],
                    usage=SimpleNamespace(prompt_tokens=20, completion_tokens=9))
        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured["client"] = kwargs
                self.chat = SimpleNamespace(completions=FakeCompletions())
        ns = {"OpenAI": FakeOpenAI, "re": re,
              "_oai_chat_messages": lambda messages, pdf_ok, image_ok=True: (
                  captured.__setitem__("image_ok", image_ok) or messages),
              "_request_timeout": lambda *a: 480.0}
        ns.update(literal_assignments("MIMO_URL", "MIMO_SEARCH_KEYWORDS"))
        load_function("_content_text", ns)
        load_function("_mimo_call", ns)
        return ns

    def test_tables_prices_and_membership(self):
        v = literal_assignments("MIMO_MODELS", "EVAL_PROVIDERS", "MODEL_ORDER", "NO_WEB_PROVIDERS",
                                "DEFAULTS", "PROVIDER_ORIGIN", "KEY_MAP", "ICON", "VOICE_KEY",
                                "BILLING_LINKS", "PROVIDER_LINKS", "REASONING_EXPOSURE")
        self.assertEqual(v["MIMO_MODELS"], {
            "V2.6 Pro (flagship)": "mimo-v2.6-pro",
            "V2.6 Flash (economy)": "mimo-v2.6-flash",
            "V2.6 Pro Ultraspeed (fast · 10× price)": "mimo-v2.6-pro-ultraspeed"})
        for table in ("EVAL_PROVIDERS", "MODEL_ORDER", "PROVIDER_ORIGIN", "ICON", "VOICE_KEY",
                      "BILLING_LINKS", "REASONING_EXPOSURE"):
            self.assertIn("MiMo", v[table], table)
        self.assertEqual(v["KEY_MAP"]["MiMo"], "xiaomi")
        self.assertIn("xiaomi", v["PROVIDER_LINKS"])
        self.assertNotIn("MiMo", v["NO_WEB_PROVIDERS"])     # hosted web_search on its endpoint
        self.assertEqual(len(v["MODEL_ORDER"]), 13)
        self.assertEqual(v["MODEL_ORDER"][:5], ["Claude", "ChatGPT", "Gemini", "Grok", "Meta"])
        self.assertFalse(v["DEFAULTS"]["k_en_mimo"]); self.assertFalse(v["DEFAULTS"]["k_web_mimo"])
        for prefix in ("k_syn", "k_fu", "k_adj", "k_res"):
            self.assertEqual(v["DEFAULTS"][f"{prefix}_mimo_model"], "V2.6 Pro (flagship)")
        prices = literal_dict_string_entries("PRICES")
        self.assertEqual(prices["mimo-v2.6-pro"], (0.435, 0.87))
        self.assertEqual(prices["mimo-v2.6-flash"], (0.14, 0.28))
        self.assertEqual(prices["mimo-v2.6-pro-ultraspeed"], (4.35, 8.70))

    def test_preset_ladders(self):
        v = literal_assignments("PRESETS", "SYNTH_PRESETS", "PRESET_SOURCES")
        tiers = ("Fast", "Light", "Balanced", "Thorough", "Deep")
        self.assertEqual([v["PRESETS"][t]["k_mimo_model"] for t in tiers],
                         ["V2.6 Flash (economy)", "V2.6 Flash (economy)", "V2.6 Pro (flagship)",
                          "V2.6 Pro (flagship)", "V2.6 Pro (flagship)"])
        self.assertEqual([v["PRESETS"][t]["k_mimo_think"] for t in tiers],
                         [False, True, True, True, True])
        for t in tiers:
            self.assertEqual(v["SYNTH_PRESETS"][t]["MiMo"],
                             {"k_syn_mimo_model": "V2.6 Pro (flagship)", "k_syn_mimo_think": True})
            self.assertIn("k_en_mimo", v["PRESET_SOURCES"][t])

    def test_mimo_request_shapes(self):
        captured = {"reply": "<think>x</think>The answer.", "reasoning": "weighing"}
        ns = self._ns(captured)
        text, itok, otok = ns["_mimo_call"]("MiMo", "k", [], {"model_id": "mimo-v2.6-pro",
                                                              "thinking": True}, False)
        self.assertEqual(captured["client"]["base_url"], "https://api.xiaomimimo.com/v1")
        req = captured["request"]
        self.assertEqual(req["model"], "mimo-v2.6-pro")
        self.assertEqual(req["extra_body"], {"thinking": {"type": "enabled"}})
        self.assertIn("max_completion_tokens", req)
        self.assertNotIn("temperature", req); self.assertNotIn("tools", req)
        self.assertTrue(captured["image_ok"])
        self.assertEqual((text, itok, otok), ("The answer.", 20, 9))
        captured = {"reply": "ok"}
        ns = self._ns(captured)
        ns["_mimo_call"]("MiMo", "k", [], {"model_id": "mimo-v2.6-flash", "thinking": False}, True)
        req = captured["request"]
        self.assertEqual(req["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(req["tools"], [{"type": "web_search", "max_keyword": 3,
                                         "force_search": True}])
        captured = {"reply": "   "}
        ns = self._ns(captured)
        with self.assertRaises(RuntimeError):
            ns["_mimo_call"]("MiMo", "k", [], {"model_id": "mimo-v2.6-pro"}, False)
        captured = {"reply": "partial", "finish": "length"}
        ns = self._ns(captured)
        text, *_ = ns["_mimo_call"]("MiMo", "k", [], {"model_id": "mimo-v2.6-pro"}, False)
        self.assertIn("truncated", text)


class CodeReviewFixTests(unittest.TestCase):

    def test_responses_call_keeps_only_text_after_the_last_tool_call(self):
        captured = {}
        def _item(kind, text=None):
            c = [SimpleNamespace(text=text)] if text is not None else []
            return SimpleNamespace(type=kind, content=c)
        class FakeResponses:
            def create(self, **kw):
                captured["kw"] = kw
                return SimpleNamespace(
                    output_text="Let me search for that. Now composing.# Real Answer body",
                    output=[_item("message", "Let me search for that. "),
                            _item("web_search_call"),
                            _item("message", "Now composing."),
                            _item("web_search_call"),
                            _item("reasoning"),
                            _item("message", "# Real Answer body")],
                    status="completed",
                    usage=SimpleNamespace(input_tokens=5, output_tokens=6))
        client = SimpleNamespace(responses=FakeResponses())
        call = load_function("_responses_call", {})
        text, itok, otok = call(client, "gpt-5.6-terra", [], "low")
        self.assertEqual(text, "# Real Answer body")     # narration sliced off
        self.assertEqual((itok, otok), (5, 6))
        # no tool call at all -> the full text as before
        class FakeNoTools:
            def create(self, **kw):
                return SimpleNamespace(output_text="Plain answer",
                                       output=[_item("message", "Plain answer")],
                                       status="completed",
                                       usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        text, *_ = call(SimpleNamespace(responses=FakeNoTools()), "m", [], "low")
        self.assertEqual(text, "Plain answer")

    def test_chat_completion_helper_raises_on_empty_and_marks_truncation(self):
        def _client(content, finish, usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4)):
            class C:
                def create(self, **kw):
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=content), finish_reason=finish)],
                        usage=usage)
            return SimpleNamespace(chat=SimpleNamespace(completions=C()))
        ns = {}
        load_function("_content_text", ns)
        helper = load_function("_chat_completion_text", ns)
        with self.assertRaises(RuntimeError):
            helper(_client(None, "content_filter"), {"model": "gpt-5.6-luna"})
        with self.assertRaises(RuntimeError):     # reasoning ate the whole cap
            helper(_client("", "length"), {"model": "grok-4.6"})
        text, itok, otok = helper(_client("Cut off mid", "length"), {"model": "m"})
        self.assertTrue(text.startswith("Cut off mid"))
        self.assertIn("truncated", text)
        self.assertEqual((itok, otok), (3, 4))
        text, itok, otok = helper(_client("fine", "stop", usage=None), {"model": "m"})
        self.assertEqual((text, itok, otok), ("fine", None, None))   # no usage -> no crash

    def test_timeout_classifier_covers_the_review_gaps(self):
        timeout = load_function("_request_timeout", {
            "LIGHT_REQUEST_TIMEOUT": 150.0, "HEAVY_REQUEST_TIMEOUT": 480.0,
            "CLAUDE_ALWAYS_THINK": set(), "KIMI_ALWAYS_THINK": {"kimi-k3"},
            "GLM_ALWAYS_THINK": {"glm-5.3", "glm-5.3-flash"},
            "MINIMAX_THINK_OFF_OK": {"MiniMax-M3"},
        })
        self.assertEqual(timeout("Meta", {"effort": "xhigh"}, False), 480.0)
        self.assertEqual(timeout("Meta", {"effort": "minimal"}, False), 150.0)
        self.assertEqual(timeout("MiniMax", {"model_id": "MiniMax-M2.7", "thinking": False}, False), 480.0)
        self.assertEqual(timeout("MiniMax", {"model_id": "MiniMax-M3", "thinking": False}, False), 150.0)
        self.assertEqual(timeout("Grok", {"mode": "Fast"}, True), 480.0)
        self.assertEqual(timeout("Grok", {"mode": "Fast"}, False), 150.0)
        self.assertEqual(timeout("Kimi", {"model_id": "kimi-k2.6", "thinking": True}, False), 480.0)


class ChatTitleTests(unittest.TestCase):
    def test_chat_title_comes_from_the_first_answer_heading(self):
        ns = {}
        load_function("answer_heading", ns)
        title = load_function("chat_display_title", ns)
        nl = chr(10)
        entry = {"mode": "synthesis", "prompt": "what was the war of 1812 about?",
                 "synth": {"thread": [
                     {"role": "user", "content": "x"},
                     {"role": "assistant",
                      "content": "# The War of 1812: Causes and Legacy" + nl + nl + "Body..."}]}}
        self.assertEqual(title(entry), "The War of 1812: Causes and Legacy")
        entry["synth"]["thread"][1]["content"] = "Plain answer without a heading."
        self.assertEqual(title(entry), "what was the war of 1812 about?")
        self.assertEqual(title({"mode": "synthesis", "prompt": "  hello   there "}), "hello there")
        research = {"mode": "research", "prompt": "q", "research": {"thread": [
            {"role": "assistant", "content": "## Comparative Review: Shaman Specs"}]}}
        self.assertEqual(title(research), "Comparative Review: Shaman Specs")


class HistoryHelperTests(unittest.TestCase):
    def test_history_display_date_and_entry_meta(self):
        display = load_function("history_display_date", {"datetime": datetime})
        self.assertEqual(display("2026-08-20T10:30:00"), "Aug 20, 2026 · 10:30 AM")
        self.assertEqual(display("garbage"), "garbage")
        self.assertEqual(display(None), "Unknown date")

        meta = load_function("history_entry_meta", {})
        entry = {"mode": "synthesis",
                 "threads": {"ChatGPT": [{}, {}], "Claude": [{}]},
                 "synth": {"label": "Claude Sonnet 5"}}
        self.assertEqual(meta(entry), "2 sources · 3 source messages · Claude Sonnet 5")

        research = {"mode": "research",
                    "threads": {"Gemini": [{}]},
                    "research": {"label": "Gemini 3.5 Pro"},
                    "synth": {"label": "ignored"}}
        self.assertEqual(meta(research), "1 source · 1 source messages · Gemini 3.5 Pro")

        v2 = {"results": {"ChatGPT": {"text": "a"}}}
        self.assertEqual(meta(v2), "1 source")
        self.assertEqual(meta({}), "0 sources")

    def test_strip_files_deep_and_v2_conversion(self):
        strip = load_function("_strip_files_deep", {})
        payload = {
            "threads": {"ChatGPT": [
                {"role": "user", "content": "Q",
                 "files": [{"name": "a.png", "kind": "image", "b64": "Zm9v"}]},
                {"role": "assistant", "content": "A"},
            ]},
            "files": None,
        }
        cleaned = strip(payload)
        self.assertEqual(cleaned["files"], [])
        stripped_file = cleaned["threads"]["ChatGPT"][0]["files"][0]
        self.assertEqual(stripped_file, {"name": "a.png", "kind": "image", "stripped": True})
        self.assertNotIn("b64", stripped_file)
        # original untouched
        self.assertIn("b64", payload["threads"]["ChatGPT"][0]["files"][0])

        convert = load_function("v2_entry_to_session", {})
        threads = convert({"prompt": "Old",
                           "results": {"Claude": {"text": "old answer", "secs": 2.0}},
                           "model_id": {"Claude": "claude-x"},
                           "settings": {"Claude": "effort high"}})
        self.assertEqual(threads["Claude"][0], {"role": "user", "content": "Old"})
        reply = threads["Claude"][1]
        self.assertEqual(reply["content"], "old answer")
        self.assertEqual(reply["model_id"], "claude-x")
        self.assertEqual(reply["settings"], "effort high")

    def test_chat_topic_icon_and_day_group(self):
        ns = literal_assignments("CHAT_TOPIC_ICONS")
        icon = load_function("chat_topic_icon", ns)
        self.assertEqual(icon("World of Warcraft raid help"), "\U0001F3AE")
        self.assertEqual(icon("Mortgage refinance options"), "\U0001F4B0")
        self.assertEqual(icon("zzz nothing matches", "research"), "\U0001F52C")
        self.assertEqual(icon(None), "⭐")

        group = load_function("chat_day_group", {"datetime": datetime})
        now = datetime.now()
        self.assertEqual(group(now.isoformat(timespec="seconds")), "Today")
        self.assertEqual(group((now - timedelta(days=1)).isoformat()), "Yesterday")
        self.assertEqual(group((now - timedelta(days=2)).isoformat()), "Two days ago")
        self.assertEqual(group((now - timedelta(days=3)).isoformat()), "Three days ago")
        self.assertEqual(group((now - timedelta(days=5)).isoformat()), "Previous 7 days")
        self.assertEqual(group((now - timedelta(days=10)).isoformat()), "Older than 7 days")
        self.assertEqual(group((now - timedelta(days=30)).isoformat()), "Older than 14 days")
        self.assertEqual(group((now - timedelta(days=45)).isoformat()), "Older than 30 days")
        self.assertEqual(group((now - timedelta(days=75)).isoformat()), "Older than 60 days")
        self.assertEqual(group((now - timedelta(days=120)).isoformat()), "Older than 90 days")
        self.assertEqual(group((now - timedelta(days=200)).isoformat()), "Older than 6 months")
        self.assertEqual(group((now - timedelta(days=400)).isoformat()), "Older than a year")
        self.assertEqual(group("garbage"), "Undated")
        self.assertEqual(group(None), "Undated")

    def _session_namespace(self, tmp):
        ns = load_history_helpers(Path(tmp) / "history.json")
        ns.update(literal_assignments("ICON", "EVAL_MEMO_AUDIO_NAMES",
                                      "VIEW_SYNTH", "VIEW_RES"))
        ns["st"] = fake_st()
        for name in ("_strip_files_deep", "v2_entry_to_session", "bump_chat_opens",
                     "_land_on_latest_answer",
                     "persist_session", "load_session_into_state", "delete_session"):
            load_function(name, ns)
        return ns

    def test_persist_load_and_delete_session_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            ns = self._session_namespace(tmp)
            state = ns["st"].session_state
            sid = "2026-08-20T10:00:00"
            state.update({
                "session_id": sid, "prompt": "First question",
                "threads": {"ChatGPT": [
                    {"role": "user", "content": "First question",
                     "files": [{"name": "chart.png", "kind": "image", "b64": "Zm9v"}]},
                    {"role": "assistant", "content": "Answer"},
                ]},
                "synth": {"label": "Claude Sonnet 5", "thread": []},
            })
            ns["persist_session"]()
            saved = ns["load_history"]()
            self.assertEqual(len(saved), 1)
            entry = saved[0]
            self.assertEqual(entry["mode"], "synthesis")
            self.assertEqual(entry["threads"]["ChatGPT"][0]["files"],
                             [{"name": "chart.png", "kind": "image", "stripped": True}])

            def _decorate(history):
                history[0]["pinned"] = True
                history[0]["opens"] = 2
                history[0]["last_open"] = "2026-08-19T09:00:00"
                return history
            ns["update_history"](_decorate)

            state["prompt"] = "First question (edited)"
            ns["persist_session"]()
            saved = ns["load_history"]()
            self.assertEqual(len(saved), 1)
            entry = saved[0]
            self.assertEqual(entry["prompt"], "First question (edited)")
            self.assertTrue(entry["pinned"])
            self.assertEqual(entry["opens"], 2)
            self.assertEqual(entry["last_open"], "2026-08-19T09:00:00")

            state.clear()
            ns["load_session_into_state"](entry)
            self.assertEqual(state["session_id"], sid)
            self.assertEqual(state["prompt"], "First question (edited)")
            self.assertEqual(state["main_view"], ns["VIEW_SYNTH"])
            self.assertEqual(state["sp_view"], "⭐ Answer")
            reloaded = ns["load_history"]()[0]
            self.assertEqual(reloaded["opens"], 3)
            self.assertNotEqual(reloaded["last_open"], "2026-08-19T09:00:00")

            state.clear()
            state.update({"res_session_id": "2026-08-20T11:00:00", "res_prompt": "R?",
                          "res_threads": {"Gemini": []},
                          "research": {"label": "Gemini 3.5 Pro", "thread": []}})
            ns["persist_session"]("research")
            saved = ns["load_history"]()
            self.assertEqual({e["mode"] for e in saved}, {"synthesis", "research"})
            research_entry = next(e for e in saved if e["mode"] == "research")

            state.clear()
            ns["load_session_into_state"](research_entry)
            self.assertEqual(state["main_view"], ns["VIEW_RES"])
            self.assertEqual(state["res_session_id"], "2026-08-20T11:00:00")

            state.clear()
            state.update({"session_id": sid, "threads": {}, "synth": None,
                          "prompt": "p", "audio_ChatGPT": b"stale"})
            ns["delete_session"](sid)
            remaining = ns["load_history"]()
            self.assertEqual([e["mode"] for e in remaining], ["research"])
            self.assertNotIn("session_id", state)
            self.assertNotIn("threads", state)

    def test_load_session_supports_v2_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            ns = self._session_namespace(tmp)
            state = ns["st"].session_state
            v2 = {"ts": "2026-08-18T08:00:00", "prompt": "Old",
                  "results": {"Claude": {"text": "old answer", "secs": 2.0}}}
            ns["load_session_into_state"](v2)
            self.assertEqual(state["threads"]["Claude"][1]["content"], "old answer")
            self.assertIsNone(state["synth"])
            self.assertEqual(state["sp_view"], "\U0001F9E9 The Sources")
            self.assertEqual(state["main_view"], ns["VIEW_SYNTH"])


class ProviderRuntimeHelperTests(unittest.TestCase):
    def test_is_credit_error_classifies_provider_errors(self):
        ns = load_credit_error()
        is_credit = ns["_is_credit_error"]
        OpenAIErr = ns["OpenAIRateLimitError"]
        AnthropicErr = ns["AnthropicAPIStatusError"]
        GenaiErr = ns["genai_errors"].APIError

        self.assertTrue(is_credit("ChatGPT", OpenAIErr("quota", code="insufficient_quota")))
        self.assertFalse(is_credit("ChatGPT", OpenAIErr("Rate limit reached",
                                                        code="rate_limit_exceeded")))
        self.assertTrue(is_credit("Claude", AnthropicErr("blocked", type="billing_error")))
        self.assertTrue(is_credit("Gemini", GenaiErr("exhausted", code=429,
                                                     status="RESOURCE_EXHAUSTED")))

        class DeepSeekErr(Exception):
            status_code = 402
        self.assertTrue(is_credit("DeepSeek", DeepSeekErr("blocked")))
        self.assertTrue(is_credit("Grok", ValueError("Your credit balance is too low")))
        self.assertFalse(is_credit("Grok", ValueError("boom")))

    def test_run_parallel_aggregates_results_and_quota_flags(self):
        outcomes = {
            "ChatGPT": {"text": "ok", "secs": 0.5, "provider": "ChatGPT",
                        "err": False, "credit_err": False, "web": True},
            "Grok": {"text": "Error from Grok", "secs": 0.1, "provider": "Grok",
                     "err": True, "credit_err": True, "web": False},
        }

        bodies, labels = [], []

        class FakeStatus:
            def empty(self):
                return SimpleNamespace(markdown=bodies.append)

            def update(self, label=None, state=None, expanded=None):
                labels.append(label)

        state = {"quota_err_ChatGPT": True}
        st = SimpleNamespace(session_state=state,
                             status=lambda label, expanded=True: FakeStatus())
        ns = {
            "st": st, "time": time, "ICON": {"ChatGPT": "C", "Grok": "G"},
            "ThreadPoolExecutor": ThreadPoolExecutor, "wait": wait,
            "FIRST_COMPLETED": FIRST_COMPLETED,
            "run_one": lambda name, key, messages, cfg, web: outcomes[name],
        }
        run_parallel = load_function("run_parallel", ns)

        jobs = {n: {"key": "k", "messages": [], "cfg": {}, "web": False}
                for n in outcomes}
        results = run_parallel(jobs, "Council run")

        self.assertEqual(results, outcomes)
        self.assertTrue(state.get("quota_err_Grok"))
        self.assertNotIn("quota_err_ChatGPT", state)
        self.assertTrue(labels[-1].startswith("✅ Council run — done in"))
        final_body = bodies[-1]
        self.assertIn("ChatGPT", final_body)
        self.assertIn("\U0001F310", final_body)          # web badge
        self.assertIn("⚠️\U0001F4B3", final_body)        # credit warning

    def _speech_namespace(self, state, keys, transcription_create=None,
                          speech_create=None):
        ns = load_credit_error()
        ns.update(literal_assignments("VOICE_KEY", "TTS1_FALLBACK_VOICES"))
        ns.update({
            "load_keys": lambda: keys,
            "OpenAI": make_fake_openai(transcription_create, speech_create),
            "st": fake_st(state),
            "LIGHT_REQUEST_TIMEOUT": 150.0,
        })
        load_function("_model_fallback_allowed", ns)
        return ns

    def test_transcribe_falls_back_and_tracks_quota(self):
        calls = []

        def create(model, file):
            calls.append(model)
            if model == "gpt-transcribe":
                raise RuntimeError("model_not_found: no access")
            return SimpleNamespace(text="hello world")

        state = {"quota_err_ChatGPT": True}
        ns = self._speech_namespace(state, {"openai": "sk-test"},
                                    transcription_create=create)
        transcribe = load_function("transcribe", ns)
        text, err = transcribe(b"wav")
        self.assertEqual(text, "hello world")
        self.assertIsNone(err)
        self.assertEqual(calls, ["gpt-transcribe", "gpt-4o-transcribe"])
        self.assertNotIn("quota_err_ChatGPT", state)

        ns = self._speech_namespace({}, {})
        transcribe = load_function("transcribe", ns)
        text, err = transcribe(b"wav")
        self.assertIsNone(text)
        self.assertIn("OpenAI key", err)

        def quota_create(model, file):
            raise RuntimeError("insufficient_quota")

        state = {}
        ns = self._speech_namespace(state, {"openai": "sk-test"},
                                    transcription_create=quota_create)
        transcribe = load_function("transcribe", ns)
        text, err = transcribe(b"wav")
        self.assertIsNone(text)
        self.assertIn("Transcription failed", err)
        self.assertTrue(state.get("quota_err_ChatGPT"))

    def test_speak_falls_back_to_supported_tts1_voice(self):
        calls = []

        def create(model, voice, input):
            calls.append((model, voice))
            if model == "gpt-4o-mini-tts":
                raise RuntimeError("model_not_found")
            return SimpleNamespace(content=b"mp3-bytes")

        state = {"k_v_syn": "marin", "quota_err_ChatGPT": True,
                 "audio_err_Synthesizer": "stale"}
        ns = self._speech_namespace(state, {"openai": "sk-test"},
                                    speech_create=create)
        speak = load_function("speak", ns)
        speak("Synthesizer", "Read this aloud")
        self.assertEqual(calls, [("gpt-4o-mini-tts", "marin"), ("tts-1", "alloy")])
        self.assertEqual(state["audio_Synthesizer"], b"mp3-bytes")
        self.assertNotIn("audio_err_Synthesizer", state)
        self.assertNotIn("quota_err_ChatGPT", state)

        state = {}
        ns = self._speech_namespace(state, {})
        speak = load_function("speak", ns)
        speak("Synthesizer", "text")
        self.assertIn("OpenAI key", state["audio_err_Synthesizer"])

        def broken(model, voice, input):
            raise RuntimeError("boom")

        state = {}
        ns = self._speech_namespace(state, {"openai": "sk-test"},
                                    speech_create=broken)
        speak = load_function("speak", ns)
        speak("ChatGPT", "text", cache_key="answer")
        self.assertIn("TTS failed", state["audio_err_answer"])


class ContextHelperTests(unittest.TestCase):
    def test_clip_context_and_state_capsule_helpers(self):
        ns = literal_assignments("STATE_CAPSULE_LIMIT", "STATE_BEGIN", "STATE_END")
        ns["re"] = re
        clip = load_function("_clip_context_text", ns)
        self.assertEqual(clip("short", 100), "short")
        self.assertEqual(clip("abcdef", 3), "abc")
        long_text = "H" * 400 + "T" * 400
        clipped = clip(long_text, 200)
        self.assertIn("[older detail compacted]", clipped)
        self.assertTrue(clipped.startswith("H"))
        self.assertTrue(clipped.endswith("T"))
        self.assertEqual(clip(None, 50), "")

        split = load_function("split_state_capsule", ns)
        capsule = f"Visible answer {ns['STATE_BEGIN']} durable facts {ns['STATE_END']} more"
        visible, captured_state = split(capsule)
        self.assertEqual(captured_state, "durable facts")
        self.assertNotIn(ns["STATE_BEGIN"], visible)
        self.assertIn("Visible answer", visible)
        self.assertEqual(split("plain"), ("plain", ""))

        capsule_fallback = load_function("fallback_state_capsule", ns)
        combined = capsule_fallback("prior", "question?", "conclusion")
        self.assertIn("Prior durable state:\nprior", combined)
        self.assertIn("Latest user turn:\nquestion?", combined)
        self.assertIn("Latest council conclusion:\nconclusion", combined)
        self.assertEqual(capsule_fallback("", "", ""), "")

    def test_next_turn_id_scans_turns_and_thread(self):
        next_turn_id = load_function("next_turn_id", {})
        self.assertEqual(next_turn_id(None), 0)
        self.assertEqual(next_turn_id({}), 0)
        record = {"turns": [{"turn_id": 3}, {"turn_id": "x"}],
                  "thread": [{"turn_id": 5}, {}]}
        self.assertEqual(next_turn_id(record), 6)

    def test_meta_caption_and_downloads_serialize_current_session(self):
        meta_caption = load_function("meta_caption", {})
        caption = meta_caption({"model_id": "claude-x", "settings": "effort high",
                                "web": True, "secs": 1.5, "in_tok": 100,
                                "out_tok": 20, "cost": 0.0012})
        self.assertIn("claude-x", caption)
        self.assertIn("\U0001F310 web", caption)
        self.assertIn("120 tok", caption)
        self.assertIn("$0.0012", caption)

        state = {
            "prompt": "P",
            "synth": {"label": "Claude Sonnet 5",
                      "thread": [{"role": "user", "content": "seed"},
                                 {"role": "user", "content": "apiF", "display": "F"},
                                 {"role": "assistant", "content": "final answer"}],
                      "legend": {"C": "ChatGPT"}},
            "threads": {"ChatGPT": [
                {"role": "user", "content": "P"},
                {"role": "assistant", "content": "gpt answer", "model_id": "gpt-x",
                 "settings": "low", "secs": 1.0, "in_tok": 10, "out_tok": 5,
                 "cost": 0.001, "web": True},
            ]},
        }
        ns = {"st": fake_st(state)}
        load_function("user_text_of", ns)
        load_function("meta_caption", ns)
        build_synth = load_function("build_synthesis_download", ns)
        text = build_synth()
        self.assertIn("PROMPT: P", text)
        self.assertIn("SYNTHESIS (Claude Sonnet 5)", text)
        self.assertIn("[SYNTHESIZER]\nfinal answer", text)
        self.assertIn("[YOU]\nF", text)
        self.assertNotIn("seed", text)
        self.assertIn("Legend: C=ChatGPT", text)
        self.assertIn("===== ChatGPT =====", text)
        self.assertIn("gpt answer", text)

        res_state = {
            "res_prompt": "RQ",
            "res_threads": {
                "Gemini": [{"role": "user", "content": "RQ"},
                           {"role": "assistant", "content": "gem answer",
                            "model_id": "gem-x", "settings": "bal", "secs": 2.0}],
                "Grok": [{"role": "user", "content": "RQ"}],
            },
            "research": {"label": "Claude Sonnet 5",
                         "thread": [{"role": "user", "content": "seed"},
                                    {"role": "assistant", "content": "evaluation"}],
                         "legend": {"G": "Gemini"}},
        }
        ns = {"st": fake_st(res_state)}
        load_function("user_text_of", ns)
        load_function("meta_caption", ns)
        build_res = load_function("build_research_download", ns)
        text = build_res()
        self.assertIn("RESEARCH QUESTION: RQ", text)
        self.assertIn("gem answer", text)
        self.assertNotIn("===== Grok", text)
        self.assertIn("EVALUATION (Claude Sonnet 5)", text)
        self.assertIn("[EVALUATOR]\nevaluation", text)
        self.assertIn("Legend: G=Gemini", text)


ALL_KEYS = {v: "sk" for v in literal_assignments("KEY_MAP")["KEY_MAP"].values()}


class FollowupOrchestrationTests(unittest.TestCase):
    def _make_followup(self, state, enabled, keys, captured):
        ns = literal_assignments("MODEL_ORDER", "KEY_MAP")
        model_order = ns["MODEL_ORDER"]
        ns.update({
            "st": fake_st(state),
            "ENABLED": enabled,
            "CFG": {n: {"dial": n} for n in model_order},
            "WEB": {n: False for n in model_order},
            "load_keys": lambda: keys,
            "adjudicator_stack_active": lambda: state.get("_adj_on", False),
            "current_preset_name": lambda: "Light",
            "build_working_context":
                lambda record, original, promotions, tier: f"CTX:{tier}",
            "build_source_turn_message":
                lambda question, context, continuation=False, research=False:
                    f"TURN:{question}|{context}|{continuation}|{research}",
            "evaluator_job": lambda prefix, msgs, web, maxsearch, **kwargs:
                ("Claude", "claude-sonnet-5", {"effort": "high"},
                 {"key": "anth", "web": web}),
            "eval_label": lambda prefix: "Claude Sonnet 5",
            "settings_str": lambda name, cfg, web: f"settings:{name}",
            "build_memo_job":
                lambda prefix, question, context, continuation, **kwargs: {"memo_for": prefix},
            "model_id_of": lambda name, cfg: f"id-{name}",
            "bg_start": captured.append,
        })
        load_function("eval_provider_of", ns)
        load_function("next_turn_id", ns)
        load_function("build_source_jobs", ns)
        load_function("build_eval_phase", ns)
        load_function("keyed_sources", ns)
        return load_function("start_council_followup", ns)

    def test_solo_followup_is_the_synthesis_default(self):
        captured = []
        state = {"synth": {"turns": [], "thread": []},
                 "k_fu_web": False, "k_fu_maxsearch": 3,
                 "session_id": "2026-08-20T10:00:00",
                 "source_promotions": [{"id": "p1", "status": "pending"},
                                       {"id": "p2", "status": "used"}]}
        followup = self._make_followup(state, {}, {"openai": "sk"}, captured)
        self.assertIsNone(followup("synthesis", "Next question", [], "fu_main"))
        plan = captured[0]
        self.assertEqual(plan["sources"], {})
        self.assertEqual(plan["phase2"]["kind"], "solo")
        self.assertEqual(plan["phase2"]["min_good"], 0)
        self.assertIsNone(plan["synth_memo"])
        self.assertIsNone(plan["phase3"])
        self.assertIsNone(plan["phase4"])
        self.assertTrue(plan["continuation"])
        self.assertEqual(plan["turn_id"], 0)
        self.assertEqual(plan["context"], "CTX:Light")
        self.assertEqual(plan["promotion_ids"], ["p1"])
        self.assertEqual(plan["session_id"], "2026-08-20T10:00:00")
        self.assertEqual(plan["user_msg"]["display"], "Next question")

    def test_context_mode_overrides_context_tier(self):
        captured = []
        state = {"synth": {"turns": [], "thread": []},
                 "k_fu_web": False, "k_fu_maxsearch": 3,
                 "k_context_mode": "Lean"}
        followup = self._make_followup(state, {}, {"openai": "sk"}, captured)
        self.assertIsNone(followup("synthesis", "Q", [], "fu_main"))
        self.assertEqual(captured[0]["context"], "CTX:Fast")

    def test_full_council_excludes_evaluator_and_adjudicator(self):
        captured = []
        state = {"synth": {"turns": [{"turn_id": 4}], "thread": []},
                 "k_full_council": True, "_adj_on": True,
                 "k_adj_provider": "ChatGPT", "k_adj_web": False,
                 "k_adj_maxsearch": 3,
                 "k_fu_web": False, "k_fu_maxsearch": 3}
        enabled = {n: True for n in
                   ("ChatGPT", "Claude", "Gemini", "Grok", "DeepSeek", "Qwen")}
        followup = self._make_followup(state, enabled, ALL_KEYS, captured)
        self.assertIsNone(followup("synthesis", "Q", [], "fu_main"))
        plan = captured[0]
        self.assertEqual(set(plan["sources"]),
                         {"Gemini", "Grok", "DeepSeek", "Qwen"})
        self.assertEqual(plan["turn_id"], 5)
        self.assertEqual(plan["phase2"]["kind"], "synth")
        self.assertEqual(plan["phase2"]["min_good"], 1)
        self.assertEqual(plan["synth_memo"], {"memo_for": "k_fu"})
        self.assertEqual(plan["adj_memo"], {"memo_for": "k_adj"})
        self.assertEqual(plan["phase3"]["label_key"], "Adjudicator")
        self.assertEqual(plan["phase4"]["label_key"], "Revision")
        self.assertEqual(plan["src_meta"]["Gemini"]["model_id"], "id-Gemini")
        for job in plan["sources"].values():
            self.assertEqual(job["messages"][0]["content"],
                             "TURN:Q|CTX:Light|True|False")

    def test_followup_guards_report_actionable_messages(self):
        followup = self._make_followup({}, {}, ALL_KEYS, [])
        self.assertEqual(followup("synthesis", "Q", [], "fu_main"),
                         "There is no completed council answer to continue yet.")

        state = {"synth": {"turns": [], "thread": []},
                 "k_fu_web": False, "k_fu_maxsearch": 3}
        followup = self._make_followup(state, {}, {"openai": ""}, [])
        self.assertEqual(followup("synthesis", "Q", [], "fu_main"),
                         "No keys found - open Settings & API Keys.")

        state = {"synth": {"turns": [], "thread": []},
                 "k_full_council": True, "_adj_on": True,
                 "k_adj_provider": "ChatGPT",
                 "k_fu_web": False, "k_fu_maxsearch": 3}
        followup = self._make_followup(state, {"ChatGPT": True, "Claude": True},
                                       ALL_KEYS, [])
        message = followup("synthesis", "Q", [], "fu_main")
        self.assertIn("at least one independent source", message)
        self.assertIn("ChatGPT the Adjudicator", message)

        state = {"research": {"turns": [], "thread": []},
                 "k_res_web": False, "k_res_maxsearch": 3}
        followup = self._make_followup(state, {"Gemini": True, "Claude": True},
                                       ALL_KEYS, [])
        message = followup("research", "Q", [], "fu_res")
        self.assertIn("at least two independent sources", message)

    def test_research_followup_always_runs_full_council(self):
        captured = []
        state = {"research": {"turns": [], "thread": []},
                 "_adj_on": True, "k_adj_provider": "ChatGPT",
                 "k_res_web": False, "k_res_maxsearch": 3}
        enabled = {"Gemini": True, "Grok": True, "Claude": True}
        followup = self._make_followup(state, enabled, ALL_KEYS, captured)
        self.assertIsNone(followup("research", "RQ", [], "fu_res"))
        plan = captured[0]
        self.assertEqual(set(plan["sources"]), {"Gemini", "Grok"})
        self.assertEqual(plan["phase2"]["kind"], "research")
        self.assertEqual(plan["phase2"]["min_good"], 2)
        self.assertIsNone(plan["synth_memo"])
        self.assertIsNone(plan["phase3"])
        self.assertIsNone(plan["phase4"])
        keys = dict(ALL_KEYS); keys.pop(literal_assignments("KEY_MAP")["KEY_MAP"]["Grok"])
        followup = self._make_followup(state, {"Gemini": True, "Grok": True, "DeepSeek": True,
                                               "Claude": True}, keys, captured)
        self.assertIsNone(followup("research", "RQ", [], "fu_res"))
        self.assertEqual(set(captured[-1]["sources"]), {"Gemini", "DeepSeek"})

    def test_promote_source_answer_queues_pending_insight(self):
        persisted = []
        state = {"source_chats": {"Gemini": [
            {"role": "user", "content": "private q"},
            {"role": "assistant", "content": "private insight"},
            {"role": "assistant", "content": "broken", "err": True},
        ]}}
        ns = {"st": fake_st(state), "uuid": uuid, "datetime": datetime,
              "persist_session": persisted.append}
        load_function("_private_source_state_keys", ns)
        promote = load_function("_promote_source_answer", ns)

        promote("synthesis", "Gemini", 1)
        branch = state["source_chats"]["Gemini"]
        self.assertTrue(branch[1]["promoted"])
        promotions = state["source_promotions"]
        self.assertEqual(len(promotions), 1)
        self.assertEqual(promotions[0]["provider"], "Gemini")
        self.assertEqual(promotions[0]["content"], "private insight")
        self.assertEqual(promotions[0]["status"], "pending")
        self.assertEqual(promotions[0]["id"], branch[1]["promotion_id"])
        self.assertEqual(persisted, ["synthesis"])

        promote("synthesis", "Gemini", 1)   # already promoted
        promote("synthesis", "Gemini", 0)   # user message
        promote("synthesis", "Gemini", 2)   # errored answer
        promote("synthesis", "Gemini", 9)   # out of range
        self.assertEqual(len(state["source_promotions"]), 1)
        self.assertEqual(persisted, ["synthesis"])

        res_state = {"res_source_chats": {"Grok": [
            {"role": "assistant", "content": "research insight"},
        ]}}
        ns = {"st": fake_st(res_state), "uuid": uuid, "datetime": datetime,
              "persist_session": persisted.append}
        load_function("_private_source_state_keys", ns)
        promote = load_function("_promote_source_answer", ns)
        promote("research", "Grok", 0)
        self.assertEqual(len(res_state["res_source_promotions"]), 1)
        self.assertEqual(persisted[-1], "research")


class HistoryCorruptionBannerTests(unittest.TestCase):
    def test_quarantine_banner_persists_across_reruns(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("CONCLAVE_TEST_DATA_DIR")
            os.environ["CONCLAVE_TEST_DATA_DIR"] = tmp
            try:
                (Path(tmp) / "ai_history.json").write_text("{corrupt not json",
                                                           encoding="utf-8")
                app = AppTest.from_file(str(APP), default_timeout=20).run()
                self.assertEqual(list(app.exception), [])
                self.assertTrue(any(".corrupt" in w.value for w in app.warning))
                self.assertTrue(any(name.endswith(".corrupt")
                                    for name in os.listdir(tmp)))
                app.run()
                self.assertEqual(list(app.exception), [])
                self.assertTrue(
                    any(".corrupt" in w.value for w in app.warning),
                    "quarantine banner vanished on the next rerun")
            finally:
                if previous is None:
                    os.environ.pop("CONCLAVE_TEST_DATA_DIR", None)
                else:
                    os.environ["CONCLAVE_TEST_DATA_DIR"] = previous


class StreamlitSmokeTests(unittest.TestCase):
    def setUp(self):
        self.test_data = tempfile.TemporaryDirectory()
        self.previous_data_dir = os.environ.get("CONCLAVE_TEST_DATA_DIR")
        os.environ["CONCLAVE_TEST_DATA_DIR"] = self.test_data.name
        try:
            self.app = AppTest.from_file(str(APP), default_timeout=20).run()
            self.assertEqual(list(self.app.exception), [])
        except Exception:
            self._restore_test_data_dir()
            raise

    def tearDown(self):
        self._restore_test_data_dir()

    def _restore_test_data_dir(self):
        if self.previous_data_dir is None:
            os.environ.pop("CONCLAVE_TEST_DATA_DIR", None)
        else:
            os.environ["CONCLAVE_TEST_DATA_DIR"] = self.previous_data_dir
        if self.test_data is not None:
            self.test_data.cleanup()
            self.test_data = None

    def set_preset(self, name):
        if not any(s.key == "run_preset_choice" for s in self.app.segmented_control):
            self.navigate("nav_council")
        dial = next(s for s in self.app.segmented_control if s.key == "run_preset_choice")
        dial.set_value(name).run()

    def navigate(self, key):
        button = next(button for button in self.app.button if button.key == key)
        button.click().run()
        self.assertEqual(list(self.app.exception), [])

    def test_speed_helpers_render_without_keys(self):
        self.assertFalse(any(b.key == "jev_suggest_main" for b in self.app.button))
        self.navigate("nav_settings")
        labels = [t.label for t in self.app.text_input]
        self.assertIn("TypeSafe (Jev decision checks)", labels)
        self.assertIn("Cerebras (live draft)", labels)
        self.navigate("nav_council")
        quorum = next(r for r in self.app.radio if r.key == "k_quorum_mode")
        self.assertEqual(quorum.value, "Start when most are in (faster)")
        self.assertTrue(next(t for t in self.app.toggle if t.key == "k_live_preview").disabled)
        self.assertTrue(next(b for b in self.app.selectbox if b.key == "k_jev_mode").disabled)
        quorum.set_value("Wait for every source").run()
        self.assertEqual(list(self.app.exception), [])
        self.assertEqual(self.app.session_state["k_quorum_mode"], "Wait for every source")

    def test_all_top_level_views_render(self):
        for key, expected in (
            ("nav_research", "🔬 Research Synthesizer"),
            ("nav_document", "📄 Document"),
            ("nav_projects", "▦ Projects"),
            ("nav_lenses", "◉ Tasks & Lenses"),
            ("nav_history", "🕘 Historical Chats"),
            ("nav_settings", "⚙ Settings"),
            ("nav_synthesis", "⭐ Synthesis"),
        ):
            self.navigate(key)
            self.assertEqual(self.app.session_state["main_view"], expected)

    def test_new_chat_from_non_chat_view_returns_to_fresh_synthesis(self):
        self.navigate("nav_document")
        self.app.session_state["threads"] = {"ChatGPT": [{"role": "assistant", "content": "old"}]}
        self.app.session_state["eval_threads"] = {
            "Synthesizer": [{"role": "assistant", "content": "old memo"}]}
        button = next(button for button in self.app.button if button.label == "➕ New chat")
        self.assertFalse(button.disabled)
        button.click().run()
        self.assertEqual(self.app.session_state["main_view"], "⭐ Synthesis")
        self.assertNotIn("threads", self.app.session_state)
        self.assertNotIn("eval_threads", self.app.session_state)

    def test_background_errors_stay_with_their_originating_tab(self):
        self.app.session_state["_bg_error_synthesis"] = "fixture failure"
        self.navigate("nav_research")
        self.assertFalse(any("fixture failure" in error.value for error in self.app.error))
        self.navigate("nav_synthesis")
        self.assertTrue(any("fixture failure" in error.value for error in self.app.error))

    def test_compact_preset_picker_is_ordered_and_custom_is_selectable(self):
        self.navigate("nav_council")
        source = APP.read_text(encoding="utf-8")
        self.assertIn('key="nav_council"', source)
        picker = next(slider for slider in self.app.segmented_control
                      if slider.key == "run_preset_choice")
        self.assertEqual(list(picker.options),
                         ["Fast", "Light", "Balanced", "Thorough", "Deep"])
        self.assertEqual(picker.value, "Light")

        custom_pill = next(b for b in self.app.button if b.key == "preset_custom_btn")
        self.assertEqual(custom_pill.proto.type, "secondary")
        custom_pill.click().run()
        self.assertTrue(self.app.session_state["k_preset_custom"])
        # While Custom is live the dial parks at the last NAMED tier and the
        # pill lights up primary -- the header/caption carry the Custom state.
        picker = next(slider for slider in self.app.segmented_control
                      if slider.key == "run_preset_choice")
        self.assertEqual(picker.value, "Light")
        custom_pill = next(b for b in self.app.button if b.key == "preset_custom_btn")
        self.assertEqual(custom_pill.proto.type, "primary")
        self.assertTrue(any("Custom (manual)" in caption.value
                            for caption in self.app.caption))

        picker.set_value("Fast").run()
        self.assertFalse(self.app.session_state["k_preset_custom"])
        self.assertEqual(self.app.session_state["k_gpt_model"], "Luna (economy)")
        self.assertEqual(self.app.session_state["k_syn_cla_model"], "Sonnet 5.5")
        self.assertEqual(self.app.session_state["k_syn_cla_eff"], "Low")
        self.assertEqual(self.app.session_state["k_syn_maxsearch"], 2)
        self.assertEqual(self.app.session_state["k_cla_model"], "Haiku 5.5")

    def test_presets_set_default_source_activation(self):
        self.navigate("nav_council")
        sources_table = literal_dict_string_entries("PRESET_SOURCES")
        for tier in ("Fast", "Light", "Balanced", "Thorough", "Deep"):
            for key in ("k_en_gpt", "k_en_cla", "k_en_gem"):
                self.assertTrue(sources_table[tier][key], f"{tier}:{key}")
        on = {tier: {k for k, v in sources_table[tier].items() if v}
              for tier in ("Fast", "Light", "Balanced", "Thorough", "Deep")}
        self.assertEqual(on["Fast"], {"k_en_gpt", "k_en_cla", "k_en_gem"})
        self.assertEqual(on["Light"] - on["Fast"], {"k_en_grok", "k_en_ds"})
        self.assertEqual(on["Balanced"] - on["Light"], {"k_en_meta", "k_en_qwen", "k_en_mistral"})
        self.assertEqual(on["Thorough"] - on["Balanced"], {"k_en_kimi", "k_en_mimo", "k_en_glm"})
        self.assertEqual(on["Deep"] - on["Thorough"], {"k_en_mm", "k_en_nv"})
        self.assertEqual([len(on[t]) for t in on], [3, 5, 8, 11, 13])

        # Live: sliding the dial applies the activation defaults...
        dial = next(s for s in self.app.segmented_control
                    if s.key == "run_preset_choice")
        dial.set_value("Balanced").run()
        self.assertTrue(self.app.session_state["k_en_ds"])
        self.assertTrue(self.app.session_state["k_en_grok"])
        self.assertTrue(self.app.session_state["k_en_qwen"])
        dial = next(s for s in self.app.segmented_control
                    if s.key == "run_preset_choice")
        dial.set_value("Fast").run()
        self.assertFalse(self.app.session_state["k_en_ds"])
        self.assertFalse(self.app.session_state["k_en_grok"])
        # ...and a manual activation override afterward does NOT move the
        # preset display to Custom (activation is a meta-control, not a
        # dial in the preset comparison).
        self.app.session_state["k_en_grok"] = True
        self.app.run()
        dial = next(s for s in self.app.segmented_control
                    if s.key == "run_preset_choice")
        self.assertEqual(dial.value, "Fast")
        self.assertFalse(self.app.session_state["k_preset_custom"])

    def test_composer_web_button_mirrors_the_council_switch(self):
        self.assertFalse(any(s.key == "cmp_run_preset" for s in self.app.segmented_control))
        btn = next(b for b in self.app.button if b.key == "cmp_web_btn")
        self.assertEqual(btn.proto.type, "secondary")
        btn.click().run()
        self.assertTrue(self.app.session_state["k_web_all"])
        self.assertTrue(self.app.session_state["k_web_gpt"])
        self.assertTrue(self.app.session_state["k_syn_web"])
        btn = next(b for b in self.app.button if b.key == "cmp_web_btn")
        self.assertEqual(btn.proto.type, "primary")
        self.navigate("nav_council")
        council_switch = next(tg for tg in self.app.toggle if tg.key == "global_k_web_all")
        self.assertTrue(council_switch.value)
        council_switch.set_value(False).run()
        self.assertFalse(self.app.session_state["k_web_gpt"])
        self.navigate("nav_synthesis")
        btn = next(b for b in self.app.button if b.key == "cmp_web_btn")
        self.assertEqual(btn.proto.type, "secondary")
        # the Council dial is still the one place the preset is set
        self.navigate("nav_council")
        self.assertTrue(any(s.key == "run_preset_choice" for s in self.app.segmented_control))

    def test_sidebar_synth_picker_switches_provider_and_retunes_it(self):
        self.navigate("nav_council")
        self.app.session_state["card_open_Synthesizer"] = True
        self.app.run()
        syn_pick = next(sb for sb in self.app.selectbox
                        if sb.key == "pick_k_syn_provider")
        self.assertEqual(syn_pick.value, "Claude")
        self.assertFalse(any(o.endswith("ChatGPT") for o in syn_pick.options))
        self.assertFalse(any(b.key == "card_btn_Claude" for b in self.app.button))
        self.assertTrue(any(b.key == "card_btn_ChatGPT" for b in self.app.button))
        self.assertTrue(any(b.key == "card_btn_Gemini" for b in self.app.button))

        syn_pick.set_value("Gemini").run()
        self.assertEqual(self.app.session_state["k_syn_provider"], "Gemini")
        self.assertFalse(self.app.session_state["k_preset_custom"])
        self.assertEqual(self.app.session_state["k_syn_gem_model"], "Flash (3.8)")
        self.assertEqual(self.app.session_state["k_syn_gem_t"], "Standard")
        self.assertEqual(self.app.session_state["k_adj_provider"], "ChatGPT")
        self.assertTrue(any(b.key == "card_btn_Claude" for b in self.app.button))
        self.assertFalse(any(b.key == "card_btn_Gemini" for b in self.app.button))
        st = self.app.session_state
        st["k_syn_provider"] = "Claude"
        self.app.run()
        self.assertEqual(self.app.session_state["k_fu_provider"], "Claude")
        self.assertEqual(self.app.session_state["k_res_provider"], "Claude")

        # Changing the run preset tier afterward must keep retuning BOTH
        # roles (Claude Synthesizer, ChatGPT Adjudicator), not silently
        # fall back to their old defaults.
        picker = next(slider for slider in self.app.segmented_control
                      if slider.key == "run_preset_choice")
        self.assertEqual(picker.value, "Light")
        picker.set_value("Thorough").run()
        self.assertEqual(self.app.session_state["k_syn_provider"], "Claude")
        self.assertEqual(self.app.session_state["k_syn_cla_model"], "Opus 5.5")
        self.assertEqual(self.app.session_state["k_syn_cla_eff"], "Extra (xhigh)")
        self.assertEqual(self.app.session_state["k_adj_provider"], "ChatGPT")
        self.assertEqual(self.app.session_state["k_adj_gpt_model"], "Sol (advanced)")
        self.assertEqual(self.app.session_state["k_adj_gpt_eff"], "Extra (xhigh)")
        self.assertTrue(self.app.session_state["k_adjudicator_on"])
        self.assertFalse(any(b.key == "card_btn_ChatGPT" for b in self.app.button))
        self.assertFalse(any(b.key == "card_btn_Claude" for b in self.app.button))

    def test_synthesizer_and_adjudicator_panels_are_collapsible_cards(self):
        self.navigate("nav_council")
        syn_card = next(b for b in self.app.button if b.key == "card_btn_Synthesizer")
        adj_card = next(b for b in self.app.button if b.key == "card_btn_Adjudicator")
        self.assertTrue(syn_card.label.startswith("▸"))
        self.assertIn("Synthesizer", syn_card.label)
        self.assertIn("Claude Sonnet 5", syn_card.label)
        self.assertTrue(adj_card.label.startswith("▸"))
        self.assertIn("Adjudicator", adj_card.label)
        self.assertIn("ChatGPT GPT-6.1 Sol", adj_card.label)
        self.assertFalse(any(toggle.label == "Auto-synthesize on send"
                             for toggle in self.app.toggle))

        syn_card.click().run()
        syn_card = next(b for b in self.app.button if b.key == "card_btn_Synthesizer")
        self.assertTrue(syn_card.label.startswith("▾"))
        self.assertTrue(any(toggle.label == "Auto-synthesize on send"
                            for toggle in self.app.toggle))

    def test_synthesis_view_toggle_appears_top_and_bottom_and_stays_synced(self):
        self.assertEqual(self.app.session_state["sp_view"], "⭐ Answer")
        self.assertFalse(any(w.key in ("top_sp_view", "sp_view")
                             for w in self.app.segmented_control))
        self.assertTrue(any("conclave-greeting" in item.value
                            for item in self.app.markdown))
        self.app.session_state["threads"] = {
            "Gemini": [{"role": "user", "content": "fixture"},
                       {"role": "assistant", "content": "fixture answer",
                        "secs": 1.0, "in_tok": 5, "out_tok": 5, "cost": 0.001,
                        "web": False, "model_id": "gemini-fixture",
                        "settings": "balanced", "turn_id": 0}]}
        self.app.run()
        top = next(w for w in self.app.segmented_control if w.key == "top_sp_view")
        bottom = next(w for w in self.app.segmented_control if w.key == "sp_view")
        self.assertEqual(top.value, "⭐ Answer")
        self.assertEqual(bottom.value, "⭐ Answer")

        top.set_value("\U0001F9E9 The Sources").run()
        self.assertEqual(self.app.session_state["sp_view"], "\U0001F9E9 The Sources")
        bottom = next(w for w in self.app.segmented_control if w.key == "sp_view")
        self.assertEqual(bottom.value, "\U0001F9E9 The Sources")
        self.assertTrue(any("fixture answer" in item.value
                            for item in self.app.markdown))

        bottom.set_value("⭐ Answer").run()
        self.assertEqual(self.app.session_state["sp_view"], "⭐ Answer")
        top = next(w for w in self.app.segmented_control if w.key == "top_sp_view")
        self.assertEqual(top.value, "⭐ Answer")

    def test_adjudicator_picker_enforces_cross_provider_constraint(self):
        self.navigate("nav_council")
        self.assertEqual(self.app.session_state["k_syn_provider"], "Claude")
        self.assertEqual(self.app.session_state["k_adj_provider"], "ChatGPT")

        self.app.session_state["card_open_Synthesizer"] = True
        self.app.session_state["card_open_Adjudicator"] = True
        self.app.run()
        syn_pick = next(sb for sb in self.app.selectbox
                        if sb.key == "pick_k_syn_provider")
        adj_pick = next(sb for sb in self.app.selectbox
                        if sb.key == "pick_k_adj_provider")
        # .options carries the formatted labels (icon + name), so match by suffix.
        self.assertFalse(any(o.endswith("ChatGPT") for o in syn_pick.options))  # adjudicates
        self.assertFalse(any(o.endswith("Claude") for o in adj_pick.options))   # Claude synthesizes
        self.assertEqual(adj_pick.value, "ChatGPT")

        adj_pick.set_value("Gemini").run()
        self.assertEqual(self.app.session_state["k_adj_provider"], "Gemini")
        self.assertEqual(self.app.session_state["k_syn_provider"], "Claude")  # unaffected
        syn_pick = next(sb for sb in self.app.selectbox
                        if sb.key == "pick_k_syn_provider")
        self.assertTrue(any(o.endswith("ChatGPT") for o in syn_pick.options))   # free again
        self.assertFalse(any(o.endswith("Gemini") for o in syn_pick.options))   # Gemini adjudicates now
        self.assertFalse(self.app.session_state["k_preset_custom"])
        self.assertEqual(self.app.session_state["k_adj_gem_model"], "Flash (3.8)")
        self.assertEqual(self.app.session_state["k_adj_gem_t"], "Standard")

        # And it keeps retuning on later preset changes, same as the
        # Synthesizer already did.
        picker = next(slider for slider in self.app.segmented_control
                      if slider.key == "run_preset_choice")
        picker.set_value("Deep").run()
        self.assertEqual(self.app.session_state["k_adj_gem_model"], "Argon (4)")
        self.assertEqual(self.app.session_state["k_adj_gem_t"], "Extended")

    def test_preset_switch_retunes_the_followup_synthesizer_too(self):
        self.navigate("nav_council")
        self.assertEqual(self.app.session_state["k_fu_cla_model"], "Sonnet 5.5")
        self.assertEqual(self.app.session_state["k_fu_cla_eff"], "Medium")
        self.assertTrue(self.app.session_state["k_fu_cla_think"])

        picker = next(slider for slider in self.app.segmented_control
                      if slider.key == "run_preset_choice")
        picker.set_value("Fast").run()
        self.assertFalse(self.app.session_state["k_preset_custom"])
        self.assertEqual(self.app.session_state["k_fu_cla_model"], "Sonnet 5.5")
        self.assertEqual(self.app.session_state["k_fu_cla_eff"], "Low")
        self.assertFalse(self.app.session_state["k_fu_cla_think"])

        # And it keeps retuning on later preset changes, same as the
        # Synthesizer and Adjudicator already do.
        picker = next(slider for slider in self.app.segmented_control
                      if slider.key == "run_preset_choice")
        picker.set_value("Deep").run()
        self.assertEqual(self.app.session_state["k_fu_cla_model"], "Fable 5.1")
        self.assertEqual(self.app.session_state["k_fu_cla_eff"], "Max")

    def test_followup_provider_switch_retunes_to_the_active_preset_tier(self):
        state = {"k_fu_provider": "Claude", "k_fu_gpt_model": "Sol (advanced)",
                "k_fu_gpt_eff": "High", "k_fu_gpt_speed": "Standard",
                "k_web_all": False}
        fake_st = SimpleNamespace(session_state=state)
        synth_presets = literal_assignments("SYNTH_PRESETS")["SYNTH_PRESETS"]
        followup_settings = load_function("followup_preset_settings", {
            "st": fake_st, "SYNTH_PRESETS": synth_presets,
        })
        apply_followup = load_function("_apply_followup_preset", {
            "st": fake_st, "followup_preset_settings": followup_settings,
        })
        evaluator_changed = load_function("_evaluator_provider_changed", {
            "st": fake_st, "PRESETS": literal_assignments("PRESETS")["PRESETS"],
            "NO_WEB_PROVIDERS": ("Qwen", "Mistral", "Kimi"),
            "_apply_synth_preset": lambda *a: None,
            "_apply_adjudicator_preset": lambda *a: None,
            "_apply_followup_preset": apply_followup,
            "NO_WEB_PROVIDERS": ("Qwen", "Mistral", "Kimi"),
            "_sync_evaluator_claude_search_cap": lambda *a: None,
        })

        state["k_fu_provider"] = "ChatGPT"
        evaluator_changed("k_fu", "Light")
        self.assertEqual(state["k_fu_gpt_model"], "Sol (advanced)")
        self.assertEqual(state["k_fu_gpt_eff"], "Low")
        self.assertEqual(state["k_fu_gpt_speed"], "Standard")

    def test_cross_provider_constraint_bumps_the_other_role_defensively(self):
        state = {"k_syn_provider": "Claude", "k_adj_provider": "ChatGPT",
                "k_preset_custom": True}
        fake_st = SimpleNamespace(session_state=state)
        model_order = literal_assignments("MODEL_ORDER")["MODEL_ORDER"]
        next_other = load_function("_next_provider_other_than", {"MODEL_ORDER": model_order})
        evaluator_changed = load_function("_evaluator_provider_changed", {
            "st": fake_st, "PRESETS": literal_assignments("PRESETS")["PRESETS"],
            "NO_WEB_PROVIDERS": ("Qwen", "Mistral", "Kimi"),
            "_apply_synth_preset": lambda *a: None,
            "_sync_evaluator_claude_search_cap": lambda *a: None,
        })
        set_synth = load_function("_set_synth_provider", {
            "st": fake_st, "current_preset_name": lambda: "Custom",
            "_evaluator_provider_changed": evaluator_changed,
            "_enforce_adjudicator_cross_provider": load_function(
                "_enforce_adjudicator_cross_provider",
                {"st": fake_st, "_next_provider_other_than": next_other,
                 "_evaluator_provider_changed": evaluator_changed,
                 "current_preset_name": lambda: "Custom"}),
        })
        set_adj = load_function("_set_adjudicator_provider", {
            "st": fake_st, "current_preset_name": lambda: "Custom",
            "_evaluator_provider_changed": evaluator_changed,
            "_next_provider_other_than": next_other,
        })

        set_synth("ChatGPT")
        self.assertEqual(state["k_syn_provider"], "ChatGPT")
        self.assertNotEqual(state["k_adj_provider"], "ChatGPT")

        colliding = state["k_syn_provider"]
        set_adj(colliding)
        self.assertEqual(state["k_adj_provider"], colliding)
        self.assertNotEqual(state["k_syn_provider"], colliding)

    def test_run_cost_breakdown_includes_synthesizer_and_true_total(self):
        source_message = {
            "role": "assistant", "content": "source", "secs": 1.0,
            "in_tok": 10, "out_tok": 5, "cost": 0.01, "web": False,
            "model_id": "gpt-5.6-terra", "settings": "effort medium", "turn_id": 0,
        }
        synth_message = {
            "role": "assistant",
            "content": "Definitive answer.\n\n---\n\n**How I weighed the sources**\nUsed A.",
            "secs": 2.0,
            "in_tok": 20, "out_tok": 8, "cost": 0.02, "web": False,
            "model_id": "claude-opus-5", "settings": "effort high", "turn_id": 0,
            "turn_secs": 45.0,
        }
        self.app.session_state["threads"] = {"ChatGPT": [source_message]}
        self.app.session_state["synth"] = {
            "label": "Claude Opus 5", "provider": "Claude", "legend": {},
            "thread": [{"role": "user", "content": "fixture"}, synth_message],
        }
        self.app.session_state["sp_view"] = "⭐ Answer"
        self.app.run()

        captions = [caption.value for caption in self.app.caption]
        self.assertTrue(any("Synthesizer subtotal: ~$0.0200" in value
                            for value in captions))
        markdown = [item.value for item in self.app.markdown]
        self.assertTrue(any("True total: ~$0.0300" in value for value in markdown))
        self.assertTrue(any("Total time: 45s" in value for value in markdown))
        self.assertIn("How I weighed the sources",
                      [expander.label for expander in self.app.expander])
        self.assertTrue(any(expander.label.startswith("💵 Turn 1 cost")
                            for expander in self.app.expander))
        self.assertTrue(any("Total time: 45s" in value for value in captions))
        self.assertIn("📊 Run stats & true cost",
                      [expander.label for expander in self.app.expander])
        self.assertTrue(any(selectbox.label == "Synthesizer voice"
                            for selectbox in self.app.selectbox))

    def test_turn_cost_itemizes_memos_audit_and_pre_revision_draft(self):
        source_message = {
            "role": "assistant", "content": "source", "secs": 1.0,
            "in_tok": 10, "out_tok": 5, "cost": 0.01, "web": False,
            "model_id": "gpt-5.6-terra", "settings": "effort medium", "turn_id": 0,
        }
        synth_message = {
            "role": "assistant",
            "content": "Revised answer.\n\n---\n\n**How I weighed the sources**\nUsed A.\n\n"
                      "---\n\n**Response to the Adjudicator**\nAccepted and corrected.",
            "secs": 2.0, "in_tok": 20, "out_tok": 8, "cost": 0.02, "web": False,
            "model_id": "claude-opus-5", "settings": "effort high", "turn_id": 0,
            "turn_secs": 45.0, "revised": True,
            "pre_revision": {"cost": 0.015, "secs": 1.8, "in_tok": 18, "out_tok": 7,
                             "model_id": "claude-opus-5", "settings": "effort high"},
            "adjudicator": {
                "role": "assistant", "content": "Found one issue.", "secs": 1.2,
                "in_tok": 12, "out_tok": 6, "cost": 0.005, "web": False,
                "model_id": "gpt-5.6-luna", "settings": "effort none",
                "material_defects": True, "provider": "ChatGPT",
            },
        }
        self.app.session_state["threads"] = {"ChatGPT": [source_message]}
        self.app.session_state["synth"] = {
            "label": "Claude Opus 5", "provider": "Claude", "legend": {},
            "thread": [{"role": "user", "content": "fixture"}, synth_message],
        }
        self.app.session_state["eval_threads"] = {
            "Synthesizer": [{"role": "assistant", "content": "memo", "secs": 0.9,
                             "in_tok": 9, "out_tok": 4, "cost": 0.003, "web": False,
                             "model_id": "claude-opus-5", "settings": "effort high",
                             "turn_id": 0}],
            "Adjudicator": [{"role": "assistant", "content": "memo", "secs": 0.8,
                             "in_tok": 8, "out_tok": 3, "cost": 0.004, "web": False,
                             "model_id": "gpt-5.6-luna", "settings": "effort none",
                             "turn_id": 0}],
        }
        self.app.session_state["sp_view"] = "⭐ Answer"
        self.app.run()

        turn_expander = next(e for e in self.app.expander
                             if e.label.startswith("💵 Turn 1 cost"))
        row_text = "\n".join(c.value for c in turn_expander.caption)
        self.assertIn("Synthesizer (pre-revision draft)", row_text)
        self.assertIn("Adjudicator audit", row_text)
        self.assertIn("Synthesizer memo", row_text)
        self.assertIn("Adjudicator memo", row_text)
        self.assertIn("~$0.0570", turn_expander.label)
        markdown = [item.value for item in self.app.markdown]
        self.assertTrue(any("True total: ~$0.0570" in value for value in markdown))

    def test_followup_composer_renders_full_council_toggle_and_caption(self):
        self.app.session_state["threads"] = {}
        self.app.session_state["synth"] = {
            "label": "Claude Sonnet 5", "provider": "Claude", "legend": {},
            "thread": [
                {"role": "user", "content": "fixture question"},
                {"role": "assistant", "content": "# Fixture Answer\nText.",
                 "secs": 1.0, "in_tok": 10, "out_tok": 5, "cost": 0.01,
                 "web": False, "model_id": "claude-sonnet-5",
                 "settings": "effort medium", "turn_id": 0},
            ],
        }
        self.app.session_state["sp_view"] = "⭐ Answer"
        self.app.run()

        toggle = next(t for t in self.app.toggle if t.key == "k_full_council")
        self.assertFalse(toggle.value)
        captions = "\n".join(item.value for item in self.app.caption)
        self.assertIn("Synthesizer alone", captions)

        toggle.set_value(True).run()
        captions = "\n".join(item.value for item in self.app.caption)
        self.assertIn("full council", captions)
        self.assertIn("synthesizes", captions)

    def test_revised_answer_shows_disposition_and_addressed_note(self):
        revised_message = {
            "role": "assistant",
            "content": ("# Corrected Answer\nThe fixed text.\n\n---\n\n"
                       "**How I weighed the sources**\nUsed A and B.\n\n---\n\n"
                       "**Response to the Adjudicator**\nAccepted the finding; corrected the claim."),
            "secs": 3.0, "in_tok": 30, "out_tok": 12, "cost": 0.025, "web": False,
            "model_id": "claude-opus-5", "settings": "effort high", "turn_id": 0,
            "revised": True,
            "adjudicator": {
                "role": "assistant", "content": "Found one unsupported claim.",
                "secs": 1.0, "in_tok": 10, "out_tok": 5, "cost": 0.005, "web": False,
                "model_id": "gpt-5.6-luna", "settings": "effort none",
                "material_defects": True, "provider": "ChatGPT",
            },
        }
        self.app.session_state["threads"] = {}
        self.app.session_state["synth"] = {
            "label": "Claude Opus 5", "provider": "Claude", "legend": {},
            "thread": [{"role": "user", "content": "fixture"}, revised_message],
        }
        self.app.session_state["sp_view"] = "⭐ Answer"
        self.app.run()

        markdown = [item.value for item in self.app.markdown]
        self.assertTrue(any("The fixed text." in value for value in markdown))
        expander_labels = [expander.label for expander in self.app.expander]
        self.assertIn("⚖️ Adjudicator's review — flagged material issues — addressed below",
                      expander_labels)
        self.assertIn("✏️ Response to the Adjudicator's review", expander_labels)
        disposition_expander = next(e for e in self.app.expander
                                    if e.label == "✏️ Response to the Adjudicator's review")
        self.assertTrue(any("Accepted the finding; corrected the claim." in m.value
                            for m in disposition_expander.markdown))
        weighing_expander = next(e for e in self.app.expander
                                 if e.label == "How I weighed the sources")
        self.assertFalse(any("Response to the Adjudicator" in m.value
                             for m in weighing_expander.markdown))

    def test_revision_failure_falls_back_to_original_draft_honestly(self):
        fallback_message = {
            "role": "assistant",
            "content": "The original draft.\n\n---\n\n**How I weighed the sources**\nUsed A.",
            "secs": 2.0, "in_tok": 20, "out_tok": 8, "cost": 0.02, "web": False,
            "model_id": "claude-opus-5", "settings": "effort high", "turn_id": 0,
            "revision_failed": True,
            "adjudicator": {
                "role": "assistant", "content": "Found one unsupported claim.",
                "secs": 1.0, "in_tok": 10, "out_tok": 5, "cost": 0.005, "web": False,
                "model_id": "gpt-5.6-luna", "settings": "effort none",
                "material_defects": True, "provider": "ChatGPT",
            },
        }
        self.app.session_state["threads"] = {}
        self.app.session_state["synth"] = {
            "label": "Claude Opus 5", "provider": "Claude", "legend": {},
            "thread": [{"role": "user", "content": "fixture"}, fallback_message],
        }
        self.app.session_state["sp_view"] = "⭐ Answer"
        self.app.run()

        markdown = [item.value for item in self.app.markdown]
        self.assertTrue(any("The original draft." in value for value in markdown))
        expander_labels = [expander.label for expander in self.app.expander]
        self.assertIn("⚖️ Adjudicator's review — flagged material issues", expander_labels)
        self.assertNotIn("⚖️ Adjudicator's review — flagged material issues — addressed below",
                         expander_labels)
        review_expander = next(e for e in self.app.expander
                               if e.label == "⚖️ Adjudicator's review — flagged material issues")
        self.assertTrue(any("revision pass did not complete" in c.value
                            for c in review_expander.caption))

    def test_sources_tab_reveals_evaluator_memos_after_synthesis_completes(self):
        source_message = {
            "role": "assistant", "content": "ChatGPT source answer", "secs": 1.0,
            "in_tok": 10, "out_tok": 5, "cost": 0.01, "web": False,
            "model_id": "gpt-5.6-terra", "settings": "effort medium", "turn_id": 0,
        }
        self.app.session_state["threads"] = {"ChatGPT": [source_message]}
        self.app.session_state["eval_threads"] = {
            "Synthesizer": [{
                "role": "assistant", "content": "Claude's own independent first take.",
                "secs": 3.0, "in_tok": 40, "out_tok": 20, "cost": 0.03, "web": False,
                "model_id": "claude-sonnet-5", "settings": "effort medium", "turn_id": 0,
            }],
            "Adjudicator": [{
                "role": "assistant", "content": "ChatGPT's own independent first take.",
                "secs": 2.0, "in_tok": 30, "out_tok": 15, "cost": 0.02, "web": False,
                "model_id": "gpt-5.6-terra", "settings": "effort low", "turn_id": 0,
            }],
        }
        self.app.session_state["sp_view"] = "\U0001F9E9 The Sources"
        self.app.run()

        subview = next(w for w in self.app.segmented_control if w.key == "src_subview")
        self.assertEqual(subview.options, ["Source models", "Evaluators"])
        # Defaults to Source models -- the memos stay a deliberate second step,
        # not the first thing shown.
        markdown_before = [m.value for m in self.app.markdown]
        self.assertFalse(any("independent first take" in v for v in markdown_before))

        subview.set_value("Evaluators").run()
        markdown = [m.value for m in self.app.markdown]
        self.assertTrue(any("Claude's own independent first take." in v for v in markdown))
        self.assertTrue(any("ChatGPT's own independent first take." in v for v in markdown))
        self.assertTrue(any("revealed here only after synthesis completes" in c.value
                            for c in self.app.caption))
        self.assertTrue(any(sb.label == "Synthesizer voice" for sb in self.app.selectbox))
        self.assertTrue(any(sb.label == "Adjudicator voice" for sb in self.app.selectbox))
        button_keys = {b.key for b in self.app.button}
        self.assertIn("say_eval_Synthesizer", button_keys)
        self.assertIn("say_eval_Adjudicator", button_keys)

        # An empty eval_threads (no completed synthesis yet) shows an honest
        # explanation instead of an empty page.
        self.app.session_state["eval_threads"] = {}
        self.app.run()
        subview = next(w for w in self.app.segmented_control if w.key == "src_subview")
        subview.set_value("Evaluators").run()
        self.assertTrue(any("will appear here once a council turn finishes" in i.value
                            for i in self.app.info))

    def test_chatgpt_card_exposes_full_model_effort_and_speed_controls(self):
        self.navigate("nav_council")
        self.app.session_state["k_adj_provider"] = "Gemini"
        self.app.run()
        card_button = next(button for button in self.app.button
                           if "ChatGPT" in button.label)
        self.assertIn("**ChatGPT", card_button.label)
        self.assertFalse(card_button.label.endswith("Standard**"))
        card_button.click().run()
        controls = {selectbox.label: selectbox for selectbox in self.app.selectbox}

        self.assertEqual(controls["GPT model"].options,
                         ["Astra (flagship)", "Sol (advanced)", "Luna (economy)"])
        self.assertEqual(controls["Reasoning effort"].options,
                         ["Instant", "Low", "Medium", "High", "Extra (xhigh)", "Max"])
        self.assertEqual(controls["Processing speed"].options,
                         ["Standard", "Fast", "Flex", "Ultrafast"])
        controls["Processing speed"].set_value("Fast").run()
        self.assertTrue(any("OpenAI Fast mode" in caption.value
                            for caption in self.app.caption))

    def test_historical_chat_search_and_sidebar_order(self):
        self.navigate("nav_council")
        source = APP.read_text(encoding="utf-8")
        self.assertNotIn('with st.expander("✅ Active models"', source)
        self.assertNotIn('with st.expander("\\U0001F50A Read-aloud voices"', source)
        self.assertEqual(source.count('st.toggle("Active source"'), 13)
        self.assertTrue(self.app.session_state["k_en_ds"])
        self.assertTrue(self.app.session_state["k_en_grok"])
        self.assertFalse(self.app.session_state["k_en_qwen"])

        gemini_card = next(button for button in self.app.button
                           if button.key == "card_btn_Gemini")
        gemini_card.click().run()
        self.assertTrue(any(toggle.label == "Active source" for toggle in self.app.toggle))

        self.assertFalse(any(b.key == "card_btn_history" for b in self.app.button))
        self.assertTrue(any("No saved chats yet" in caption.value
                            for caption in self.app.caption))
        see_all = next(button for button in self.app.button
                       if button.key == "nav_history")
        self.assertIn("See all", see_all.label)

    def test_chat_day_group_buckets_and_pin_roundtrip(self):
        from datetime import timedelta
        group = load_function("chat_day_group", {"datetime": datetime})
        now = datetime.now()
        self.assertEqual(group(now.isoformat()), "Today")
        self.assertEqual(group((now - timedelta(days=1)).isoformat()), "Yesterday")
        self.assertEqual(group((now - timedelta(days=2)).isoformat()), "Two days ago")
        self.assertEqual(group((now - timedelta(days=3)).isoformat()), "Three days ago")
        self.assertEqual(group((now - timedelta(days=5)).isoformat()), "Previous 7 days")
        self.assertEqual(group((now - timedelta(days=10)).isoformat()), "Older than 7 days")
        self.assertEqual(group((now - timedelta(days=30)).isoformat()), "Older than 14 days")
        self.assertEqual(group((now - timedelta(days=45)).isoformat()), "Older than 30 days")
        self.assertEqual(group((now - timedelta(days=75)).isoformat()), "Older than 60 days")
        self.assertEqual(group((now - timedelta(days=120)).isoformat()), "Older than 90 days")
        self.assertEqual(group((now - timedelta(days=200)).isoformat()), "Older than 6 months")
        self.assertEqual(group((now - timedelta(days=400)).isoformat()), "Older than a year")
        self.assertEqual(group(None), "Undated")
        self.assertEqual(group("not-a-timestamp"), "Undated")

        with tempfile.TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "history.json"
            helpers = load_history_helpers(history_file)
            helpers["update_history"](lambda h: [
                {"ts": "2026-08-15T10:00:00", "mode": "synthesis", "prompt": "fixture"}])
            toggle_pin = load_function("toggle_pin_session", {
                "update_history": helpers["update_history"],
                "update_history_archive": helpers["update_history_archive"],
                "HISTORY_ARCHIVE_FILE": str(history_file.with_name("history_archive.json")),
                "os": os,
                "st": SimpleNamespace(session_state={
                    "session_id": "2026-08-15T10:00:00",
                    # Deliberately stale: storage starts unpinned, so the
                    # callback must use the persisted result rather than
                    # blindly negating this hint.
                    "session_pinned": True,
                }),
            })
            toggle_pin("2026-08-15T10:00:00", "synthesis")
            self.assertTrue(helpers["load_history"]()[0].get("pinned"))
            self.assertTrue(toggle_pin.__globals__["st"].session_state["session_pinned"])
            toggle_pin("missing", "synthesis")
            self.assertTrue(toggle_pin.__globals__["st"].session_state["session_pinned"])
            toggle_pin("2026-08-15T10:00:00", "synthesis")
            self.assertNotIn("pinned", helpers["load_history"]()[0])
            self.assertFalse(toggle_pin.__globals__["st"].session_state["session_pinned"])

        source = APP.read_text(encoding="utf-8")
        self.assertIn('key="pin_chat_main"', source)
        pin_btn = source.index('key="pin_chat_main"')
        self.assertIn('on_click=toggle_pin_session', source[pin_btn:pin_btn + 400])
        self.assertIn('st.session_state.get("session_pinned", False)', source)
        self.assertIn('e["last_open"] = datetime.now().isoformat(timespec="seconds")', source)
        self.assertIn('clean_entry["last_open"] = old["last_open"]', source)
        self.assertIn('if int(h.get("opens") or 0) >= 2 and _freq_fresh(h)', source)
        persist_node = next(node for node in source_tree().body
                            if isinstance(node, ast.FunctionDef)
                            and node.name == "persist_session")
        persist_source = ast.get_source_segment(source, persist_node)
        self.assertIn('if old and old.get("pinned"):', persist_source)
        self.assertIn('clean_entry["pinned"] = True', persist_source)

    def test_platform_navigation_and_full_history_workspace(self):
        buttons = list(self.app.button)
        new_chat_index = next(i for i, button in enumerate(buttons)
                              if button.key == "new_chat_btn")
        history_index = next(i for i, button in enumerate(buttons)
                             if button.key == "nav_history")
        projects_index = next(i for i, button in enumerate(buttons)
                              if button.key == "nav_projects")
        lenses_index = next(i for i, button in enumerate(buttons)
                            if button.key == "nav_lenses")
        document_index = next(i for i, button in enumerate(buttons)
                              if button.key == "nav_document")
        settings_index = next(i for i, button in enumerate(buttons)
                              if button.key == "nav_settings")
        self.assertLess(new_chat_index, projects_index)
        self.assertLess(projects_index, lenses_index)
        self.assertLess(lenses_index, document_index)
        self.assertLess(document_index, history_index)
        self.assertLess(history_index, settings_index)
        source = APP.read_text(encoding="utf-8")
        self.assertLess(source.index('key="new_chat_btn"'),
                        source.index('key="nav_search_chats"'))
        self.assertLess(source.index('key="nav_search_chats"'),
                        source.index('key="nav_history"'))
        self.assertLess(source.index('key="nav_history"'),
                        source.index('key="sidebar_pinned_footer"'))
        self.assertLess(source.index('key="sidebar_pinned_footer"'),
                        source.index('key="nav_council"'))
        self.assertFalse(buttons[projects_index].disabled)
        self.assertFalse(buttons[lenses_index].disabled)

        self.assertFalse(any("Launchpad" in expander.label for expander in self.app.expander))
        self.assertTrue(any(expander.label.startswith("🕘 Chats")
                            for expander in self.app.expander))
        self.assertIn('key="chats_expander", type="compact"', source)

        # Settings & API Keys sits in its own pinned-footer container --
        # confirm the container exists and carries the CSS class the sticky
        # rule targets (live sticky-scroll behavior itself needs a real
        # browser, verified separately, not by AppTest).
        self.assertIn('st.container(key="sidebar_pinned_footer")', source)
        self.assertIn(".st-key-sidebar_pinned_footer {", source)
        self.assertIn("position: sticky;", source)
        self.assertIn('st.popover(f"⚙ Conclave · v{APP_VERSION}"', source)
        self.assertTrue(any(button.key == "footer_about" for button in self.app.button))
        about_btn = next(button for button in self.app.button
                         if button.key == "footer_about")
        about_btn.click().run()
        self.assertTrue(any("One prompt. Multiple frontier AIs." in item.value
                            for item in self.app.markdown))

        self.navigate("nav_history")
        self.assertTrue(any(field.label == "Search chats" for field in self.app.text_input))
        select_labels = {selectbox.label for selectbox in self.app.selectbox}
        self.assertIn("Chat type", select_labels)
        self.assertIn("Sort by", select_labels)

        include_archive = next(toggle for toggle in self.app.toggle
                               if toggle.key == "k_hist_include_archive")
        self.assertFalse(include_archive.value)

        self.navigate("nav_settings")
        self.assertTrue(any(button.key == "settings_open_about"
                            for button in self.app.button))
        self.assertFalse(any(button.key == "nav_about" for button in self.app.button))

    def test_global_web_switch_controls_sources_and_synthesis_evaluators(self):
        self.navigate("nav_council")
        self.set_preset("Thorough")
        web_all = next(toggle for toggle in self.app.toggle
                       if toggle.key == "global_k_web_all")
        self.assertFalse(web_all.value)
        web_all.set_value(True).run()
        for key in ("k_web_gpt", "k_web_cla", "k_web_gem", "k_web_grok", "k_web_kimi", "k_web_mm"):
            self.assertTrue(self.app.session_state[key])
        self.assertFalse(self.app.session_state["k_web_ds"])
        self.assertTrue(self.app.session_state["k_syn_web"])
        self.assertTrue(self.app.session_state["k_fu_web"])
        self.assertTrue(self.app.session_state["k_adj_web"])
        self.assertFalse(self.app.session_state["k_res_web"])

        adj_card = next(b for b in self.app.button if b.key == "card_btn_Adjudicator")
        adj_card.click().run()
        adj_toggle = next(t for t in self.app.toggle if t.key == "w_k_adj_web")
        adj_toggle.set_value(False).run()
        web_all = next(toggle for toggle in self.app.toggle
                       if toggle.key == "global_k_web_all")
        self.assertFalse(web_all.value)

    def test_deepseek_has_no_web_switch_and_parks_while_web_is_on(self):
        _testing = literal_assignments("ALL_SOURCES_ON_FOR_TESTING")["ALL_SOURCES_ON_FOR_TESTING"]
        self.navigate("nav_council")
        self.app.session_state["card_open_DeepSeek"] = True
        self.app.run()
        self.assertFalse(any(tg.key == "k_web_ds" for tg in self.app.toggle))
        model = next(s for s in self.app.selectbox if s.key == "k_ds_model")
        self.assertEqual(model.options, ["V4.1 Flash (fast · vision)"])
        web_all = next(toggle for toggle in self.app.toggle
                       if toggle.key == "global_k_web_all")
        web_all.set_value(True).run()
        self.assertFalse(self.app.session_state["k_web_ds"])
        if _testing:
            self.assertFalse(self.app.session_state["k_en_ds"])
            self.assertIn("DeepSeek", self.app.session_state["_web_parked"])

    def test_settings_owns_keys_and_opens_about_dialog(self):
        markdown = "\n".join(item.value for item in self.app.markdown)
        self.assertNotIn("API funds", markdown)
        self.assertFalse(any(expander.label.startswith("🔑 API Keys")
                             for expander in self.app.expander))

        self.navigate("nav_settings")
        self.assertTrue(any(expander.label.startswith("🔑 API Keys")
                            for expander in self.app.expander))
        # A new provider must be a real, saveable key field, not just active
        # by default with no way to actually authenticate it.
        self.assertTrue(any(field.label.startswith("Qwen")
                            for field in self.app.text_input))
        about = next(button for button in self.app.button
                     if button.key == "settings_open_about")
        about.click().run()
        self.assertEqual(list(self.app.exception), [])
        self.assertTrue(any("One prompt. Multiple frontier AIs." in item.value
                            for item in self.app.markdown))

    def test_about_dialog_how_to_use_is_collapsed_and_explains_significance(self):
        self.navigate("nav_settings")
        about = next(button for button in self.app.button
                     if button.key == "settings_open_about")
        about.click().run()
        self.assertEqual(list(self.app.exception), [])
        markdown = "\n".join(item.value for item in self.app.markdown)
        self.assertIn("Why Conclave is different", markdown)
        self.assertIn("Adjudicator",
                      markdown.split("**Conclave's workspaces are separate**")[0])
        source = APP.read_text(encoding="utf-8")
        self.assertIn(
            'with st.expander("\\U0001F4CB How to use it (step-by-step)", expanded=_howto):',
            source)
        howto = next(e for e in self.app.expander if "How to use it" in e.label)
        self.assertFalse(howto.proto.expanded)
        self.assertIn('on_click=_open_about_dialog, args=("howto",)', source)
        self.assertIn('id="conclave-about-howto"', source)
        self.assertNotIn('"**How to use it**\\n"', source)

    def test_about_dialog_section_order_and_technical_details_auto_expanded(self):
        source = APP.read_text(encoding="utf-8")
        i_readmore = source.index('with st.expander("\\U0001F4D6 Read more')
        i_tech = source.index('with st.expander("\\U0001F527 Technical details')
        i_howto = source.index('with st.expander("\\U0001F4CB How to use it')
        self.assertLess(i_readmore, i_tech)
        self.assertLess(i_tech, i_howto)
        self.assertIn(
            'with st.expander("\\U0001F527 Technical details & versions", expanded=False):',
            source)
        self.assertIn(
            'with st.expander("\\U0001F4D6 Read more — why synthesis beats any single model", '
            'expanded=False):', source)
        self.assertIn(
            'with st.expander("\\U0001F4CB How to use it (step-by-step)", expanded=_howto):',
            source)

        self.navigate("nav_settings")
        about = next(button for button in self.app.button
                     if button.key == "settings_open_about")
        about.click().run()
        expander_labels = [expander.label for expander in self.app.expander]
        tech_i = next(i for i, label in enumerate(expander_labels)
                      if "Technical details" in label)
        howto_i = next(i for i, label in enumerate(expander_labels)
                       if "How to use it" in label)
        readmore_i = next(i for i, label in enumerate(expander_labels)
                          if "Read more" in label)
        self.assertLess(readmore_i, tech_i)
        self.assertLess(tech_i, howto_i)
        self.assertFalse(any(e.proto.expanded for e in self.app.expander
                             if any(k in e.label for k in ("Read more", "Technical details", "How to use it"))))
        self.assertIn("render_api_keys_panel(expanded=False)", source)

    def test_sidebar_workspaces_and_source_models_are_collapsible_groups(self):
        self.navigate("nav_council")
        source = APP.read_text(encoding="utf-8")
        self.assertTrue(any(expander.label.startswith("Workspaces")
                            for expander in self.app.expander))
        # (the test opens the Council page first, so the context-aware
        # Workspaces header names that location)
        self.assertTrue(any("🏛️ Council" in expander.label
                            for expander in self.app.expander
                            if expander.label.startswith("Workspaces")))
        self.assertIn('key="workspaces_expander", type="compact"', source)
        self.assertTrue(any(expander.label.startswith("Source models ·")
                            for expander in self.app.expander))
        self.assertIn(
            'with st.expander(f"Source models · {len(_src_now)} active", expanded=True,',
            source)
        self.assertIn('key="source_models_expander", type="compact"):', source)
        # The count in the label reflects the real active/excluded source
        # set, not a hardcoded number.
        label = next(expander.label for expander in self.app.expander
                    if expander.label.startswith("Source models ·"))
        self.assertRegex(label, r"^Source models · \d+ active$")

    def test_run_preset_web_badge_and_synth_adjudicator_on_off_badges(self):
        self.navigate("nav_council")
        preset_label = next(expander.label for expander in self.app.expander
                            if expander.label.startswith("Run preset ·"))
        self.assertEqual(preset_label, "Run preset · Light")
        syn_card = next(b for b in self.app.button if b.key == "card_btn_Synthesizer")
        adj_card = next(b for b in self.app.button if b.key == "card_btn_Adjudicator")
        syn_on = next(b for b in self.app.button if b.key == "evalon_Synthesizer")
        self.assertEqual(syn_on.proto.type, "primary")
        adj_on = next(b for b in self.app.button if b.key == "evalon_Adjudicator")
        self.assertEqual(adj_on.proto.type, "secondary")

        self.app.session_state["k_web_gem"] = True
        self.app.run()
        preset_label = next(expander.label for expander in self.app.expander
                            if expander.label.startswith("Run preset ·"))
        self.assertIn("\U0001F310", preset_label)

        style = "\n".join(item.value for item in self.app.markdown
                          if ".st-key-card_btn_" in item.value)
        self.assertIn(".st-key-card_btn_Synthesizer button { "
                      "border-left: 2px solid var(--conclave-accent);", style)
        self.assertIn(".st-key-card_btn_Adjudicator button { opacity: 0.5; }", style)

        self.app.session_state["k_adjudicator_on"] = True
        self.app.run()
        adj_card = next(b for b in self.app.button if b.key == "card_btn_Adjudicator")
        adj_on = next(b for b in self.app.button if b.key == "evalon_Adjudicator")
        self.assertEqual(adj_on.proto.type, "primary")
        style = "\n".join(item.value for item in self.app.markdown
                          if ".st-key-card_btn_" in item.value)
        self.assertIn(".st-key-card_btn_Adjudicator button { "
                      "border-left: 2px solid var(--conclave-accent);", style)

    def test_page_width_setting_exists_and_overrides_content_max_width(self):
        self.navigate("nav_settings")
        width_select = next(sb for sb in self.app.selectbox if sb.label == "Page width")
        self.assertEqual(list(width_select.options),
                         ["Focused", "Extra narrow", "Narrow", "Medium", "Wide"])
        self.assertEqual(width_select.value, "Narrow")
        markdown = "\n".join(item.value for item in self.app.markdown)
        self.assertIn("max-width: 1150px !important", markdown)
        self.assertNotIn("max-width: 1900px !important", markdown)

        self.assertIn("max-width: min(210ch, 100%) !important", markdown)
        width_select = next(sb for sb in self.app.selectbox if sb.label == "Page width")
        width_select.set_value("Wide").run()
        markdown = "\n".join(item.value for item in self.app.markdown)
        self.assertIn("max-width: 1900px !important", markdown)
        self.assertIn("max-width: 100% !important", markdown)
        self.assertNotIn("max-width: min(210ch, 100%) !important", markdown)

    def test_appearance_mode_and_accent_presets_override_the_palette(self):
        self.navigate("nav_settings")
        theme = next(sb for sb in self.app.selectbox if sb.label == "Appearance")
        accent = next(sb for sb in self.app.selectbox if sb.label == "Accent color")
        self.assertEqual(theme.value, "Dark")
        self.assertEqual(accent.value, "Slate Mono")
        markdown = "\n".join(item.value for item in self.app.markdown)
        self.assertIn("--conclave-accent: #9aa7b5;", markdown)

        theme.set_value("Dark").run()
        markdown = "\n".join(item.value for item in self.app.markdown)
        self.assertIn("--conclave-base: #0e1117;", markdown)
        self.assertIn("--conclave-text: #d8dee5;", markdown)

        theme = next(sb for sb in self.app.selectbox if sb.label == "Appearance")
        theme.set_value("Light").run()
        markdown = "\n".join(item.value for item in self.app.markdown)
        self.assertIn("--conclave-base: #eff1f4;", markdown)

        theme = next(sb for sb in self.app.selectbox if sb.label == "Appearance")
        theme.set_value("System").run()
        markdown = "\n".join(item.value for item in self.app.markdown)
        theme_css = literal_assignments(
            "STREAMLIT_THEME_REANCHOR_CSS")["STREAMLIT_THEME_REANCHOR_CSS"]
        self.assertIn("html.conclave-day { ", markdown)
        self.assertIn(theme_css, markdown)
        self.assertIn("html.conclave-night { ", markdown)
        self.assertNotIn("@media (prefers-color-scheme: light) {", markdown)

        accent = next(sb for sb in self.app.selectbox if sb.label == "Accent color")
        accent.set_value("Ember Coral").run()
        markdown = "\n".join(item.value for item in self.app.markdown)
        self.assertIn("--conclave-accent: #e0857e;", markdown)
        self.assertIn("--conclave-action: #a04a44;", markdown)

    def test_header_theme_switcher_mirrors_the_appearance_setting(self):
        switch = next(c for c in self.app.segmented_control
                      if c.key == "hdr_k_theme_mode")
        self.assertEqual(switch.value, "Dark")
        switch.set_value("System").run()
        # System mode emits the solar day/night classes
        markdown0 = chr(10).join(item.value for item in self.app.markdown)
        self.assertIn("html.conclave-day {", markdown0)
        self.assertIn("html.conclave-night {", markdown0)
        switch.set_value("Light").run()
        self.assertEqual(self.app.session_state["k_theme_mode"], "Light")
        markdown = "\n".join(item.value for item in self.app.markdown)
        self.assertIn("--conclave-base: #eff1f4;", markdown)
        # ...and a Settings-side change flows back into the header control.
        self.app.session_state["k_theme_mode"] = "System"
        self.app.run()
        switch = next(c for c in self.app.segmented_control
                      if c.key == "hdr_k_theme_mode")
        self.assertEqual(switch.value, "System")

    def test_web_on_parks_no_web_sources_and_web_off_restores_them(self):
        _testing = literal_assignments("ALL_SOURCES_ON_FOR_TESTING")["ALL_SOURCES_ON_FOR_TESTING"]
        no_web = literal_assignments("NO_WEB_PROVIDERS")["NO_WEB_PROVIDERS"]
        self.app.session_state["k_en_qwen"] = True
        self.app.run()
        self.assertTrue(self.app.session_state["k_en_qwen"])
        self.assertEqual(self.app.session_state["k_en_mistral"], _testing)
        btn = next(b for b in self.app.button if b.key == "cmp_web_btn")
        btn.click().run()                                         # web ON everywhere
        self.assertTrue(self.app.session_state["k_web_gpt"])
        self.assertTrue(self.app.session_state["k_en_qwen"])
        self.assertNotIn("Qwen", no_web)
        self.assertTrue(self.app.session_state["k_web_qwen"])
        if _testing:
            self.assertFalse(self.app.session_state["k_en_mistral"])
        expected_parked = list(no_web) if _testing else ["DeepSeek"]
        self.assertIn("DeepSeek", no_web)
        self.assertNotIn("Kimi", no_web); self.assertNotIn("MiniMax", no_web)
        self.assertEqual(sorted(self.app.session_state["_web_parked"]),
                         sorted(expected_parked))
        self.assertTrue(self.app.session_state["k_web_grok"])
        self.assertTrue(self.app.session_state["k_web_kimi"])
        self.assertTrue(self.app.session_state["k_web_mm"])
        self.assertFalse(self.app.session_state["k_web_ds"])
        self.assertFalse(self.app.session_state["k_en_ds"])
        self.assertTrue(self.app.session_state["k_en_grok"])
        if _testing:
            self.assertTrue(self.app.session_state["k_en_kimi"])
        warnings = " ".join(w.value for w in self.app.warning)
        self.assertIn("Grok", warnings); self.assertNotIn("DeepSeek", warnings)
        self.assertIn("take longer and cost more", warnings)
        # at Thorough Grok is not below its floor -- no warning
        self.navigate("nav_council")
        dial = next(s for s in self.app.segmented_control if s.key == "run_preset_choice")
        dial.set_value("Thorough").run()
        self.assertNotIn("take longer and cost more",
                         " ".join(w.value for w in self.app.warning))
        dial = next(s for s in self.app.segmented_control if s.key == "run_preset_choice")
        dial.set_value("Light").run()
        self.assertIn("take longer and cost more",
                      " ".join(w.value for w in self.app.warning))
        self.navigate("back_from_council")     # the rest of the test needs the composer
        # a manual re-activation while web is on is re-parked next run
        if _testing:
            self.app.session_state["k_en_mistral"] = True
            self.app.run()
            self.assertFalse(self.app.session_state["k_en_mistral"])
        btn = next(b for b in self.app.button if b.key == "cmp_web_btn")
        btn.click().run()                                         # web OFF
        self.assertEqual(self.app.session_state["k_en_mistral"], _testing)   # restored
        # the dial went back to Light above, whose council has Qwen off
        self.assertFalse(self.app.session_state["k_en_qwen"])
        self.assertTrue(self.app.session_state["k_en_ds"])            # restored
        self.assertEqual(self.app.session_state["_web_parked"], [])

    def test_full_council_toggle_restores_the_initial_council_not_the_preset(self):
        fs = fake_st()
        restore = load_function("_restore_initial_council", {
            "st": fs, "NO_WEB_PROVIDERS": ("Qwen", "Mistral", "Kimi", "MiniMax", "NVIDIA"),
            "_sync_evaluator_deepseek_web_toggle": lambda _pfx: None})
        snap = load_function("snapshot_initial_council",
                             {"st": fs, "adjudicator_stack_active": lambda: True,
                              "_EN_KEY_TO_NAME": {},
                              "_EN_KEYS_ALL": ("k_en_gpt", "k_en_cla", "k_en_gem", "k_en_ds",
                                               "k_en_grok", "k_en_qwen", "k_en_mistral",
                                               "k_en_kimi")})
        fs.session_state["k_en_grok"] = True
        fs.session_state["k_en_mistral"] = True
        fs.session_state["k_en_qwen"] = False
        fs.session_state["k_adjudicator_on"] = True
        fs.session_state["initial_council"] = snap()
        # later the preset/cards drift: Grok off, Qwen on, adjudication off
        fs.session_state["k_en_grok"] = False
        fs.session_state["k_en_qwen"] = True
        fs.session_state["k_adjudicator_on"] = False
        fs.session_state["k_full_council"] = True
        restore()
        self.assertTrue(fs.session_state["k_en_grok"])
        self.assertTrue(fs.session_state["k_en_mistral"])
        self.assertFalse(fs.session_state["k_en_qwen"])
        self.assertTrue(fs.session_state["k_adjudicator_on"])
        # turning the toggle OFF changes nothing
        fs.session_state["k_en_grok"] = False
        fs.session_state["k_full_council"] = False
        restore()
        self.assertFalse(fs.session_state["k_en_grok"])
        # the initial send records the snapshot on the plan, the commit keeps it
        source = APP.read_text(encoding="utf-8")
        self.assertIn('"initial_council": snapshot_initial_council(),', source)
        self.assertIn('record["initial_council"] = plan["initial_council"]', source)
        self.assertIn('on_change=_restore_initial_council', source)

    def test_back_button_on_orphaned_views_returns_to_the_previous_one(self):
        self.assertEqual(self.app.session_state["main_view"], "⭐ Synthesis")
        for nav_key, back_key in (
                ("nav_history", "back_from_history"),
                ("nav_settings", "back_from_settings"),
                ("nav_projects", "back_from_projects"),
                ("nav_lenses", "back_from_lenses"),
                ("nav_document", "back_from_document"),
        ):
            self.navigate(nav_key)
            self.assertEqual(self.app.session_state["_prev_view"], "⭐ Synthesis")
            back = next(b for b in self.app.button if b.key == back_key)
            back.click().run()
            self.assertEqual(self.app.session_state["main_view"], "⭐ Synthesis")

        # A second hop (Settings -> Historical Chats) must remember Settings,
        # not silently fall back to Synthesis or keep the original view.
        self.navigate("nav_settings")
        self.navigate("nav_history")
        self.assertEqual(self.app.session_state["_prev_view"], "⚙ Settings")
        back = next(b for b in self.app.button if b.key == "back_from_history")
        back.click().run()
        self.assertEqual(self.app.session_state["main_view"], "⚙ Settings")

    def test_unreadable_key_file_can_be_replaced_only_after_confirmation(self):
        key_file = Path(self.test_data.name) / "api_keys.json"
        key_file.write_text("{malformed fixture", encoding="utf-8")
        self.app.run()
        self.navigate("nav_settings")

        save_button = next(button for button in self.app.button
                           if button.label == "💾 Save All Keys")
        recovery_button = next(button for button in self.app.button
                               if button.key == "replace_unreadable_keys")
        self.assertTrue(save_button.disabled)
        self.assertTrue(recovery_button.disabled)

        openai_input = next(field for field in self.app.text_input
                            if field.label == "OpenAI (ChatGPT)")
        openai_input.set_value("fixture-replacement-key").run()
        confirmation = next(box for box in self.app.checkbox
                            if box.key == "confirm_key_recovery")
        confirmation.set_value(True).run()
        recovery_button = next(button for button in self.app.button
                               if button.key == "replace_unreadable_keys")
        self.assertFalse(recovery_button.disabled)
        recovery_button.click().run()

        self.assertEqual(list(self.app.exception), [])
        self.assertTrue(any("Replacement keys saved" in success.value
                            for success in self.app.success))
        self.assertTrue(key_file.exists())
        self.assertEqual(len(list(Path(self.test_data.name).glob(
            "api_keys.json.*.unreadable-backup"))), 1)

    def test_main_composer_identifies_selected_synthesizer(self):
        self.assertFalse(any(b.key == "syn_dlg_top" for b in self.app.button))
        self.assertEqual(next(button for button in self.app.button
                              if button.key == "send_synth").label, "🚀 Send")
        self.navigate("nav_research")
        res_settings = next(button for button in self.app.button
                            if button.key == "res_dlg_btn")
        self.assertIn("Evaluator", res_settings.label)
        self.assertIn("Claude Opus 5", res_settings.label)
        self.assertNotIn("\U0001F310", res_settings.label)

    def test_evaluator_button_shows_globe_when_web_verify_is_on(self):
        self.navigate("nav_research")
        self.app.session_state["k_res_web"] = True
        self.app.run()
        res_settings = next(button for button in self.app.button
                            if button.key == "res_dlg_btn")
        self.assertIn("\U0001F310", res_settings.label)

    def test_synthesis_uses_one_contextual_composer(self):
        source_message = {
            "role": "assistant", "content": "Source answer.", "secs": 1.0,
            "in_tok": 10, "out_tok": 5, "cost": 0.01, "web": False,
            "model_id": "gpt-5.6-terra", "settings": "effort low",
        }
        synth_message = {
            "role": "assistant", "content": "Definitive answer.", "secs": 2.0,
            "in_tok": 20, "out_tok": 8, "cost": 0.02, "web": False,
            "model_id": "claude-sonnet-5", "settings": "effort medium",
        }
        self.app.session_state["threads"] = {
            "ChatGPT": [
                {"role": "user", "content": "fixture"},
                source_message,
            ],
        }
        self.app.session_state["synth"] = {
            "label": "Claude Sonnet 5", "provider": "Claude", "legend": {},
            "thread": [{"role": "user", "content": "fixture"}, synth_message],
        }
        self.app.session_state["sp_view"] = "⭐ Answer"
        self.app.run()

        button_keys = {button.key for button in self.app.button}
        self.assertNotIn("send_synth", button_keys)
        self.assertIn("sendbtn_fu_synth", button_keys)

        self.app.session_state["sp_view"] = "🧩 The Sources"
        self.app.run()
        self.assertNotIn("send_synth", {button.key for button in self.app.button})

        next(button for button in self.app.button
             if button.key == "new_chat_btn").click().run()
        button_keys = {button.key for button in self.app.button}
        self.assertIn("send_synth", button_keys)
        self.assertNotIn("sendbtn_fu_synth", button_keys)

    def test_background_view_uses_one_progress_surface_and_keeps_stop_controls(self):
        job_id = "fixture-running-job"
        self.app.session_state["_bg_reg"] = {
            "lock": threading.Lock(),
            "jobs": {
                job_id: {
                    "status": "running",
                    "plan": {
                        "mode": "synthesis",
                        "sources": {"ChatGPT": {"web": False, "cfg": {
                            "model_id": "gpt-5.6-terra",
                        }}},
                        "src_meta": {"ChatGPT": {
                            "model_id": "gpt-5.6-terra", "settings": "effort low",
                        }},
                        "phase2": {
                            "label_key": "Synthesizer", "label": "Claude Opus 5",
                            "kind": "synth", "web": False,
                            "model_id": "claude-opus-5", "settings": "effort high",
                        },
                    },
                    "phase": 1,
                    "progress": [],
                    "started": time.time(),
                    "error": None,
                    "cancel_requested": False,
                },
            },
        }
        self.app.session_state["prompt_text"] = "Fixture question to revise"
        self.app.session_state["bg_job_id"] = job_id
        self.app.run()
        button_keys = {button.key for button in self.app.button}
        self.assertNotIn("send_synth", button_keys)
        self.assertIn("stop_run_sidebar", button_keys)
        self.assertIn("stop_run_main_synthesis", button_keys)
        source = APP.read_text(encoding="utf-8")
        self.assertNotIn("bg_progress(compact=True)", source)
        self.assertIn("bg_refresh()", source)
        self.assertEqual(len(re.findall(r"(?m)^@st\.fragment\(run_every=0\.6\)", source)), 1)
        labels = [expander.label for expander in self.app.expander]
        self.assertFalse(any(label.startswith("Run details ·") for label in labels))
        markdown = "\n".join(item.value for item in self.app.markdown)
        self.assertIn("ChatGPT", markdown)
        self.assertIn("Synthesizer · Claude Opus 5", markdown)

        stop = next(button for button in self.app.button
                    if button.key == "stop_run_main_synthesis")
        stop.click().run()
        self.assertTrue(self.app.session_state["_bg_reg"]["jobs"][job_id]
                        ["cancel_requested"])
        stopping = next(button for button in self.app.button
                        if button.key == "stop_run_main_synthesis")
        self.assertTrue(stopping.disabled)

        with self.app.session_state["_bg_reg"]["lock"]:
            self.app.session_state["_bg_reg"]["jobs"][job_id]["status"] = "cancelled"
        self.app.run()
        self.assertNotIn("bg_job_id", self.app.session_state)
        self.assertEqual(self.app.session_state["prompt_text"],
                         "Fixture question to revise")
        self.assertIn("send_synth", {button.key for button in self.app.button})
        self.assertTrue(any("Run stopped" in warning.value for warning in self.app.warning))

    def test_brand_asset_and_ctrl_enter_bridge_are_present(self):
        source = APP.read_text(encoding="utf-8")
        self.assertTrue((ROOT / "assets" / "conclave_mark.png").is_file())
        self.assertIn("BRAND_MARK", source)
        self.assertIn("page_icon=BRAND_MARK", source)
        self.assertIn('event.key !== "Enter" || event.shiftKey', source)
        self.assertNotIn("!(event.ctrlKey || event.metaKey)", source)
        self.assertIn(".st-key-send_synth button", source)
        self.assertIn(".st-key-run_res_btn button", source)
        self.assertIn(".st-key-sendbtn_${followUp[1]} button", source)


class ProcessTransparencyTests(unittest.TestCase):

    def test_contribution_ledger_round_trips_through_the_legend(self):
        h = transparency_helpers()
        legend = {"A": "Claude", "B": "Gemini", "C": "Mistral"}
        raw = ("# Heading\nAnswer body.\n\n<<<CONCLAVE_STATE>>>facts<<<END_CONCLAVE_STATE>>>\n"
               f"{h['LEDGER_BEGIN']}\nA: the 1836 Specie Circular; Ohio bank failures\n"
               "- B: nothing unique\nAnswer C: a caveat on dating.\nZ: ignored\n"
               f"{h['LEDGER_END']}")
        visible, ledger = h["split_contribution_ledger"](raw)
        self.assertNotIn("CONCLAVE_CONTRIBUTIONS", visible)
        self.assertIn("<<<CONCLAVE_STATE>>>", visible)   # the capsule split is separate
        parsed = h["parse_contribution_ledger"](ledger, legend)
        self.assertEqual(parsed, {"Claude": ["the 1836 Specie Circular", "Ohio bank failures"],
                                  "Gemini": [], "Mistral": ["a caveat on dating"]})
        # no block present -> unchanged text, empty ledger
        self.assertEqual(h["split_contribution_ledger"]("plain"), ("plain", ""))
        self.assertEqual(h["parse_contribution_ledger"]("", legend), {})
        # the instruction names every letter and both markers
        text = h["contribution_ledger_instruction"](legend)
        for token in ("A, B, C", h["LEDGER_BEGIN"], h["LEDGER_END"], h["LEDGER_NOTHING"]):
            self.assertIn(token, text)

    def test_synth_and_disposition_prompts_request_the_ledger_and_context_carries_it(self):
        h = transparency_helpers()
        consts = literal_assignments("CONTEXT_CHAR_BUDGET")
        ns = {"random": __import__("random"),
              "state_capsule_instruction": lambda research=False: "STATE CAPSULE"}
        build = load_function("build_synth_prompt", ns)
        prompt, legend = build("Q?", {"Claude": "a", "Gemini": "b"}, False)
        self.assertIn(h["LEDGER_BEGIN"], prompt)
        self.assertLess(prompt.index("STATE CAPSULE"), prompt.index(h["LEDGER_BEGIN"]))
        dispo = load_function("build_disposition_prompt", dict(ns))
        text = dispo("Q?", "draft", "report", {"Claude": "a"}, {"A": "Claude"}, "Light")
        self.assertIn(h["LEDGER_BEGIN"], text)
        solo = load_function("build_solo_followup_prompt", dict(ns))("Q?", "ctx", False)
        self.assertIn("ledger of what each independent source uniquely contributed", solo)
        ctx = load_function("build_working_context",
                            {"CONTEXT_CHAR_BUDGET": consts["CONTEXT_CHAR_BUDGET"]})
        record = {"memory": "facts", "thread": [],
                  "turns": [{"turn_id": 0, "question": "Q?",
                             "contributions": {"Claude": ["x"], "Gemini": []}}]}
        out = ctx(record, "Q?", [], "Light")
        self.assertIn("Source contributions on record", out)
        self.assertIn("Claude: x", out)
        self.assertIn("Gemini: " + h["LEDGER_NOTHING"], out)
        self.assertNotIn("Source contributions", ctx({"memory": "f", "thread": []}, "Q?", [], "Light"))

    def test_worker_decodes_the_ledger_onto_phase2_output(self):
        h = transparency_helpers()
        wanted = {"_bg_worker", "_bg_update", "_bg_cancel_requested", "_bg_mark_cancelled",
                  "_bg_set_error"}
        nodes = [node for node in source_tree().body
                 if isinstance(node, ast.FunctionDef) and node.name in wanted]
        ledger_text = (f"{h['LEDGER_BEGIN']}\nA: unique fact\n{h['LEDGER_END']}")

        def fake_run_one(name, key, messages, cfg, web):
            text = ("# Title\nbody" + ledger_text) if name == "Claude" else "src"
            return {"text": text, "secs": 1.0, "in_tok": 1, "out_tok": 1, "cost": 0.0,
                    "web": False, "provider": name, "credit_err": False, "err": False}

        namespace = {
            "time": time, "ThreadPoolExecutor": ThreadPoolExecutor, "wait": wait,
            "FIRST_COMPLETED": FIRST_COMPLETED, "run_one": fake_run_one,
            "build_source_turn_message": lambda *a, **k: "src msg",
            "build_synth_prompt": lambda *_a: ("packet", {"A": "Gemini"}),
            "build_research_prompt": lambda *_a: self.fail("not research"),
            "split_state_capsule": lambda text: (text, ""),
        }
        for _k, _v in h.items():
            namespace.setdefault(_k, _v)
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        exec(compile(module, str(APP), "exec"), namespace)
        job_id = "ledger-fixture"
        reg = {"lock": threading.Lock(), "jobs": {job_id: {
            "status": "running", "progress": [], "cancel_requested": False}}}
        plan = {"prompt": "Q?", "turn_id": 0, "continuation": False,
                "sources": {"Gemini": {"provider": "Gemini", "key": "k", "messages": [],
                                       "cfg": {}, "web": False}},
                "phase2": {"kind": "synth", "provider": "Claude", "key": "k", "cfg": {},
                           "web": False, "label_key": "Synthesizer", "files": [],
                           "min_good": 1}}
        namespace["_bg_worker"](reg, job_id, plan)
        job = reg["jobs"][job_id]
        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertEqual(job["phase2"]["contributions"], {"Gemini": ["unique fact"]})
        self.assertNotIn("CONCLAVE_CONTRIBUTIONS", job["phase2"]["result"]["text"])

    def test_reasoning_capture_is_thread_local_and_reads_every_shape(self):
        h = transparency_helpers()
        h["_take_reasoning"]()
        # OpenAI-compatible reasoning_content extra
        choice = SimpleNamespace(message=SimpleNamespace(
            reasoning_content="I think...", content="answer"))
        h["_reasoning_from_choice"](choice)
        # Mistral thinking chunk
        choice2 = SimpleNamespace(message=SimpleNamespace(
            reasoning_content=None, model_extra={},
            content=[{"type": "thinking", "thinking": [{"type": "text", "text": "chunk"}]},
                     {"type": "text", "text": "answer"}]))
        h["_reasoning_from_choice"](choice2)
        h["_reasoning_from_inline"]("<think>inline</think>answer")
        got = h["_take_reasoning"]()
        self.assertEqual(got, "I think...\n\nchunk\n\ninline")
        self.assertIsNone(h["_take_reasoning"]())           # slot cleared
        seen = {}
        def other():
            h["_note_reasoning"]("theirs")
            seen["other"] = h["_take_reasoning"]()
        h["_note_reasoning"]("mine")
        th = threading.Thread(target=other); th.start(); th.join()
        self.assertEqual(seen["other"], "theirs")
        self.assertEqual(h["_take_reasoning"](), "mine")
        # the cap holds
        h["_note_reasoning"]("x" * (h["REASONING_STORE_LIMIT"] + 500))
        self.assertEqual(len(h["_take_reasoning"]()), h["REASONING_STORE_LIMIT"])

    def test_run_one_and_assistant_msg_carry_returned_reasoning(self):
        h = transparency_helpers()
        def fake_call(name, key, messages, cfg, web):
            h["_note_reasoning"]("because")
            return "answer", 1, 1
        run_one = load_function("run_one", {
            "time": time, "call_model": fake_call, "cost_of": lambda *a: 0.0,
            "model_id_of": lambda n, c: "m", "_is_timeout_error": lambda e: False,
            "_is_credit_error": lambda n, e: False})
        r = run_one("Claude", "key", [], {}, False)
        self.assertEqual(r["reasoning"], "because")
        msg = load_function("assistant_msg", {})(r, "m", "s", 0)
        self.assertEqual(msg["reasoning"], "because")
        # a failed call keeps no partial reasoning
        def boom(name, key, messages, cfg, web):
            h["_note_reasoning"]("partial"); raise RuntimeError("down")
        run_one = load_function("run_one", {
            "time": time, "call_model": boom, "cost_of": lambda *a: 0.0,
            "model_id_of": lambda n, c: "m", "_is_timeout_error": lambda e: False,
            "_is_credit_error": lambda n, e: False})
        r = run_one("Claude", "key", [], {}, False)
        self.assertTrue(r["err"]); self.assertIsNone(r["reasoning"])
        self.assertNotIn("reasoning", load_function("assistant_msg", {})(r, "m", "s"))

    def test_reasoning_exposure_covers_every_council_provider(self):
        consts = literal_assignments("REASONING_EXPOSURE", "MODEL_ORDER")
        table = consts["REASONING_EXPOSURE"]
        for name in consts["MODEL_ORDER"]:
            self.assertIn(name, table)
            self.assertIn(table[name][0], ("returns", "private"))
        self.assertEqual(table["ChatGPT"][0], "private")
        self.assertEqual(table["DeepSeek"][0], "returns")
        src = APP.read_text(encoding="utf-8")
        # every provider branch that receives reasoning hands it over
        self.assertGreaterEqual(src.count("_reasoning_from_choice(choice)"), 8)
        self.assertIn('if b.type == "thinking":', src)
        self.assertIn("reasoning_coverage_caption(names)", src)

    def test_synthesizer_provider_lock_guards_the_picker_and_the_bump(self):
        state = {"synth": {"provider": "Claude"}, "k_syn_provider": "Claude",
                 "k_adj_provider": "ChatGPT"}
        ns = {"st": SimpleNamespace(session_state=state), "EVAL_PROVIDERS": ["Claude", "ChatGPT", "Gemini"],
              "MODEL_ORDER": ["Claude", "ChatGPT", "Gemini"],
              "current_preset_name": lambda: "Light", "retunes": [],
              "_enforce_adjudicator_cross_provider": lambda tier=None: None}
        ns["_evaluator_provider_changed"] = lambda prefix, tier: ns["retunes"].append(prefix)
        locked = load_function("synth_locked_provider", ns)
        self.assertEqual(locked(), "Claude")
        set_synth = load_function("_set_synth_provider", ns)
        set_synth("Gemini")
        self.assertEqual(state["k_syn_provider"], "Claude")      # ignored while locked
        # a colliding Adjudicator pick bumps the ADJUDICATOR, never the locked seat
        load_function("_next_provider_other_than", ns)
        set_adj = load_function("_set_adjudicator_provider", ns)
        set_adj("Claude")
        self.assertEqual(state["k_syn_provider"], "Claude")
        self.assertEqual(state["k_adj_provider"], "ChatGPT")
        # a stale picker value is re-asserted every run
        state["k_syn_provider"] = "Gemini"
        load_function("_enforce_synth_lock", ns)()
        self.assertEqual(state["k_syn_provider"], "Claude")
        # unlock -> free again; no chat -> never locked
        load_function("_unlock_synth_provider", ns)()
        self.assertIsNone(locked())
        set_synth("Gemini")
        self.assertEqual(state["k_syn_provider"], "Gemini")
        state.pop("synth"); state.pop("_synth_unlocked")
        self.assertIsNone(locked())

    def test_run_report_helpers_count_citations_and_place_bars(self):
        ns = {}
        mentions = load_function("_letter_mentions", ns)
        legend = {"A": "Claude", "B": "Gemini"}
        self.assertEqual(mentions("Answer A gave X; B and A agreed. A C-grade note.", legend),
                         {"Claude": 3, "Gemini": 1})
        timeline = load_function("_timeline_html", {"html": html})
        out = timeline([{"label": "Claude", "start": 0.0, "secs": 10.0, "color": "#fff"},
                        {"label": "⭐ Synth", "start": 10.0, "secs": 5.0, "color": "#000"}], 20.0)
        self.assertIn("left:0.0%", out); self.assertIn("left:50.0%", out)
        self.assertIn("width:25.0%", out); self.assertIn("0s → 20s", out)
        src = APP.read_text(encoding="utf-8")
        self.assertIn("render_run_report(\n", src)
        self.assertIn('"contributions": contributions,', src)


class LocalOnlyPrivacyTests(unittest.TestCase):
    """The app has no login of its own, so its exposure surface is the config."""

    def test_server_binds_loopback_only_everywhere_it_is_started(self):
        config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('address = "127.0.0.1"', config)
        self.assertIn("enableXsrfProtection = true", config)
        self.assertIn("enableCORS = true", config)
        self.assertIn("gatherUsageStats = false", config)

        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("--server.address 127.0.0.1", launcher)

    def _key_helpers(self, keys_file, dpapi):
        nodes = [node for node in source_tree().body
                 if isinstance(node, (ast.FunctionDef, ast.Assign))
                 and (getattr(node, "name", None) in {
                     "_exclusive_file_lock", "_atomic_write_json", "_enc", "_dec",
                     "_require_key_encryption", "save_keys", "load_keys",
                 } or any(isinstance(target, ast.Name)
                          and target.id == "KEYS_UNENCRYPTED_MESSAGE"
                          for target in getattr(node, "targets", [])))]
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        namespace = {
            "KEYS_FILE": str(keys_file),
            "_DPAPI": dpapi,
            "base64": __import__("base64"),
            "contextmanager": contextmanager,
            "json": json,
            "msvcrt": msvcrt,
            "os": os,
            "threading": threading,
            "time": time,
            "uuid": uuid,
        }
        for _k, _v in transparency_helpers().items():
            namespace.setdefault(_k, _v)
        exec(compile(module, str(APP), "exec"), namespace)
        return namespace

    def test_keys_are_never_written_readable_when_dpapi_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            keys_file = Path(tmp) / "api_keys.json"
            helpers = self._key_helpers(keys_file, dpapi=False)
            with self.assertRaises(RuntimeError):
                helpers["save_keys"]({"openai": "sk-fixture-not-a-real-key"})
            self.assertFalse(keys_file.exists())
            # Clearing keys must still work without DPAPI.
            helpers["save_keys"]({"openai": ""})
            self.assertTrue(keys_file.exists())

    def test_saving_disabled_in_settings_without_key_encryption(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("disabled=_keys_file_problem or not _DPAPI", source)
        self.assertIn("_require_key_encryption(keys)", source)


class ProviderSweepV368Tests(unittest.TestCase):

    def test_mistral_image_shape_is_a_bare_data_url_string(self):
        ns = {}
        for fn in ("_inline_text_files", "_binary_files", "_image_text_only_note",
                   "_pdf_unsupported_note"):
            load_function(fn, ns)
        build = load_function("_oai_chat_messages", ns)
        image = {"name": "chart.png", "kind": "image", "mime": "image/png", "b64": "aW1n"}
        msgs = [{"role": "user", "content": "Q", "files": [image]}]
        string_shape = build(msgs, pdf_ok=False, image_ok=True, image_shape="string")
        self.assertEqual(string_shape[0]["content"][0],
                         {"type": "image_url", "image_url": "data:image/png;base64,aW1n"})
        object_shape = build(msgs, pdf_ok=False, image_ok=True)
        self.assertEqual(object_shape[0]["content"][0]["image_url"],
                         {"url": "data:image/png;base64,aW1n"})

    def test_claude_always_think_models_request_the_summary_display(self):
        captured = {}

        class FakeMessages:
            def create(self, **kw):
                captured.clear(); captured.update(kw)
                return SimpleNamespace(
                    content=[SimpleNamespace(type="thinking", thinking="why"),
                             SimpleNamespace(type="text", text="Answer")],
                    stop_reason="end_turn",
                    usage=SimpleNamespace(input_tokens=3, output_tokens=2))

        class FakeAnthropic:
            def __init__(self, **_kw):
                self.messages = FakeMessages()

        ns = literal_assignments("CLAUDE_NO_DIALS", "CLAUDE_ALWAYS_THINK", "CLAUDE_OLD_SEARCH",
                                 "CLAUDE_NO_DISABLE_ABOVE_HIGH")
        ns.update({"Anthropic": FakeAnthropic, "_request_timeout": lambda *a: 150.0,
                   "_claude_messages": lambda m: m})
        load_function("claude_thinking_forced", ns)
        call = load_function("call_model", ns)
        ns["_take_reasoning"]()
        text, *_ = call("Claude", "k", [{"role": "user", "content": "Q"}],
                        {"model_id": "claude-fable-5-1", "effort": "max", "thinking": True,
                         "max_searches": 2}, False)
        self.assertEqual(text, "Answer")
        self.assertEqual(captured["thinking"], {"type": "adaptive", "display": "summarized"})
        self.assertEqual(captured["output_config"], {"effort": "max"})
        self.assertIn("why", ns["_take_reasoning"]())
        # an explicit OFF never carries the display field (invalid with disabled)
        call("Claude", "k", [{"role": "user", "content": "Q"}],
             {"model_id": "claude-sonnet-5", "effort": "low", "thinking": False,
              "max_searches": 2}, False)
        self.assertEqual(captured["thinking"], {"type": "disabled"})
        captured.clear()
        call("Claude", "k", [{"role": "user", "content": "Q"}],
             {"model_id": "claude-haiku-4-5", "effort": "low", "thinking": False,
              "max_searches": 2}, False)
        self.assertNotIn("thinking", captured)
        self.assertNotIn("output_config", captured)

    def test_astra_never_sends_effort_none(self):
        captured = {}

        class FakeCompletions:
            def create(self, **kw):
                captured.update(kw)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="ok"),
                                             finish_reason="stop")],
                    usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))

        class FakeOpenAI:
            def __init__(self, **_kw):
                self.chat = SimpleNamespace(completions=FakeCompletions())

        ns = {"OpenAI": FakeOpenAI, "_request_timeout": lambda *a: 150.0,
              "_oai_responses_input": lambda messages, pdf_ok: messages,
              "_responses_call": lambda client, model, inp, effort, **k:
                  captured.update(effort=effort, **k) or ("ok", 1, 1)}
        ns.update(literal_assignments("CHATGPT_SPEED"))
        call = load_function("call_model", ns)
        call("ChatGPT", "k", [{"role": "user", "content": "Q"}],
             {"model_id": "gpt-6-astra", "effort": "none", "speed": "Standard"}, False)
        self.assertEqual(captured["effort"], "low")
        self.assertFalse(captured["tools"]); self.assertEqual(captured["reasoning_summary"], "auto")
        self.assertNotIn("temperature", captured)
        settings_str = load_function("settings_str", dict(ns, **literal_assignments(
            "CLAUDE_NO_DIALS", "CLAUDE_ALWAYS_THINK", "MISTRAL_REASONING_MODELS",
            "KIMI_ALWAYS_THINK", "MINIMAX_THINK_OFF_OK", "GLM_ALWAYS_THINK")))
        self.assertIn("no Instant on this model",
                      settings_str("ChatGPT", {"model_id": "gpt-6-astra", "effort": "none"}))
        self.assertNotIn("Astra",
                         settings_str("ChatGPT", {"model_id": "gpt-5.6-luna", "effort": "none"}))

    def test_priority_tiers_and_spend_cap_error(self):
        ns = {"PRICES": literal_dict_string_entries("PRICES"),
              "PRIORITY_PRICES": literal_assignments("PRIORITY_PRICES")["PRIORITY_PRICES"]}
        cost_of = load_function("cost_of", ns)
        self.assertAlmostEqual(cost_of("MiniMax-M3", 1_000_000, 0, "Priority"), 0.45)
        self.assertAlmostEqual(cost_of("MiniMax-M3", 1_000_000, 0, "Standard"), 0.30)
        self.assertAlmostEqual(cost_of("gpt-6-astra", 1_000_000, 0, "Fast"), 20.0)
        self.assertAlmostEqual(cost_of("gpt-6-astra", 0, 1_000_000), 50.0)
        creds = load_credit_error()
        AnthropicErr = creds["AnthropicAPIStatusError"]
        err = AnthropicErr("Too many requests", type="rate_limit_error")
        err.body = {"error": {"type": "rate_limit_error",
                              "details": {"error_code": "enforced_spend_limit_reached"}}}
        self.assertTrue(creds["_is_credit_error"]("Claude", err))
        plain = AnthropicErr("Too many requests", type="rate_limit_error")
        plain.body = {"error": {"type": "rate_limit_error"}}
        self.assertFalse(creds["_is_credit_error"]("Claude", plain))

    def test_retired_labels_all_migrate_to_live_picker_options(self):
        v = literal_assignments("_RETIRED_MODEL_LABELS", "CHATGPT_MODELS", "GEMINI_MODELS",
                                "META_MODELS", "QWEN_MODELS", "GLM_MODELS", "CLAUDE_MODELS",
                                "DEEPSEEK_MODELS", "MINIMAX_MODELS", "NVIDIA_MODELS",
                                "MISTRAL_MODELS", "KIMI_MODELS")
        tables = {"Kimi": v["KIMI_MODELS"], "NVIDIA": v["NVIDIA_MODELS"], "ChatGPT": v["CHATGPT_MODELS"], "Gemini": v["GEMINI_MODELS"],
                  "Meta": v["META_MODELS"], "Qwen": v["QWEN_MODELS"], "GLM": v["GLM_MODELS"],
                  "Claude": v["CLAUDE_MODELS"], "DeepSeek": v["DEEPSEEK_MODELS"],
                  "MiniMax": v["MINIMAX_MODELS"], "Mistral": v["MISTRAL_MODELS"]}
        for provider, aliases in v["_RETIRED_MODEL_LABELS"].items():
            for old, new in aliases.items():
                self.assertNotIn(old, tables[provider], (provider, old))
                self.assertIn(new, tables[provider], (provider, new))
        # every retired ID still has a price so saved chats keep costing
        prices = literal_dict_string_entries("PRICES")
        for mid in ("gpt-5.6-sol", "gemini-3.7-flash", "muse-spark-1.2", "qwen3.6-flash",
                    "glm-5-turbo", "claude-fable-5", "deepseek-v4-flash",
                    "deepseek-v4-flash-vision-exp", "claude-opus-5", "grok-4.6",
                    "MiniMax-M2.7", "glm-4.7", "claude-sonnet-5",
                    "nvidia/nemotron-3-nano-30b-a3b",
                    "claude-haiku-4-5", "mistral-large-latest"):
            self.assertIn(mid, prices, mid)


class ProviderSweepV385Tests(unittest.TestCase):

    def _claude(self, model_id, effort, thinking, web=False, fail_first=None):
        sent = []

        class Messages:
            def create(self, **kw):
                sent.append(json.loads(json.dumps(kw)))
                if fail_first and len(sent) == 1:
                    raise RuntimeError(fail_first)
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="answer")],
                                       stop_reason="end_turn",
                                       usage=SimpleNamespace(input_tokens=10, output_tokens=5))
        ns = dict(sweep_helpers())
        ns.update(literal_assignments("CLAUDE_NO_DIALS", "CLAUDE_OLD_SEARCH",
                                      "CLAUDE_NO_DISABLE_ABOVE_HIGH"))
        ns.update({
            "Anthropic": lambda **kw: SimpleNamespace(messages=Messages()),
            "_request_timeout": lambda *a: 1, "_claude_messages": lambda m: m,
        })
        load_function("claude_thinking_forced", ns)
        call = load_function("call_model", ns)
        call("Claude", "k", [{"role": "user", "content": "Q"}],
             {"model_id": model_id, "effort": effort, "thinking": thinking, "max_searches": 2}, web)
        return sent

    def test_haiku_55_gets_real_dials_and_the_right_off_shape(self):
        v = literal_assignments("CLAUDE_MODELS", "CLAUDE_NO_DIALS", "CLAUDE_NO_DISABLE_ABOVE_HIGH")
        self.assertEqual(v["CLAUDE_MODELS"]["Haiku 5.5"], "claude-haiku-5-5")
        self.assertNotIn("claude-haiku-5-5", v["CLAUDE_NO_DIALS"])
        self.assertIn("claude-haiku-5-5", v["CLAUDE_NO_DISABLE_ABOVE_HIGH"])
        off = self._claude("claude-haiku-5-5", "low", False)[0]
        self.assertEqual(off["output_config"], {"effort": "low"})
        self.assertEqual(off["thinking"], {"type": "disabled"})
        for banned in ("temperature", "top_p", "top_k"):
            self.assertNotIn(banned, off)
        on = self._claude("claude-haiku-5-5", "medium", True)[0]
        self.assertEqual(on["thinking"], {"type": "adaptive", "display": "summarized"})
        forced = self._claude("claude-haiku-5-5", "max", False)[0]   # off is a 400 at max
        self.assertEqual(forced["thinking"]["type"], "adaptive")

    def test_claude_web_retry_catches_allowed_callers(self):
        sent = self._claude("claude-haiku-5-5", "low", False, web=True,
                            fail_first="400: set allowed_callers to ['direct'] for this model")
        self.assertEqual(sent[0]["tools"][0]["type"], "web_search_20260209")
        self.assertEqual(sent[1]["tools"][0]["type"], "web_search_20250305")

    def test_chatgpt_flex_ultrafast_and_long_prompt_pricing(self):
        ns = dict(sweep_helpers())
        ns["PRICES"] = literal_dict_string_entries("PRICES")
        ns.update(literal_assignments("PRIORITY_PRICES"))
        cost = load_function("cost_of", ns)
        self.assertAlmostEqual(cost("gpt-6.1-sol", 1_000_000, 1_000_000, "Flex"), 6.0)
        self.assertAlmostEqual(cost("gpt-6.1-sol", 1_000_000, 1_000_000, "Ultrafast"), 72.0)
        self.assertAlmostEqual(cost("gpt-6.1-sol", 1_000_000, 0, "Fast"), 4.0)
        self.assertAlmostEqual(cost("claude-haiku-5-5", 50_000, 0), 0.005)
        self.assertAlmostEqual(cost("claude-haiku-5-5", 200_000, 0), 0.10)     # >100K tier
        self.assertAlmostEqual(cost("grok-4.7", 250_000, 0), 1.0)
        used = ns["chatgpt_speed_used"]
        self.assertEqual(used("gpt-6-luna", "Ultrafast"), "Fast")     # Luna has no Ultrafast
        self.assertEqual(used("gpt-6-astra", "Ultrafast"), "Ultrafast")
        self.assertEqual(used("gpt-6-luna", "Flex"), "Flex")
        for mid in ns["CHATGPT_ULTRAFAST_MODELS"]:
            self.assertIn(mid, ns["ULTRAFAST_PRICES"])
        prices = literal_dict_string_entries("PRICES")
        for mid, (i, o) in ns["FLEX_PRICES"].items():
            self.assertEqual((i, o), tuple(x / 2 for x in prices[mid]), mid)

    def test_flex_busy_resends_at_standard_and_is_priced_so(self):
        tiers = []

        def fake_responses(client, model_id, inp, effort, **kw):
            tiers.append(kw["service_tier"])
            if kw["service_tier"] == "flex":
                raise RuntimeError("Error code: 429 - Resource Unavailable")
            return "answer", 1_000_000, 0
        ns = dict(sweep_helpers())
        ns["PRICES"] = literal_dict_string_entries("PRICES")
        ns.update(literal_assignments("PRIORITY_PRICES"))
        ns.update({"OpenAI": lambda **kw: SimpleNamespace(), "_request_timeout": lambda *a: 1,
                   "_responses_call": fake_responses, "_oai_responses_input": lambda m, **k: m})
        call = load_function("call_model", ns)
        ns["_take_speed"]()
        call("ChatGPT", "k", [], {"model_id": "gpt-6.1-sol", "effort": "low", "speed": "Flex"}, False)
        self.assertEqual(tiers, ["flex", "default"])
        self.assertEqual(ns["_take_speed"](), "Standard")
        self.assertFalse(ns["_is_flex_busy_error"](RuntimeError("429 rate_limit_exceeded")))
        cap = load_function("meta_caption", {})({"model_id": "m", "settings": "s",
                                                 "speed_used": "Standard"})
        self.assertIn("ran at Standard", cap)

    def test_mistral_large_4_ladder_and_alternate_id(self):
        presets = literal_assignments("PRESETS")["PRESETS"]
        self.assertEqual([presets[n]["k_mistral_model"] for n in
                          ("Fast", "Light", "Balanced", "Thorough", "Deep")],
                         ["Small 4 (economy)", "Small 4 (economy)", "Large 3 (balanced)",
                          "Large 4 (flagship · preview)", "Large 4 (flagship · preview)"])
        sent = []

        class Completions:
            def create(self, **kw):
                sent.append(dict(kw))
                if kw["model"] == "mistral-large-4":
                    raise RuntimeError("Invalid model: mistral-large-4")
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="ok"),
                                             finish_reason="stop")],
                    usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
        ns = dict(sweep_helpers())
        ns.update({"OpenAI": lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
                   "_request_timeout": lambda *a: 1,
                   "_oai_chat_messages": lambda m, **k: m,
                   "_reasoning_from_choice": lambda c: None,
                   "_content_text": lambda c: c})
        fn = load_function("_mistral_call", ns)
        fn("Mistral", "k", [], {"model_id": "mistral-large-4", "effort": "high"}, False)
        self.assertEqual([s["model"] for s in sent], ["mistral-large-4", "mistral-large-4-0"])
        self.assertEqual(sent[0]["reasoning_effort"], "high")

    def test_qwen_reads_pages_by_default_but_can_search_itself(self):
        v = literal_assignments("PROVIDERS", "WEBSRC_SHARED_DEFAULT", "QWEN_WEB_MODELS")
        self.assertEqual(v["PROVIDERS"]["Qwen"]["web_key"], "k_web_qwen")
        self.assertTrue(v["WEBSRC_SHARED_DEFAULT"]["Qwen"])
        self.assertEqual(v["QWEN_WEB_MODELS"], {"qwen3.8-max", "qwen3.8-flash"})


class ReleasePrepV388Tests(unittest.TestCase):

    def test_model_check_finds_retired_and_new_models(self):
        ns = literal_assignments("MODEL_CHECK_SKIP")
        cmp = load_function("compare_model_lists", ns)
        missing, new = cmp(["gpt-6-astra", "gpt-6.1-sol"],
                           ["gpt-6-astra", "gpt-6-astra-2026-09-03", "gpt-6.2-sol",
                            "gpt-6-tts", "text-embedding-4"], "gpt-")
        self.assertEqual(missing, ["gpt-6.1-sol"])
        self.assertEqual(new, ["gpt-6.2-sol"])            # snapshot, tts, embedding left out
        missing, new = cmp(["gemini-4-argon"], ["models/gemini-4-argon", "models/gemini-5-pro"],
                           "gemini-")
        self.assertEqual((missing, new), ([], ["gemini-5-pro"]))
        missing, _ = cmp(["mistral-large-4"], ["mistral-large-4-0"], "mistral-",
                         {"mistral-large-4": "mistral-large-4-0"})
        self.assertEqual(missing, [])                     # its alternate ID counts

    def test_sources_without_a_key_are_skipped(self):
        ns = {"KEY_MAP": {"Claude": "anthropic", "ChatGPT": "openai", "Gemini": "google"}}
        keyed = load_function("keyed_sources", ns)
        self.assertEqual(keyed(["Claude", "ChatGPT", "Gemini"], {"openai": "k", "google": ""}),
                         (["ChatGPT"], ["Claude", "Gemini"]))
        self.assertIn("ALL_SOURCES_ON_FOR_TESTING = False", APP.read_text(encoding="utf-8"))


class ModelAuditV387Tests(unittest.TestCase):

    def test_argon_is_offered_priced_and_has_no_minimal(self):
        models = literal_assignments("GEMINI_MODELS")["GEMINI_MODELS"]
        self.assertEqual(models["Argon (4)"], "gemini-4-argon")
        self.assertEqual(literal_dict_string_entries("PRICES")["gemini-4-argon"], (2.00, 10.00))
        self.assertNotIn("gemini-4-argon",
                         literal_assignments("GEMINI_MINIMAL_MODELS")["GEMINI_MINIMAL_MODELS"])
        self.assertIn('SYNTH_GEMINI = "gemini-4-argon"', APP.read_text(encoding="utf-8"))

    def test_claude_cache_reads_are_priced_per_model(self):
        ns = {"PRICES": {"claude-opus-5-5": (4.0, 20.0), "claude-fable-5-1": (10.0, 50.0),
                         "claude-haiku-5-5": (0.1, 0.5)}, "PRIORITY_PRICES": {}}
        ns.update(literal_assignments("CLAUDE_CACHE_WRITE", "CLAUDE_CACHE_READ",
                                      "CACHED_INPUT_PRICES", "CLAUDE_CACHE_READ_BY_MODEL"))
        ns["cost_of"] = lambda m, i, o, s="Standard": i / 1e6 * ns["PRICES"][m][0]
        cwc = load_function("cost_with_cache", ns)
        self.assertAlmostEqual(cwc("claude-opus-5-5", 100_000, 0, cache={"read": 100_000}),
                               0.4 - 100_000 * 0.95 * 4 / 1e6)
        self.assertAlmostEqual(cwc("claude-fable-5-1", 100_000, 0, cache={"read": 100_000}),
                               1.0 - 100_000 * 0.975 * 10 / 1e6)
        self.assertAlmostEqual(cwc("claude-haiku-5-5", 100_000, 0, cache={"read": 100_000}),
                               0.01 - 100_000 * 0.9 * 0.1 / 1e6)

    def test_flash_lite_answers_on_flash_while_searching(self):
        seen = []
        ns = dict(sweep_helpers())
        ns["PRICES"] = literal_dict_string_entries("PRICES")
        ns.update(literal_assignments("PRIORITY_PRICES"))
        ns["GEMINI_NO_SEARCH_MODELS"] = ("gemini-3.5-flash-lite",)
        load_function("gemini_cfg_for_web", ns)
        load_function("cost_with_cache", ns)
        load_function("_is_timeout_error", ns)
        load_function("_is_web_unavailable_error", ns)
        ns.update({"call_model": lambda n, k, m, cfg, w: (seen.append((cfg["model_id"], w)) or ("ok", 10, 5)),
                   "_is_credit_error": lambda *a: False, "_take_reasoning": lambda: None,
                   "cost_of": lambda m, i, o, s="Standard": 0.0,
                   "model_id_of": lambda n, c: c["model_id"]})
        run_one = load_function("run_one", ns)
        assistant_msg = load_function("assistant_msg", ns)
        cfg = {"model_id": "gemini-3.5-flash-lite", "level": "minimal"}
        r = run_one("Gemini", "k", [], cfg, True)
        self.assertEqual(seen[-1], ("gemini-3.8-flash", True))
        self.assertEqual(r["model_used"], "gemini-3.8-flash")
        self.assertEqual(cfg["model_id"], "gemini-3.5-flash-lite")
        msg = assistant_msg(r, "gemini-3.5-flash-lite", "minimal")
        self.assertEqual(msg["model_id"], "gemini-3.8-flash")
        self.assertIn("answered by gemini-3.8-flash", msg["settings"])
        r = run_one("Gemini", "k", [], cfg, False)                       # web off: unchanged
        self.assertEqual(seen[-1], ("gemini-3.5-flash-lite", False))
        self.assertNotIn("model_used", r)


class AuditV386Tests(unittest.TestCase):

    def test_billed_but_rejected_calls_keep_their_cost(self):
        ns = dict(sweep_helpers())
        ns["PRICES"] = literal_dict_string_entries("PRICES")
        ns.update(literal_assignments("PRIORITY_PRICES"))
        ns["cost_of"] = lambda m, i, o, s="Standard": i / 1e6 * 2.0 + (o or 0) / 1e6 * 10.0
        load_function("cost_with_cache", ns)
        load_function("_is_timeout_error", ns)
        load_function("_is_web_unavailable_error", ns)
        load_function("assistant_msg", ns)

        def boom(*a, **k):
            raise ns["_billed_error"]("empty", 1000, 200)
        ns.update({"call_model": boom, "_is_credit_error": lambda *a: False,
                   "model_id_of": lambda n, c: "gpt-6.1-sol",
                   "_take_reasoning": lambda: None})
        run_one = load_function("run_one", ns)
        r = run_one("ChatGPT", "k", [], {"model_id": "gpt-6.1-sol"}, False)
        self.assertTrue(r["err"])
        self.assertEqual((r["in_tok"], r["out_tok"]), (1000, 200))
        self.assertAlmostEqual(r["cost"], 1000 / 1e6 * 2.0 + 200 / 1e6 * 10.0)

    def test_claude_continues_a_paused_search_turn(self):
        calls = []

        class Blk(SimpleNamespace):
            def model_dump(self, exclude_none=True):
                return {"type": self.type}

        def fake_create(client, kw):
            calls.append(kw)
            if len(calls) == 1:
                return SimpleNamespace(content=[Blk(type="server_tool_use")], stop_reason="pause_turn",
                                       usage=SimpleNamespace(input_tokens=100, output_tokens=10))
            return SimpleNamespace(content=[Blk(type="web_search_tool_result"),
                                            Blk(type="text", text="final answer")],
                                   stop_reason="end_turn",
                                   usage=SimpleNamespace(input_tokens=50, output_tokens=20))
        ns = dict(sweep_helpers())
        ns.update(literal_assignments("CLAUDE_NO_DIALS", "CLAUDE_OLD_SEARCH",
                                      "CLAUDE_NO_DISABLE_ABOVE_HIGH"))
        ns.update({"Anthropic": lambda **kw: SimpleNamespace(), "_request_timeout": lambda *a: 1,
                   "_claude_messages": lambda m: m, "_claude_create": fake_create})
        load_function("claude_thinking_forced", ns)
        call = load_function("call_model", ns)
        text, itok, otok = call("Claude", "k", [{"role": "user", "content": "Q"}],
                                {"model_id": "claude-sonnet-5-5", "effort": "low", "thinking": False,
                                 "max_searches": 2}, True)
        self.assertEqual(text, "final answer")
        self.assertEqual((itok, otok), (150, 30))           # both legs billed
        self.assertEqual(calls[1]["messages"][-1]["role"], "assistant")

    def test_no_web_source_parks_unless_it_reads_the_pages(self):
        state = {"k_pack_ds": False, "k_fresh_mode": "When web search is on"}
        ns = {"st": fake_st(state), "shared_search_ready": lambda keys=None: True,
              "any_source_web_on": lambda: True,
              "NO_WEB_PROVIDERS": ("DeepSeek", "Mistral", "NVIDIA")}
        ns.update(literal_assignments("PACK_KEYS", "FRESH_SOURCE_MODES"))
        ns["FRESH_MODE_CODES"] = dict(zip(ns["FRESH_SOURCE_MODES"], ("web", "jev", "always")))
        load_function("_no_web_gets_pages", ns)
        parked = load_function("source_parked", ns)
        self.assertTrue(parked("DeepSeek"))                 # web on, no pages -> sits out
        state["k_pack_ds"] = True
        self.assertFalse(parked("DeepSeek"))                # reads the pages -> stays in
        state["k_pack_ds"] = False
        state["k_fresh_mode"] = ns["FRESH_SOURCE_MODES"][2]
        self.assertFalse(parked("DeepSeek"))

    def test_followups_mirror_every_synthesizer_dial(self):
        src = APP.read_text(encoding="utf-8")
        start = src.index("def inherit_followup_from_initial")
        body = src[start:src.index("# ---- Synthesizer prompt ----", start)]
        for suffix in ("qwen_effort", "mm_speed", "gpt_speed", "meta_model"):
            self.assertIn(f'"{suffix}"', body, suffix)


class ProviderAuditV371Tests(unittest.TestCase):

    def _oai_ns(self, captured, reply="answer", tool_rounds=0):
        """Fake OpenAI-compatible client; `tool_rounds` first replies come
        back as $web_search tool calls (Kimi's builtin loop)."""
        calls = []
        class FakeCompletions:
            def create(self, **kwargs):
                calls.append(kwargs)
                captured["request"] = kwargs
                if len(calls) <= tool_rounds and kwargs.get("tools"):   # no tool offered -> no tool call
                    tc = SimpleNamespace(id=f"call{len(calls)}",
                                         function=SimpleNamespace(name="web_search",
                                                                  arguments='{"query": "x"}'))
                    msg = SimpleNamespace(content=None, tool_calls=[tc],
                                          model_dump=lambda exclude_none=True:
                                              {"role": "assistant", "content": None,
                                               "tool_calls": [{"id": tc.id}]})
                    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")],
                                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2))
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=reply, tool_calls=None),
                                             finish_reason="stop")],
                    usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7))
        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured["client"] = kwargs
                self.chat = SimpleNamespace(completions=FakeCompletions())
                self.files = None
        ns = {"OpenAI": FakeOpenAI, "re": re, "json": json, "_request_timeout": lambda *a: 150.0,
              "_oai_chat_messages": lambda messages, pdf_ok, image_ok=True, image_shape="object",
                                           pdf_shape="file", skip_pdf=False:
                  captured.update(image_ok=image_ok, pdf_ok=pdf_ok, skip_pdf=skip_pdf) or list(messages),
              "_oai_responses_input": lambda messages, pdf_ok, image_ok=True:
                  captured.update(image_ok=image_ok) or list(messages),
              "_responses_call": lambda client, model, inp, effort, **k:
                  captured.update(responses=dict(model=model, effort=effort, **k)) or ("web", 1, 1),
              "_kimi_extract_pdfs": lambda client, messages: captured.get("pdf_text", ""),
              "_kimi_web_search": lambda key, query: captured.setdefault("searched", []).append(query)
                  or f"results for {query}",
              "MINIMAX_THINK_OFF_OK": {"MiniMax-M3"}, "MISTRAL_REASONING_MODELS": set()}
        captured["calls"] = calls
        for fn in ("_content_text", "_chat_completion_text", "_kimi_call", "_minimax_call",
                   "_glm_call", "_nvidia_call", "_mistral_call"):
            load_function(fn, ns)
        return ns

    def test_kimi_web_search_runs_the_client_side_search_tool(self):
        captured = {}
        ns = self._oai_ns(captured, reply="searched", tool_rounds=1)
        text, itok, otok = ns["_kimi_call"]("Kimi", "k", [{"role": "user", "content": "Q"}],
                                            {"model_id": "kimi-k2.6", "thinking": False}, True)
        self.assertEqual(text, "searched")
        self.assertEqual(len(captured["calls"]), 2)                    # two round trips
        first = captured["calls"][0]
        self.assertEqual(first["tools"][0]["type"], "function")
        self.assertEqual(first["tools"][0]["function"]["name"], "web_search")
        self.assertEqual(first["extra_body"], {"thinking": {"type": "disabled"}})   # toggle honoured
        self.assertEqual(captured["searched"], ["x"])
        second_msgs = captured["calls"][1]["messages"]
        self.assertEqual(second_msgs[-1], {"role": "tool", "tool_call_id": "call1",
                                           "name": "web_search", "content": "results for x"})
        self.assertEqual(second_msgs[-2]["role"], "assistant")
        self.assertEqual((itok, otok), (22, 9))                        # usage summed across rounds
        # web off: one call, no tools, the toggle is honoured, PDFs skipped when extracted
        captured = {"pdf_text": "Attached file a.pdf: body"}
        ns = self._oai_ns(captured)
        ns["_kimi_call"]("Kimi", "k", [{"role": "user", "content": "Q"}],
                         {"model_id": "kimi-k2.6", "thinking": False}, False)
        req = captured["request"]
        self.assertNotIn("tools", req)
        self.assertEqual(req["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(req["messages"][0], {"role": "system", "content": "Attached file a.pdf: body"})
        self.assertTrue(captured["skip_pdf"])

    def test_kimi_search_loop_stops_searching_once_the_time_budget_is_spent(self):
        captured = {}
        ns = self._oai_ns(captured, reply="answered", tool_rounds=10)
        ns["KIMI_WEB_BUDGET_S"] = -1          # budget already spent
        text, *_ = ns["_kimi_call"]("Kimi", "k", [{"role": "user", "content": "Q"}],
                                    {"model_id": "kimi-k3", "effort": "low"}, True)
        self.assertEqual(len(captured["calls"]), 1)
        self.assertNotIn("tools", captured["calls"][0])
        self.assertNotIn("searched", captured)

    def test_minimax_web_rides_the_responses_helper(self):
        captured = {}
        ns = self._oai_ns(captured)
        text, *_ = ns["_minimax_call"]("MiniMax", "k", [], {"model_id": "MiniMax-M3", "thinking": True,
                                                            "speed": "Priority"}, True)
        self.assertEqual(text, "web")
        r = captured["responses"]
        self.assertEqual(r["model"], "MiniMax-M3"); self.assertEqual(r["effort"], "medium")
        self.assertEqual(r["service_tier"], "priority"); self.assertEqual(r["max_out"], 32000)
        self.assertTrue(captured["image_ok"])
        self.assertNotIn("request", captured)                          # no chat-completions call

    def test_vision_gates_glm_flash_nvidia_omni_and_mistral_documents(self):
        captured = {}
        ns = self._oai_ns(captured)
        ns["_glm_call"]("GLM", "k", [], {"model_id": "glm-5.3-flash", "effort": "high"}, False)
        self.assertTrue(captured["image_ok"])
        ns["_glm_call"]("GLM", "k", [], {"model_id": "glm-5.3", "effort": "max"}, False)
        self.assertFalse(captured["image_ok"])
        self.assertEqual(captured["request"]["max_tokens"], 64000)     # Max -> heavy cap
        ns["_nvidia_call"]("NVIDIA", "k", [], {"model_id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
                                               "thinking": True, "budget": 4000}, False)
        self.assertTrue(captured["image_ok"])
        ns["_nvidia_call"]("NVIDIA", "k", [], {"model_id": "nvidia/nemotron-3-super-120b-a12b",
                                               "thinking": True, "budget": 4000}, False)
        self.assertFalse(captured["image_ok"])
        ns["_mistral_call"]("Mistral", "k", [], {"model_id": "mistral-large-latest"}, False)
        self.assertTrue(captured["pdf_ok"])
        prices = literal_dict_string_entries("PRICES")
        self.assertEqual(prices["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"], (0.0, 0.0))

    def test_output_caps_grow_with_the_dial(self):
        cap = load_function("output_cap", {"CLAUDE_ALWAYS_THINK": {"claude-fable-5-1"}})
        self.assertEqual(cap("Claude", {"model_id": "claude-haiku-4-5", "effort": "low"}), 16000)
        self.assertEqual(cap("Claude", {"model_id": "claude-sonnet-5", "effort": "high"}), 32000)
        self.assertEqual(cap("Claude", {"model_id": "claude-fable-5-1", "effort": "max"}), 64000)
        self.assertEqual(cap("ChatGPT", {"model_id": "gpt-5.6-luna", "effort": "none"}), 16000)
        self.assertEqual(cap("ChatGPT", {"model_id": "gpt-6-astra", "effort": "none"}), 16000)   # -> low
        self.assertEqual(cap("Grok", {"mode": "High"}), 32000); self.assertEqual(cap("Grok", {"mode": "Expert"}), 64000)
        self.assertEqual(cap("Gemini", {"level": "minimal"}), 16000)
        self.assertEqual(cap("Kimi", {"model_id": "kimi-k3", "effort": "high"}), 32000)
        self.assertEqual(cap("Qwen", {"thinking": False}), 16000)
        self.assertTrue(all(v <= 65536 for v in literal_assignments("OUTPUT_CAPS")["OUTPUT_CAPS"].values()))

    def test_gemini_minimal_is_flash_lite_only_and_haiku_gets_manual_thinking(self):
        sent = load_function("gemini_level_sent", {})
        self.assertEqual(sent("gemini-3.5-flash-lite", "minimal"), "minimal")
        self.assertEqual(sent("gemini-3.8-flash", "minimal"), "low")
        self.assertEqual(sent("gemini-3.1-pro-preview", "high"), "high")
        think = literal_assignments("GEMINI_THINK")["GEMINI_THINK"]
        self.assertEqual(list(think), ["Minimal", "Standard", "Balanced", "Extended"])
        # Haiku: the thinking box sends manual extended thinking with a budget
        captured = {}
        class _Messages:
            def create(self, **kw):
                captured.update(kw)
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="A")],
                                       stop_reason="end_turn",
                                       usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        class _Client:
            def __init__(self, **kw):
                self.messages = _Messages()
        ns = {"Anthropic": _Client, "_request_timeout": lambda *a: 60.0, "_claude_messages": lambda m: m}
        ns.update(literal_assignments("CLAUDE_NO_DIALS", "CLAUDE_OLD_SEARCH"))
        load_function("claude_thinking_forced", ns)
        call = load_function("call_model", ns)
        call("Claude", "k", [{"role": "user", "content": "Q"}],
             {"model_id": "claude-haiku-4-5", "thinking": True, "max_searches": 2}, False)
        self.assertEqual(captured["thinking"], {"type": "enabled", "budget_tokens": 8000})
        self.assertNotIn("output_config", captured)
        self.assertEqual(captured["max_tokens"], 32000)
        captured.clear()
        call("Claude", "k", [{"role": "user", "content": "Q"}],
             {"model_id": "claude-haiku-4-5", "thinking": False, "max_searches": 2}, False)
        self.assertNotIn("thinking", captured)

    def test_grok_rides_responses_for_web_off_too(self):
        captured = {}
        ns = {"OpenAI": lambda **kw: SimpleNamespace(), "_request_timeout": lambda *a: 60.0,
              "_oai_responses_input": lambda messages, pdf_ok: messages,
              "_responses_call": lambda client, model, inp, effort, **k:
                  captured.update(model=model, effort=effort, **k) or ("ok", 1, 1)}
        ns.update(literal_assignments("GROK_EFFORT", "MODEL_GROK"))
        call = load_function("call_model", ns)
        call("Grok", "k", [{"role": "user", "content": "Q"}], {"mode": "High"}, False)
        self.assertEqual(captured["model"], "grok-4.7"); self.assertEqual(captured["effort"], "high")
        self.assertFalse(captured["tools"]); self.assertEqual(captured["reasoning_summary"], "auto")
        call("Grok", "k", [{"role": "user", "content": "Q"}], {"mode": "Expert"}, True)
        self.assertTrue(captured["tools"]); self.assertEqual(captured["max_out"], 64000)
        req = {}
        class FakeResponses:
            def create(self, **kwargs):
                req.update(kwargs)
                return SimpleNamespace(output_text="A", status="completed", output=[],
                                       usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        helper = load_function("_responses_call", {})
        helper(SimpleNamespace(responses=FakeResponses()), "gpt-5.6-luna", [], "none",
               reasoning_summary="auto", tools=False)
        self.assertNotIn("tools", req)
        self.assertEqual(req["reasoning"], {"effort": "none"})       # no summary with none


class SonnetFiveFiveAndDegenerateOutputTests(unittest.TestCase):

    def _claude(self):
        captured = {}

        class FakeMessages:
            def create(self, **kw):
                captured.clear(); captured.update(kw)
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="Answer")],
                                       stop_reason="end_turn",
                                       usage=SimpleNamespace(input_tokens=3, output_tokens=2))

        class FakeAnthropic:
            def __init__(self, **_kw):
                self.messages = FakeMessages()

        ns = literal_assignments("CLAUDE_NO_DIALS", "CLAUDE_ALWAYS_THINK", "CLAUDE_OLD_SEARCH",
                                 "CLAUDE_NO_DISABLE_ABOVE_HIGH")
        ns.update({"Anthropic": FakeAnthropic, "_request_timeout": lambda *a: 150.0,
                   "_claude_messages": lambda m: m})
        load_function("claude_thinking_forced", ns)
        return load_function("call_model", ns), captured

    def test_sonnet_5_5_turns_thinking_off_with_between_tools(self):
        call, captured = self._claude()
        cfg = {"model_id": "claude-sonnet-5-5", "effort": "low", "thinking": False, "max_searches": 2}
        call("Claude", "k", [{"role": "user", "content": "Q"}], cfg, False)
        self.assertEqual(captured["thinking"], {"type": "between_tools"})
        # between_tools is a 400 at xhigh/max, so thinking is forced on there
        call("Claude", "k", [{"role": "user", "content": "Q"}], dict(cfg, effort="max"), False)
        self.assertEqual(captured["thinking"], {"type": "adaptive", "display": "summarized"})
        # Sonnet 5 (legacy, saved chats) keeps its own shape
        call("Claude", "k", [{"role": "user", "content": "Q"}],
             dict(cfg, model_id="claude-sonnet-5"), False)
        self.assertEqual(captured["thinking"], {"type": "disabled"})

    def test_sonnet_5_5_replaces_sonnet_5_everywhere(self):
        v = literal_assignments("CLAUDE_MODELS", "PRESETS", "SYNTH_PRESETS",
                                "_RETIRED_MODEL_LABELS", "CLAUDE_SEARCH_CAPS")
        src = APP.read_text(encoding="utf-8")
        self.assertEqual(v["CLAUDE_MODELS"]["Sonnet 5.5"], "claude-sonnet-5-5")
        self.assertNotIn("Sonnet 5", v["CLAUDE_MODELS"])
        self.assertIn('"claude-sonnet-5-5": (2.00, 10.00)', src)
        self.assertIn('"claude-sonnet-5": (2.00, 10.00)', src)
        self.assertEqual(v["_RETIRED_MODEL_LABELS"]["Claude"]["Sonnet 5"], "Sonnet 5.5")
        self.assertIn("Sonnet 5.5", v["CLAUDE_SEARCH_CAPS"])
        self.assertNotIn('"Sonnet 5"', json.dumps([v["PRESETS"], v["SYNTH_PRESETS"]]))

    def test_every_request_carries_todays_date_without_touching_history(self):
        ns = dict(sweep_helpers())
        original = [{"role": "user", "content": "first"},
                    {"role": "assistant", "content": "a"},
                    {"role": "user", "content": "now"}]
        out = ns["_with_today"](original)
        self.assertTrue(out[-1]["content"].startswith("[Today's date: "))
        self.assertIn(str(datetime.now().year), out[-1]["content"])
        self.assertEqual(out[0]["content"], "first")
        self.assertEqual(original[-1]["content"], "now")      # stored copy untouched
        self.assertEqual(ns["_with_today"](out), out)          # never doubled

    def test_max_effort_calls_get_one_long_attempt(self):
        ns = dict(sweep_helpers())
        self.assertEqual(ns["_request_retries"]("ChatGPT", {"effort": "max"}, False), 0)
        self.assertEqual(ns["_request_retries"]("ChatGPT", {"effort": "xhigh"}, False), 1)
        v = literal_assignments("MAX_EFFORT_REQUEST_TIMEOUT", "HEAVY_REQUEST_TIMEOUT")
        self.assertGreater(v["MAX_EFFORT_REQUEST_TIMEOUT"], v["HEAVY_REQUEST_TIMEOUT"])
        src = APP.read_text(encoding="utf-8")
        self.assertNotIn("timeout=_request_timeout(name, cfg, web), max_retries=1)", src)

    def test_qwen_cap_exceeds_budget_and_nvidia_retries_without_budget(self):
        src = APP.read_text(encoding="utf-8")
        self.assertIn('kw["max_completion_tokens"] = max(kw["max_completion_tokens"], _qb + 8192)', src)
        calls = []

        class FakeCompletions:
            def create(self, **kw):
                calls.append(deepcopy(kw))
                if "reasoning_budget" in kw["extra_body"]:
                    raise RuntimeError("thinking_token_budget is not yet supported by the V2 model runner")
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content="Answer", reasoning_content=None),
                    finish_reason="stop")], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))

        class FakeOpenAI:
            def __init__(self, **_kw):
                self.chat = SimpleNamespace(completions=FakeCompletions())

        ns = literal_assignments("NVIDIA_VISION_MODELS")
        ns.update({"OpenAI": FakeOpenAI, "_request_timeout": lambda *a: 150.0,
                   "_oai_chat_messages": lambda m, **k: m,
                   "_content_text": lambda c: c or ""})
        call = load_function("_nvidia_call", ns)
        text, *_ = call("NVIDIA", "k", [{"role": "user", "content": "Q"}],
                        {"model_id": "nvidia/nemotron-3-ultra-550b-a55b", "thinking": True,
                         "budget": 24000}, False)
        self.assertEqual(text, "Answer")
        self.assertEqual(len(calls), 2)
        self.assertNotIn("reasoning_budget", calls[1]["extra_body"]["chat_template_kwargs"])

    def test_repetition_loop_is_detected_and_real_answers_are_not(self):
        rep = sweep_helpers()["_degenerate_repetition"]
        loop = ("I need to be careful here. These results look fictional to me. "
                "Let me search for verified facts instead. ") * 120
        self.assertTrue(rep(loop))
        normal = " ".join(f"Point {i} explains a distinct idea about topic {i}." for i in range(400))
        self.assertFalse(rep(normal))
        self.assertFalse(rep("short answer. " * 5))


class ImagePreparationTests(unittest.TestCase):

    def _prepare(self):
        ns = {"io": __import__("io"),
              "IMG_MIME": {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                           "gif": "image/gif", "webp": "image/webp"}}
        ns.update(literal_assignments("IMAGE_SEND_MAX_EDGE", "IMAGE_SEND_MAX_BYTES"))
        load_function("_shrink_image", ns)
        return load_function("_prepare_image", ns)

    def test_a_damaged_big_image_is_skipped_not_fatal(self):
        import io as _io
        from PIL import Image
        big = Image.frombytes("RGB", (3840, 2160), os.urandom(3840 * 2160 * 3))
        buf = _io.BytesIO(); big.save(buf, "PNG"); raw = buf.getvalue()
        cut = raw[: len(raw) // 2]
        self.assertGreater(len(cut), 3 * 1024 * 1024)
        self.assertEqual(self._prepare()(cut, "png"), (None, None, None))

    def test_big_images_shrink_and_small_ones_pass_through(self):
        import io as _io
        from PIL import Image
        prep = self._prepare()
        big = Image.frombytes("RGB", (3840, 2160), os.urandom(3840 * 2160 * 3))
        buf = _io.BytesIO(); big.save(buf, "PNG"); raw = buf.getvalue()
        data, mime, note = prep(raw, "png")
        self.assertLessEqual(len(data), 3 * 1024 * 1024)
        self.assertEqual(max(note["sent_dims"]), 2576)
        self.assertEqual(note["orig_bytes"], len(raw))
        small = _io.BytesIO(); Image.new("RGB", (640, 480)).save(small, "PNG")
        data, mime, note = prep(small.getvalue(), "png")
        self.assertIsNone(note); self.assertEqual(mime, "image/png")
        webp = _io.BytesIO(); Image.new("RGB", (64, 64)).save(webp, "WEBP")
        data, mime, note = prep(webp.getvalue(), "webp")
        self.assertIn(mime, ("image/jpeg", "image/png"))   # xAI reads only JPEG/PNG

    def test_grok_never_stores_and_history_keeps_the_note(self):
        src = APP.read_text(encoding="utf-8")
        self.assertIn('tools=bool(web), store=False)', src)
        self.assertIn('base["store"] = store', src)
        strip = load_function("_strip_files_deep", {})
        out = strip({"files": [{"name": "a.png", "kind": "image", "b64": "AAAA",
                                "prepared": {"orig_bytes": 9, "sent_bytes": 3}}]})
        self.assertEqual(out["files"][0]["prepared"]["sent_bytes"], 3)
        self.assertNotIn("b64", out["files"][0])


class SettingsConsistencyTests(unittest.TestCase):

    TABLES = {
        "gpt_model": "CHATGPT_MODELS", "gpt": "CHATGPT_EFFORT", "gpt_eff": "CHATGPT_EFFORT",
        "gpt_speed": "CHATGPT_SPEED", "cla_model": "CLAUDE_MODELS", "cla": "CLAUDE_EFFORT",
        "cla_eff": "CLAUDE_EFFORT", "gem": "GEMINI_MODELS", "gem_model": "GEMINI_MODELS",
        "gem_t": "GEMINI_THINK", "grok": "GROK_MODES", "grok_mode": "GROK_MODES",
        "ds_model": "DEEPSEEK_MODELS", "ds_effort": "DEEPSEEK_EFFORT",
        "qwen_model": "QWEN_MODELS", "qwen_effort": "QWEN_EFFORT",
        "mistral_model": "MISTRAL_MODELS", "mistral_eff": "MISTRAL_EFFORT",
        "kimi_model": "KIMI_MODELS", "kimi_effort": "KIMI_EFFORT",
        "meta_model": "META_MODELS", "meta_eff": "META_EFFORT",
        "mm_model": "MINIMAX_MODELS", "mm_speed": "MINIMAX_SPEED",
        "nv_model": "NVIDIA_MODELS", "glm_model": "GLM_MODELS", "glm_eff": "GLM_EFFORT",
    }
    MODEL_TABLES = ("CHATGPT_MODELS", "CLAUDE_MODELS", "GEMINI_MODELS", "DEEPSEEK_MODELS",
                    "QWEN_MODELS", "MISTRAL_MODELS", "KIMI_MODELS", "META_MODELS",
                    "MINIMAX_MODELS", "NVIDIA_MODELS", "GLM_MODELS")

    @classmethod
    def setUpClass(cls):
        cls.v = literal_assignments(*set(cls.TABLES.values()), "PRESETS", "SYNTH_PRESETS",
                                    "DEFAULTS", "_RETIRED_MODEL_LABELS", "MODEL_GROK")

    def _table_for(self, key):
        for prefix in ("k_syn_", "k_fu_", "k_adj_", "k_res_", "k_"):
            if key.startswith(prefix) and key[len(prefix):] in self.TABLES:
                return self.v[self.TABLES[key[len(prefix):]]]
        return None

    def _check(self, where, settings):
        for key, value in settings.items():
            table = self._table_for(key)
            if table is not None:
                self.assertIn(value, table, f"{where}: {key}={value!r} is not an option")

    def test_every_preset_seat_and_default_value_is_a_real_option(self):
        for tier, settings in self.v["PRESETS"].items():
            self._check(f"PRESETS[{tier}]", settings)
        for tier, seats in self.v["SYNTH_PRESETS"].items():
            for provider, settings in seats.items():
                self._check(f"SYNTH_PRESETS[{tier}][{provider}]", settings)
        self._check("DEFAULTS", self.v["DEFAULTS"])

    def test_every_tier_sets_the_same_dials(self):
        keysets = [set(d) for d in self.v["PRESETS"].values()]
        self.assertTrue(all(k == keysets[0] for k in keysets))
        seat_sets = [{p: set(d) for p, d in t.items()} for t in self.v["SYNTH_PRESETS"].values()]
        self.assertTrue(all(s == seat_sets[0] for s in seat_sets))

    def test_every_offered_model_has_a_price(self):
        prices = literal_dict_string_entries("PRICES")
        for table in self.MODEL_TABLES:
            for label, model_id in literal_assignments(table)[table].items():
                self.assertIn(model_id, prices, f"{table}[{label}] has no price")
        self.assertIn(self.v["MODEL_GROK"], APP.read_text(encoding="utf-8"))

    def test_every_retired_label_migrates_to_a_live_option(self):
        names = {"ChatGPT": "CHATGPT_MODELS", "Claude": "CLAUDE_MODELS",
                 "Gemini": "GEMINI_MODELS", "DeepSeek": "DEEPSEEK_MODELS",
                 "Qwen": "QWEN_MODELS", "Meta": "META_MODELS", "GLM": "GLM_MODELS",
                 "MiniMax": "MINIMAX_MODELS", "NVIDIA": "NVIDIA_MODELS",
                 "Mistral": "MISTRAL_MODELS", "Kimi": "KIMI_MODELS"}
        for provider, aliases in self.v["_RETIRED_MODEL_LABELS"].items():
            table = literal_assignments(names[provider])[names[provider]]
            for old, new in aliases.items():
                self.assertNotIn(old, table, (provider, old))
                self.assertIn(new, table, (provider, new))

    def test_kimi_search_helper_builds_the_documented_request(self):
        seen = {}
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps({"search_results": [
                    {"title": "T", "url": "https://x", "date": "2026-09-28",
                     "site_name": "X", "snippet": "S", "text": "", "icon": "",
                     "mime": "", "authority": ""}]}).encode()
        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url; seen["body"] = json.loads(req.data)
            seen["auth"] = req.headers.get("Authorization")
            return FakeResp()
        search = load_function("_kimi_web_search", {"json": json,
                                                    "KIMI_SEARCH_URL": "https://api.moonshot.ai/v1/tools/search"})
        with patch("urllib.request.urlopen", fake_urlopen):
            out = json.loads(search("fixture-key", "latest news"))
        self.assertEqual(seen["url"], "https://api.moonshot.ai/v1/tools/search")
        self.assertEqual(seen["body"]["text_query"], "latest news")
        self.assertEqual(seen["body"]["limit"], 5)
        self.assertEqual(seen["auth"], "Bearer fixture-key")
        self.assertEqual(out["results"][0]["url"], "https://x")
        self.assertNotIn("text", out["results"][0])
        self.assertIn("error", json.loads(search("k", "   ")))     # no network call for empty


class SmokeTestFixesV3701Tests(unittest.TestCase):

    def test_staged_uploads_survive_a_page_change_but_clear_on_user_removal(self):
        packed = [{"name": "shot.png", "kind": "image", "mime": "image/png", "b64": "AAAA"}]
        state = {}
        ns = {"st": SimpleNamespace(session_state=state),
              "pack_files": lambda uploaded: list(packed) if uploaded else []}
        stage = load_function("stage_uploads", ns)
        def new_run():
            state["_uploaders_mounted_prev"] = state.get("_uploaders_mounted_now") or set()
            state["_uploaders_mounted_now"] = set()
        # run 1: the user attaches a file on the composer
        new_run(); state["up_main_0"] = ["fileobj"]
        self.assertEqual(stage("up_main_0", "_staged_main"), packed)
        # run 2: the Council page -- the composer (and its uploader) is not
        # rendered at all, Streamlit deletes the widget key
        new_run(); state.pop("up_main_0", None)
        new_run(); state["up_main_0"] = []
        self.assertEqual(stage("up_main_0", "_staged_main"), packed)
        new_run(); state["up_main_0"] = []
        self.assertEqual(stage("up_main_0", "_staged_main"), packed)
        # the send sites read the stage, never the widget
        src = APP.read_text(encoding="utf-8")
        self.assertIn('files_main = list(st.session_state.pop("_staged_main", None) or [])', src)
        self.assertIn('files_res = list(st.session_state.pop("_staged_res", None) or [])', src)
        self.assertIn('files = list(st.session_state.get(f"_staged_{form_key}") or [])', src)
        self.assertNotIn("pack_files(st.session_state.get(MAIN_UP_KEY))", src)

    def test_qwen_web_on_implies_a_3_8_model(self):
        qm = literal_assignments("QWEN_MODELS")["QWEN_MODELS"]
        idle = load_function("qwen_best_web_model", {"QWEN_WEB_MODELS": set()})
        self.assertEqual(idle("Plus 3.7 (balanced)"), "Plus 3.7 (balanced)")
        self.assertEqual(idle("Flash 3.7 (cheapest)"), "Flash 3.7 (cheapest)")
        # The rescue itself still works if a searchable model ever returns.
        best = load_function("qwen_best_web_model", {"QWEN_WEB_MODELS": {"qwen3.8-max"}})
        self.assertEqual(best("Flash 3.7 (cheapest)"), "Flash 3.8 (economy)")
        self.assertEqual(best("Plus 3.7 (balanced)"), "Max 3.8 (flagship)")
        self.assertIn(best("Flash 3.7 (cheapest)"), qm); self.assertIn(best("Plus 3.7 (balanced)"), qm)
        state = {"k_qwen_model": "Plus 3.7 (balanced)", "k_web_qwen": True}
        ns = {"st": SimpleNamespace(session_state=state), "QWEN_MODELS": qm,
              "qwen_best_web_model": best}
        load_function("_source_qwen_web_supported", ns)
        toggle = load_function("_sync_source_qwen_web_toggle", ns)
        toggle()
        self.assertEqual(state["k_qwen_model"], "Max 3.8 (flagship)")   # web on -> 3.8
        state["k_qwen_model"] = "Flash 3.7 (cheapest)"
        load_function("_sync_source_qwen_web", ns)()
        self.assertFalse(state["k_web_qwen"])                            # 3.7 picked -> web off
        src = APP.read_text(encoding="utf-8")
        self.assertIn('follow_up_form(form_key, f"Your private follow-up to {n}", provider=n)', src)
        self.assertIn('_render_side_chat_web_button(provider, f"webbtn_{form_key}")', src)


class SpeedLayerV378Tests(unittest.TestCase):

    def _consts(self, *names):
        return literal_assignments(*names)

    def _worker_ns(self, run_one, extra=(), **values):
        wanted = {"_bg_update", "_bg_cancel_requested", "_bg_mark_cancelled", "_bg_worker",
                  *extra}
        nodes = [node for node in source_tree().body
                 if isinstance(node, ast.FunctionDef) and node.name in wanted]
        ns = {"ThreadPoolExecutor": ThreadPoolExecutor, "wait": wait,
              "FIRST_COMPLETED": FIRST_COMPLETED, "time": time, "threading": threading,
              "run_one": run_one, "split_state_capsule": lambda t: (t, "")}
        ns.update(self._consts("QUORUM_FRACTION", "JEV_HOLD_VERDICTS", "JEV_HOLD_CONFIDENCE"))
        ns.update({"QUORUM_GRACE_MIN": 0.0, "QUORUM_GRACE_MAX": 0.0, "QUORUM_GRACE_SHARE": 0.0})
        ns.update(values)
        for k, v in transparency_helpers().items():
            ns.setdefault(k, v)
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                     str(APP), "exec"), ns)
        return ns

    @staticmethod
    def _ok(provider, text):
        return {"text": text, "secs": 0.1, "cost": 0.001, "in_tok": 10, "out_tok": 5,
                "err": False, "credit_err": False, "web": False, "provider": provider}

    @staticmethod
    def _plan(names, **extra):
        plan = {"prompt": "q", "turn_id": 0,
                "sources": {n: {"provider": n, "key": "k", "messages": [], "cfg": {},
                                "web": False} for n in names},
                "phase2": {"kind": "synth", "provider": "Claude", "key": "synth", "cfg": {},
                           "web": False, "label_key": "Synthesizer", "min_good": 1}}
        plan.update(extra)
        return plan

    @staticmethod
    def _reg(job_id):
        return {"lock": threading.Lock(), "jobs": {job_id: {
            "status": "running", "progress": [], "cancel_requested": False,
            "started": time.time()}}}

    def test_quorum_rule(self):
        ns = dict(self._consts("QUORUM_FRACTION", "QUORUM_GRACE_MIN", "QUORUM_GRACE_MAX",
                               "QUORUM_GRACE_SHARE"))
        ready = load_function("quorum_ready", ns)
        cfg = {"enabled": True, "fraction": 0.75, "deadline": 120}
        self.assertEqual(ready(10, 0, 12, 8, 1, None, cfg), (False, None))     # 8 < 9
        go, at = ready(10, 0, 12, 9, 1, None, cfg)
        self.assertFalse(go); self.assertEqual(at, 10)                         # grace starts
        self.assertFalse(ready(17, 0, 12, 9, 1, 10, cfg)[0])                   # 8 s grace min
        self.assertTrue(ready(18.5, 0, 12, 9, 1, 10, cfg)[0])
        self.assertTrue(ready(121, 0, 12, 1, 1, None, cfg)[0])                 # deadline
        self.assertFalse(ready(121, 0, 12, 0, 1, None, cfg)[0])                # nothing usable
        self.assertFalse(ready(500, 0, 12, 12, 1, None, None)[0])              # off
        self.assertFalse(ready(10, 0, 4, 2, 3, None, cfg)[0])                  # research min_good

    def test_quorum_start_keeps_late_answers_out_of_the_pool(self):
        slow_go, synth_go = threading.Event(), threading.Event()
        seen = {}

        def fake_run_one(provider, key, messages, cfg, web):
            if key == "synth":
                seen["pool"] = messages[0]["content"]
                synth_go.wait(timeout=5)
                return self._ok(provider, "the answer")
            if provider == "Slow":
                slow_go.wait(timeout=5)
            return self._ok(provider, f"{provider} says")

        ns = self._worker_ns(fake_run_one, extra={"quorum_ready"},
                             build_synth_prompt=lambda q, good, w, c, p: ("|".join(sorted(good)), {}))
        reg = self._reg("q")
        plan = self._plan(["A", "B", "C", "Slow"],
                          quorum={"enabled": True, "fraction": 0.75, "deadline": 300})
        t = threading.Thread(target=ns["_bg_worker"], args=(reg, "q", plan))
        t.start()
        until = time.time() + 4
        while "pool" not in seen and time.time() < until:
            time.sleep(0.02)
        self.assertEqual(seen.get("pool"), "A|B|C")            # started without Slow
        slow_go.set()
        until = time.time() + 4
        while time.time() < until:
            with reg["lock"]:
                if any(p["name"] == "Slow" for p in reg["jobs"]["q"]["progress"]):
                    break
            time.sleep(0.02)
        synth_go.set()
        t.join(timeout=5)
        job = reg["jobs"]["q"]
        self.assertEqual(job["status"], "done")
        self.assertTrue(job["early_start"])
        self.assertEqual(job["phase1"]["Slow"]["text"], "Slow says")
        self.assertTrue(job["phase1"]["Slow"]["late"])
        self.assertNotIn("late", job["phase1"]["A"])

    def test_a_straggler_still_running_at_the_end_is_dropped_honestly(self):
        slow_go = threading.Event()

        def fake_run_one(provider, key, messages, cfg, web):
            if provider == "Slow":
                slow_go.wait(timeout=10)
            return self._ok(provider, "fine")

        ns = self._worker_ns(fake_run_one, extra={"quorum_ready"},
                             build_synth_prompt=lambda q, good, w, c, p: ("pool", {}))
        reg = self._reg("d")
        plan = self._plan(["A", "B", "C", "Slow"],
                          quorum={"enabled": True, "fraction": 0.75, "deadline": 300})
        try:
            ns["_bg_worker"](reg, "d", plan)
        finally:
            slow_go.set()
        slow = reg["jobs"]["d"]["phase1"]["Slow"]
        self.assertTrue(slow["dropped"] and slow["err"] and slow["late"])
        self.assertIn("stopped waiting", slow["text"])
        self.assertIsNone(slow["cost"])

    def test_without_quorum_the_worker_waits_for_everyone(self):
        def fake_run_one(provider, key, messages, cfg, web):
            if provider == "Slow":
                time.sleep(0.4)
            return self._ok(provider, provider)
        pools = []
        ns = self._worker_ns(fake_run_one,
                             build_synth_prompt=lambda q, good, w, c, p: (pools.append(sorted(good)) or "x", {}))
        reg = self._reg("w")
        ns["_bg_worker"](reg, "w", self._plan(["A", "Slow"]))
        self.assertEqual(pools, [["A", "Slow"]])

    def test_the_synthesis_streams_into_the_job(self):
        def fake_run_one(provider, key, messages, cfg, web):
            if key == "synth":
                ns["_stream_emit"]("Hel")
                ns["_stream_emit"]("lo")
            return self._ok(provider, "Hello")
        ns = self._worker_ns(fake_run_one, extra={"_run_streaming", "_stream_emit"},
                             _stream_slot=threading.local(),
                             build_synth_prompt=lambda q, good, w, c, p: ("pool", {}))
        reg = self._reg("s")
        ns["_bg_worker"](reg, "s", self._plan(["A"], stream=["Claude"]))
        self.assertEqual("".join(reg["jobs"]["s"]["stream"]["answer"]), "Hello")
        self.assertIsNone(getattr(ns["_stream_slot"], "cb", None))   # cleared after the call

    def test_claude_and_responses_streaming_helpers(self):
        ns = {"_stream_slot": threading.local()}
        for fn in ("_stream_emit", "_claude_create", "_responses_create"):
            load_function(fn, ns)
        E = SimpleNamespace

        class Stream:
            def __init__(self, events, final): self.events, self.final = events, final
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def __iter__(self): return iter(self.events)
            def get_final_message(self): return self.final
            def get_final_response(self): return self.final

        claude_events = [
            E(type="content_block_start", content_block=E(type="text")),
            E(type="content_block_delta", delta=E(type="text_delta", text="Let me search. ")),
            E(type="content_block_start", content_block=E(type="server_tool_use")),
            E(type="content_block_start", content_block=E(type="text")),
            E(type="content_block_delta", delta=E(type="text_delta", text="# Answer")),
        ]
        live = []

        def cb(delta, reset):
            if reset:
                live.clear()
            if delta:
                live.append(delta)
        client = E(messages=E(stream=lambda **kw: Stream(claude_events, "MSG"),
                              create=lambda **kw: self.fail("must stream")))
        ns["_stream_slot"].cb = cb
        self.assertEqual(ns["_claude_create"](client, {"model": "m"}), "MSG")
        self.assertEqual(live, ["# Answer"])          # narration before the search dropped
        resp_events = [E(type="response.output_text.delta", delta="a"),
                       E(type="response.output_item.added", item=E(type="web_search_call")),
                       E(type="response.output_text.delta", delta="b")]
        rclient = E(responses=E(stream=lambda **kw: Stream(resp_events, "RESP"),
                                create=lambda **kw: self.fail("must stream")))
        self.assertEqual(ns["_responses_create"](rclient, {"model": "m"}), "RESP")
        self.assertEqual(live, ["b"])
        ns["_stream_slot"].cb = None
        plain = E(messages=E(create=lambda **kw: "PLAIN"))
        self.assertEqual(ns["_claude_create"](plain, {"model": "m"}), "PLAIN")

    def test_live_answer_text_hides_the_hidden_blocks(self):
        consts = self._consts("LEDGER_BEGIN", "STATE_BEGIN")
        live = load_function("live_answer_text", dict(consts))
        self.assertEqual(live("Answer body\n" + consts["LEDGER_BEGIN"] + "\nA: x"), "Answer body")
        self.assertEqual(live("Answer body\n<<<CONC"), "Answer body")
        self.assertEqual(live("plain"), "plain")

    def _jev_ns(self):
        ns = dict(self._consts("TYPESAFE_URL", "JEV_MODEL", "JEV_PRICE_IN",
                               "JEV_STATE_CHAR_BUDGET", "JEV_MAX_CLAIMS", "JEV_HOLD_VERDICTS",
                               "JEV_HOLD_CONFIDENCE", "JEV_CONTRADICT_FLAG",
                               "JEV_UNADDRESSED_SHARE", "JEV_PRESET_LADDER",
                               "JEV_DEPTH_LEVELS", "JEV_CHECK_VERDICTS", "JEV_CLAIM_CRITERIA"))
        ns.update({"json": json, "time": time, "re": re})
        for fn in ("_split_after_heading", "split_source_weighing", "jev_ask", "_jev_usage",
                   "jev_route", "jev_check_answer", "jev_should_hold", "extract_claims",
                   "jev_claim_check"):
            load_function(fn, ns)
        return ns

    def test_jev_request_shape_retry_and_key_free_errors(self):
        import urllib.error
        ns = self._jev_ns()
        sent = []

        class Resp:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(self.body).encode()

        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            sent.append(req)
            if calls["n"] == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "busy", {}, None)
            return Resp({"model": "jev-1.14.0",
                         "answers": {"x": {"type": "noul", "noul": 0.9}},
                         "usage": {"input_tokens": 1_000_000, "output_tokens": 3}})

        with patch("urllib.request.urlopen", fake_urlopen), patch("time.sleep", lambda s: None):
            r = ns["jev_ask"]("SECRET-KEY", {"q": "hi"}, {"x": {"type": "noul"}})
        self.assertEqual(calls["n"], 2)                               # one retry on 429
        body = json.loads(sent[-1].data.decode())
        self.assertEqual(body["model"], "jev-1.14.0")
        self.assertEqual(set(body), {"model", "state", "questions"})
        self.assertEqual(sent[-1].full_url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(sent[-1].get_header("Authorization"), "Bearer SECRET-KEY")
        self.assertAlmostEqual(r["cost"], 0.042)                       # $0.042 / M, output free

        def failing(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, "bad key SECRET-KEY", {}, None)
        with patch("urllib.request.urlopen", failing):
            with self.assertRaises(RuntimeError) as ctx:
                ns["jev_ask"]("SECRET-KEY", {}, {})
        self.assertNotIn("SECRET", str(ctx.exception))

    def test_jev_route_maps_depth_onto_the_presets(self):
        ns = self._jev_ns()
        ns["jev_ask"] = lambda key, state, q, timeout=20.0: {
            "answers": {"needs_web": {"noul": 0.81},
                        "depth": {"score": 3.0, "confidence": 0.7,
                                  "legend": {str(i): "x" for i in range(5)}},
                        "needs_council": {"noul": 0.2}},
            "secs": 0.3, "in_tok": 100, "out_tok": 0, "cost": 0.0, "model": "jev"}
        out = ns["jev_route"]("k", "Is this deal worth it?", follow_up=True,
                              previous_question="earlier")
        self.assertEqual(out["preset"], "Thorough")
        self.assertEqual(out["needs_web"], 0.81)
        self.assertEqual(out["needs_council"], 0.2)

    def test_jev_hold_rule_and_claim_extraction(self):
        ns = self._jev_ns()
        hold = ns["jev_should_hold"]
        self.assertTrue(hold({"verdict": "refuses", "confidence": 0.9}))
        self.assertFalse(hold({"verdict": "refuses", "confidence": 0.6}))   # not a clear read
        self.assertFalse(hold({"verdict": "answers", "confidence": 0.99}))
        self.assertFalse(hold(None))
        text = ("# Title\n\nThe Treaty of Paris was signed in 1783 by Britain and the United "
                "States. It ended the war.\n\n- In 1784 Congress ratified the treaty after "
                "months of delay.\n| a | b |\n```\ncode line that is long enough to count here\n```\n"
                "\nHow I weighed the sources\nA and B agreed on the 1783 date for the treaty.")
        claims = ns["extract_claims"](text)
        self.assertIn("The Treaty of Paris was signed in 1783 by Britain and the United States.", claims)
        self.assertIn("In 1784 Congress ratified the treaty after months of delay.", claims)
        self.assertFalse(any("weighed" in c or "agreed on the 1783" in c for c in claims))
        self.assertFalse(any("code line" in c for c in claims))

    def test_jev_claim_check_clean_and_flagged(self):
        ns = self._jev_ns()
        draft = " ".join(f"Claim number {i} says the value was {i * 10} units in 2025." for i in range(4))

        def answers_with(verdicts):
            def fake(key, state, questions, timeout=20.0):
                self.assertLessEqual(len(json.dumps(state)), 120_000)
                return {"answers": {qid: {"choice": verdicts[i], "confidence": 0.9,
                                          "probabilities": {"contradicted": 0.6 if verdicts[i] == "contradicted" else 0.02}}
                                    for i, qid in enumerate(questions)},
                        "secs": 0.5, "in_tok": 900, "out_tok": 0, "cost": 0.00004, "model": "jev"}
            return fake
        ns["jev_ask"] = answers_with(["supported"] * 4)
        r = ns["jev_claim_check"]("k", "q", draft, {"Claude": "a" * 200_000, "Grok": "b"},
                                  {"A": "Claude", "B": "Grok"})
        self.assertTrue(r["clean"]); self.assertEqual(r["counts"]["supported"], 4)
        ns["jev_ask"] = answers_with(["supported", "contradicted", "supported", "supported"])
        r = ns["jev_claim_check"]("k", "q", draft, {"Claude": "a"}, {"A": "Claude"})
        self.assertFalse(r["clean"]); self.assertEqual(r["flagged"], 1)
        ns["jev_ask"] = answers_with(["unaddressed", "unaddressed", "supported", "supported"])
        self.assertFalse(ns["jev_claim_check"]("k", "q", draft, {"Claude": "a"}, {"A": "Claude"})["clean"])
        self.assertIsNone(ns["jev_claim_check"]("k", "q", "Too short.", {}, {})["clean"])

    def _jev_worker(self, mode):
        audits = []

        def fake_run_one(provider, key, messages, cfg, web):
            if key == "adj":
                audits.append(1)
                return self._ok(provider, "fine\n<<<ADJUDICATOR_VERDICT>>>\nmaterial_defects: no")
            if key == "synth":
                return self._ok(provider, "the draft")
            return self._ok(provider, f"{provider} answer")
        pools = []
        consts = self._consts("JEV_HOLD_VERDICTS", "JEV_HOLD_CONFIDENCE")
        ns = self._worker_ns(
            fake_run_one, extra={"jev_should_hold"},
            build_synth_prompt=lambda q, good, w, c, p: (pools.append(sorted(good)) or "pool",
                                                         {"A": "A"}),
            build_adjudicator_prompt=lambda *a: "audit",
            split_adjudicator_verdict=lambda t: (t, False),
            jev_route=lambda *a: {"preset": "Light", "needs_web": 0.1},
            jev_check_answer=lambda key, q, text, today, web: (
                {"verdict": "refuses", "confidence": 0.95} if text.startswith("B")
                else {"verdict": "answers", "confidence": 0.98}),
            jev_claim_check=lambda *a: {"claims": [{"claim": "c"}], "clean": True,
                                        "flagged": 0, "counts": {}},
            **consts)
        reg = self._reg("j")
        plan = self._plan(["A", "B", "C"], jev={"key": "k", "mode": mode, "today": ""},
                          phase3={"provider": "ChatGPT", "key": "adj", "cfg": {}, "web": False,
                                  "label_key": "Adjudicator"})
        ns["_bg_worker"](reg, "j", plan)
        return reg["jobs"]["j"], pools, audits

    def test_jev_active_sets_aside_clear_defects_and_skips_a_clean_audit(self):
        job, pools, audits = self._jev_worker("active")
        self.assertEqual(job["status"], "done")
        self.assertEqual(pools, [["A", "C"]])
        self.assertEqual(job["jev"]["held_out"], ["B"])
        self.assertTrue(job["audit_skipped"]); self.assertEqual(audits, [])
        self.assertEqual(job["jev"]["router"]["preset"], "Light")

    def test_jev_shadow_changes_nothing(self):
        job, pools, audits = self._jev_worker("shadow")
        self.assertEqual(pools, [["A", "B", "C"]])
        self.assertEqual(audits, [1])
        self.assertEqual(job["jev"]["would_hold"], ["B"])
        self.assertEqual(job["jev"]["held_out"], [])
        self.assertTrue(job["jev"]["claims"]["clean"])

    def test_cerebras_preview_request_and_refresh_schedule(self):
        captured = []

        class FakeOpenAI:
            def __init__(self, **kw):
                captured.append(("client", kw))
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

            def create(self, **kw):
                captured.append(("call", kw))
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="**Agree** - x"))],
                    usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200))
        ns = dict(self._consts("CEREBRAS_URL", "CEREBRAS_PREVIEW_MODEL", "PREVIEW_MAX_CALLS",
                               "PREVIEW_ANSWER_CHARS", "PREVIEW_TOTAL_CHARS",
                               "PREVIEW_MAX_TOKENS"))
        ns.update({"OpenAI": FakeOpenAI, "time": time, "threading": threading,
                   "cost_of": lambda m, i, o: 0.001,
                   "_content_text": lambda c: c})
        for fn in ("cerebras_preview", "_preview_loop", "_bg_update"):
            load_function(fn, ns)
        r = ns["cerebras_preview"]("ck", "Q?", ["one", "two"], 8, "[Today]", ("pic.png",))
        client_kw = captured[0][1]
        self.assertEqual(client_kw["base_url"], "https://api.cerebras.ai/v1")
        call = captured[1][1]
        self.assertEqual(call["model"], "gpt-oss-120b")
        self.assertEqual(call["reasoning_effort"], "low")
        self.assertIn("2 of 8 answers", call["messages"][1]["content"])
        self.assertIn("pic.png", call["messages"][1]["content"])
        self.assertEqual(r["text"], "**Agree** - x")
        # schedule: a first take, then refreshes as quarters of 4 arrive, then stop at phase 2
        reg = {"lock": threading.Lock(), "jobs": {"p": {"status": "running", "progress": [],
                                                        "arrived": {}, "phase": 1}}}
        loop = threading.Thread(target=ns["_preview_loop"],
                                args=(reg, "p", {"prompt": "Q", "preview": {"key": "ck", "expected": 4}}))
        loop.start()
        for i in range(4):
            time.sleep(0.6)
            with reg["lock"]:
                reg["jobs"]["p"]["arrived"] = {str(k): "a" for k in range(i + 1)}
        time.sleep(0.6)
        with reg["lock"]:
            reg["jobs"]["p"]["phase"] = 2
        loop.join(timeout=5)
        self.assertFalse(loop.is_alive())
        calls = reg["jobs"]["p"]["preview_calls"]
        self.assertLessEqual(len(calls), 5)
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(reg["jobs"]["p"]["preview"]["after"], 4)

    def test_report_cards_from_saved_runs(self):
        ns = {}
        for fn in ("_median", "model_report_stats", "report_card_for", "report_card_text"):
            load_function(fn, ns)
        msg = lambda secs, **kw: dict({"role": "assistant", "content": "x", "model_id": "m1",
                                       "settings": "s", "secs": secs, "cost": 0.01}, **kw)
        entries = [{"threads": {"Grok": [msg(40), msg(60, web=True), msg(0, err=True),
                                         {"role": "assistant", "content": "No key for Grok",
                                          "err": True, "model_id": "m1", "settings": "s"},
                                         msg(90, late=True)]}}]
        stats = ns["model_report_stats"](entries)
        card = ns["report_card_for"](stats, "Grok", "m1", "s")
        self.assertEqual(card["n"], 4)                  # the missing-key error is not counted
        self.assertEqual(card["fail"], 1)
        self.assertEqual(card["late"], 1)
        self.assertEqual(card["secs"], 65)              # median of 40 and 90 (web kept apart)
        self.assertEqual(card["secs_web"], 60)
        self.assertIn("25% failed", ns["report_card_text"](card))
        other = ns["report_card_for"](stats, "Grok", "m1", "different settings")
        self.assertEqual(other["scope"], "all settings")
        self.assertIsNone(ns["report_card_for"](stats, "Claude", "x", ""))

    def test_helper_costs_are_itemized_per_turn(self):
        ns = {"CEREBRAS_PREVIEW_MODEL": "gpt-oss-120b"}
        rows = load_function("helper_cost_rows", ns)({
            "preview_calls": [{"cost": 0.002, "secs": 0.5, "in_tok": 100, "out_tok": 10,
                               "model": "gpt-oss-120b"}] * 3,
            "jev": {"calls": [{"cost": 0.00001, "secs": 0.2, "in_tok": 200, "out_tok": 0,
                               "model": "jev-1.14.0"}]}})
        self.assertEqual([r["icon"] for r in rows], ["👀", "🧭"])
        self.assertAlmostEqual(rows[0]["cost"], 0.006)
        self.assertEqual(rows[0]["settings"], "3 calls")
        snap = load_function("turn_cost_snapshot", {
            "MODEL_ORDER": ["Claude"], "ICON": {},
            "_assistant_for_turn": lambda thread, tid: thread[-1] if thread else None})
        out = snap({"Claude": [{"role": "assistant", "cost": 0.01, "turn_id": 0}]},
                   {"role": "assistant", "cost": 0.02, "turn_id": 0, "helpers": rows},
                   "Synthesizer")
        self.assertAlmostEqual(out["source_total"], 0.01)
        self.assertAlmostEqual(out["helper_total"], 0.00601)
        self.assertAlmostEqual(out["total"], 0.03601)

    def test_jev_suggestion_apply_is_the_only_thing_that_changes_settings(self):
        state = {"k_web_all": False, "k_full_council": False,
                 "_jev_suggestion_fu": {"preset": "Thorough", "needs_web": 0.9,
                                        "needs_council": 0.8, "follow_up": True}}
        applied = []
        ns = {"st": SimpleNamespace(session_state=state), "PRESETS": {"Light": {}, "Thorough": {}},
              "current_preset_name": lambda: "Light",
              "_apply_preset": applied.append,
              "_set_all_source_web": lambda: applied.append("web"),
              "_restore_initial_council": lambda: applied.append("council")}
        load_function("_apply_jev_suggestion", ns)("_jev_suggestion_fu")
        self.assertEqual(applied, ["Thorough", "web", "council"])
        self.assertTrue(state["k_web_all"]); self.assertTrue(state["k_full_council"])
        self.assertNotIn("_jev_suggestion_fu", state)

    def test_live_timeline_places_each_phase_where_it_began(self):
        fn = load_function("live_timeline_bars", {"time": time})
        job = {"started": 100.0, "phase1_done_at": 140.0,
               "plan": {"phase2": {"label_key": "Synthesizer"}}}
        rows = [{"name": "A", "label": "A", "state": "done", "secs": 30.0},
                {"name": "Slow", "label": "Slow", "state": "active", "started_at": 100.0},
                {"name": "Synthesizer", "label": "Synthesizer", "state": "active",
                 "started_at": 140.0},
                {"name": "Adjudicator", "label": "Adjudicator", "state": "queued"}]
        bars, total = fn(job, rows, now=150.0)
        self.assertEqual(total, 50.0)
        self.assertEqual([(b["start"], b["secs"]) for b in bars],
                         [(0.0, 30.0), (0.0, 50.0), (40.0, 10.0)])   # queued rows are not drawn

    def test_seat_stats_and_jev_record(self):
        ns = {}
        for fn in ("_median", "seat_report_stats"):
            load_function(fn, ns)
        entries = [{"threads": {"Grok": [{"role": "assistant", "jev": {"verdict": "answers"}},
                                         {"role": "assistant", "jev": {"verdict": "refuses"}}]},
                    "synth": {"provider": "Claude",
                              "turns": [{"turn_id": 0, "evaluator_provider": "Claude"}],
                              "thread": [{"role": "assistant", "turn_id": 0, "model_id": "m",
                                          "secs": 40, "cost": 0.1, "revised": True,
                                          "pre_revision": {"model_id": "m", "secs": 20, "cost": 0.05},
                                          "adjudicator": {"provider": "ChatGPT", "model_id": "g",
                                                          "secs": 30, "cost": 0.02,
                                                          "material_defects": True},
                                          "jev": {"claims": {"clean": False}, "would_hold": ["Grok"]},
                                          "helpers": [{"name": "Live draft", "cost": 0.003}]},
                                         {"role": "assistant", "turn_id": 1, "solo": True,
                                          "model_id": "m", "secs": 9, "cost": 0.01}]}}]
        seats, jev, helpers = ns["seat_report_stats"](entries)
        self.assertEqual(seats[("Synthesizer", "Claude", "m")]["secs"], 20)   # the first draft
        self.assertEqual(seats[("Synthesizer (solo reply)", "Claude", "m")]["n"], 1)
        self.assertEqual(seats[("Adjudicator", "ChatGPT", "g")]["cost"], 0.02)
        self.assertEqual(jev, {"claim_checks": 1, "compared": 1, "agreed": 1, "would_hold": 1,
                               "answers_read": 2, "flagged_answers": 1})
        self.assertEqual(helpers, {"Live draft": {"runs": 1, "cost": 0.003}})

    def test_bg_start_attaches_helpers_without_ever_blocking_a_run(self):
        src = APP.read_text(encoding="utf-8")
        start = src.index("def bg_start(plan):")
        body = src[start:src.index("def bg_active():")]
        self.assertIn("build_speed_plan(plan)", body)
        self.assertIn("except Exception:", body)
        self.assertIn('extra["quorum"]', src[src.index("def build_speed_plan"):start])


if __name__ == "__main__":
    unittest.main()
