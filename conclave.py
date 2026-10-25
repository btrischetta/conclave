# Conclave — https://github.com/btrischetta/conclave
# Copyright 2026 Brian Trischetta. Licensed under the Apache License,
# Version 2.0 (see LICENSE and NOTICE).
import streamlit as st
import json, os, sys, time, base64, random, uuid, html, re, shutil, io
from datetime import datetime
from contextlib import contextmanager
from importlib import metadata as _pkgmeta
from openai import OpenAI, RateLimitError as OpenAIRateLimitError
from anthropic import Anthropic, APIStatusError as AnthropicAPIStatusError
from google import genai
from google.genai import types
from google.genai import errors as genai_errors
import threading
from copy import deepcopy
import msvcrt
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

import openai.resources.chat        # noqa: E402,F401  -- see deadlock note above
import openai.resources.responses   # noqa: E402,F401
import openai.resources.audio       # noqa: E402,F401

APP_VERSION = "3.88.0"

try:
    import win32crypt
    _DPAPI = True
except Exception:
    _DPAPI = False

def _enc(s):
    if not _DPAPI or not s: return s
    blob = win32crypt.CryptProtectData(s.encode(), None, None, None, None, 0)
    return "enc:" + base64.b64encode(blob).decode()

def _dec(s):
    if not isinstance(s, str) or not s.startswith("enc:"): return s
    if not _DPAPI: return ""
    try:
        blob = base64.b64decode(s[4:].encode())
        _, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return data.decode()
    except Exception:
        return ""

APP_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.environ.get("CONCLAVE_TEST_DATA_DIR", APP_DIR)
KEYS_FILE    = os.path.join(DATA_DIR, "api_keys.json")
HISTORY_FILE = os.path.join(DATA_DIR, "ai_history.json")
HISTORY_ARCHIVE_FILE = os.path.join(DATA_DIR, "ai_history_archive.json")
HISTORY_RECENT_LIMIT = 50
BRAND_MARK   = os.path.join(APP_DIR, "assets", "conclave_mark.png")

CONCLAVE_SEAL_SVG = """<svg class="seal-rot" viewBox="-130 -130 260 260" xmlns="http://www.w3.org/2000/svg">
  <g fill="none" stroke-linecap="round">
    <path d="M 78 -62 A 100 100 0 0 0 -100 0" stroke="#c3d0e0" stroke-width="21"/>
    <path d="M -100 0 A 100 100 0 0 0 78 62" stroke="#8494a6" stroke-width="21"/>
    <path d="M 96 -32 A 100 100 0 0 0 90 -45" stroke="#2fd4c6" stroke-width="21"/>
  </g>
</svg><svg class="seal-fix" viewBox="-130 -130 260 260" xmlns="http://www.w3.org/2000/svg">
  <circle class="seal-hone" cx="0" cy="0" r="34" fill="none" stroke="#2fd4c6" stroke-width="7" opacity="0"/>
  <circle class="seal-dot" cx="93" cy="42" r="17" fill="#2fd4c6"/>
  <circle cx="0" cy="0" r="7" fill="#f2f6fa"/>
</svg>"""

def seal_mark_html(px, loading=False):
    cls = "conclave-mark conclave-seal-loading" if loading else "conclave-mark"
    return (f'<div class="{cls}" style="width:{px}px;height:{px}px;">'
            f'{CONCLAVE_SEAL_SVG}</div>')

def render_brand_mark(px):
    st.markdown(seal_mark_html(px), unsafe_allow_html=True)
KEY_MAP   = {"ChatGPT": "openai", "Claude": "anthropic", "Gemini": "gemini",
             "DeepSeek": "deepseek", "Grok": "grok", "Qwen": "qwen",
             "Mistral": "mistral", "Kimi": "moonshot",
             "Meta": "meta", "MiniMax": "minimax", "NVIDIA": "nvidia", "GLM": "zai",
             "MiMo": "xiaomi"}
VOICE_KEY = {"ChatGPT": "k_v_gpt", "Claude": "k_v_cla", "Gemini": "k_v_gem",
             "DeepSeek": "k_v_ds", "Grok": "k_v_grok", "Qwen": "k_v_qwen",
             "Mistral": "k_v_mistral", "Kimi": "k_v_kimi",
             "Meta": "k_v_meta", "MiniMax": "k_v_mm", "NVIDIA": "k_v_nv", "GLM": "k_v_glm",
             "MiMo": "k_v_mimo",
             "Synthesizer": "k_v_syn", "Researcher": "k_v_res", "Adjudicator": "k_v_adj"}
ICON      = {"ChatGPT": "\U0001F4AC", "Claude": "\u2734\ufe0f", "Gemini": "\U0001F537",
             "DeepSeek": "\U0001F30A", "Grok": "\u26A1", "Qwen": "\U0001F7E3",
             "Mistral": "🟠", "Kimi": "🌙",
             "Meta": "♾️", "MiniMax": "🔶", "NVIDIA": "🟩", "GLM": "🌀", "MiMo": "🟧",
             "Synthesizer": "⭐", "Researcher": "\U0001F52C", "Adjudicator": "\u2696\ufe0f"}
MODEL_ORDER = ["Claude", "ChatGPT", "Gemini", "Grok", "Meta", "DeepSeek", "Kimi",
               "Qwen", "MiMo", "GLM", "MiniMax", "Mistral", "NVIDIA"]
EVAL_MEMO_AUDIO_NAMES = ["Synthesizer_memo", "Adjudicator_memo"]
BILLING_LINKS = {
    "ChatGPT": "https://platform.openai.com/settings/organization/billing/overview",
    "Claude":  "https://console.anthropic.com/settings/billing",
    "Gemini":  "https://aistudio.google.com/app/billing",
    "DeepSeek": "https://platform.deepseek.com/usage",
    "Grok":    "https://console.x.ai/team/default/billing",
    "Qwen":    "https://dashscope-intl.console.aliyun.com/",
    "Mistral": "https://admin.mistral.ai/organization/billing",
    "Kimi":    "https://platform.kimi.ai/console/account",
    "Meta":    "https://dev.meta.ai",
    "MiniMax": "https://platform.minimax.io/user-center/payment/token-plan",
    "NVIDIA":  "https://build.nvidia.com/settings/api-keys",
    "GLM":     "https://z.ai/manage-apikey/billing",
    "MiMo":    "https://platform.xiaomimimo.com/#/console/usage",
}

PROVIDER_ORIGIN = {
    "ChatGPT": "🇺🇸", "Claude": "🇺🇸", "Gemini": "🇺🇸", "Grok": "🇺🇸",
    "DeepSeek": "🇨🇳", "Qwen": "🇨🇳", "Mistral": "🇫🇷", "Kimi": "🇨🇳",
    "Meta": "🇺🇸", "MiniMax": "🇨🇳", "NVIDIA": "🇺🇸", "GLM": "🇨🇳", "MiMo": "🇨🇳",
}

_FLAG_SVG = {
    "🇺🇸": ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 14'>"
             "<rect width='20' height='14' fill='%23b22234'/>"
             "<g fill='%23fff'><rect y='2' width='20' height='2'/>"
             "<rect y='6' width='20' height='2'/><rect y='10' width='20' height='2'/></g>"
             "<rect width='9' height='7' fill='%233c3b6e'/></svg>"),
    "🇨🇳": ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 14'>"
             "<rect width='20' height='14' fill='%23de2910'/>"
             "<path fill='%23ffde00' d='M4 1.6l.8 2.4h2.5l-2 1.5.7 2.4-2-1.5-2 1.5.7-2.4-2-1.5h2.5z'/></svg>"),
    "🇫🇷": ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 14'>"
             "<rect width='20' height='14' fill='%23fff'/>"
             "<rect width='7' height='14' fill='%23002395'/>"
             "<rect x='13' width='7' height='14' fill='%23ed2939'/></svg>"),
}

def origin_flag_css(origin):
    """The ::after declarations for one origin: the (emoji-or-letters) text
    plus the little SVG flag drawn to its left."""
    svg = _FLAG_SVG.get(origin, "")
    bg = f' background-image: url("data:image/svg+xml,{svg}");' if svg else ""
    return f'content: "{origin}";{bg}'

PROVIDER_LINKS = {
    "openai":    ("https://platform.openai.com/api-keys",
                  "https://platform.openai.com/settings/organization/billing/overview"),
    "anthropic": ("https://console.anthropic.com/settings/keys",
                  "https://console.anthropic.com/settings/billing"),
    "gemini":    ("https://aistudio.google.com/app/apikey",
                  "https://aistudio.google.com/app/billing"),
    "deepseek":  ("https://platform.deepseek.com/api_keys",
                  "https://platform.deepseek.com/usage"),
    "grok":      ("https://console.x.ai/team/default/api-keys",
                  "https://console.x.ai/team/default/billing"),
    "qwen":      ("https://dashscope-intl.console.aliyun.com/apiKey",
                  "https://dashscope-intl.console.aliyun.com/"),
    "mistral":   ("https://console.mistral.ai/?profile_dialog=api-keys",
                  "https://admin.mistral.ai/organization/billing"),
    "moonshot":  ("https://platform.kimi.ai/console/api-keys",
                  "https://platform.kimi.ai/console/account"),
    "meta":      ("https://dev.meta.ai",
                  "https://dev.meta.ai"),
    "minimax":   ("https://platform.minimax.io/user-center/basic-information/interface-key",
                  "https://platform.minimax.io/user-center/payment/token-plan"),
    "nvidia":    ("https://build.nvidia.com/settings/api-keys",
                  "https://build.nvidia.com/settings/api-keys"),
    "zai":       ("https://z.ai/manage-apikey/apikey-list",
                  "https://z.ai/manage-apikey/billing"),
    "xiaomi":    ("https://platform.xiaomimimo.com/#/console/api-keys",
                  "https://platform.xiaomimimo.com/#/console/usage"),
    "typesafe":  ("https://typesafe.ai", "https://typesafe.ai"),
    "cerebras":  ("https://cloud.cerebras.ai", "https://cloud.cerebras.ai"),
    "tavily":    ("https://app.tavily.com", "https://app.tavily.com"),
    "brave":     ("https://api-dashboard.search.brave.com/app/keys",
                  "https://api-dashboard.search.brave.com"),
}

def provider_links_caption(key_name, short=False):
    """The two-link caption rendered under a Settings key field."""
    keys_url, billing_url = PROVIDER_LINKS[key_name]
    if short:
        return f"[🔑 Keys]({keys_url}) · [💳 Billing]({billing_url})"
    return (f"[🔑 Create / manage API keys]({keys_url})  ·  "
            f"[💳 Billing & payments]({billing_url})")


API_KEY_GROUPS = [
    ("🏛️ Council models", "The thirteen AI providers that answer, synthesize and audit. "
     "A provider without a key simply can't take part.", [
        ("anthropic", "Anthropic (Claude)", None),
        ("openai", "OpenAI (ChatGPT)", None),
        ("gemini", "Google Gemini", None),
        ("grok", "xAI Grok", None),
        ("meta", "Meta (Muse Wonder · Spark)",
         "From dev.meta.ai → your team's dashboard → API keys. Public preview; $20 in free "
         "credits at signup; limits are per team."),
        ("deepseek", "DeepSeek", None),
        ("moonshot", "Kimi (Moonshot AI)",
         "From platform.kimi.ai → Console → API Keys. Prepaid: at least $1 to call at all, "
         "$10 cumulative lifts the 3-requests/minute starter limit the council would hit."),
        ("qwen", "Qwen (Alibaba Cloud)",
         "From the Alibaba Cloud Model Studio / DashScope console, US (Virginia) region."),
        ("xiaomi", "Xiaomi MiMo",
         "From platform.xiaomimimo.com → Console → API Keys (Xiaomi account; keys start with "
         "sk-). For MiMo's own web search, also switch on the Web Search Plugin under "
         "Console → Plugin Management."),
        ("zai", "Z.ai (GLM)",
         "From z.ai/model-api → Manage API Keys. Note: Z.ai is on the US Commerce Entity "
         "List (a US trade restriction) — fine for everyday questions; avoid sending it sensitive material."),
        ("minimax", "MiniMax",
         "From platform.minimax.io (international) → User Center → API Keys. Pay-as-you-go: "
         "add balance or a Token Plan first."),
        ("mistral", "Mistral AI",
         "From console.mistral.ai → API Keys (pay-as-you-go; a free rate-limited tier needs "
         "no card)."),
        ("nvidia", "NVIDIA (Nemotron)",
         "From build.nvidia.com → any Nemotron model page → Get API Key. Free trial endpoint: "
         "no billing, ~40 requests per minute."),
    ], 3),
    ("🔎 Web sources", "Search engines, not AI models: each turn they fetch current web "
     "pages and hand them to every source whose 🔎 switch is on (Council page; engine "
     "settings under Speed & helpers). They never answer questions themselves.", [
        ("tavily", "Tavily",
         "Ranked pages with their most relevant passages. About 1.6 cents a search (0.8 at "
         "basic depth); 1,000 free credits a month. Sends the search query to Tavily."),
        ("brave", "Brave Search",
         "Brave's own index, searched at the same time as Tavily; pages are merged without "
         "duplicates. Half a cent a search; $5 of free searches a month. Sends the search "
         "query to Brave."),
    ], 3),
    ("🧭 Helpers", "Not council members: they make runs faster and check quality.", [
        ("typesafe", "TypeSafe (Jev decision checks)",
         "Jev returns typed decisions (yes/no, a pick, a score), never text: it reads each "
         "question, checks each source answer, and checks the draft's claims. About $0.04 "
         "per million tokens read. Each check sends the question and answers to TypeSafe."),
        ("cerebras", "Cerebras (live draft)",
         "Writes the quick live draft you read while the council works (GPT-OSS 120B at "
         "~3,000 tokens a second, about a cent per run). Sends the question and answer "
         "excerpts to Cerebras."),
    ], 3),
]

CHATGPT_MODELS = {
    "Astra (flagship)": "gpt-6-astra",
    "Sol (advanced)": "gpt-6.1-sol",
    "Luna (economy)": "gpt-6-luna",
}
CHATGPT_EFFORT = {"Instant": "none", "Low": "low", "Medium": "medium", "High": "high",
                   "Extra (xhigh)": "xhigh", "Max": "max"}
CHATGPT_NO_NONE = {"gpt-6-astra", "gpt-6.1-sol"}   # reasoning cannot be turned off on these

def chatgpt_effort_sent(model_id, effort):
    """The effort value actually sent: Astra and GPT-6.1 Sol reject "none",
    so Instant becomes "low" there (settings_str reports it honestly)."""
    if model_id in CHATGPT_NO_NONE and effort in ("none", "", None):
        return "low"
    return effort

def chatgpt_version_of(model_id):
    """Card/label version text derived from the ID ("gpt-6-astra" -> "6")."""
    return (model_id or "").replace("gpt-", "").split("-")[0]
CHATGPT_SPEED = {"Standard": "default", "Fast": "fast", "Flex": "flex", "Ultrafast": "ultrafast"}
CHATGPT_SPEED_DESC = {
    "Standard": "Normal pay-as-you-go processing at the model's standard token price.",
    "Fast": ("OpenAI Fast mode: faster, more consistent output at 2× token pricing. "
             "Prompts above 272K input tokens are not eligible."),
    "Flex": ("OpenAI Flex: half the Standard price, but slower and sometimes "
             "busy. If Flex has no room, Conclave re-sends at Standard and says so."),
    "Ultrafast": ("OpenAI Ultrafast: the quickest output at 6× Standard pricing — "
                  "Astra and Sol 6.1 only (Luna runs at Fast instead)."),
}
CHATGPT_ULTRAFAST_MODELS = {"gpt-6-astra", "gpt-6.1-sol"}


def chatgpt_speed_used(model_id, speed_label):
    """The processing speed a ChatGPT call actually requests: Ultrafast on a
    model without it (Luna) runs at Fast. Pure."""
    if speed_label == "Ultrafast" and model_id not in CHATGPT_ULTRAFAST_MODELS:
        return "Fast"
    return speed_label if speed_label in CHATGPT_SPEED else "Standard"


def _is_flex_busy_error(e):
    """OpenAI Flex's documented "429 Resource Unavailable" (no capacity; not
    billed) -- never an ordinary rate limit or a billing error."""
    msg = str(e).lower()
    return "resource unavailable" in msg or "resource_unavailable" in msg
CLAUDE_EFFORT  = {"Low": "low", "Medium": "medium", "High": "high", "Extra (xhigh)": "xhigh", "Max": "max"}
GEMINI_MODELS  = {"Flash-Lite (3.5)": "gemini-3.5-flash-lite", "Flash (3.8)": "gemini-3.8-flash",
                  "Pro (3.1)": "gemini-3.1-pro-preview", "Argon (4)": "gemini-4-argon"}
GEMINI_THINK   = {"Minimal": "minimal", "Standard": "low", "Balanced": "medium", "Extended": "high"}
GEMINI_MINIMAL_MODELS = {"gemini-3.5-flash-lite"}

GEMINI_NO_SEARCH_MODELS = ()
GEMINI_SEARCH_STANDIN = "gemini-3.8-flash"


def gemini_cfg_for_web(cfg):
    """The Gemini settings actually used for a web-on call."""
    if cfg.get("model_id") in GEMINI_NO_SEARCH_MODELS:
        cfg = dict(cfg)
        cfg["model_id"] = GEMINI_SEARCH_STANDIN
    return cfg


def gemini_level_sent(model_id, level):
    """The thinking_level actually sent: Minimal only where it is accepted."""
    if level == "minimal" and model_id not in GEMINI_MINIMAL_MODELS:
        return "low"
    return level
GROK_MODES     = ["Fast", "Balanced", "High", "Expert"]
GROK_EFFORT    = {"Fast": "low", "Balanced": "medium", "High": "high", "Expert": "xhigh"}
DEEPSEEK_MODELS = {
    "V4.1 Flash (fast · vision)": "deepseek-flash",
}
DEEPSEEK_VISION_MODELS = {"deepseek-flash", "deepseek-v4-flash-vision-exp"}
DEEPSEEK_EFFORT = {"Low": "low", "High": "high", "Max": "max"}
QWEN_MODELS = {
    "Max 3.8 (flagship)": "qwen3.8-max",
    "Plus 3.7 (balanced)": "qwen3.7-plus",
    "Flash 3.8 (economy)": "qwen3.8-flash",
    "Flash 3.7 (cheapest)": "qwen3.7-flash",
}
QWEN_EFFORT = {"Default": None, "Low": "low", "Medium": "medium", "Extra (xhigh)": "xhigh"}
QWEN_GRADED_EFFORT_MODELS = {"qwen3.8-max", "qwen3.8-flash"}
QWEN_BUDGET = {"Light": 4000, "Standard": 8000, "Deep": 16000, "Max": 32000}

MISTRAL_MODELS = {
    "Large 4 (flagship · preview)": "mistral-large-4",
    "Medium 3.5 (advanced)": "mistral-medium-latest",
    "Large 3 (balanced)":    "mistral-large-2512",
    "Small 4 (economy)":     "mistral-small-latest",
}
MISTRAL_ID_ALTERNATES = {"mistral-large-4": "mistral-large-4-0"}
MISTRAL_EFFORT = {"Off": "none", "Minimal": "minimal", "Low": "low", "Medium": "medium",
                  "High": "high", "Extra (xhigh)": "xhigh"}
MISTRAL_VISION_MODELS = {"mistral-large-latest", "mistral-medium-latest", "mistral-large-2512",
                         "mistral-large-4", "mistral-large-4-0"}
MISTRAL_REASONING_MODELS = {"mistral-medium-latest", "mistral-small-latest",
                            "mistral-large-4", "mistral-large-4-0"}

KIMI_MODELS = {
    "K3.1 Pro (flagship)": "kimi-k3.1-pro",
    "K3 (prior)":      "kimi-k3",
    "K2.6 (balanced)": "kimi-k2.6",
}
KIMI_EFFORT = {"Low": "low", "High": "high", "Max": "max"}   # K3 only
KIMI_ALWAYS_THINK = {"kimi-k3", "kimi-k3.1-pro"}
KIMI_MAX_TOKENS = {"kimi-k3": 32000, "kimi-k3.1-pro": 32000, "kimi-k2.6": 16000}

META_MODELS = {
    "Wonder 1.0 (flagship · preview)": "muse-wonder-1.0",
    "Spark 1.3 (balanced)": "muse-spark-1.3",
}
META_EFFORT = {"Minimal": "minimal", "Low": "low", "Medium": "medium", "High": "high",
               "Extra (xhigh)": "xhigh", "Max": "max"}   # same dial on Wonder and Spark 1.3

MINIMAX_MODELS = {
    "M3 (flagship)":          "MiniMax-M3",
}
MINIMAX_THINK_OFF_OK = {"MiniMax-M3"}
MINIMAX_SPEED = {"Standard": None, "Priority": "priority"}
MINIMAX_SPEED_DESC = {
    "Standard": "Normal processing at MiniMax's standard token price.",
    "Priority": "MiniMax priority tier: faster scheduling at 1.5× token pricing.",
}

NVIDIA_MODELS = {
    "Nemotron 3 Ultra (flagship)": "nvidia/nemotron-3-ultra-550b-a55b",
    "Nemotron 3 Super (balanced)": "nvidia/nemotron-3-super-120b-a12b",
    "Nemotron 3.5 Lightning (economy)": "nvidia/nemotron-3.5-lightning-30b-a3b",
    "Nemotron 3 Nano Omni (vision)": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
}
NVIDIA_VISION_MODELS = {"nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"}

GLM_MODELS = {
    "GLM-5.3 (flagship)":     "glm-5.3",
    "GLM-5.3 Flash (economy)": "glm-5.3-flash",
}
GLM_EFFORT = {"Low": "low", "High": "high", "Max": "max"}   # glm-5.3 only
GLM_ALWAYS_THINK = {"glm-5.3", "glm-5.3-flash"}
GLM_VISION_MODELS = {"glm-5.3-flash"}

MIMO_MODELS = {
    "V2.6 Pro (flagship)": "mimo-v2.6-pro",
    "V2.6 Flash (economy)": "mimo-v2.6-flash",
    "V2.6 Pro Ultraspeed (fast · 10× price)": "mimo-v2.6-pro-ultraspeed",
}
MIMO_URL = "https://api.xiaomimimo.com/v1"
MIMO_SEARCH_KEYWORDS = 3      # caps searches per round ($0.005 each)

PROVIDERS = {
    "ChatGPT": {"enabled_key": "k_en_gpt", "web_key": "k_web_gpt"},
    "Claude": {"enabled_key": "k_en_cla", "web_key": "k_web_cla"},
    "Gemini": {"enabled_key": "k_en_gem", "web_key": "k_web_gem"},
    "DeepSeek": {"enabled_key": "k_en_ds", "web_key": None},
    "Grok": {"enabled_key": "k_en_grok", "web_key": "k_web_grok"},
    "Qwen": {"enabled_key": "k_en_qwen", "web_key": "k_web_qwen"},
    "Mistral": {"enabled_key": "k_en_mistral", "web_key": None},
    "Kimi": {"enabled_key": "k_en_kimi", "web_key": "k_web_kimi"},
    "Meta": {"enabled_key": "k_en_meta", "web_key": "k_web_meta"},
    "MiniMax": {"enabled_key": "k_en_mm", "web_key": "k_web_mm"},
    "NVIDIA": {"enabled_key": "k_en_nv", "web_key": None},
    "GLM": {"enabled_key": "k_en_glm", "web_key": "k_web_glm"},
    "MiMo": {"enabled_key": "k_en_mimo", "web_key": "k_web_mimo"},
}
NO_WEB_PROVIDERS = tuple(p for p, spec in PROVIDERS.items() if spec["web_key"] is None)
_NAME_TO_EN_KEY = {p: spec["enabled_key"] for p, spec in PROVIDERS.items()}
_EN_KEY_TO_NAME = {key: p for p, key in _NAME_TO_EN_KEY.items()}
_EN_KEYS_ALL = tuple(_EN_KEY_TO_NAME)
_SRC_WEB_KEY = {p: spec["web_key"] for p, spec in PROVIDERS.items() if spec["web_key"]}
_SOURCE_WEB_KEYS = tuple(_SRC_WEB_KEY.values())
_NO_WEB_EN_KEYS = {p: _NAME_TO_EN_KEY[p] for p in NO_WEB_PROVIDERS}

QWEN_WEB_MODELS = {"qwen3.8-max", "qwen3.8-flash"}

def qwen_web_supported(model_id):
    return model_id in QWEN_WEB_MODELS

def deepseek_web_supported(model_id):
    return False

def deepseek_best_web_model():
    """Fallback if a selected DeepSeek model cannot search. Both current IDs
    support hosted search, so this should almost never fire; keep Flash as the
    cheap rescue so an unknown future ID still gets a working search path."""
    for label, mid in DEEPSEEK_MODELS.items():
        if deepseek_web_supported(mid):
            return label
    return next(iter(DEEPSEEK_MODELS))  # defensive only; unreachable while Flash exists

VOICES = ["alloy", "ash", "ballad", "cedar", "coral", "echo", "fable", "marin",
          "nova", "onyx", "sage", "shimmer", "verse"]
TTS1_FALLBACK_VOICES = {"alloy", "ash", "coral", "echo", "fable", "nova", "onyx",
                        "sage", "shimmer"}

MODEL_GPT    = CHATGPT_MODELS["Astra (flagship)"]  # compatibility/default constant
MODEL_GROK   = "grok-4.7"
GPT_VER  = MODEL_GPT.replace("gpt-", "").split("-")[0]
GROK_VER = MODEL_GROK.replace("grok-", "").split("-")[0]
CLAUDE_MODELS = {                                   # display name -> API id
    "Haiku 5.5":  "claude-haiku-5-5",
    "Sonnet 5.5": "claude-sonnet-5-5",
    "Opus 5.5":   "claude-opus-5-5",
    "Fable 5.1":  "claude-fable-5-1",
}
# Capability quirks within the Claude family (verified against Anthropic docs):
CLAUDE_NO_DIALS     = {"claude-haiku-4-5"}   # rejects effort param & adaptive thinking
CLAUDE_ALWAYS_THINK = {"claude-fable-5", "claude-fable-5-1", "claude-opus-5-5"}   # thinking always on; explicit off = error
CLAUDE_OLD_SEARCH   = {"claude-haiku-4-5"}   # needs the older web_search tool version
CLAUDE_THINKING_ON = {"type": "adaptive", "display": "summarized"}
CLAUDE_HAIKU_THINKING_BUDGET = 8000
CLAUDE_NO_DISABLE_ABOVE_HIGH = {"claude-opus-5", "claude-sonnet-5-5", "claude-haiku-5-5"}
CLAUDE_BETWEEN_TOOLS_OFF = {"claude-sonnet-5-5"}
CLAUDE_PAUSE_CONTINUES = 3

def claude_thinking_forced(model_id, effort):
    """True when this model/effort pair cannot accept an explicit thinking-off."""
    return model_id in CLAUDE_NO_DISABLE_ABOVE_HIGH and effort in ("xhigh", "max")

# Conclave recommendations, not Anthropic API limits. The web-search tool accepts
# the same hard range for every Claude model; these defaults simply keep the cap
# proportionate to the selected model and effort. Users can override the number.
CLAUDE_SEARCH_CAPS = {
    "Haiku 5.5":  {"Low": 2, "Medium": 2, "High": 3, "Extra (xhigh)": 4, "Max": 5},
    "Sonnet 5.5": {"Low": 2, "Medium": 3, "High": 5, "Extra (xhigh)": 8, "Max": 10},
    "Opus 5.5":   {"Low": 3, "Medium": 5, "High": 7, "Extra (xhigh)": 10, "Max": 14},
    "Fable 5.1":  {"Low": 4, "Medium": 6, "High": 8, "Extra (xhigh)": 12, "Max": 20},
}

def recommended_claude_search_cap(model_label, effort_label):
    """Return Conclave's cost/depth recommendation for Claude web search."""
    model_caps = CLAUDE_SEARCH_CAPS.get(model_label, CLAUDE_SEARCH_CAPS["Sonnet 5.5"])
    return model_caps.get(effort_label, model_caps["Medium"])

# USD per 1M tokens (input, output) -- APPROXIMATE, edit if rates change
PRICES = {
    "gpt-5.6-sol": (4.00, 20.00), "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-6-astra": (10.00, 50.00),
    "gpt-6-sol": (2.00, 10.00), "gpt-6-luna": (0.10, 0.50),
    "gpt-6.1-sol": (2.00, 10.00),
    "gpt-oss-120b": (0.35, 0.75),
    "claude-haiku-4-5": (1.00, 5.00), "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00), "claude-fable-5": (10.00, 50.00),
    "claude-fable-5-1": (10.00, 50.00),
    "claude-opus-5-5": (4.00, 20.00),
    "gemini-3.1-pro-preview": (2.00, 12.00), "gemini-3.7-flash": (0.75, 3.75),
    "gemini-4-argon": (2.00, 10.00),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "deepseek-flash": (0.30, 1.20),
    "deepseek-v4-flash": (0.44, 1.32), "deepseek-v4-pro": (1.32, 3.96),
    "mimo-v2.6-pro": (0.435, 0.87), "mimo-v2.6-flash": (0.14, 0.28),
    "mimo-v2.6-pro-ultraspeed": (4.35, 8.70),
    "deepseek-v4-flash-vision-exp": (0.44, 1.32),
    MODEL_GROK: (2.00, 6.00),
    "grok-4.6": (2.00, 6.00),
    "qwen3.8-max": (2.00, 6.00),
    "qwen3.7-max": (1.65, 4.951), "qwen3.7-plus": (0.40, 1.60),
    "qwen3.6-flash": (0.165, 0.99),
    "qwen3.8-flash": (0.15, 0.47),
    "qwen3.7-flash": (0.030, 0.13),
    "mistral-medium-latest": (1.50, 7.50), "mistral-large-latest": (0.50, 1.50),
    "mistral-large-2512": (0.50, 1.50),
    "mistral-large-4": (1.36, 4.18), "mistral-large-4-0": (1.36, 4.18),
    "mistral-small-latest": (0.15, 0.60),
    "kimi-k3": (3.00, 15.00), "kimi-k2.6": (0.95, 4.00),
    "kimi-k3.1-pro": (2.00, 10.00),
    "muse-spark-1.3": (1.25, 4.25), "muse-spark-1.2": (1.25, 4.25),
    "muse-wonder-1.0": (4.00, 20.00),
    "muse-spark-1.1": (1.25, 4.25),   # retired from the picker; kept for saved chats
    "MiniMax-M3": (0.30, 1.20), "MiniMax-M2.7": (0.30, 1.20),
    "MiniMax-M2.7-highspeed": (0.60, 2.40),
    "nvidia/nemotron-3-ultra-550b-a55b": (0.0, 0.0),
    "nvidia/nemotron-3-super-120b-a12b": (0.0, 0.0),
    "nvidia/nemotron-3-nano-30b-a3b": (0.0, 0.0),
    "nvidia/nemotron-3.5-lightning-30b-a3b": (0.0, 0.0),
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning": (0.0, 0.0),
    "glm-5.3": (1.40, 4.40), "glm-5-turbo": (1.20, 4.00), "glm-4.7": (0.60, 2.20),
    "glm-5.3-flash": (0.15, 0.50),
}
FLEX_PRICES = {"gpt-6-astra": (5.00, 25.00), "gpt-6.1-sol": (1.00, 5.00),
               "gpt-6-luna": (0.05, 0.25)}
ULTRAFAST_PRICES = {"gpt-6-astra": (60.00, 300.00), "gpt-6.1-sol": (12.00, 60.00)}
LONG_PROMPT_PRICES = {
    "claude-haiku-5-5": (100_000, 0.50, 2.50),
    "gemini-3.1-pro-preview": (200_000, 4.00, 18.00),
    "grok-4.7": (200_000, 4.00, 12.00),
    "gpt-6-astra": (272_000, 20.00, 75.00),
    "gpt-6.1-sol": (272_000, 4.00, 15.00),
    "gpt-6-luna": (272_000, 0.20, 0.75),
}
PRIORITY_PRICES = {
    "gpt-6-astra": (20.00, 100.00),
    "gpt-6-sol": (4.00, 20.00), "gpt-6-luna": (0.20, 1.00),
    "gpt-6.1-sol": (4.00, 20.00),
    "gpt-5.6-sol": (8.00, 40.00), "gpt-5.6-terra": (4.00, 24.00),
    "gpt-5.6-luna": (0.40, 2.40),
    "MiniMax-M3": (0.45, 1.80), "MiniMax-M2.7": (0.45, 1.80),
    "MiniMax-M2.7-highspeed": (0.90, 3.60),
}

# One-line descriptions shown in the expandable preset picker. Named presets
# coordinate the full run: source strength plus matching settings for whichever
# initial Synthesizer provider the user chose. Web-search toggles stay independent
# because searching is an explicit user choice, not an effort.
PAGE_WIDTHS = {
    "Focused":      "700px",
    "Extra narrow": "900px",
    "Narrow":       "1150px",
    "Medium":       "1400px",
    "Wide":         "1900px",
}

THEME_TOKENS = {
    "Light": {
        "--conclave-base": "#eff1f4", "--conclave-text": "#3b434d",
        "--conclave-muted": "#6f7984", "--conclave-line": "rgba(38, 52, 64, 0.13)",
        "--conclave-surface": "#e6e9ed", "--conclave-field": "#f3f4f6",
        "--conclave-card": "#f7f8fa",
    },
    "Dark": {
        "--conclave-base": "#0e1117", "--conclave-text": "#d8dee5",
        "--conclave-muted": "#98a3ad", "--conclave-line": "rgba(216, 222, 229, 0.15)",
        "--conclave-surface": "#141922", "--conclave-field": "#1d222c",
        "--conclave-card": "#171c24",
    },
}

STREAMLIT_THEME_REANCHOR_CSS = (
    '[data-testid="stAppViewContainer"], section[data-testid="stSidebar"] { '
    "color: var(--conclave-text); }\n"
    '[data-testid="stAppViewContainer"] [data-testid="stMarkdownContainer"], '
    'section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"], '
    '[data-testid="stAppViewContainer"] p, section[data-testid="stSidebar"] p { '
    "color: var(--conclave-text); }\n"
    '[data-testid="stAppViewContainer"] textarea, '
    '[data-testid="stAppViewContainer"] input, '
    'section[data-testid="stSidebar"] textarea, '
    'section[data-testid="stSidebar"] input { '
    "background: var(--conclave-field) !important; "
    "color: var(--conclave-text) !important; }\n"
    '[data-testid="stAppViewContainer"] [data-baseweb="select"] > div, '
    'section[data-testid="stSidebar"] [data-baseweb="select"] > div { '
    "background: var(--conclave-field) !important; "
    "color: var(--conclave-text) !important; }\n"
    '[data-testid="stBaseButton-secondary"], [data-testid="stPopoverButton"], '
    '[data-testid="stDownloadButton"] button, '
    '[data-testid="stBaseButton-segmented_control"], '
    '[data-testid="stBaseButton-pills"], [data-testid="stBaseButton-header"] { '
    "background: var(--conclave-card) !important; color: var(--conclave-text) !important; "
    "border-color: var(--conclave-hairline, var(--conclave-line)) !important; }\n"
    '[data-testid="stBaseButton-secondary"] p, [data-testid="stPopoverButton"] p, '
    '[data-testid="stBaseButton-segmented_control"] p { color: var(--conclave-text) !important; }\n'
    'button[kind="primary"], [data-testid="stBaseButton-primary"] { '
    "background: color-mix(in srgb, var(--conclave-accent) 22%, var(--conclave-card)) !important; "
    "color: var(--conclave-text) !important; "
    "border-color: color-mix(in srgb, var(--conclave-accent) 70%, var(--conclave-line)) !important; }\n"
    'button[kind="primary"] p, [data-testid="stBaseButton-primary"] p { '
    "color: var(--conclave-text) !important; }\n"
    'button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover { '
    "background: color-mix(in srgb, var(--conclave-accent) 34%, var(--conclave-card)) !important; }\n"
    '[data-testid="stBaseButton-segmented_controlActive"] { '
    "background: color-mix(in srgb, var(--conclave-accent) 24%, var(--conclave-card)) !important; "
    "color: var(--conclave-text) !important; }\n"
    '[data-testid="stButtonGroup"] { background: transparent !important; }\n'
    '[data-testid="stHeader"] { background: var(--conclave-base) !important; }\n'
    '[data-testid="stHeader"] button, [data-testid="stHeader"] svg { color: var(--conclave-muted) !important; }\n'
    '[data-baseweb="popover"] > div, [data-baseweb="popover"] [data-testid="stPopoverBody"] { '
    "background: var(--conclave-card) !important; color: var(--conclave-text) !important; "
    "border-color: var(--conclave-hairline, var(--conclave-line)) !important; }\n"
    '[data-testid="stExpander"] details, [data-testid="stExpander"] summary { '
    "background: var(--conclave-card) !important; color: var(--conclave-text) !important; }\n"
    '[data-testid="stTextAreaRootElement"], [data-testid="stTextInputRootElement"], '
    '[data-testid="stNumberInputContainer"], [data-testid="stSelectbox"] [role="group"], '
    '[data-testid="stMultiSelect"] [role="group"] { '
    "background: var(--conclave-field) !important; color: var(--conclave-text) !important; "
    "border-color: var(--conclave-hairline, var(--conclave-line)) !important; }\n"
    '[data-testid="stTextInputRootElement"] button, [data-testid="stTextInputRootElement"] svg, '
    '[data-testid="stSelectbox"] [role="group"] svg, [data-testid="stNumberInputContainer"] button { '
    "background: transparent !important; color: var(--conclave-muted) !important; "
    "fill: var(--conclave-muted) !important; }\n"
    '[data-testid="stButtonGroup"] button { '
    "background: var(--conclave-card) !important; color: var(--conclave-text) !important; "
    "border-color: var(--conclave-hairline, var(--conclave-line)) !important; }\n"
    '[data-testid="stButtonGroup"] button[aria-checked="true"], '
    '[data-testid="stButtonGroup"] button[aria-pressed="true"] { '
    "background: color-mix(in srgb, var(--conclave-accent) 24%, var(--conclave-card)) !important; }\n"
    '[data-testid="stButtonGroup"] button p, [data-testid="stButtonGroup"] button span { '
    "color: var(--conclave-text) !important; }\n"
    '[data-testid="stPopoverBody"], [data-testid="stPopoverBody"] [data-testid="stTextInputRootElement"], '
    '[data-testid="stPopoverBody"] [data-testid="stSelectbox"] [role="group"] { '
    "background: var(--conclave-card) !important; color: var(--conclave-text) !important; "
    "border-color: var(--conclave-hairline, var(--conclave-line)) !important; }\n"
    '[data-testid="stPopoverBody"] p, [data-testid="stPopoverBody"] label, '
    '[data-testid="stPopoverBody"] [data-testid="stMarkdownContainer"] { color: var(--conclave-text) !important; }\n'
    '[data-testid="stPopoverBody"] [data-testid="stSelectbox"] input { '
    "background: var(--conclave-field) !important; color: var(--conclave-text) !important; }\n"
    '[data-testid="stDialog"] > div, [data-testid="stDialog"] [role="dialog"] { '
    "background: var(--conclave-base) !important; color: var(--conclave-text) !important; }\n"
    '[data-testid="stDialog"] p, [data-testid="stDialog"] li, [data-testid="stDialog"] td, '
    '[data-testid="stDialog"] th, [data-testid="stDialog"] [data-testid="stMarkdownContainer"], '
    '[data-testid="stDialog"] h1, [data-testid="stDialog"] h2, [data-testid="stDialog"] h3 { '
    "color: var(--conclave-text) !important; }\n"
    '[data-testid="stDialog"] [data-testid="stTextInputRootElement"], '
    '[data-testid="stDialog"] [data-testid="stSelectbox"] [role="group"] { '
    "background: var(--conclave-field) !important; }\n"
    'ul[role="listbox"], [data-testid="stSelectboxVirtualDropdown"], [data-baseweb="menu"], '
    '[role="listbox"] li, [role="option"] { '
    "background: var(--conclave-card) !important; color: var(--conclave-text) !important; }\n"
    '[role="option"][aria-selected="true"], [role="option"]:hover, li[aria-selected="true"] { '
    "background: color-mix(in srgb, var(--conclave-accent) 18%, var(--conclave-card)) !important; }\n"
)

SOLAR_THEME_JS = """<script>
(function () {
  if (window.__conclaveSolarTimer) { clearInterval(window.__conclaveSolarTimer); }
  function sunUTC(lat, lon, date, rising) {
    var rad = Math.PI / 180, zenith = 90.833;
    var start = Date.UTC(date.getUTCFullYear(), 0, 0);
    var n = Math.floor((Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()) - start) / 864e5);
    var lngHour = lon / 15, t = n + ((rising ? 6 : 18) - lngHour) / 24;
    var M = 0.9856 * t - 3.289;
    var L = (M + 1.916 * Math.sin(M * rad) + 0.020 * Math.sin(2 * M * rad) + 282.634 + 720) % 360;
    var RA = (Math.atan(0.91764 * Math.tan(L * rad)) / rad + 360) % 360;
    RA = (RA + Math.floor(L / 90) * 90 - Math.floor(RA / 90) * 90) / 15;
    var sinDec = 0.39782 * Math.sin(L * rad), cosDec = Math.cos(Math.asin(sinDec));
    var cosH = (Math.cos(zenith * rad) - sinDec * Math.sin(lat * rad)) / (cosDec * Math.cos(lat * rad));
    if (cosH > 1) return rising ? 24 : 0;   /* polar night */
    if (cosH < -1) return rising ? 0 : 24;  /* midnight sun */
    var H = (rising ? 360 - Math.acos(cosH) / rad : Math.acos(cosH) / rad) / 15;
    return (H + RA - 0.06571 * t - 6.622 - lngHour + 48) % 24;
  }
  function apply() {
    var now = new Date(), day;
    var geo = null;
    try { geo = JSON.parse(localStorage.getItem("conclave_geo") || "null"); } catch (e) {}
    if (geo && typeof geo.lat === "number") {
      var utcH = now.getUTCHours() + now.getUTCMinutes() / 60;
      var rise = sunUTC(geo.lat, geo.lon, now, true), set = sunUTC(geo.lat, geo.lon, now, false);
      day = rise < set ? (utcH >= rise && utcH < set) : (utcH >= rise || utcH < set);
    } else {
      var h = now.getHours() + now.getMinutes() / 60;
      day = h >= 6.5 && h < 19;
    }
    var root = document.documentElement;
    root.classList.toggle("conclave-day", day);
    root.classList.toggle("conclave-night", !day);
  }
  apply();
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(function (pos) {
      try { localStorage.setItem("conclave_geo", JSON.stringify({lat: pos.coords.latitude, lon: pos.coords.longitude})); } catch (e) {}
      apply();
    }, function () {}, {maximumAge: 6 * 3600 * 1000, timeout: 8000});
  }
  window.__conclaveSolarTimer = setInterval(apply, 60000);
})();

(function () {
  if (window.__conclaveScrollKeeper) { return; }
  window.__conclaveScrollKeeper = true;
  var saved = 0, restoring = false, lastW = 0;
  function box() {
    var sb = document.querySelector('[data-testid="stSidebar"]');
    return sb && sb.querySelector('[data-testid="stSidebarContent"]');
  }
  function attach() {
    var c = box();
    if (!c || c.__conclaveKept) { return; }
    c.__conclaveKept = true;
    c.addEventListener('scroll', function () { if (!restoring) { saved = c.scrollTop; } }, {passive: true});
  }
  function restore() {
    var c = box();
    if (c && saved > 0 && c.scrollTop === 0 && c.scrollHeight > c.clientHeight) {
      restoring = true; c.scrollTop = saved;
      setTimeout(function () { restoring = false; }, 80);
    }
  }
  attach();
  var sb = document.querySelector('[data-testid="stSidebar"]');
  if (window.ResizeObserver && sb) {
    new ResizeObserver(function (entries) {
      var w = entries[0].contentRect.width;
      if (lastW && Math.abs(w - lastW) > 1) { restore(); setTimeout(restore, 0); setTimeout(restore, 120); }
      lastW = w;
    }).observe(sb);
  }
  new MutationObserver(function () { attach(); restore(); }).observe(document.body, {childList: true, subtree: true});
})();

(function () {
  var last = null;
  function align() {
    var sw = document.querySelector('.st-key-hdr_k_theme_mode');
    if (!sw) { return; }
    var r = sw.getBoundingClientRect();
    if (!r.width) { return; }
    var offset = Math.round(window.innerWidth - r.left + 10);
    if (offset !== last) {
      last = offset;
      document.documentElement.style.setProperty('--conclave-hdr-stepper-right', offset + 'px');
    }
  }
  align();
  window.addEventListener('resize', align);
  if (window.ResizeObserver) {
    var seen = null;
    new MutationObserver(function () {
      var sw = document.querySelector('.st-key-hdr_k_theme_mode');
      if (sw && sw !== seen) { seen = sw; new ResizeObserver(align).observe(sw); }
      align();
    }).observe(document.body, {childList: true, subtree: true});
  } else {
    new MutationObserver(align).observe(document.body, {childList: true, subtree: true});
  }
})();
</script>"""

ACCENT_PRESETS = {
    "Conclave Teal": {},   # the default palette, no override
    "Ember Coral":   {"--conclave-accent": "#e0857e", "--conclave-action": "#a04a44",
                      "--conclave-action-hover": "#b25650"},
    "Ocean Indigo":  {"--conclave-accent": "#8098f9", "--conclave-action": "#3b4a9f",
                      "--conclave-action-hover": "#4757b0"},
    "Forest Green":  {"--conclave-accent": "#6fbf8f", "--conclave-action": "#2f6b4f",
                      "--conclave-action-hover": "#38795b"},
    "Royal Violet":  {"--conclave-accent": "#a98fe3", "--conclave-action": "#5b3f8f",
                      "--conclave-action-hover": "#684aa0"},
    "Slate Mono":    {"--conclave-accent": "#9aa7b5", "--conclave-action": "#45525f",
                      "--conclave-action-hover": "#525f6d"},
}

TEXT_SIZES = {"Compact": "15.5px", "Default": "17px", "Large": "18.5px",
              "Extra large": "20px"}
SIDEBAR_TEXT_SCALES = {"Compact": "0.9", "Default": "1", "Large": "1.14",
                       "Extra large": "1.28"}

PAGE_PROSE_WIDTHS = {
    "Focused":      "min(210ch, 100%)",
    "Extra narrow": "min(210ch, 100%)",
    "Narrow":       "min(210ch, 100%)",
    "Medium":       "min(210ch, 100%)",
    "Wide":         "100%",
}

PRESET_SOURCES = {
    "Fast": {"k_en_gpt": True, "k_en_cla": True, "k_en_gem": True, "k_en_ds": False, "k_en_grok": False, "k_en_qwen": False, "k_en_mistral": False, "k_en_kimi": False, "k_en_meta": False, "k_en_mm": False, "k_en_nv": False, "k_en_glm": False, "k_en_mimo": False},
    "Light": {"k_en_gpt": True, "k_en_cla": True, "k_en_gem": True, "k_en_ds": True, "k_en_grok": True, "k_en_qwen": False, "k_en_mistral": False, "k_en_kimi": False, "k_en_meta": False, "k_en_mm": False, "k_en_nv": False, "k_en_glm": False, "k_en_mimo": False},
    "Balanced": {"k_en_gpt": True, "k_en_cla": True, "k_en_gem": True, "k_en_ds": True, "k_en_grok": True, "k_en_qwen": True, "k_en_mistral": True, "k_en_kimi": False, "k_en_meta": True, "k_en_mm": False, "k_en_nv": False, "k_en_glm": False, "k_en_mimo": False},
    "Thorough": {"k_en_gpt": True, "k_en_cla": True, "k_en_gem": True, "k_en_ds": True, "k_en_grok": True, "k_en_qwen": True, "k_en_mistral": True, "k_en_kimi": True, "k_en_meta": True, "k_en_mm": False, "k_en_nv": False, "k_en_glm": True, "k_en_mimo": True},
    "Deep": {"k_en_gpt": True, "k_en_cla": True, "k_en_gem": True, "k_en_ds": True, "k_en_grok": True, "k_en_qwen": True, "k_en_mistral": True, "k_en_kimi": True, "k_en_meta": True, "k_en_mm": True, "k_en_nv": True, "k_en_glm": True, "k_en_mimo": True},
}

ALL_SOURCES_ON_FOR_TESTING = False
ADJUDICATION_ON_FOR_TESTING = False
if ALL_SOURCES_ON_FOR_TESTING:
    PRESET_SOURCES = {tier: {key: True for key in flags}
                      for tier, flags in PRESET_SOURCES.items()}


def preset_dial_help(current=None):
    lines = []
    if current == "Custom":
        lines.append("⚙ **Currently: Custom (manual)** — your own dial configuration is live.")
    elif current in PRESET_DESC:
        lines.append(f"\U0001F3AF **Currently: {current}** — {PRESET_DESC[current]}")
    lines.append("**The council's effort dial** — one click sets who's in the room, how "
                 "hard every mind works, and whether the ⚖️ Adjudicator audits the answer:")
    for name in PRESETS:
        roster = ", ".join(_EN_KEY_TO_NAME[k]
                           for k, on in PRESET_SOURCES.get(name, {}).items() if on)
        adj = ("⚖️ Adjudicator audits by default" if adjudicator_default_for_preset(name)
               else "no adjudication by default")
        lines.append(f"**{name}** — {PRESET_DESC[name]} Council: {roster} · {adj}.")
    lines.append("🌐 Web search always starts OFF at every tier — flip it on per source "
                 "or with the one global switch when you want it.")
    return "\n\n".join(lines)

def preset_dial_help_text(current=None):
    lines = []
    if current == "Custom":
        lines.append("Currently: Custom (manual) — your own dial configuration is live.")
    elif current in PRESET_DESC:
        lines.append(f"Currently: {current} — {PRESET_DESC[current]}")
    for name in PRESETS:
        roster = ", ".join(_EN_KEY_TO_NAME[k]
                           for k, on in PRESET_SOURCES.get(name, {}).items() if on)
        adj = ("Adjudicator audits by default" if adjudicator_default_for_preset(name)
               else "No adjudication by default")
        lines.append(f"{name}: {PRESET_DESC[name]} Council: {roster}. {adj}.")
    lines.append("Web search always starts OFF at every tier.")
    return "\n".join(lines)

def web_toggle_help_text(current=None):
    if current == "Custom":
        preset_line = "Custom (manual) — your own dial configuration is live"
    elif current in PRESET_DESC:
        preset_line = f"{current} — {PRESET_DESC[current]}"
    else:
        preset_line = ""
    text = ("Web search for every supported source, the Synthesizer, and the "
            "Adjudicator — one switch for the whole run. Always OFF by default; "
            "each source's own switches are on its card on the Council page.")
    if preset_line:
        text += f"\nCurrent preset: {preset_line}"
    return text

CONCLAVE_GREETINGS = [
    "The council is ready.",
    "One question. Every mind. One answer.",
    "The conclave is seated.",
    "Ask once — hear them all, read one.",
    "Many minds. One verdict.",
    "The frontier models await your question.",
    "Where great minds are made to agree.",
    "Your council of rivals is assembled.",
    "Many minds walk in. One answer walks out.",
    "Deliberation, distilled.",
    "The smartest room you can ask.",
    "Every answer, cross-examined.",
    "Consensus is earned here, not assumed.",
    "Ask the council anything.",
    "Independent minds. Definitive answers.",
    "The synthesis engine is warm.",
    "No single model gets the last word.",
    "Claim by claim, the truth gets sorted.",
    "Your question convenes the council.",
    "Rival minds, reconciled.",
    "The debate happens so you don't have to.",
    "One prompt starts the deliberation.",
    "Evidence in. Answer out.",
    "The council never answers alone.",
    "Blind review. Honest verdicts.",
    "Where answers must survive scrutiny.",
    "Assembled. Anonymized. Adjudicated.",
    "Great minds, checked against each other.",
    "The whole frontier, one conversation.",
    "Your definitive answer is many drafts deep.",
    "Truth by triangulation.",
    "The gavel is yours.",
]

def conclave_greeting():
    """Pick this moment's greeting -- time-bucketed (rotates every 20s) so
    it's stable across the reruns of one interaction but fresh across
    visits, never a jittery per-rerun random."""
    return CONCLAVE_GREETINGS[int(time.time() // 20) % len(CONCLAVE_GREETINGS)]

PRESET_DESC = {
    "Fast":     "Lowest-cost full run for simple questions and quick drafts.",
    "Light":    "Recommended everyday default with economical source and synthesis settings.",
    "Balanced": "Stronger reasoning for consequential questions without maximum cost.",
    "Thorough": "Heavy analysis and polished decisions using advanced model settings.",
    "Deep":     "Maximum-depth source and synthesis settings; slowest and most expensive.",
    "Custom":   "Keep the current source and Synthesizer settings exactly as configured. Any manual departure from a named run preset selects Custom automatically.",
}

PRESETS = {
    "Fast":     {"k_gpt_model": "Luna (economy)",   "k_gpt": "Instant", "k_gpt_speed": "Standard", "k_cla_model": "Haiku 5.5", "k_cla": "Low",           "k_cla_think": False, "k_gem": "Flash-Lite (3.5)", "k_gem_t": "Minimal", "k_ds_model": "V4.1 Flash (fast · vision)", "k_ds_think": False, "k_ds_effort": "High", "k_grok": "Fast",     "k_cla_maxsearch": 2, "k_qwen_model": "Flash 3.7 (cheapest)", "k_qwen_think": False, "k_qwen_budget": 4000, "k_qwen_effort": "Default", "k_mistral_model": "Small 4 (economy)", "k_mistral_eff": "Off", "k_kimi_model": "K2.6 (balanced)", "k_kimi_think": False, "k_kimi_effort": "Low", "k_meta_model": "Spark 1.3 (balanced)", "k_meta_eff": "Minimal", "k_mm_model": "M3 (flagship)", "k_mm_speed": "Standard", "k_mm_think": False, "k_nv_model": "Nemotron 3.5 Lightning (economy)", "k_nv_think": False, "k_nv_budget": 4000, "k_glm_model": "GLM-5.3 Flash (economy)", "k_glm_think": True, "k_glm_eff": "Low", "k_mimo_model": "V2.6 Flash (economy)", "k_mimo_think": False},
    "Light":    {"k_gpt_model": "Sol (advanced)", "k_gpt": "Low",     "k_gpt_speed": "Standard", "k_cla_model": "Sonnet 5.5",  "k_cla": "Medium",        "k_cla_think": True,  "k_gem": "Flash-Lite (3.5)", "k_gem_t": "Balanced", "k_ds_model": "V4.1 Flash (fast · vision)", "k_ds_think": True, "k_ds_effort": "High", "k_grok": "Fast",     "k_cla_maxsearch": 3, "k_qwen_model": "Flash 3.8 (economy)", "k_qwen_think": True, "k_qwen_budget": 4000, "k_qwen_effort": "Default", "k_mistral_model": "Small 4 (economy)", "k_mistral_eff": "Low", "k_kimi_model": "K2.6 (balanced)", "k_kimi_think": False, "k_kimi_effort": "Low", "k_meta_model": "Spark 1.3 (balanced)", "k_meta_eff": "Low", "k_mm_model": "M3 (flagship)", "k_mm_speed": "Standard", "k_mm_think": True, "k_nv_model": "Nemotron 3.5 Lightning (economy)", "k_nv_think": True, "k_nv_budget": 4000, "k_glm_model": "GLM-5.3 Flash (economy)", "k_glm_think": True, "k_glm_eff": "Low", "k_mimo_model": "V2.6 Flash (economy)", "k_mimo_think": True},
    "Balanced": {"k_gpt_model": "Sol (advanced)", "k_gpt": "Medium",  "k_gpt_speed": "Standard", "k_cla_model": "Sonnet 5.5",  "k_cla": "High",          "k_cla_think": True,  "k_gem": "Flash (3.8)",      "k_gem_t": "Balanced", "k_ds_model": "V4.1 Flash (fast · vision)", "k_ds_think": True, "k_ds_effort": "Max", "k_grok": "Balanced", "k_cla_maxsearch": 5, "k_qwen_model": "Plus 3.7 (balanced)", "k_qwen_think": True, "k_qwen_budget": 8000, "k_qwen_effort": "Default", "k_mistral_model": "Large 3 (balanced)", "k_mistral_eff": "Medium", "k_kimi_model": "K2.6 (balanced)", "k_kimi_think": True, "k_kimi_effort": "High", "k_meta_model": "Spark 1.3 (balanced)", "k_meta_eff": "Medium", "k_mm_model": "M3 (flagship)", "k_mm_speed": "Standard", "k_mm_think": True, "k_nv_model": "Nemotron 3 Super (balanced)", "k_nv_think": True, "k_nv_budget": 8000, "k_glm_model": "GLM-5.3 Flash (economy)", "k_glm_think": True, "k_glm_eff": "High", "k_mimo_model": "V2.6 Pro (flagship)", "k_mimo_think": True},
    "Thorough": {"k_gpt_model": "Sol (advanced)",   "k_gpt": "Extra (xhigh)",    "k_gpt_speed": "Standard", "k_cla_model": "Opus 5.5",    "k_cla": "Extra (xhigh)", "k_cla_think": True,  "k_gem": "Argon (4)",        "k_gem_t": "Balanced", "k_ds_model": "V4.1 Flash (fast · vision)", "k_ds_think": True, "k_ds_effort": "Max", "k_grok": "High",     "k_cla_maxsearch": 10, "k_qwen_model": "Plus 3.7 (balanced)", "k_qwen_think": True, "k_qwen_budget": 16000, "k_qwen_effort": "Default", "k_mistral_model": "Large 4 (flagship · preview)", "k_mistral_eff": "Medium", "k_kimi_model": "K3.1 Pro (flagship)", "k_kimi_think": True, "k_kimi_effort": "High", "k_meta_model": "Wonder 1.0 (flagship · preview)", "k_meta_eff": "High", "k_mm_model": "M3 (flagship)", "k_mm_speed": "Standard", "k_mm_think": True, "k_nv_model": "Nemotron 3 Ultra (flagship)", "k_nv_think": True, "k_nv_budget": 16000, "k_glm_model": "GLM-5.3 (flagship)", "k_glm_think": True, "k_glm_eff": "High", "k_mimo_model": "V2.6 Pro (flagship)", "k_mimo_think": True},
    "Deep":     {"k_gpt_model": "Astra (flagship)",   "k_gpt": "Max",     "k_gpt_speed": "Standard", "k_cla_model": "Fable 5.1", "k_cla": "Max",           "k_cla_think": True,  "k_gem": "Argon (4)",        "k_gem_t": "Extended", "k_ds_model": "V4.1 Flash (fast · vision)", "k_ds_think": True, "k_ds_effort": "Max", "k_grok": "Expert",   "k_cla_maxsearch": 20, "k_qwen_model": "Max 3.8 (flagship)", "k_qwen_think": True, "k_qwen_budget": 32000, "k_qwen_effort": "Default", "k_mistral_model": "Large 4 (flagship · preview)", "k_mistral_eff": "High", "k_kimi_model": "K3.1 Pro (flagship)", "k_kimi_think": True, "k_kimi_effort": "Max", "k_meta_model": "Wonder 1.0 (flagship · preview)", "k_meta_eff": "Max", "k_mm_model": "M3 (flagship)", "k_mm_speed": "Standard", "k_mm_think": True, "k_nv_model": "Nemotron 3 Ultra (flagship)", "k_nv_think": True, "k_nv_budget": 24000, "k_glm_model": "GLM-5.3 (flagship)", "k_glm_think": True, "k_glm_eff": "Max", "k_mimo_model": "V2.6 Pro (flagship)", "k_mimo_think": True},
}

# One explicit per-provider seat ladder. ADJ_PRESETS derives independent
# dictionaries with the k_adj_ prefix so swapping seats preserves effort/cost.
# Regression checks evaluate these configuration assignments without importing
# the app or opening local user data.
SYNTH_PRESETS = {
    "Fast": {
        "Claude":  {"k_syn_cla_model": "Sonnet 5.5", "k_syn_cla_eff": "Low", "k_syn_cla_think": False, "k_syn_maxsearch": 2},
        "ChatGPT": {"k_syn_gpt_model": "Sol (advanced)", "k_syn_gpt_eff": "Low", "k_syn_gpt_speed": "Standard"},
        "Gemini":  {"k_syn_gem_model": "Flash (3.8)", "k_syn_gem_t": "Standard"},
        "DeepSeek": {"k_syn_ds_model": "V4.1 Flash (fast · vision)", "k_syn_ds_think": True,
                     "k_syn_ds_effort": "High"},
        "Grok":    {"k_syn_grok_mode": "Fast"},
        "Qwen":    {"k_syn_qwen_model": "Plus 3.7 (balanced)", "k_syn_qwen_think": True, "k_syn_qwen_budget": 4000, "k_syn_qwen_effort": "Default"},
        "Mistral": {"k_syn_mistral_model": "Large 3 (balanced)", "k_syn_mistral_eff": "Medium"},
        "Kimi":    {"k_syn_kimi_model": "K2.6 (balanced)", "k_syn_kimi_think": True, "k_syn_kimi_effort": "Low"},
        "Meta":    {"k_syn_meta_model": "Spark 1.3 (balanced)", "k_syn_meta_eff": "Low"},
        "MiniMax": {"k_syn_mm_model": "M3 (flagship)", "k_syn_mm_speed": "Standard", "k_syn_mm_think": True},
        "NVIDIA":  {"k_syn_nv_model": "Nemotron 3 Super (balanced)", "k_syn_nv_think": True, "k_syn_nv_budget": 8000},
        "GLM":     {"k_syn_glm_model": "GLM-5.3 (flagship)", "k_syn_glm_think": True, "k_syn_glm_eff": "Low"},
        "MiMo":    {"k_syn_mimo_model": "V2.6 Pro (flagship)", "k_syn_mimo_think": True},
    },
    "Light": {
        "Claude":  {"k_syn_cla_model": "Sonnet 5.5", "k_syn_cla_eff": "Medium", "k_syn_cla_think": True, "k_syn_maxsearch": 3},
        "ChatGPT": {"k_syn_gpt_model": "Sol (advanced)", "k_syn_gpt_eff": "Low", "k_syn_gpt_speed": "Standard"},
        "Gemini":  {"k_syn_gem_model": "Flash (3.8)", "k_syn_gem_t": "Standard"},
        "DeepSeek": {"k_syn_ds_model": "V4.1 Flash (fast · vision)", "k_syn_ds_think": True,
                     "k_syn_ds_effort": "High"},
        "Grok":    {"k_syn_grok_mode": "Fast"},
        "Qwen":    {"k_syn_qwen_model": "Plus 3.7 (balanced)", "k_syn_qwen_think": True, "k_syn_qwen_budget": 4000, "k_syn_qwen_effort": "Default"},
        "Mistral": {"k_syn_mistral_model": "Large 3 (balanced)", "k_syn_mistral_eff": "Medium"},
        "Kimi":    {"k_syn_kimi_model": "K2.6 (balanced)", "k_syn_kimi_think": True, "k_syn_kimi_effort": "Low"},
        "Meta":    {"k_syn_meta_model": "Spark 1.3 (balanced)", "k_syn_meta_eff": "Low"},
        "MiniMax": {"k_syn_mm_model": "M3 (flagship)", "k_syn_mm_speed": "Standard", "k_syn_mm_think": True},
        "NVIDIA":  {"k_syn_nv_model": "Nemotron 3 Super (balanced)", "k_syn_nv_think": True, "k_syn_nv_budget": 8000},
        "GLM":     {"k_syn_glm_model": "GLM-5.3 (flagship)", "k_syn_glm_think": True, "k_syn_glm_eff": "Low"},
        "MiMo":    {"k_syn_mimo_model": "V2.6 Pro (flagship)", "k_syn_mimo_think": True},
    },
    "Balanced": {
        "Claude":  {"k_syn_cla_model": "Sonnet 5.5", "k_syn_cla_eff": "High", "k_syn_cla_think": True, "k_syn_maxsearch": 5},
        "ChatGPT": {"k_syn_gpt_model": "Sol (advanced)", "k_syn_gpt_eff": "Medium", "k_syn_gpt_speed": "Standard"},
        "Gemini":  {"k_syn_gem_model": "Flash (3.8)", "k_syn_gem_t": "Balanced"},
        "DeepSeek": {"k_syn_ds_model": "V4.1 Flash (fast · vision)", "k_syn_ds_think": True,
                     "k_syn_ds_effort": "Max"},
        "Grok":    {"k_syn_grok_mode": "Balanced"},
        "Qwen":    {"k_syn_qwen_model": "Plus 3.7 (balanced)", "k_syn_qwen_think": True, "k_syn_qwen_budget": 8000, "k_syn_qwen_effort": "Default"},
        "Mistral": {"k_syn_mistral_model": "Medium 3.5 (advanced)", "k_syn_mistral_eff": "Medium"},
        "Kimi":    {"k_syn_kimi_model": "K3.1 Pro (flagship)", "k_syn_kimi_think": True, "k_syn_kimi_effort": "Low"},
        "Meta":    {"k_syn_meta_model": "Spark 1.3 (balanced)", "k_syn_meta_eff": "Medium"},
        "MiniMax": {"k_syn_mm_model": "M3 (flagship)", "k_syn_mm_speed": "Standard", "k_syn_mm_think": True},
        "NVIDIA":  {"k_syn_nv_model": "Nemotron 3 Ultra (flagship)", "k_syn_nv_think": True, "k_syn_nv_budget": 16000},
        "GLM":     {"k_syn_glm_model": "GLM-5.3 (flagship)", "k_syn_glm_think": True, "k_syn_glm_eff": "Low"},
        "MiMo":    {"k_syn_mimo_model": "V2.6 Pro (flagship)", "k_syn_mimo_think": True},
    },
    "Thorough": {
        "Claude":  {"k_syn_cla_model": "Opus 5.5", "k_syn_cla_eff": "Extra (xhigh)", "k_syn_cla_think": True, "k_syn_maxsearch": 10},
        "ChatGPT": {"k_syn_gpt_model": "Sol (advanced)", "k_syn_gpt_eff": "Extra (xhigh)", "k_syn_gpt_speed": "Standard"},
        "Gemini":  {"k_syn_gem_model": "Argon (4)", "k_syn_gem_t": "Balanced"},
        "DeepSeek": {"k_syn_ds_model": "V4.1 Flash (fast · vision)", "k_syn_ds_think": True,
                     "k_syn_ds_effort": "Max"},
        "Grok":    {"k_syn_grok_mode": "High"},
        "Qwen":    {"k_syn_qwen_model": "Plus 3.7 (balanced)", "k_syn_qwen_think": True, "k_syn_qwen_budget": 16000, "k_syn_qwen_effort": "Default"},
        "Mistral": {"k_syn_mistral_model": "Large 4 (flagship · preview)", "k_syn_mistral_eff": "High"},
        "Kimi":    {"k_syn_kimi_model": "K3.1 Pro (flagship)", "k_syn_kimi_think": True, "k_syn_kimi_effort": "High"},
        "Meta":    {"k_syn_meta_model": "Wonder 1.0 (flagship · preview)", "k_syn_meta_eff": "High"},
        "MiniMax": {"k_syn_mm_model": "M3 (flagship)", "k_syn_mm_speed": "Standard", "k_syn_mm_think": True},
        "NVIDIA":  {"k_syn_nv_model": "Nemotron 3 Ultra (flagship)", "k_syn_nv_think": True, "k_syn_nv_budget": 24000},
        "GLM":     {"k_syn_glm_model": "GLM-5.3 (flagship)", "k_syn_glm_think": True, "k_syn_glm_eff": "High"},
        "MiMo":    {"k_syn_mimo_model": "V2.6 Pro (flagship)", "k_syn_mimo_think": True},
    },
    "Deep": {
        "Claude":  {"k_syn_cla_model": "Fable 5.1", "k_syn_cla_eff": "Max", "k_syn_cla_think": True, "k_syn_maxsearch": 20},
        "ChatGPT": {"k_syn_gpt_model": "Astra (flagship)", "k_syn_gpt_eff": "Max", "k_syn_gpt_speed": "Standard"},
        "Gemini":  {"k_syn_gem_model": "Argon (4)", "k_syn_gem_t": "Extended"},
        "DeepSeek": {"k_syn_ds_model": "V4.1 Flash (fast · vision)", "k_syn_ds_think": True,
                     "k_syn_ds_effort": "Max"},
        "Grok":    {"k_syn_grok_mode": "Expert"},
        "Qwen":    {"k_syn_qwen_model": "Max 3.8 (flagship)", "k_syn_qwen_think": True, "k_syn_qwen_budget": 32000, "k_syn_qwen_effort": "Default"},
        "Mistral": {"k_syn_mistral_model": "Large 4 (flagship · preview)", "k_syn_mistral_eff": "Extra (xhigh)"},
        "Kimi":    {"k_syn_kimi_model": "K3.1 Pro (flagship)", "k_syn_kimi_think": True, "k_syn_kimi_effort": "Max"},
        "Meta":    {"k_syn_meta_model": "Wonder 1.0 (flagship · preview)", "k_syn_meta_eff": "Max"},
        "MiniMax": {"k_syn_mm_model": "M3 (flagship)", "k_syn_mm_speed": "Standard", "k_syn_mm_think": True},
        "NVIDIA":  {"k_syn_nv_model": "Nemotron 3 Ultra (flagship)", "k_syn_nv_think": True, "k_syn_nv_budget": 24000},
        "GLM":     {"k_syn_glm_model": "GLM-5.3 (flagship)", "k_syn_glm_think": True, "k_syn_glm_eff": "Max"},
        "MiMo":    {"k_syn_mimo_model": "V2.6 Pro (flagship)", "k_syn_mimo_think": True},
    },
}

# Independent dictionaries, derived from the same provider-preserving seat ladder.
ADJ_PRESETS = {
    tier: {provider: {key.replace("k_syn_", "k_adj_", 1): value
                      for key, value in settings.items()}
           for provider, settings in seats.items()}
    for tier, seats in SYNTH_PRESETS.items()
}

EVAL_PROVIDERS = ["Claude", "ChatGPT", "Gemini", "DeepSeek", "Grok", "Qwen", "Mistral", "Kimi",
                  "Meta", "MiniMax", "NVIDIA", "GLM", "MiMo"]
SYNTH_GEMINI = "gemini-4-argon"

CONTEXT_CHAR_BUDGET = {
    "Fast": 6000, "Light": 9000, "Balanced": 14000,
    "Thorough": 22000, "Deep": 32000, "Custom": 14000,
}
STATE_CAPSULE_LIMIT = 5000
STATE_BEGIN = "<<<CONCLAVE_STATE>>>"
STATE_END = "<<<END_CONCLAVE_STATE>>>"

ADJ_VERDICT_BEGIN = "<<<ADJUDICATOR_VERDICT>>>"
ADJ_VERDICT_END = "<<<END_ADJUDICATOR_VERDICT>>>"

LEDGER_BEGIN = "<<<CONCLAVE_CONTRIBUTIONS>>>"
LEDGER_END = "<<<END_CONCLAVE_CONTRIBUTIONS>>>"
LEDGER_TURNS_IN_CONTEXT = 3          # newest turns whose ledgers ride along
LEDGER_NOTHING = "nothing unique"

REASONING_EXPOSURE = {
    "Claude":   ("returns", "Anthropic's thinking summary — Conclave requests "
                            "display: summarized on the 5-series; the raw trace is never returned"),
    "DeepSeek": ("returns", "reasoning_content when thinking is on (the non-web path); "
                            "the web path returns only reasoning summaries, when any"),
    "Qwen":     ("returns", "reasoning_content when thinking is on"),
    "Kimi":     ("returns", "reasoning_content when the model reasons"),
    "GLM":      ("returns", "reasoning_content when the model reasons"),
    "MiMo":     ("returns", "reasoning_content when thinking is on"),
    "MiniMax":  ("returns", "a separate reasoning field when thinking is on"),
    "NVIDIA":   ("returns", "reasoning_content when thinking is on"),
    "Mistral":  ("returns", "thinking chunks when a reasoning effort is set"),
    "ChatGPT":  ("private", "OpenAI keeps its reasoning private — the API returns at most "
                            "an optional summary, which Conclave does not request"),
    "Gemini":   ("private", "Google returns only optional thought summaries, not requested "
                            "in Conclave's Interactions requests"),
    "Grok":     ("private", "xAI returns reasoning only in encrypted form"),
    "Meta":     ("private", "Meta redacts reasoning on Chat Completions; with web on, a "
                            "reasoning summary is requested from its Responses path and shown "
                            "when Meta returns one"),
}
REASONING_STORE_LIMIT = 24000        # chars kept per call (history file size guard)

VIEW_SYNTH  = "⭐ Synthesis"
VIEW_RES    = "\U0001F52C Research Synthesizer"
VIEW_DOC    = "\U0001F4C4 Document"
VIEW_PROJECTS = "▦ Projects"
VIEW_LENSES = "◉ Tasks & Lenses"
VIEW_HISTORY = "\U0001F558 Historical Chats"
VIEW_SETTINGS = "⚙ Settings"
VIEW_COUNCIL = "🏛️ Council"
VIEW_ABOUT  = "ℹ️ About"
# Human-readable evaluator labels are derived from model constants/state, so a
# model bump cannot leave stale version text in the UI.
SYNTH_GEMINI_LABEL = next((k for k, v in GEMINI_MODELS.items() if v == SYNTH_GEMINI), "Pro")

CLAUDE_CACHE_WRITE = 1.25
CLAUDE_CACHE_READ = 0.10
CLAUDE_CACHE_READ_BY_MODEL = {"claude-opus-5-5": 0.05, "claude-sonnet-5-5": 0.05,
                              "claude-fable-5-1": 0.025}
CACHED_INPUT_PRICES = {"gpt-6.1-sol": 0.10,
                       "muse-wonder-1.0": 0.20, "muse-spark-1.3": 0.15}


def cost_with_cache(model_id, in_tok, out_tok, speed="Standard", cache=None):
    """cost_of, adjusted for tokens served from (or written to) a prompt cache."""
    cost = cost_of(model_id, in_tok, out_tok, speed)
    if cost is None or not cache or speed not in (None, "", "Standard"):
        return cost
    rate = PRICES.get(model_id)
    if not rate:
        return cost
    read, write = int(cache.get("read") or 0), int(cache.get("write") or 0)
    if str(model_id).startswith("claude-"):
        _rd = CLAUDE_CACHE_READ_BY_MODEL.get(model_id, CLAUDE_CACHE_READ)
        cost += (write * (CLAUDE_CACHE_WRITE - 1) - read * (1 - _rd)) * rate[0] / 1e6
    elif model_id in CACHED_INPUT_PRICES:
        cost -= read * max(0.0, rate[0] - CACHED_INPUT_PRICES[model_id]) / 1e6
    return max(cost, 0.0)


def cost_of(model_id, in_tok, out_tok, speed="Standard"):
    if speed == "Flex" and model_id in FLEX_PRICES:
        rate = FLEX_PRICES[model_id]
    elif speed == "Ultrafast" and model_id in ULTRAFAST_PRICES:
        rate = ULTRAFAST_PRICES[model_id]
    elif speed not in (None, "", "Standard"):
        rate = PRIORITY_PRICES.get(model_id)
    else:
        rate = PRICES.get(model_id)
        tier = LONG_PROMPT_PRICES.get(model_id)
        if tier and in_tok is not None and in_tok > tier[0]:
            rate = tier[1:]
    if not rate or in_tok is None: return None
    return (in_tok / 1_000_000) * rate[0] + ((out_tok or 0) / 1_000_000) * rate[1]

def load_keys():
    raw = {}
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, encoding="utf-8") as f: raw = json.load(f)
        except (OSError, ValueError, UnicodeDecodeError): raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {k: _dec(v) for k, v in raw.items()}

def keys_decrypt_failed():
    """True when saved keys cannot be loaded safely enough to overwrite."""
    raw = {}
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, encoding="utf-8") as f: raw = json.load(f)
        except (OSError, ValueError, UnicodeDecodeError): return True
    if not isinstance(raw, dict):
        return True
    return any(isinstance(v, str) and v.startswith("enc:") and not _dec(v)
               for v in raw.values())

@contextmanager
def _exclusive_file_lock(path, timeout=10.0):
    """Serialize local writers across Streamlit processes on this Windows app."""
    lock_path = path + ".lock"
    lock_file = open(lock_path, "a+b")
    locked = False
    try:
        if os.path.getsize(lock_path) == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for {lock_path}")
                time.sleep(0.05)
        yield
    finally:
        if locked:
            try:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        lock_file.close()

def _atomic_write_json(path, obj):
    # Each writer gets its own temp file. A shared path.tmp lets two app instances
    # truncate the same file before either replace completes.
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass

KEYS_UNENCRYPTED_MESSAGE = (
    "Key storage is unavailable: without pywin32, Conclave cannot encrypt keys for "
    "this Windows login, and it will not write them to disk as readable text. "
    "Run `pip install pywin32` in this app's environment, then save again."
)

def _require_key_encryption(keys):
    """Refuse to persist key material that cannot be encrypted.

    Without DPAPI, _enc() returns the value unchanged, so a save would leave
    usable provider credentials on disk for anything that can read the app
    folder. Empty boxes stay saveable so clearing keys always works.
    """
    if not _DPAPI and any(keys.values()):
        raise RuntimeError(KEYS_UNENCRYPTED_MESSAGE)

def save_keys(keys):
    _require_key_encryption(keys)
    with _exclusive_file_lock(KEYS_FILE):
        _atomic_write_json(KEYS_FILE, {k: _enc(v) for k, v in keys.items()})

def replace_unreadable_keys(keys):
    """Preserve an unreadable key file, then save confirmed replacements.

    This is intentionally separate from save_keys(): ordinary saves must never
    overwrite a file whose encrypted contents cannot currently be recovered.
    """
    _require_key_encryption(keys)
    with _exclusive_file_lock(KEYS_FILE):
        if not os.path.exists(KEYS_FILE) or not keys_decrypt_failed():
            raise RuntimeError("The saved key file is readable now; reload Settings and save normally.")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = f"{KEYS_FILE}.{stamp}.unreadable-backup"
        os.replace(KEYS_FILE, backup_path)
        try:
            _atomic_write_json(KEYS_FILE, {k: _enc(v) for k, v in keys.items()})
        except Exception:
            if not os.path.exists(KEYS_FILE) and os.path.exists(backup_path):
                os.replace(backup_path, KEYS_FILE)
            raise
        return os.path.basename(backup_path)

_history_alerts = []
_history_write_blocked = {}

def _history_alert(notice):
    if notice not in _history_alerts:
        _history_alerts.append(notice)
    try:
        log = st.session_state.setdefault("_history_alert_log", [])
        if notice not in log:
            log.append(notice)
    except Exception:
        pass

def _load_history_unlocked(path=None):
    path = path or HISTORY_FILE
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                history = json.load(f)
            if not isinstance(history, list):
                raise ValueError("history root must be a list")
            _history_write_blocked.pop(path, None)
            return history
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as damage:
            # Unreadable (truncated/corrupt) history: preserve the evidence
            # instead of leaving it in place to be overwritten by the next save.
            suffix = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            quarantine = f"{path}.{suffix}.corrupt"
            preserved = True
            try:
                os.replace(path, quarantine)
            except OSError:
                # The rename can fail while another Conclave instance holds
                # the file open — a copy still preserves the evidence.
                try:
                    shutil.copyfile(path, quarantine)
                except OSError:
                    preserved = False
            name = os.path.basename(path)
            if preserved:
                _history_write_blocked.pop(path, None)
                _history_alert(
                    f"⚠️ {name} could not be read ({type(damage).__name__}) and was set "
                    f"aside as {os.path.basename(quarantine)}. The chats saved there can "
                    "be recovered from that file; new chats keep saving normally.")
            else:
                _history_write_blocked[path] = (
                    f"{name} is unreadable and could not be set aside as a backup, so "
                    "saving to it is paused to avoid overwriting the only copy of those chats.")
                _history_alert(
                    "⚠️ " + _history_write_blocked[path] +
                    " Close any other Conclave window, check file permissions, then reload.")
            return []
    return []

def _history_signature(path):
    try:
        stat = os.stat(path)
        return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
    except FileNotFoundError:
        return None


def _load_history_cached(path):
    """Session-local read cache; mutations always reread disk under the lock.

    Cache entries never escape directly. File replacement, deletion, and writes
    by another process are detected before reuse. Corrupt/blocked reads are not
    cached, so their preservation and warning paths run normally.
    """
    cache = st.session_state.setdefault("_history_read_cache", {})
    with _exclusive_file_lock(path):
        signature = _history_signature(path)
        entry = cache.get(path)
        if signature is not None and entry is not None and entry[0] == signature:
            return deepcopy(entry[1])
        cache.pop(path, None)
        notices_before = len(_history_alerts)
        history = _load_history_unlocked(path)
        if (signature is not None and path not in _history_write_blocked
                and len(_history_alerts) == notices_before
                and _history_signature(path) == signature):
            cache[path] = (signature, deepcopy(history))
        return history


def load_history():
    return _load_history_cached(HISTORY_FILE)


def load_history_archive():
    return _load_history_cached(HISTORY_ARCHIVE_FILE)


def _history_key(entry):
    return (entry.get("ts"), entry.get("mode", "synthesis"))

def history_matches(entry, query):
    """Case-insensitive, all-terms search over saved conversation text.

    History is nested (threads, synthesis, research, and attachment metadata),
    so searching only the opening prompt would miss most of a resumed chat.
    Binary values are skipped defensively even though persisted attachments are
    already stripped to names and kinds.
    """
    terms = str(query or "").casefold().split()
    if not terms:
        return True

    text_parts = []
    stack = [entry]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            text_parts.append(value)
        elif isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
        elif value is not None and not isinstance(value, (bytes, bytearray)):
            text_parts.append(str(value))
    haystack = "\n".join(text_parts).casefold()
    return all(term in haystack for term in terms)

def filtered_history(entries, query="", sort_order="Newest first", mode_filter="All chats"):
    """Return a display-only history slice without mutating stored sessions."""
    selected = [entry for entry in entries if history_matches(entry, query)]
    if mode_filter == "Synthesis":
        selected = [entry for entry in selected if entry.get("mode", "synthesis") != "research"]
    elif mode_filter == "Research":
        selected = [entry for entry in selected if entry.get("mode") == "research"]
    if sort_order == "Oldest first":
        return list(reversed(selected))
    if sort_order == "A–Z":
        return sorted(selected, key=lambda entry: str(entry.get("prompt", "") or "").casefold())
    return selected

def history_display_date(value):
    try:
        return datetime.fromisoformat(str(value)).strftime("%b %d, %Y · %I:%M %p")
    except (TypeError, ValueError):
        return str(value or "Unknown date")

def history_entry_meta(entry):
    """Compact metadata for the full Historical Chats workspace."""
    threads = entry.get("threads") or {}
    if not threads and entry.get("results"):
        threads = entry["results"]
    source_count = len(threads)
    message_count = sum(len(thread) for thread in threads.values()
                        if isinstance(thread, list))
    evaluator = entry.get("research") if entry.get("mode") == "research" else entry.get("synth")
    evaluator_label = evaluator.get("label", "") if isinstance(evaluator, dict) else ""
    parts = [f"{source_count} source{'s' if source_count != 1 else ''}"]
    if message_count:
        parts.append(f"{message_count} source messages")
    if evaluator_label:
        parts.append(evaluator_label)
    return " · ".join(parts)

def update_history(mutator):
    """Atomically update the fast history index and preserve overflow.

    The newest 50 chats stay quick to load in the sidebar. Older complete entries
    move to a separate local archive instead of being silently discarded.
    """
    with _exclusive_file_lock(HISTORY_FILE):
        updated = mutator(_load_history_unlocked())
        if not isinstance(updated, list):
            raise TypeError("history mutator must return a list")
        if HISTORY_FILE in _history_write_blocked:
            raise RuntimeError(_history_write_blocked[HISTORY_FILE])
        overflow = updated[HISTORY_RECENT_LIMIT:]
        if overflow:
            with _exclusive_file_lock(HISTORY_ARCHIVE_FILE):
                archived = _load_history_unlocked(HISTORY_ARCHIVE_FILE)
                if HISTORY_ARCHIVE_FILE in _history_write_blocked:
                    raise RuntimeError(_history_write_blocked[HISTORY_ARCHIVE_FILE])
                overflow_keys = {_history_key(entry) for entry in overflow}
                archived = overflow + [entry for entry in archived
                                       if _history_key(entry) not in overflow_keys]
                _atomic_write_json(HISTORY_ARCHIVE_FILE, archived)
        _atomic_write_json(HISTORY_FILE, updated[:HISTORY_RECENT_LIMIT])

def update_history_archive(mutator):
    """Apply a locked update to archived chats without loading them on every rerun."""
    with _exclusive_file_lock(HISTORY_ARCHIVE_FILE):
        updated = mutator(_load_history_unlocked(HISTORY_ARCHIVE_FILE))
        if not isinstance(updated, list):
            raise TypeError("history archive mutator must return a list")
        if HISTORY_ARCHIVE_FILE in _history_write_blocked:
            raise RuntimeError(_history_write_blocked[HISTORY_ARCHIVE_FILE])
        _atomic_write_json(HISTORY_ARCHIVE_FILE, updated)

# ---- speech-to-text & text-to-speech (both use the OpenAI key) ----
def _model_fallback_allowed(e):
    """Only use a legacy speech model when the preferred model is unavailable."""
    code = str(getattr(e, "code", "") or "").lower()
    msg = str(e).lower()
    return code == "model_not_found" or any(term in msg for term in (
        "model_not_found", "model not found", "does not exist", "do not have access",
        "don't have access", "unsupported model"))

LIGHT_REQUEST_TIMEOUT = 150.0
HEAVY_REQUEST_TIMEOUT = 480.0
MAX_EFFORT_REQUEST_TIMEOUT = 900.0


def _max_effort_call(name, cfg):
    """True for a provider's very top reasoning setting."""
    return cfg.get("effort") == "max"


def _request_retries(name, cfg, web):
    """SDK retry count: one retry normally, none for a max-effort call."""
    return 0 if _max_effort_call(name, cfg) else 1


def _request_timeout(name, cfg, web):
    """How long to wait before giving up on one provider call, scaled by how
    heavy `cfg`/`web` configure this specific call to be. Each provider's
    own top reasoning tier, or a generously-capped web search, counts as
    heavy; everything else gets the tighter default. Unknown/missing cfg
    fields default to "not heavy" -- a request this function doesn't
    recognize is assumed ordinary rather than silently over-trusted."""
    heavy = False
    if name == "Claude":
        heavy = (
            cfg.get("effort") in ("high", "xhigh", "max")
            or (web and bool(cfg.get("thinking")))
            or (web and cfg.get("max_searches", 5) >= 5)
        )
    elif name == "ChatGPT":
        heavy = cfg.get("effort") in ("xhigh", "max") or web or cfg.get("speed") == "Flex"
    elif name == "Gemini":
        heavy = cfg.get("level") == "high"
    elif name == "DeepSeek":
        heavy = (bool(cfg.get("thinking", True)) and cfg.get("effort") in ("high", "max")) or web
    elif name == "Grok":
        heavy = cfg.get("mode") in ("High", "Expert") or web
    elif name == "Qwen":
        heavy = bool(cfg.get("thinking")) and (cfg.get("budget", 0) >= 16000
                                               or cfg.get("effort") == "xhigh")
    elif name == "Mistral":
        heavy = (cfg.get("effort") in ("high", "xhigh")
                 or (cfg.get("model_id") in MISTRAL_ID_ALTERNATES and cfg.get("effort") != "none"))
    elif name == "Kimi":
        heavy = cfg.get("model_id") in KIMI_ALWAYS_THINK or bool(cfg.get("thinking")) or web
    elif name == "Meta":
        heavy = cfg.get("effort") in ("high", "xhigh", "max") or web
    elif name == "MiniMax":
        heavy = bool(cfg.get("thinking")) or cfg.get("model_id") not in MINIMAX_THINK_OFF_OK
    elif name == "NVIDIA":
        heavy = bool(cfg.get("thinking")) and cfg.get("budget", 0) >= 16000
    elif name == "GLM":
        heavy = (cfg.get("model_id") in GLM_ALWAYS_THINK
                 or bool(cfg.get("thinking")) or web)
    elif name == "MiMo":
        heavy = bool(cfg.get("thinking", True)) or web
    if _max_effort_call(name, cfg):
        return MAX_EFFORT_REQUEST_TIMEOUT
    return HEAVY_REQUEST_TIMEOUT if heavy else LIGHT_REQUEST_TIMEOUT

def transcribe(audio_bytes):
    key = load_keys().get("openai", "")
    if not key: return None, "Add your OpenAI key to use dictation."
    client = OpenAI(api_key=key, timeout=LIGHT_REQUEST_TIMEOUT)
    try:
        try:
            tr = client.audio.transcriptions.create(model="gpt-transcribe", file=("speech.wav", audio_bytes))
        except Exception as primary_error:
            if not _model_fallback_allowed(primary_error):
                raise
            tr = client.audio.transcriptions.create(model="gpt-4o-transcribe", file=("speech.wav", audio_bytes))
        st.session_state.pop("quota_err_ChatGPT", None)
        return tr.text, None
    except Exception as e:
        if _is_credit_error("ChatGPT", e):
            st.session_state["quota_err_ChatGPT"] = True
        return None, f"Transcription failed: {e}"

def speak(name, text, cache_key=None):
    """cache_key defaults to `name` (unchanged behavior for every existing
    caller). Passed explicitly when the SAME role (e.g. "Synthesizer") has
    more than one independently-readable text visible at once -- the
    Evaluators sub-view's locked memo vs. the Answer tab's final response --
    so generating audio for one can't silently overwrite/clobber the other's
    cached audio under the same "audio_{name}" slot."""
    cache_key = cache_key or name
    key = load_keys().get("openai", "")
    if not key:
        st.session_state[f"audio_err_{cache_key}"] = "Add your OpenAI key for read-aloud."; return
    voice = st.session_state.get(VOICE_KEY.get(name, ""), "alloy")
    try:
        client = OpenAI(api_key=key, timeout=LIGHT_REQUEST_TIMEOUT)
        try:
            r = client.audio.speech.create(model="gpt-4o-mini-tts", voice=voice, input=text[:4000])
        except Exception as primary_error:
            if not _model_fallback_allowed(primary_error):
                raise
            fallback_voice = voice if voice in TTS1_FALLBACK_VOICES else "alloy"
            r = client.audio.speech.create(model="tts-1", voice=fallback_voice, input=text[:4000])
        st.session_state[f"audio_{cache_key}"] = r.content
        st.session_state.pop(f"audio_err_{cache_key}", None)
        st.session_state.pop("quota_err_ChatGPT", None)
    except Exception as e:
        if _is_credit_error("ChatGPT", e):
            st.session_state["quota_err_ChatGPT"] = True
        st.session_state[f"audio_err_{cache_key}"] = f"TTS failed: {e}"

ATTACH_TYPES = ["png", "jpg", "jpeg", "gif", "webp", "pdf",
                "txt", "md", "csv", "json", "py", "log", "html", "xml"]
IMG_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "webp": "image/webp"}
MAX_FILE_MB   = 20
MAX_TEXT_CHARS = 80_000   # per text file, inlined into the prompt

IMAGE_SEND_MAX_EDGE = 2576
IMAGE_SEND_MAX_BYTES = 3_145_728   # 3 MB
IMAGE_LIMITS = {
    "Claude":  "10 MB per image after encoding (about 7.5 MB file), 8000 px max",
    "Grok":    "20 MiB per image, JPEG or PNG only",
    "ChatGPT": "512 MB per request, PNG / JPEG / WEBP / still GIF",
    "Gemini":  "20 MB for the whole request, images included",
    "GLM":     "5 MB per image",
}


def _prepare_image(raw, ext):
    """Return (bytes, mime, note). note is None when the image was sent as is."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        w, h = img.size
        frames = getattr(img, "n_frames", 1) or 1
    except Exception:
        return raw, IMG_MIME[ext], None
    if (len(raw) <= IMAGE_SEND_MAX_BYTES and max(w, h) <= IMAGE_SEND_MAX_EDGE
            and ext in ("png", "jpg", "jpeg") and frames == 1):
        return raw, IMG_MIME[ext], None
    try:
        return _shrink_image(img, raw, w, h, frames)
    except (OSError, ValueError, SyntaxError, EOFError):
        if len(raw) <= IMAGE_SEND_MAX_BYTES:
            return raw, IMG_MIME[ext], None
        return None, None, None


def _shrink_image(img, raw, w, h, frames):
    from PIL import Image
    img.seek(0)
    alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if alpha else "RGB")
    scale = min(1.0, IMAGE_SEND_MAX_EDGE / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    data, mime = b"", "image/png"
    if alpha:
        buf = io.BytesIO(); img.save(buf, "PNG", optimize=True); data = buf.getvalue()
    if not alpha or len(data) > IMAGE_SEND_MAX_BYTES:
        rgb = img.convert("RGB")
        for quality in (90, 82, 74, 66):
            buf = io.BytesIO(); rgb.save(buf, "JPEG", quality=quality, optimize=True)
            data, mime = buf.getvalue(), "image/jpeg"
            if len(data) <= IMAGE_SEND_MAX_BYTES:
                break
    return data, mime, {"orig_bytes": len(raw), "orig_dims": [w, h],
                        "sent_bytes": len(data), "sent_dims": list(img.size),
                        "animated": frames > 1}


def pack_files(uploaded):
    """st.file_uploader results -> serializable attachment dicts."""
    packed = []
    for uf in (uploaded or []):
        name = uf.name
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        raw = uf.getvalue()
        if len(raw) > MAX_FILE_MB * 1024 * 1024:
            packed.append({"name": name, "kind": "text",
                           "text": f"(File '{name}' was skipped — larger than the {MAX_FILE_MB} MB cap.)"})
        elif ext in IMG_MIME:
            data, mime, note = _prepare_image(raw, ext)
            if data is None:
                packed.append({"name": name, "kind": "text",
                               "text": f"(Image '{name}' could not be read — the file "
                                       "looks damaged or incomplete, so it was skipped.)"})
                continue
            item = {"name": name, "kind": "image", "mime": mime,
                    "b64": base64.b64encode(data).decode()}
            if note:
                item["prepared"] = note
            packed.append(item)
        elif ext == "pdf":
            packed.append({"name": name, "kind": "pdf", "mime": "application/pdf",
                           "b64": base64.b64encode(raw).decode()})
        else:
            text = raw.decode("utf-8", errors="replace")
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS] + "\n...[truncated — file exceeds inline limit]"
            packed.append({"name": name, "kind": "text", "text": text})
    return packed

def _inline_text_files(text, files):
    """Fold text-file contents + notes about unavailable files into the prompt text."""
    text = text or ""
    notes = []
    for f in (files or []):
        if f.get("stripped"):
            notes.append(f"[Attachment '{f.get('name','?')}' from an earlier session is no longer available.]")
        elif f["kind"] == "text":
            notes.append(f"[Attached file: {f['name']}]\n```\n{f.get('text','')}\n```")
    return ("\n\n".join(notes) + "\n\n" + text) if notes else text

def _binary_files(m):
    return [f for f in m.get("files", []) if not f.get("stripped") and f.get("kind") in ("image", "pdf")]

# ---- per-provider message builders (thread msgs -> native API shapes) ----
def _image_text_only_note(f, text):
    return (f"[Image '{f['name']}' attached, but this model's current API "
            "contract is text-only - other compatible sources received it.]\n\n" + text)

def _pdf_unsupported_note(f, text):
    return f"[PDF '{f['name']}' attached, but this model can't read PDFs — other models received it.]\n\n" + text

def _oai_chat_messages(messages, pdf_ok, image_ok=True, image_shape="object",
                       pdf_shape="file", skip_pdf=False):
    out = []
    for m in messages:
        text = _inline_text_files(m["content"], m.get("files"))
        bins = _binary_files(m) if m["role"] == "user" else []
        parts = []
        for f in bins:
            if f["kind"] == "image" and image_ok:
                _url = f"data:{f['mime']};base64,{f['b64']}"
                parts.append({"type": "image_url",
                              "image_url": _url if image_shape == "string" else {"url": _url}})
            elif f["kind"] == "image":
                text = _image_text_only_note(f, text)
            elif pdf_ok and pdf_shape == "document_url":
                parts.append({"type": "document_url",
                              "document_url": f"data:application/pdf;base64,{f['b64']}"})
            elif pdf_ok:
                parts.append({"type": "file", "file": {"filename": f["name"],
                              "file_data": f"data:application/pdf;base64,{f['b64']}"}})
            elif skip_pdf:
                pass
            else:
                text = _pdf_unsupported_note(f, text)
        if parts:
            parts.append({"type": "text", "text": text})
            out.append({"role": m["role"], "content": parts})
        else:
            out.append({"role": m["role"], "content": text})
    return out

def _oai_responses_input(messages, pdf_ok, image_ok=True):
    out = []
    for m in messages:
        text = _inline_text_files(m["content"], m.get("files"))
        bins = _binary_files(m) if m["role"] == "user" else []
        parts = []
        for f in bins:
            if f["kind"] == "image" and image_ok:
                parts.append({"type": "input_image", "image_url": f"data:{f['mime']};base64,{f['b64']}"})
            elif f["kind"] == "image":
                text = _image_text_only_note(f, text)
            elif pdf_ok:
                parts.append({"type": "input_file", "filename": f["name"],
                              "file_data": f"data:application/pdf;base64,{f['b64']}"})
            else:
                text = _pdf_unsupported_note(f, text)
        if parts:
            parts.append({"type": "input_text", "text": text})
            out.append({"role": m["role"], "content": parts})
        else:
            out.append({"role": m["role"], "content": text})
    return out

def _claude_messages(messages):
    out = []
    for m in messages:
        text = _inline_text_files(m["content"], m.get("files"))
        bins = _binary_files(m) if m["role"] == "user" else []
        blocks = []
        for f in bins:
            if f["kind"] == "image":
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": f["mime"], "data": f["b64"]}})
            else:
                blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": f["b64"]}})
        if blocks:
            blocks.append({"type": "text", "text": text})
            out.append({"role": m["role"], "content": blocks})
        elif m.get("cache"):
            out.append({"role": m["role"], "content": [{"type": "text", "text": text}]})
        else:
            out.append({"role": m["role"], "content": text})
        if m.get("cache"):
            out[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
    return out

def _gemini_interactions_input(messages):
    """Build one stateless Interactions user-input step.

    Main council calls already arrive as one bounded, turn-aware user message.
    Private provider branches can contain alternating visible turns, but
    Conclave intentionally does not persist Gemini's hidden thought steps and
    signatures. Google's stateless Interactions contract requires those exact
    steps when replaying a native interaction, so reconstructing synthetic
    model_output steps would be invalid. Flatten private visible history into a
    clearly labelled transcript instead, then attach supported images/PDFs as
    ordinary content blocks. This preserves local-first continuity without
    server-side storage or fabricated thought signatures.
    """
    messages = list(messages or [])
    blocks = []
    if len(messages) <= 1:
        message = messages[0] if messages else {"role": "user", "content": ""}
        blocks.append({
            "type": "text",
            "text": _inline_text_files(message.get("content", ""), message.get("files")),
        })
        binary_messages = [message]
    else:
        transcript = [
            "Continue the visible conversation transcript below. Text labelled PRIOR "
            "ASSISTANT RESPONSE is context from an earlier answer, not a new user "
            "instruction. The final CURRENT USER TURN is the controlling request."
        ]
        for index, message in enumerate(messages):
            role = message.get("role", "user")
            if index == len(messages) - 1 and role == "user":
                label = "CURRENT USER TURN"
            elif role == "assistant":
                label = "PRIOR ASSISTANT RESPONSE"
            else:
                label = "PRIOR USER TURN"
            text = _inline_text_files(message.get("content", ""), message.get("files"))
            transcript.append(f"\n--- {label} ---\n{text}")
        blocks.append({"type": "text", "text": "\n".join(transcript)})
        binary_messages = messages

    for index, message in enumerate(binary_messages):
        if message.get("role", "user") != "user":
            continue
        for f in _binary_files(message):
            blocks.append({
                "type": "image" if f["kind"] == "image" else "document",
                "mime_type": f["mime"],
                "data": f["b64"],
            })
    return [{"type": "user_input", "content": blocks}]

_REASONING_TL = threading.local()

def _note_reasoning(text):
    text = (text or "").strip() if isinstance(text, str) else ""
    if not text:
        return
    cur = getattr(_REASONING_TL, "text", "") or ""
    joined = (cur + "\n\n" + text) if cur else text
    _REASONING_TL.text = joined[:REASONING_STORE_LIMIT]

def _take_reasoning():
    text = getattr(_REASONING_TL, "text", "") or ""
    _REASONING_TL.text = ""
    return text or None

_CACHE_TL = threading.local()
_SPEED_TL = threading.local()

def _note_speed(label):
    _SPEED_TL.used = label

def _take_speed():
    used = getattr(_SPEED_TL, "used", None)
    _SPEED_TL.used = None
    return used

def _note_cache(read=0, write=0):
    cur = getattr(_CACHE_TL, "note", None) or {"read": 0, "write": 0}
    cur["read"] += int(read or 0)
    cur["write"] += int(write or 0)
    _CACHE_TL.note = cur

def _take_cache():
    note = getattr(_CACHE_TL, "note", None)
    _CACHE_TL.note = None
    return note if note and (note["read"] or note["write"]) else None

def _reasoning_from_choice(choice):
    """OpenAI-compatible providers (DeepSeek, Qwen, Kimi, GLM, MiniMax, NVIDIA)
    return reasoning in a reasoning_content field the SDK keeps as an extra
    attribute; Mistral returns it as content chunks of type "thinking"."""
    msg = getattr(choice, "message", None)
    if msg is None:
        return
    rc = getattr(msg, "reasoning_content", None)
    if not rc:
        extra = getattr(msg, "model_extra", None) or {}
        rc = extra.get("reasoning_content") or extra.get("reasoning")
    if isinstance(rc, str):
        _note_reasoning(rc)
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        for chunk in content:
            ctype = chunk.get("type") if isinstance(chunk, dict) else getattr(chunk, "type", None)
            if ctype != "thinking":
                continue
            inner = chunk.get("thinking") if isinstance(chunk, dict) else getattr(chunk, "thinking", None)
            if isinstance(inner, list):
                inner = "".join((x.get("text", "") if isinstance(x, dict)
                                 else (getattr(x, "text", None) or "")) for x in inner)
            if isinstance(inner, str):
                _note_reasoning(inner)

def _reasoning_from_inline(text):
    """MiniMax/GLM belt-and-braces: keep an inline <think> block before it is
    stripped out of the answer."""
    found = re.findall(r"<think>(.*?)</think>", text or "", flags=re.S)
    for block in found:
        _note_reasoning(block)

OUTPUT_CAPS = {"light": 16000, "medium": 32000, "heavy": 64000}

def output_cap(name, cfg):
    """Output-token cap for one call, from that call's own dials."""
    lvl = "light"
    eff = cfg.get("effort")
    if name == "Claude":
        mid = cfg.get("model_id", "")
        if mid in CLAUDE_ALWAYS_THINK or eff in ("xhigh", "max"):
            lvl = "heavy"
        elif eff == "high" or cfg.get("thinking"):
            lvl = "medium"
    elif name == "ChatGPT":
        eff = chatgpt_effort_sent(cfg.get("model_id"), eff or "medium")
        lvl = "heavy" if eff in ("xhigh", "max") else ("medium" if eff in ("medium", "high") else "light")
    elif name == "DeepSeek":
        lvl = ("heavy" if eff == "max" else "medium") if cfg.get("thinking", True) else "light"
    elif name == "Grok":
        lvl = {"Expert": "heavy", "High": "medium", "Balanced": "medium"}.get(cfg.get("mode"), "light")
    elif name == "Gemini":
        lvl = "medium" if cfg.get("level") in ("medium", "high") else "light"
    elif name == "Qwen":
        lvl = "medium" if cfg.get("thinking", True) else "light"
    elif name == "Mistral":
        lvl = "medium" if eff in ("high", "xhigh") else "light"
    elif name == "Kimi":
        if cfg.get("model_id") in KIMI_ALWAYS_THINK:
            lvl = "heavy" if eff == "max" else "medium"
        else:
            lvl = "medium" if cfg.get("thinking", True) else "light"
    elif name == "Meta":
        lvl = "heavy" if eff in ("xhigh", "max") else ("medium" if eff in ("medium", "high") else "light")
    elif name == "MiniMax":
        lvl = "medium"
    elif name == "GLM":
        if cfg.get("model_id") in GLM_ALWAYS_THINK:
            lvl = "heavy" if eff == "max" else "medium"
        else:
            lvl = "medium" if cfg.get("thinking", True) else "light"
    elif name == "MiMo":
        lvl = "medium" if cfg.get("thinking", True) else "light"
    return OUTPUT_CAPS[lvl]

def _responses_call(client, model, input_payload, effort, max_out=16000,
                    service_tier=None, require_reasoning=False, require_search=False,
                    reasoning_summary=None, tools=True, store=None):
    base = {"model": model, "input": input_payload, "max_output_tokens": max_out}
    if store is not None:
        base["store"] = store
    if tools:
        base["tools"] = [{"type": "web_search"}]
    if service_tier:
        base["service_tier"] = service_tier
    if require_search:
        base["tool_choice"] = "required"
    kw = dict(base)
    if effort:
        kw["reasoning"] = {"effort": effort}
        if reasoning_summary and effort not in ("none", ""):
            kw["reasoning"]["summary"] = reasoning_summary
    try:
        r = _responses_create(client, kw)
    except Exception as e:                       # if the effort value is rejected, retry without it
        if (reasoning_summary and "summar" in str(e).lower() and "reasoning" in kw):
            kw["reasoning"] = {"effort": effort}   # summary rejected: keep the effort
            r = _responses_create(client, kw)
        elif (not require_reasoning and "reasoning" in kw and
                ("effort" in str(e).lower() or "reasoning" in str(e).lower())):
            r = _responses_create(client, base)
        else:
            raise
    def _item_text(item):
        return "".join((getattr(c, "text", None) or "")
                       for c in (getattr(item, "content", None) or []))
    _items = list(getattr(r, "output", None) or [])
    _last_tool = -1
    for _i, _it in enumerate(_items):
        if getattr(_it, "type", "message") not in ("message", "reasoning"):
            _last_tool = _i
        elif getattr(_it, "type", None) == "reasoning":
            for _sm in (getattr(_it, "summary", None) or []):
                _note_reasoning(getattr(_sm, "text", None) or "")
            _note_reasoning(_item_text(_it))
    text = ""
    if _last_tool >= 0:
        text = "".join(_item_text(_it) for _it in _items[_last_tool + 1:]
                       if getattr(_it, "type", "message") == "message").strip()
    if not text:
        text = getattr(r, "output_text", None) or "".join(_item_text(_it) for _it in _items)
    if not text.strip():
        pass                                     # handled by the raise below
    elif getattr(r, "status", None) == "incomplete":
        # Reasoning tokens count against max_output_tokens too — surface a cut-off
        # answer instead of passing the fragment along as if it were complete.
        text += ("\n\n[⚠️ Answer truncated — hit the output-token cap "
                 "(reasoning tokens count against it).]")
    if not text.strip():
        _u = getattr(r, "usage", None)
        raise _billed_error(
            f"{model} returned no answer text (status={getattr(r, 'status', None)!r}). "
            "The request completed and was billed, but no response content came back.",
            getattr(_u, "input_tokens", None), getattr(_u, "output_tokens", None))
    u = getattr(r, "usage", None)
    itok = getattr(u, "input_tokens", None)
    otok = getattr(u, "output_tokens", None)
    _cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", None)
    if isinstance(_cached, int) and _cached > 0:
        _note_cache(read=_cached)
    return text, itok, otok

# ---------------- model callers ----------------
def _chat_completion_text(client, kw):
    """Chat Completions call shared by ChatGPT and Grok: retries once without
    reasoning_effort when the model rejects the parameter, then returns
    (text, input_tokens, output_tokens)."""
    try:
        r = client.chat.completions.create(**kw)
    except Exception as e:
        if "effort" in str(e).lower(): kw.pop("reasoning_effort", None); r = client.chat.completions.create(**kw)
        else: raise
    choice = r.choices[0]
    _reasoning_from_choice(choice)
    text = _content_text(choice.message.content)
    finish = getattr(choice, "finish_reason", None)
    if not text.strip():
        raise RuntimeError(f"{kw.get('model')} returned no answer (finish_reason={finish}).")
    if finish == "length":
        text += "\n\n[Warning: Answer truncated at Conclave's output-token cap.]"
    usage = getattr(r, "usage", None)
    _cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
    if isinstance(_cached, int) and _cached > 0:
        _note_cache(read=_cached)
    return (text, getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None))

def call_model(name, key, messages, cfg, web):
    if name == "ChatGPT":
        client = OpenAI(api_key=key, timeout=_request_timeout(name, cfg, web),
                    max_retries=_request_retries(name, cfg, web))
        model_id = cfg["model_id"]
        _take_speed()
        _speed = chatgpt_speed_used(model_id, cfg.get("speed", "Standard"))
        service_tier = CHATGPT_SPEED[_speed]
        if _speed != cfg.get("speed", "Standard"):
            _note_speed(_speed)
        effort = chatgpt_effort_sent(model_id, cfg["effort"])
        _input = _oai_responses_input(messages, pdf_ok=True)
        try:
            return _responses_call(client, model_id, _input,
                                   effort, max_out=output_cap("ChatGPT", cfg),
                                   service_tier=service_tier, reasoning_summary="auto",
                                   tools=bool(web))
        except Exception as e:
            if service_tier != "flex" or not _is_flex_busy_error(e):
                raise
            _note_speed("Standard")
            return _responses_call(client, model_id, _input,
                                   effort, max_out=output_cap("ChatGPT", cfg),
                                   service_tier="default", reasoning_summary="auto",
                                   tools=bool(web))

    if name == "Claude":
        client = Anthropic(api_key=key, timeout=_request_timeout(name, cfg, web),
                    max_retries=_request_retries(name, cfg, web))
        model_id = cfg["model_id"]
        _cap = output_cap("Claude", cfg)
        base = {"model": model_id, "max_tokens": _cap, "messages": _claude_messages(messages)}
        if web:
            # max_uses caps the agentic search loop — without it Claude can chain dozens of
            # searches (154k tokens / 12 min on one query). Haiku needs the older tool version.
            tool_ver = "web_search_20250305" if model_id in CLAUDE_OLD_SEARCH else "web_search_20260209"
            base["tools"] = [{"type": tool_ver, "name": "web_search", "max_uses": int(cfg.get("max_searches", 5))}]
        if model_id in CLAUDE_NO_DIALS:
            kw = dict(base)
            if cfg.get("thinking"):
                kw["thinking"] = {"type": "enabled",
                                  "budget_tokens": min(CLAUDE_HAIKU_THINKING_BUDGET, _cap - 4000)}
        else:
            kw = dict(base)
            _eff = cfg.get("effort", "high")
            kw["output_config"] = {"effort": _eff}
            if model_id in CLAUDE_ALWAYS_THINK:
                kw["thinking"] = dict(CLAUDE_THINKING_ON)
            else:
                _think = bool(cfg.get("thinking")) or claude_thinking_forced(model_id, _eff)
                kw["thinking"] = (dict(CLAUDE_THINKING_ON) if _think
                                  else {"type": "between_tools"} if model_id in CLAUDE_BETWEEN_TOOLS_OFF
                                  else {"type": "disabled"})
        try:
            r = _claude_create(client, kw)
        except Exception as e:
            msg = str(e).lower()
            if "display" in msg and isinstance(kw.get("thinking"), dict) and "display" in kw["thinking"]:
                kw["thinking"] = {"type": kw["thinking"]["type"]}
                r = _claude_create(client, kw)
            elif web and ("web_search" in msg or "allowed_callers" in msg):
                for k in (kw, base):
                    if k.get("tools"): k["tools"][0]["type"] = "web_search_20250305"
                try:
                    r = _claude_create(client, kw)
                except Exception as retry_error:
                    retry_msg = str(retry_error).lower()
                    if any(t in retry_msg for t in ("effort", "thinking", "output_config")):
                        r = _claude_create(client, base)
                    else:
                        raise
            elif any(t in msg for t in ("effort", "thinking", "output_config")):
                r = _claude_create(client, base)
            else:
                raise                            # real error -> run_one shows it (and classifies billing)
        _extra_in = _extra_out = 0
        _pauses = 0
        while getattr(r, "stop_reason", None) == "pause_turn" and _pauses < CLAUDE_PAUSE_CONTINUES:
            _pauses += 1
            _extra_in += getattr(r.usage, "input_tokens", 0) or 0
            _extra_out += getattr(r.usage, "output_tokens", 0) or 0
            for b in r.content:
                if b.type == "thinking":
                    _note_reasoning(getattr(b, "thinking", None) or "")
            _paused = [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b
                       for b in r.content]
            kw = dict(kw)
            kw["messages"] = list(kw["messages"]) + [{"role": "assistant", "content": _paused}]
            r = _claude_create(client, kw)
        blocks = list(r.content)
        for b in blocks:
            if b.type == "thinking":
                _note_reasoning(getattr(b, "thinking", None) or "")
            elif b.type == "redacted_thinking":
                _note_reasoning("[Anthropic redacted part of the reasoning for this call.]")
        last_tool = max((i for i, b in enumerate(blocks) if b.type != "text"),
                        default=-1)
        stop = getattr(r, "stop_reason", None)
        if web and last_tool >= 0:
            text = "".join(b.text for b in blocks[last_tool + 1:]
                           if b.type == "text")
            if not text and stop == "pause_turn":
                # Still paused after the continuations: what exists is search
                # narration, not an answer -- fail honestly (and keep the bill).
                raise _billed_error(
                    "Claude's web search was still running when Conclave stopped waiting, "
                    "so no answer was written. Try fewer searches or a lower effort.",
                    (r.usage.input_tokens or 0) + _extra_in,
                    (r.usage.output_tokens or 0) + _extra_out)
            if not text:
                text = "".join(b.text for b in blocks if b.type == "text")
        else:
            text = "".join(b.text for b in blocks if b.type == "text")
        if not text and stop == "refusal":
            raise _billed_error("the request was declined by the model's safety classifier "
                                "(stop_reason=refusal). Rephrase, or try another Claude model.",
                                (r.usage.input_tokens or 0) + _extra_in,
                                (r.usage.output_tokens or 0) + _extra_out)
        elif stop == "max_tokens":
            # max_tokens (16000) caps thinking + visible text COMBINED, so xhigh/max
            # runs on hard questions can hit it mid-answer. Make it visible instead
            # of feeding a silent fragment to the Synthesizer as a trusted source.
            text += (f"\n\n[⚠️ Answer truncated — hit the {_cap:,}-token output cap "
                     "(thinking tokens count against it). Try a lower effort tier.]")
        _cr = getattr(r.usage, "cache_read_input_tokens", None) or 0
        _cw = getattr(r.usage, "cache_creation_input_tokens", None) or 0
        if isinstance(_cr, int) and isinstance(_cw, int) and (_cr or _cw):
            _note_cache(read=_cr, write=_cw)
            return (text, (r.usage.input_tokens or 0) + _cr + _cw + _extra_in,
                    (r.usage.output_tokens or 0) + _extra_out)
        return (text, (r.usage.input_tokens or 0) + _extra_in,
                (r.usage.output_tokens or 0) + _extra_out)

    if name == "Gemini":
        client = genai.Client(api_key=key, http_options=types.HttpOptions(
            timeout=int(_request_timeout(name, cfg, web) * 1000)))
        request = {
            "model": cfg["model_id"],
            "input": _gemini_interactions_input(messages),
            "store": False,
            "generation_config": {"thinking_level": gemini_level_sent(cfg["model_id"], cfg["level"]),
                                  "thinking_summaries": "auto",
                                  "max_output_tokens": output_cap("Gemini", cfg)},
        }
        if web:
            request["tools"] = [{"type": "google_search"}]
        last = None
        for attempt in range(3):
            try:
                r = client.interactions.create(**request)
                for _step in (getattr(r, "steps", None) or []):
                    if getattr(_step, "type", None) == "thought":
                        for _sm in (getattr(_step, "summary", None) or []):
                            _note_reasoning(getattr(_sm, "text", None) or "")
                text = getattr(r, "output_text", None) or ""
                status = str(getattr(r, "status", "") or "").lower()
                if not text.strip():
                    raise RuntimeError(
                        f"Gemini returned no answer text (status={status or 'unknown'}).")
                if status in ("incomplete", "budget_exceeded"):
                    text += "\n\n[Warning: Gemini returned a partial answer.]"
                usage = getattr(r, "usage", None)
                _gi = getattr(usage, "total_input_tokens", None)
                _go = getattr(usage, "total_output_tokens", None)
                _gt = getattr(usage, "total_thought_tokens", None) or 0
                _gu = getattr(usage, "total_tool_use_tokens", None) or 0
                return (text,
                        None if _gi is None else _gi + _gu,
                        None if _go is None else _go + _gt)
            except Exception as e:
                last = e; msg = str(e).lower()
                if "503" in msg or "unavailable" in msg or "overloaded" in msg:
                    time.sleep(2 * (attempt + 1)); continue
                if "thinking_summaries" in msg and "thinking_summaries" in request["generation_config"]:
                    request["generation_config"].pop("thinking_summaries"); continue
                if "max_output_tokens" in msg and "max_output_tokens" in request["generation_config"]:
                    request["generation_config"].pop("max_output_tokens"); continue
                break
        raise last

    if name == "DeepSeek":
        client = OpenAI(base_url="https://api.deepseek.com", api_key=key,
                        timeout=_request_timeout(name, cfg, web),
                    max_retries=_request_retries(name, cfg, web))
        model_id = cfg["model_id"]
        thinking = bool(cfg.get("thinking", True))
        kw = {
            "model": model_id,
            "messages": _oai_chat_messages(messages, pdf_ok=False,
                                           image_ok=(model_id in DEEPSEEK_VISION_MODELS)),
            "max_tokens": output_cap("DeepSeek", cfg),
            "extra_body": {
                "thinking": {"type": "enabled" if thinking else "disabled"}
            },
        }
        if thinking:
            kw["reasoning_effort"] = cfg.get("effort", "high")
        r = client.chat.completions.create(**kw)
        choice = r.choices[0]
        _reasoning_from_choice(choice)
        text = choice.message.content or ""
        finish = getattr(choice, "finish_reason", None)
        if not text.strip():
            raise RuntimeError(f"DeepSeek returned no answer (finish_reason={finish}).")
        elif finish == "length":
            text += "\n\n[Warning: Answer truncated at Conclave's output-token cap.]"
        usage = getattr(r, "usage", None)
        return (text, getattr(usage, "prompt_tokens", None),
                getattr(usage, "completion_tokens", None))

    if name == "Grok":
        client = OpenAI(base_url="https://api.x.ai/v1", api_key=key,
                        timeout=_request_timeout(name, cfg, web),
                    max_retries=_request_retries(name, cfg, web))
        eff = GROK_EFFORT.get(cfg["mode"], "medium")   # always explicit -- see GROK_MODES
        return _responses_call(client, MODEL_GROK, _oai_responses_input(messages, pdf_ok=False),
                               eff, max_out=output_cap("Grok", cfg), reasoning_summary="auto",
                               tools=bool(web), store=False)

    if name == "Mistral":
        return _mistral_call(name, key, messages, cfg, web)
    if name == "Kimi":
        return _kimi_call(name, key, messages, cfg, web)
    if name == "Meta":
        return _meta_call(name, key, messages, cfg, web)
    if name == "MiniMax":
        return _minimax_call(name, key, messages, cfg, web)
    if name == "NVIDIA":
        return _nvidia_call(name, key, messages, cfg, web)
    if name == "GLM":
        return _glm_call(name, key, messages, cfg, web)
    if name == "MiMo":
        return _mimo_call(name, key, messages, cfg, web)

    if name == "Qwen":
        client = OpenAI(base_url="https://dashscope-us.aliyuncs.com/compatible-mode/v1",
                        api_key=key, timeout=_request_timeout(name, cfg, web),
                    max_retries=_request_retries(name, cfg, web))
        model_id = cfg["model_id"]
        thinking = bool(cfg.get("thinking", True))
        kw = {
            "model": model_id,
            "messages": _oai_chat_messages(messages, pdf_ok=False, image_ok=True),
            "max_completion_tokens": output_cap("Qwen", cfg),
            "extra_body": {"enable_thinking": thinking},
        }
        if web and qwen_web_supported(model_id):
            kw["extra_body"]["enable_search"] = True
            kw["extra_body"]["search_options"] = {"forced_search": True, "search_strategy": "max"}
        if thinking:
            _qeff = cfg.get("effort")
            if _qeff and model_id in QWEN_GRADED_EFFORT_MODELS:
                kw["reasoning_effort"] = _qeff
            else:
                _qb = int(cfg.get("budget", 8000))
                kw["extra_body"]["thinking_budget"] = _qb
                kw["max_completion_tokens"] = max(kw["max_completion_tokens"], _qb + 8192)
        try:
            r = client.chat.completions.create(**kw)
        except Exception as e:
            _so = kw["extra_body"].get("search_options") or {}
            _m = str(e).lower()
            if not _so or not any(t in _m for t in ("forced", "strategy", "search_options")):
                raise
            if "forced_search" in _so and "forced" in _m:
                _so.pop("forced_search")
            else:
                kw["extra_body"].pop("search_options", None)
            try:
                r = client.chat.completions.create(**kw)
            except Exception as e2:
                if ("search_options" in kw["extra_body"]
                        and any(t in str(e2).lower() for t in ("strategy", "search_options"))):
                    kw["extra_body"].pop("search_options", None)
                    r = client.chat.completions.create(**kw)
                else:
                    raise
        choice = r.choices[0]
        _reasoning_from_choice(choice)
        text = choice.message.content or ""
        finish = getattr(choice, "finish_reason", None)
        if not text.strip():
            raise RuntimeError(f"Qwen returned no answer (finish_reason={finish}).")
        elif finish == "length":
            text += "\n\n[Warning: Answer truncated at Conclave's output-token cap.]"
        usage = getattr(r, "usage", None)
        return (text, getattr(usage, "prompt_tokens", None),
                getattr(usage, "completion_tokens", None))

def _content_text(content):
    """A chat-completions `message.content` as plain text. Mistral's reasoning
    models can return a LIST of chunks ({"type": "thinking"} then {"type":
    "text"}) through the OpenAI SDK; keep only the visible text."""
    if isinstance(content, str) or content is None:
        return content or ""
    parts = []
    for c in content:
        ctype = c.get("type") if isinstance(c, dict) else getattr(c, "type", None)
        ctext = c.get("text") if isinstance(c, dict) else getattr(c, "text", None)
        if ctype in (None, "text") and ctext:
            parts.append(ctext)
    return "".join(parts)

def _mistral_call(name, key, messages, cfg, web):
    client = OpenAI(base_url="https://api.mistral.ai/v1", api_key=key,
                    timeout=_request_timeout(name, cfg, web),
                    max_retries=_request_retries(name, cfg, web))
    model_id = cfg["model_id"]
    kw = {"model": model_id,
          "messages": _oai_chat_messages(messages, pdf_ok=True, pdf_shape="document_url",
                                         image_ok=(model_id in MISTRAL_VISION_MODELS),
                                         image_shape="string"),
          "max_tokens": output_cap("Mistral", cfg)}
    if model_id in MISTRAL_REASONING_MODELS:
        kw["reasoning_effort"] = cfg.get("effort", "medium")
    try:
        r = client.chat.completions.create(**kw)
    except Exception as e:
        _m = str(e).lower()
        if "effort" in _m and "reasoning_effort" in kw:
            kw.pop("reasoning_effort"); r = client.chat.completions.create(**kw)
        elif model_id in MISTRAL_ID_ALTERNATES and "model" in _m and any(
                t in _m for t in ("invalid", "not found", "unknown", "does not exist", "404")):
            kw["model"] = MISTRAL_ID_ALTERNATES[model_id]
            r = client.chat.completions.create(**kw)
        else:
            raise
    choice = r.choices[0]
    _reasoning_from_choice(choice)
    text = _content_text(choice.message.content)
    finish = getattr(choice, "finish_reason", None)
    if not text.strip():
        raise RuntimeError(f"Mistral returned no answer (finish_reason={finish}).")
    elif finish == "length":
        text += "\n\n[Warning: Answer truncated at Conclave's output-token cap.]"
    usage = getattr(r, "usage", None)
    _cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
    if isinstance(_cached, int) and _cached > 0:
        _note_cache(read=_cached)
    return (text, getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None))

def _kimi_call(name, key, messages, cfg, web):
    client = OpenAI(base_url="https://api.moonshot.ai/v1", api_key=key,
                    timeout=_request_timeout(name, cfg, web),
                    max_retries=_request_retries(name, cfg, web))
    model_id = cfg["model_id"]
    pdf_text = _kimi_extract_pdfs(client, messages)
    msgs = _oai_chat_messages(messages, pdf_ok=False, image_ok=True, skip_pdf=bool(pdf_text))
    if pdf_text:
        msgs.insert(0, {"role": "system", "content": pdf_text})
    kw = {"model": model_id, "messages": msgs,
          "max_completion_tokens": max(KIMI_MAX_TOKENS.get(model_id, 16000), output_cap("Kimi", cfg))}
    think_on = cfg.get("thinking", True)
    if model_id in KIMI_ALWAYS_THINK:
        kw["reasoning_effort"] = cfg.get("effort", "high")
    else:
        kw["extra_body"] = {"thinking": {"type": "enabled" if think_on else "disabled"}}
    if web:
        kw["tools"] = [KIMI_SEARCH_TOOL]
    itok = otok = 0
    t0 = time.monotonic()
    for _round in range(KIMI_SEARCH_ROUNDS + 1):
        if web and (_round == KIMI_SEARCH_ROUNDS or time.monotonic() - t0 > KIMI_WEB_BUDGET_S):
            if kw.pop("tools", None) and msgs and msgs[-1].get("role") == "tool":
                msgs.append({"role": "user", "content": KIMI_SEARCH_CLOSED})
        r = client.chat.completions.create(**kw)
        usage = getattr(r, "usage", None)
        itok += getattr(usage, "prompt_tokens", 0) or 0
        otok += getattr(usage, "completion_tokens", 0) or 0
        choice = r.choices[0]
        _calls = getattr(choice.message, "tool_calls", None)
        if web and _calls and getattr(choice, "finish_reason", None) == "tool_calls":
            msgs.append(choice.message.model_dump(exclude_none=True))
            for tc in _calls:
                try:
                    query = (json.loads(tc.function.arguments or "{}") or {}).get("query", "")
                except ValueError:
                    query = ""
                msgs.append({"role": "tool", "tool_call_id": tc.id, "name": tc.function.name,
                             "content": _kimi_web_search(key, query)})
            continue
        break
    _reasoning_from_choice(choice)
    text = _content_text(choice.message.content)
    finish = getattr(choice, "finish_reason", None)
    if not text.strip():
        raise RuntimeError(f"Kimi returned no answer (finish_reason={finish}).")
    elif finish == "length":
        text += "\n\n[Warning: Answer truncated at Conclave's output-token cap.]"
    return (text, itok or None, otok or None)

KIMI_SEARCH_ROUNDS = 3
KIMI_WEB_BUDGET_S = 180
KIMI_SEARCH_URL = "https://api.moonshot.ai/v1/tools/search"
KIMI_SEARCH_CLOSED = ("Searching is finished. Write your complete final answer now from the "
                      "results above and your own knowledge. The results are current; trust "
                      "their dates. Do not mention searching.")
KIMI_SEARCH_TOOL = {"type": "function", "function": {
    "name": "web_search",
    "description": ("Search the web for current information. Call it when the answer "
                    "depends on recent or verifiable facts; call it again with a "
                    "different query if the first results are not enough."),
    "parameters": {"type": "object",
                   "properties": {"query": {"type": "string",
                                            "description": "The search query."}},
                   "required": ["query"]}}}

def _kimi_web_search(key, query, limit=5):
    import urllib.request, urllib.error
    if not (query or "").strip():
        return json.dumps({"error": "empty query"})
    body = json.dumps({"text_query": query, "limit": limit, "timeout_seconds": 20}).encode()
    req = urllib.request.Request(KIMI_SEARCH_URL, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return json.dumps({"error": f"search failed ({type(e).__name__})"})
    results = [{k: r.get(k, "") for k in ("title", "url", "date", "site_name", "snippet")}
               for r in (data.get("search_results") or [])]
    return json.dumps({"query": query, "results": results}, ensure_ascii=False)

def _kimi_extract_pdfs(client, messages):
    blocks = []
    for m in messages:
        if m.get("role") != "user":
            continue
        for f in _binary_files(m):
            if f["kind"] == "image":
                continue
            try:
                up = client.files.create(
                    file=(f["name"], base64.b64decode(f["b64"]), "application/pdf"),
                    purpose="file-extract")
                body = client.files.content(file_id=up.id).text
                try:
                    client.files.delete(up.id)
                except Exception:
                    pass
                blocks.append(f"Attached file {f['name']}:\n{body}")
            except Exception as e:
                blocks.append(f"[Attached file {f['name']} could not be read by Moonshot's "
                              f"Files API: {type(e).__name__}]")
    return "\n\n".join(blocks)

def _meta_call(name, key, messages, cfg, web):
    client = OpenAI(base_url="https://api.meta.ai/v1", api_key=key,
                    timeout=_request_timeout(name, cfg, web),
                    max_retries=_request_retries(name, cfg, web))
    model_id = cfg["model_id"]
    effort = cfg.get("effort", "medium")
    if web:
        return _responses_call(client, model_id, _oai_responses_input(messages, pdf_ok=True),
                               effort, max_out=output_cap("Meta", cfg),
                               require_reasoning=True, reasoning_summary="auto")
    kw = {"model": model_id,
          "messages": _oai_chat_messages(messages, pdf_ok=True, image_ok=True),
          "max_completion_tokens": output_cap("Meta", cfg), "reasoning_effort": effort}
    return _chat_completion_text(client, kw)

def _minimax_call(name, key, messages, cfg, web):
    client = OpenAI(base_url="https://api.minimax.io/v1", api_key=key,
                    timeout=_request_timeout(name, cfg, web),
                    max_retries=_request_retries(name, cfg, web))
    model_id = cfg["model_id"]
    think_on = bool(cfg.get("thinking", True)) or model_id not in MINIMAX_THINK_OFF_OK
    _tier = MINIMAX_SPEED.get(cfg.get("speed", "Standard"))
    if web:
        return _responses_call(client, model_id,
                               _oai_responses_input(messages, pdf_ok=False,
                                                    image_ok=(model_id == "MiniMax-M3")),
                               "medium" if think_on else None,
                               max_out=output_cap("MiniMax", cfg), service_tier=_tier)
    kw = {"model": model_id,
          "messages": _oai_chat_messages(messages, pdf_ok=False, image_ok=(model_id == "MiniMax-M3")),
          "max_completion_tokens": output_cap("MiniMax", cfg),
          "extra_body": {"thinking": {"type": "adaptive" if think_on else "disabled"},
                         "reasoning_split": True}}
    if _tier:
        kw["service_tier"] = _tier
    r = client.chat.completions.create(**kw)
    choice = r.choices[0]
    _reasoning_from_choice(choice)
    text = _content_text(choice.message.content)
    _reasoning_from_inline(text)
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.S)   # belt and braces
    finish = getattr(choice, "finish_reason", None)
    if not text.strip():
        raise RuntimeError(f"MiniMax returned no answer (finish_reason={finish}).")
    elif finish == "length":
        text += "\n\n[Warning: Answer truncated at Conclave's output-token cap.]"
    usage = getattr(r, "usage", None)
    _cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
    if isinstance(_cached, int) and _cached > 0:
        _note_cache(read=_cached)
    return (text, getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None))

def _nvidia_call(name, key, messages, cfg, web):
    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=key,
                    timeout=_request_timeout(name, cfg, web),
                    max_retries=_request_retries(name, cfg, web))
    model_id = cfg["model_id"]
    think_on = bool(cfg.get("thinking", True))
    budget = int(cfg.get("budget", 8000))
    kw = {"model": model_id,
          "messages": _oai_chat_messages(messages, pdf_ok=False,
                                         image_ok=(model_id in NVIDIA_VISION_MODELS)),
          "max_tokens": min(32768, budget + 12000) if think_on else 16000,
          "extra_body": {"chat_template_kwargs": {"enable_thinking": think_on}}}
    if think_on:
        kw["extra_body"]["reasoning_budget"] = budget
        kw["extra_body"]["chat_template_kwargs"]["reasoning_budget"] = budget
    try:
        r = client.chat.completions.create(**kw)
    except Exception as e:
        if think_on and "budget" in str(e).lower():
            kw["extra_body"].pop("reasoning_budget", None)
            kw["extra_body"]["chat_template_kwargs"].pop("reasoning_budget", None)
            r = client.chat.completions.create(**kw)
        else:
            raise
    choice = r.choices[0]
    _reasoning_from_choice(choice)
    text = _content_text(choice.message.content)
    finish = getattr(choice, "finish_reason", None)
    if not text.strip():
        raise RuntimeError(f"NVIDIA returned no answer (finish_reason={finish}).")
    elif finish == "length":
        text += "\n\n[Warning: Answer truncated at Conclave's output-token cap.]"
    usage = getattr(r, "usage", None)
    _cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
    if isinstance(_cached, int) and _cached > 0:
        _note_cache(read=_cached)
    return (text, getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None))

def _glm_call(name, key, messages, cfg, web):
    client = OpenAI(base_url="https://api.z.ai/api/paas/v4/", api_key=key,
                    timeout=_request_timeout(name, cfg, web),
                    max_retries=_request_retries(name, cfg, web))
    model_id = cfg["model_id"]
    think_on = bool(cfg.get("thinking", True)) or model_id in GLM_ALWAYS_THINK
    extra = {"thinking": {"type": "enabled" if think_on else "disabled"}}
    if model_id in GLM_ALWAYS_THINK:
        extra["reasoning_effort"] = cfg.get("effort", "high")
    kw = {"model": model_id,
          "messages": _oai_chat_messages(messages, pdf_ok=False,
                                         image_ok=(model_id in GLM_VISION_MODELS)),
          "max_tokens": output_cap("GLM", cfg), "extra_body": extra}
    if web:
        kw["tools"] = [{"type": "web_search",
                        "web_search": {"enable": True, "search_engine": "search-prime",
                                       "search_result": True}}]
    r = client.chat.completions.create(**kw)
    choice = r.choices[0]
    _reasoning_from_choice(choice)
    text = _content_text(choice.message.content)
    _reasoning_from_inline(text)
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.S)
    finish = getattr(choice, "finish_reason", None)
    if not text.strip():
        raise RuntimeError(f"GLM returned no answer (finish_reason={finish}).")
    elif finish == "length":
        text += "\n\n[Warning: Answer truncated at Conclave's output-token cap.]"
    usage = getattr(r, "usage", None)
    _cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
    if isinstance(_cached, int) and _cached > 0:
        _note_cache(read=_cached)
    return (text, getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None))

def _mimo_call(name, key, messages, cfg, web):
    client = OpenAI(base_url=MIMO_URL, api_key=key,
                    timeout=_request_timeout(name, cfg, web),
                    max_retries=_request_retries(name, cfg, web))
    model_id = cfg["model_id"]
    kw = {"model": model_id,
          "messages": _oai_chat_messages(messages, pdf_ok=False, image_ok=True),
          "max_completion_tokens": output_cap("MiMo", cfg),
          "extra_body": {"thinking": {"type": "enabled" if cfg.get("thinking", True)
                                      else "disabled"}}}
    if web:
        kw["tools"] = [{"type": "web_search", "max_keyword": MIMO_SEARCH_KEYWORDS,
                        "force_search": True}]
        kw["tool_choice"] = "auto"
    r = client.chat.completions.create(**kw)
    choice = r.choices[0]
    _reasoning_from_choice(choice)
    text = _content_text(choice.message.content)
    _reasoning_from_inline(text)
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.S)
    finish = getattr(choice, "finish_reason", None)
    if not text.strip():
        raise RuntimeError(f"MiMo returned no answer (finish_reason={finish}).")
    elif finish == "length":
        text += "\n\n[Warning: Answer truncated at Conclave's output-token cap.]"
    usage = getattr(r, "usage", None)
    _cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
    if isinstance(_cached, int) and _cached > 0:
        _note_cache(read=_cached)
    return (text, getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None))

def model_id_of(name, cfg):
    if name in ("ChatGPT", "Gemini", "Claude", "DeepSeek", "Qwen", "Mistral", "Kimi",
                "Meta", "MiniMax", "NVIDIA", "GLM", "MiMo"): return cfg["model_id"]
    return MODEL_GROK

def settings_str(name, cfg, web=False):
    if name == "ChatGPT":
        _sent = chatgpt_effort_sent(cfg.get("model_id"), cfg["effort"])
        _note = " (no Instant on this model)" if _sent != cfg["effort"] else ""
        return f"effort {_sent}{_note} · {cfg.get('speed', 'Standard').lower()} processing"
    if name == "Claude":
        mid = cfg["model_id"]
        eff = cfg.get("effort", "high")
        if mid in CLAUDE_NO_DIALS:
            return ("speed model · thinking on (manual budget)" if cfg.get("thinking")
                    else "speed model (no dials)")
        if mid in CLAUDE_ALWAYS_THINK: return f"effort {eff} · thinking always on"
        # Report what was actually SENT, not just what the box said — thinking is
        # forced on where an explicit off would be rejected (see claude_thinking_forced).
        if claude_thinking_forced(mid, eff):
            return f"effort {eff} · thinking on (required at this effort)"
        return f"effort {eff} · thinking {'on' if cfg.get('thinking') else 'off'}"
    if name == "Gemini":
        _lvl = gemini_level_sent(cfg.get("model_id", ""), cfg["level"])
        return {"minimal": "minimal", "low": "standard", "medium": "balanced",
                "high": "extended"}.get(_lvl, _lvl)
    if name == "DeepSeek":
        return (f"thinking {'on' if cfg.get('thinking') else 'off'}" +
                (f" · effort {cfg.get('effort', 'high')}" if cfg.get('thinking') else ""))
    if name == "Grok":    return cfg["mode"].lower()
    if name == "Qwen":
        if cfg.get("thinking") and cfg.get("effort") and cfg.get("model_id") in QWEN_GRADED_EFFORT_MODELS:
            return f"thinking · effort {cfg['effort']}"
        return (f"thinking · budget {cfg.get('budget', 8000):,} tok" if cfg.get("thinking")
                else "thinking off")
    if name == "Mistral":
        return (f"reasoning {cfg.get('effort', 'medium')}"
                if cfg.get("model_id") in MISTRAL_REASONING_MODELS else "no reasoning dial")
    if name == "Kimi":
        return (f"reasoning {cfg.get('effort', 'high')}"
                if cfg.get("model_id") in KIMI_ALWAYS_THINK
                else f"thinking {'on' if cfg.get('thinking', True) else 'off'}")
    if name == "Meta":
        return f"reasoning {cfg.get('effort', 'medium')}"
    if name == "MiniMax":
        _sp = " · priority tier" if cfg.get("speed") == "Priority" else ""
        if web:
            return ("reasoning medium (web path)" if (cfg.get("thinking", True) or
                                                     cfg.get("model_id") not in MINIMAX_THINK_OFF_OK)
                    else "thinking off (web path)") + _sp
        return ("thinking adaptive" if (cfg.get("thinking", True) or
                                        cfg.get("model_id") not in MINIMAX_THINK_OFF_OK)
                else "thinking off") + _sp
    if name == "NVIDIA":
        return (f"thinking · budget {cfg.get('budget', 8000):,} tok" if cfg.get("thinking")
                else "thinking off")
    if name == "GLM":
        return (f"reasoning {cfg.get('effort', 'high')}"
                if cfg.get("model_id") in GLM_ALWAYS_THINK
                else f"thinking {'on' if cfg.get('thinking', True) else 'off'}")
    if name == "MiMo":
        return f"thinking {'on' if cfg.get('thinking', True) else 'off'}"
    return ""

_CREDIT_KEYWORDS = ("insufficient_quota", "insufficient quota", "credit balance",
                    "billing", "purchase credits", "exceeded your current quota",
                    "out of credits", "insufficient credits", "insufficient funds",
                    "payment required", "spend limit", "spending limit",
                    "monthly spend", "available credits",
                    "enforced_spend_limit_reached", "spend_limit")

def _is_credit_error(name, e):
    if name == "ChatGPT" and isinstance(e, OpenAIRateLimitError):
        if getattr(e, "code", None) == "insufficient_quota":
            return True
    elif name == "Claude" and isinstance(e, AnthropicAPIStatusError):
        if getattr(e, "type", None) == "billing_error":
            return True
        _body = getattr(e, "body", None)
        if isinstance(_body, dict):
            _details = (_body.get("error") or {}).get("details") if isinstance(_body.get("error"), dict) else None
            if isinstance(_details, dict) and _details.get("error_code") == "enforced_spend_limit_reached":
                return True
    elif name == "Gemini" and isinstance(e, genai_errors.APIError):
        if getattr(e, "code", None) == 429 and getattr(e, "status", None) == "RESOURCE_EXHAUSTED":
            return True
    elif name == "DeepSeek" and getattr(e, "status_code", None) == 402:
        # DeepSeek documents HTTP 402 specifically as insufficient balance.
        return True
    return any(k in str(e).lower() for k in _CREDIT_KEYWORDS)

def _is_timeout_error(e):
    """True for the request-timeout failures every SDK raises when a call
    exceeds Conclave's own ceiling (_request_timeout) -- openai/anthropic
    APITimeoutError, httpx.TimeoutException, and any provider error whose
    message says so. Never true for auth/billing/validation failures."""
    names = {c.__name__ for c in type(e).__mro__}
    if any("Timeout" in n for n in names):
        return True
    msg = str(e).lower()
    return "timed out" in msg or "timeout" in msg

def _billed_error(message, in_tok, out_tok):
    err = RuntimeError(message)
    err.billed_tokens = (in_tok, out_tok)
    return err


def _is_web_unavailable_error(e):
    msg = str(e).lower()
    squashed = msg.replace(" ", "").replace("_", "")
    return ("websearchenabledisfalse" in squashed
            or ("web search" in msg and any(t in msg for t in ("not enabled", "is disabled",
                                                                 "not activated"))))


def _today_line():
    now = datetime.now()
    return f"[Today's date: {now.strftime('%A')}, {now.strftime('%B')} {now.day}, {now.year}.]"


def _with_today(messages):
    """A copy of `messages` whose newest user turn begins with today's date.
    Stored history is never touched -- only the outgoing request."""
    out = [dict(m) for m in messages]
    for m in reversed(out):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            if not m["content"].startswith("[Today's date:"):
                m["content"] = _today_line() + "\n\n" + m["content"]
            break
    return out


def _degenerate_repetition(text):
    if len(text) < 4000:
        return False
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if len(x.strip()) > 30]
    if len(sents) < 20:
        return False
    counts = {}
    for x in sents:
        counts[x] = counts.get(x, 0) + 1
    top = max(counts.values())
    repeated = sum(c for c in counts.values() if c >= 5)
    return top >= 10 and repeated >= 0.5 * len(sents)


def run_one(name, key, messages, cfg, web):
    if not key:
        return {"text": f"No key for {name}", "secs": 0, "in_tok": None, "out_tok": None,
                "cost": None, "web": web, "provider": name, "credit_err": False, "err": True}
    t0 = time.perf_counter()
    credit_err = False
    err = False
    web_fallback = False
    web_fallback_reason = None
    _take_reasoning()
    _take_cache()
    _take_speed()
    messages = _with_today(messages)
    _asked_model = cfg.get("model_id")
    if name == "Gemini" and web:
        cfg = gemini_cfg_for_web(cfg)
    try:
        text, itok, otok = call_model(name, key, messages, cfg, web)
        if _degenerate_repetition(text):
            raise _billed_error("the model got stuck repeating itself, so its answer "
                                "was discarded and left out of the synthesis", itok, otok)
    except Exception as e:
        _take_reasoning()
        if web and (_is_timeout_error(e) or _is_web_unavailable_error(e)):
            web_fallback_reason = "timeout" if _is_timeout_error(e) else "unavailable"
            try:
                text, itok, otok = call_model(name, key, messages, cfg, False)
                web, web_fallback = False, True
            except Exception as e2:
                credit_err = _is_credit_error(name, e2)
                err = True
                itok, otok = getattr(e2, "billed_tokens", (None, None))
                text = f"Error from {name}: {e2}"
        else:
            credit_err = _is_credit_error(name, e)
            err = True
            itok, otok = getattr(e, "billed_tokens", (None, None))
            text = f"Error from {name}: {e}"
    secs = time.perf_counter() - t0
    reasoning = None if err else _take_reasoning()
    cache = None if err else _take_cache()
    speed_used = _take_speed()
    speed = speed_used or cfg.get("speed", "Standard")
    out = {"text": text, "secs": secs, "in_tok": itok, "out_tok": otok,
           "cost": cost_with_cache(model_id_of(name, cfg), itok, otok,
                                   speed, cache), "web": web,
           "provider": name, "credit_err": credit_err, "err": err,
           "web_fallback": web_fallback, "reasoning": reasoning}
    if web_fallback and web_fallback_reason == "unavailable":
        out["web_fallback_reason"] = "unavailable"
    if speed_used and not err:
        out["speed_used"] = speed_used
    if cache:
        out["cache"] = cache
    if cfg.get("model_id") != _asked_model:
        out["model_used"] = cfg.get("model_id")
    return out

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.14.0"
JEV_PRICE_IN = 0.042
JEV_STATE_CHAR_BUDGET = 88_000          # ~22k tokens of evidence, under the 32k state ceiling
JEV_MAX_CLAIMS = 20
JEV_MODES = ["Off", "Shadow (record only)", "Active"]
JEV_HOLD_VERDICTS = ("refuses", "no_attachment", "off_topic", "broken")
JEV_HOLD_CONFIDENCE = 0.85              # docs: act automatically only on a clear read
JEV_CONTRADICT_FLAG = 0.30
JEV_UNADDRESSED_SHARE = 0.25
JEV_PRESET_LADDER = ["Fast", "Light", "Balanced", "Thorough", "Deep"]
JEV_DEPTH_LEVELS = [
    "A quick fact, definition, conversion, or casual request that a short reply settles",
    "A routine explanation or how-to with little ambiguity and low stakes",
    "A substantive question where reasoning quality, nuance, or several factors matter",
    "A consequential decision, analysis, or contested topic where mistakes would be costly",
    "A hard, multi-part expert problem that needs deep reasoning and careful verification",
]
JEV_CHECK_VERDICTS = {
    "answers": "Makes a real attempt at answering the question asked, even if brief, "
               "partial, or possibly mistaken",
    "refuses": "Declines, deflects, or only gives warnings without attempting the question",
    "no_attachment": "Says it cannot see, open, or access a file or image that the question "
                     "refers to, so it does not answer about that file",
    "off_topic": "Answers a different question from the one asked",
    "broken": "Garbled, stuck repeating itself, or cut off before giving any usable answer",
}
JEV_CLAIM_CRITERIA = {
    "supported": "At least one source answer states or clearly implies the claim, and none "
                 "contradicts it",
    "disputed": "The source answers disagree: some support the claim and some contradict it",
    "contradicted": "Source answers that address the claim say the opposite or give clearly "
                    "different facts, and none supports it",
    "unaddressed": "No source answer addresses what the claim asserts, either way",
}

CEREBRAS_URL = "https://api.cerebras.ai/v1"
CEREBRAS_PREVIEW_MODEL = "gpt-oss-120b"
PREVIEW_MAX_CALLS = 5
PREVIEW_ANSWER_CHARS = 2_000
PREVIEW_TOTAL_CHARS = 26_000
PREVIEW_MAX_TOKENS = 1_400

QUORUM_MODES = ["Start when most are in (faster)", "Wait for every source"]
QUORUM_FRACTION = 0.75
QUORUM_GRACE_MIN = 8.0
QUORUM_GRACE_MAX = 60.0
QUORUM_GRACE_SHARE = 0.35
SOURCE_DEADLINES = {"Fast": 75, "Light": 120, "Balanced": 180, "Thorough": 300,
                    "Deep": 480, "Custom": 300}
# Providers whose synthesis is streamed (official SDK streaming helpers,
# verified on the installed anthropic 1.2 / openai 3.6). Others answer in
# one piece and the live draft covers the wait.
STREAM_PROVIDERS = ("Claude", "ChatGPT")
HELPER_KEYS = {"Jev": "typesafe", "Cerebras": "cerebras"}


def quorum_ready(now, started, n_total, good_n, min_good, quorum_at, cfg):
    """Return (start_synthesis_now, quorum_at). Pure.

    Quorum = at least QUORUM_FRACTION of the parallel calls answered well
    (never fewer than the evaluator's own minimum). Once reached, a grace of
    35% of the time it took (8-60 s) lets near-finishers make it in. A hard
    per-preset deadline starts synthesis with whatever usable answers exist."""
    if not cfg or not cfg.get("enabled") or n_total <= 1:
        return False, quorum_at
    floor = max(int(min_good or 0), 1)
    need = max(floor, -(-int(n_total * 100 * cfg.get("fraction", QUORUM_FRACTION)) // 100))
    if good_n >= need:
        if quorum_at is None:
            quorum_at = now
        grace = min(QUORUM_GRACE_MAX,
                    max(QUORUM_GRACE_MIN, QUORUM_GRACE_SHARE * (quorum_at - started)))
        if now - quorum_at >= grace:
            return True, quorum_at
    if now - started >= cfg.get("deadline", 300) and good_n >= floor:
        return True, quorum_at
    return False, quorum_at


# ---- live answer streaming (thread-local, so only the phase that asked streams)
_stream_slot = threading.local()


def _stream_emit(delta="", reset=False):
    cb = getattr(_stream_slot, "cb", None)
    if cb is not None:
        try:
            cb(delta or "", reset)
        except Exception:
            pass


def _claude_create(client, kw):
    """messages.create, or the SDK's streaming helper when this thread is
    streaming a synthesis. Same Message object either way. A new non-text
    block (thinking, a web search) resets the live text -- the same rule the
    Claude branch applies to the final answer (only text after the last tool
    call is the answer)."""
    if getattr(_stream_slot, "cb", None) is None:
        return client.messages.create(**kw)
    _stream_emit(reset=True)
    try:
        manager = client.messages.stream(**kw)
    except (AttributeError, TypeError):
        return client.messages.create(**kw)
    with manager as stream:
        for event in stream:
            etype = getattr(event, "type", "")
            if etype == "content_block_start":
                if getattr(getattr(event, "content_block", None), "type", "text") != "text":
                    _stream_emit(reset=True)
            elif etype == "content_block_delta":
                delta = getattr(event, "delta", None)
                if getattr(delta, "type", "") == "text_delta":
                    _stream_emit(getattr(delta, "text", "") or "")
        return stream.get_final_message()


def _responses_create(client, kw):
    """responses.create, or the streaming helper when streaming (ChatGPT)."""
    if getattr(_stream_slot, "cb", None) is None:
        return client.responses.create(**kw)
    _stream_emit(reset=True)
    try:
        manager = client.responses.stream(**kw)
    except (AttributeError, TypeError):
        return client.responses.create(**kw)
    with manager as stream:
        for event in stream:
            etype = getattr(event, "type", "")
            if etype == "response.output_text.delta":
                _stream_emit(getattr(event, "delta", "") or "")
            elif etype == "response.output_item.added":
                if getattr(getattr(event, "item", None), "type", "message") not in ("message", "reasoning"):
                    _stream_emit(reset=True)
        return stream.get_final_response()


def _run_streaming(reg, job_id, slot, name, key, messages, cfg, web):
    """run_one with this thread's text deltas written into job["stream"][slot]
    (a list of pieces; readers join under the lock)."""
    def cb(delta, reset):
        with reg["lock"]:
            job = reg["jobs"].get(job_id)
            if job is None:
                return
            streams = job.setdefault("stream", {})
            if reset or slot not in streams:
                streams[slot] = []
            if delta:
                streams[slot].append(delta)
    _stream_slot.cb = cb
    try:
        return run_one(name, key, messages, cfg, web)
    finally:
        _stream_slot.cb = None


def live_answer_text(text):
    """What the live panel shows of a streaming answer: everything before the
    hidden ledger / state blocks (and before a half-written marker)."""
    text = str(text or "")
    for marker in (LEDGER_BEGIN, STATE_BEGIN):
        if marker in text:
            text = text.split(marker, 1)[0]
    cut = text.rfind("<<<")
    if cut != -1 and len(text) - cut < 40:
        text = text[:cut]
    return text.rstrip()


PRIMARY_PROVIDERS = ("Claude", "ChatGPT", "Gemini", "Grok", "Meta")
SEARCH_KEYS = {"Tavily": "tavily", "Brave": "brave"}
TAVILY_URL = "https://api.tavily.com/search"
TAVILY_DEPTH = "advanced"
TAVILY_CREDIT_PRICE = 0.008
TAVILY_MAX_RESULTS = 8
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_PRICE = 0.005
BRAVE_COUNT = 10
SEARCH_TIMEOUT_S = 15.0
FRESH_SOURCES_CHAR_BUDGET = 40_000
FRESH_SOURCE_EXCERPT_CHARS = 3_000
SEARCH_QUERY_CHARS = 380               # search engines want a short query
PACK_KEYS = {"ChatGPT": "k_pack_gpt", "Claude": "k_pack_cla", "Gemini": "k_pack_gem",
             "DeepSeek": "k_pack_ds", "Grok": "k_pack_grok", "Qwen": "k_pack_qwen",
             "Mistral": "k_pack_mistral", "Kimi": "k_pack_kimi", "Meta": "k_pack_meta",
             "MiniMax": "k_pack_mm", "NVIDIA": "k_pack_nv", "GLM": "k_pack_glm",
             "MiMo": "k_pack_mimo"}
WEBSRC_SHARED_DEFAULT = {"Claude": False, "ChatGPT": False, "Gemini": False, "Grok": True,
                         "Meta": False, "Kimi": True, "MiMo": False, "GLM": False,
                         "MiniMax": True,
                         "Qwen": True}


def pages_by_default(provider):
    """True when the global web switch gives this source the shared pages
    instead of its own search: no search of its own, or WEBSRC_SHARED_DEFAULT."""
    return provider in NO_WEB_PROVIDERS or bool(WEBSRC_SHARED_DEFAULT.get(provider))
TAVILY_DEPTHS = {"Advanced (2 credits — richer excerpts)": "advanced",
                 "Basic (1 credit — doubles the free allowance)": "basic"}
FRESH_SOURCE_MODES = ["When web search is on",
                      "When web is on, or Jev says the question needs current facts",
                      "Always"]
FRESH_MODE_CODES = dict(zip(FRESH_SOURCE_MODES, ("web", "jev", "always")))
JEV_FRESH_THRESHOLD = 0.35
JEV_ROUTE_WAIT_S = 6.0


def _search_request(req, service, timeout):
    import urllib.request, urllib.error
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{service} returned HTTP {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(f"{service} request failed ({type(e).__name__})") from None
    return data if isinstance(data, dict) else {}


def _site_of(url):
    from urllib.parse import urlparse
    host = (urlparse(str(url or "")).netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def _plain_snippet(text):
    """Brave marks matches with <strong>; keep the words, drop the tags."""
    return html.unescape(re.sub(r"<[^>]+>", "", str(text or ""))).strip()


def tavily_search(key, query, max_results=TAVILY_MAX_RESULTS, timeout=SEARCH_TIMEOUT_S,
                  depth=TAVILY_DEPTH):
    import urllib.request
    payload = {"query": query, "search_depth": depth, "max_results": max_results,
               "include_published_date": True, "include_usage": True}
    if depth == "advanced":
        payload["chunks_per_source"] = 3          # advanced only (docs)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(TAVILY_URL, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    t0 = time.perf_counter()
    data = _search_request(req, "Tavily", timeout)
    results = []
    for r in data.get("results") or []:
        if not isinstance(r, dict) or not r.get("url"):
            continue
        results.append({"title": str(r.get("title") or "").strip(), "url": str(r["url"]),
                        "site": _site_of(r["url"]),
                        "date": str(r.get("published_date") or "")[:10],
                        "text": str(r.get("content") or "").strip()})
    credits = (data.get("usage") or {}).get("credits")
    if not isinstance(credits, (int, float)):
        credits = 2 if depth == "advanced" else 1
    return {"service": "Tavily", "results": results, "secs": time.perf_counter() - t0,
            "cost": credits * TAVILY_CREDIT_PRICE}


def brave_search(key, query, count=BRAVE_COUNT, timeout=SEARCH_TIMEOUT_S):
    import urllib.request
    from urllib.parse import urlencode
    url = BRAVE_URL + "?" + urlencode({"q": query, "count": count, "extra_snippets": "true"})
    req = urllib.request.Request(url, method="GET",
                                 headers={"X-Subscription-Token": key,
                                          "Accept": "application/json"})
    t0 = time.perf_counter()
    data = _search_request(req, "Brave", timeout)
    results = []
    for r in ((data.get("web") or {}).get("results") or []):
        if not isinstance(r, dict) or not r.get("url"):
            continue
        bits = [_plain_snippet(r.get("description"))]
        bits += [_plain_snippet(x) for x in (r.get("extra_snippets") or [])]
        results.append({"title": _plain_snippet(r.get("title")), "url": str(r["url"]),
                        "site": _site_of(r["url"]),
                        "date": str(r.get("page_age") or r.get("age") or "")[:10],
                        "text": " … ".join(b for b in bits if b)})
    return {"service": "Brave", "results": results, "secs": time.perf_counter() - t0,
            "cost": BRAVE_PRICE}


def _url_key(url):
    """Same page, different spelling: no scheme, no www, no trailing slash,
    no fragment, no utm_* tracking parameters."""
    from urllib.parse import urlparse, parse_qsl, urlencode
    u = urlparse(str(url or "").strip())
    host = (u.netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    query = urlencode([(k, v) for k, v in parse_qsl(u.query) if not k.lower().startswith("utm_")])
    return host + (u.path or "").rstrip("/") + (("?" + query) if query else "")


def merge_search_results(packs):
    order = [svc for svc in ("Tavily", "Brave") if svc in packs] + \
            [svc for svc in packs if svc not in ("Tavily", "Brave")]
    lists = [(svc, list(packs[svc].get("results") or [])) for svc in order]
    merged, seen, both = [], {}, 0
    for i in range(max((len(rs) for _, rs in lists), default=0)):
        for svc, rs in lists:
            if i >= len(rs):
                continue
            r = dict(rs[i])
            key = _url_key(r.get("url"))
            if key in seen:
                kept = merged[seen[key]]
                if svc not in kept["found_by"]:
                    kept["found_by"].append(svc)
                    both += 1
                if len(str(r.get("text") or "")) > len(str(kept.get("text") or "")):
                    kept["text"] = r.get("text")
                kept["date"] = kept.get("date") or r.get("date", "")
                continue
            r["found_by"] = [svc]
            seen[key] = len(merged)
            merged.append(r)
    counts = {svc: len(rs) for svc, rs in lists}
    return merged, counts, both


def fetch_fresh_sources(keys, query, depth=TAVILY_DEPTH):
    keys = keys or {}
    engines = [(svc, fn) for svc, fn in (("Tavily", tavily_search), ("Brave", brave_search))
               if keys.get(svc)]
    if not engines:
        return {"service": None, "results": [], "secs": 0.0, "cost": None,
                "errors": ["no search key"], "calls": [], "counts": {}, "both": 0}
    errors, calls, packs = [], [], {}
    with ThreadPoolExecutor(max_workers=len(engines)) as ex:
        futs = {svc: (ex.submit(fn, keys[svc], query, depth=depth) if svc == "Tavily"
                      else ex.submit(fn, keys[svc], query))
                for svc, fn in engines}
        for svc, fut in futs.items():
            try:
                pack = fut.result()
            except Exception as e:
                errors.append(str(e)[:160])
                continue
            calls.append({"model": svc, "secs": pack["secs"], "cost": pack["cost"],
                          "in_tok": None, "out_tok": None})
            if pack["results"]:
                packs[svc] = pack
            else:
                errors.append(f"{svc} found nothing")
    results, counts, both = merge_search_results(packs)
    return {"service": " + ".join(packs) or None, "results": results,
            "secs": max((c["secs"] for c in calls), default=0.0),
            "cost": sum(c["cost"] for c in calls) if calls else None,
            "errors": errors, "calls": calls, "counts": counts, "both": both}


def search_query_for(question, previous=""):
    """The one search query for a turn. A short follow-up ("and the
    submarines?") leans on the question it follows."""
    q = " ".join(str(question or "").split())
    if previous and len(q) < 120:
        q = " ".join(str(previous).split())[:200] + " — " + q
    return q[:SEARCH_QUERY_CHARS]


def fresh_pages_given(pack):
    given, budget = [], FRESH_SOURCES_CHAR_BUDGET
    for i, r in enumerate(pack.get("results") or [], 1):
        head = f"[{i}] {r.get('title') or r.get('site')} — {r.get('site')}"
        if r.get("date"):
            head += f" — {r['date']}"
        if len(r.get("found_by") or []) > 1:
            head += " — found by both engines"
        excerpt = str(r.get("text") or "")[:FRESH_SOURCE_EXCERPT_CHARS]
        block = f"{head}\n{r.get('url')}\n{excerpt}\n"
        if len(block) > budget:
            block = block[:budget]
            excerpt = excerpt[:max(0, budget - len(head) - len(str(r.get("url"))) - 3)]
        given.append({**{k: r.get(k, "") for k in ("title", "url", "site", "date", "found_by")},
                      "text": excerpt, "block": block})
        budget -= len(block)
        if budget <= 300:
            break
    return given


def format_fresh_sources(pack):
    """The block appended to a receiving source's turn message."""
    lines = [f"Fresh web sources, gathered by Conclave for this question just now "
             f"({pack.get('service')} search). If you also have a web search of your own on "
             "this run, you may still use it.",
             "Use them for anything recent or time-sensitive and judge each one's reliability. "
             "Where they conflict with your training data about recent events, prefer a "
             "credible, dated source and say so. They are excerpts and may be incomplete. "
             "When you rely on one, name its site in parentheses. Do not mention this note.",
             ""]
    lines += [g["block"] for g in fresh_pages_given(pack)]
    return "\n".join(lines)


def _with_fresh_sources(messages, text):
    """A copy of a source's messages with the fresh sources appended to its
    newest user turn (stored history is never touched)."""
    out = [dict(m) for m in messages]
    for m in reversed(out):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            m["content"] = m["content"] + "\n\n---\n" + text
            break
    return out


def jev_ask(key, state, questions, timeout=20.0):
    """One System One request. Returns answers + usage; raises RuntimeError
    with a key-free message on failure. Retries once on 429/529 (docs)."""
    import urllib.request, urllib.error
    body = json.dumps({"model": JEV_MODEL, "state": state, "questions": questions},
                      ensure_ascii=False).encode("utf-8")
    t0 = time.perf_counter()
    data = None
    for attempt in range(2):
        req = urllib.request.Request(TYPESAFE_URL, data=body, method="POST",
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and attempt == 0:
                time.sleep(1.5)
                continue
            raise RuntimeError(f"Jev returned HTTP {e.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RuntimeError(f"Jev request failed ({type(e).__name__})") from None
    data = data if isinstance(data, dict) else {}
    usage = data.get("usage") or {}
    in_tok = usage.get("input_tokens")
    return {"answers": data.get("answers") or {}, "model": data.get("model", JEV_MODEL),
            "secs": time.perf_counter() - t0, "in_tok": in_tok,
            "out_tok": usage.get("output_tokens"),
            "cost": (in_tok * JEV_PRICE_IN / 1_000_000) if in_tok is not None else None}


def _jev_usage(r):
    return {k: r.get(k) for k in ("secs", "in_tok", "out_tok", "cost", "model")}


def jev_route(key, question, follow_up=False, previous_question="", has_files=False):
    """Jev's read of a question: does it need current information, how deep
    is it (mapped onto the five presets), and -- for a follow-up -- does it
    need the full council again."""
    state = {"question": _clip_context_text(question, 6000), "has_attachments": bool(has_files)}
    questions = {
        "needs_web": {
            "type": "noul",
            "instructions": "Would a good answer to `question` need facts that may have "
                            "changed in roughly the last year, or live information such as "
                            "news, prices, schedules, current versions, standings, or recent "
                            "events?",
            "criteria": {"true": "Recent or live facts are needed for a correct, current answer",
                         "false": "Stable knowledge is enough; nothing time-sensitive is asked"}},
        "depth": {
            "type": "score",
            "instructions": "How much reasoning depth and care does `question` call for?",
            "criteria": JEV_DEPTH_LEVELS},
    }
    if follow_up:
        state["previous_question"] = _clip_context_text(previous_question, 2000)
        questions["needs_council"] = {
            "type": "noul",
            "instructions": "`question` is a follow-up in a conversation whose earlier "
                            "question is `previous_question`. Does it need fresh independent "
                            "research -- a new topic, new facts to verify, or a challenge to "
                            "an earlier claim -- rather than extending, clarifying, or "
                            "reformatting the answer already given?",
            "criteria": {"true": "New research is needed for this follow-up",
                         "false": "The existing answer can simply be extended or clarified"}}
    r = jev_ask(key, state, questions)
    a = r["answers"]
    depth = a.get("depth") or {}
    score = depth.get("score")
    levels = len(depth.get("legend") or {}) or len(JEV_DEPTH_LEVELS)
    preset = None
    if isinstance(score, (int, float)):
        idx = int(round(score * (len(JEV_PRESET_LADDER) - 1) / max(levels - 1, 1)))
        preset = JEV_PRESET_LADDER[min(len(JEV_PRESET_LADDER) - 1, max(0, idx))]
    out = {"needs_web": (a.get("needs_web") or {}).get("noul"),
           "preset": preset, "depth_score": score, "depth_confidence": depth.get("confidence"),
           "needs_council": (a.get("needs_council") or {}).get("noul") if follow_up else None}
    out.update(_jev_usage(r))
    return out


def jev_check_answer(key, question, answer, today, web_on):
    """Jev's quality read of one source answer before it reaches the pool."""
    state = {"question": _clip_context_text(question, 4000), "today": today,
             "web_search_was_available": bool(web_on),
             "answer": _clip_context_text(answer, 16000)}
    questions = {
        "verdict": {"type": "choice",
                    "instructions": "`answer` was written by an AI model in reply to "
                                    "`question`. Which description fits it best?",
                    "criteria": JEV_CHECK_VERDICTS},
        "stale": {"type": "noul",
                  "instructions": "Today's date is `today`. Does `answer` treat the current "
                                  "date, recent events, or recent facts as impossible, "
                                  "fictional, or beyond its knowledge, or explicitly limit its "
                                  "information to an earlier year?",
                  "criteria": {"true": "The answer disbelieves or ignores the present and "
                                       "relies on outdated knowledge",
                               "false": "The answer does not do this"}},
    }
    r = jev_ask(key, state, questions)
    v = r["answers"].get("verdict") or {}
    out = {"verdict": v.get("choice"), "confidence": v.get("confidence"),
           "stale": (r["answers"].get("stale") or {}).get("noul")}
    out.update(_jev_usage(r))
    return out


def jev_should_hold(check):
    """Active mode sets an answer aside only on a clear read of a real defect."""
    return bool(check and check.get("verdict") in JEV_HOLD_VERDICTS
                and (check.get("confidence") or 0) >= JEV_HOLD_CONFIDENCE)


def extract_claims(text, limit=JEV_MAX_CLAIMS):
    """Checkable sentences from a synthesized answer, in order. Code finds the
    candidates (Jev judges them, it never writes): body text only -- no
    headings, tables, code, quotes, or the weighing note -- favouring
    sentences that carry numbers, names, or dates."""
    body, _weighing = split_source_weighing(text or "")
    found, seen, in_code = [], set(), False
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not s or s[0] in "#|>":
            continue
        s = re.sub(r"^([-*+•]|\d+[.)])\s+", "", s)
        s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
        s = s.replace("**", "").replace("__", "").replace("`", "")
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])", s):
            sent = sent.strip()
            if 40 <= len(sent) <= 400 and not sent.endswith(":") and sent not in seen:
                seen.add(sent)
                found.append(sent)
    def weight(sent):
        caps = len(re.findall(r"\s[A-Z][a-zA-Z]+", sent))
        return 2 * bool(re.search(r"\d", sent)) + min(caps, 3) + ("\"" in sent or "“" in sent)
    if len(found) > limit:
        keep = set(sorted(range(len(found)), key=lambda i: (-weight(found[i]), i))[:limit])
        found = [s for i, s in enumerate(found) if i in keep]
    return found


def jev_claim_check(key, question, draft, answers, legend):
    """Each checkable claim in the draft against the anonymized evidence pool.
    `clean` is True only when no claim looks contradicted and few rest on no
    source at all -- the condition under which Active mode skips the audit."""
    claims = extract_claims(draft)
    if len(claims) < 3:
        return {"claims": [], "clean": None, "note": "too few checkable claims"}
    inverse = {provider: letter for letter, provider in (legend or {}).items()}
    items = sorted(answers.items(), key=lambda kv: inverse.get(kv[0], kv[0]))
    per = max(1500, JEV_STATE_CHAR_BUDGET // max(1, len(items)))
    state = {"question": _clip_context_text(question, 3000),
             "answers": {f"Answer {inverse.get(p, p)}": _clip_context_text(t, per)
                         for p, t in items}}
    questions = {
        f"c{i}": {"type": "choice",
                  "instructions": f"A draft answer to `question` makes this claim: «{c}» "
                                  "Compare it with the independent answers in `answers`. How do "
                                  "those answers bear on the claim?",
                  "criteria": JEV_CLAIM_CRITERIA}
        for i, c in enumerate(claims)}
    r = jev_ask(key, state, questions, timeout=40.0)
    rows = []
    for i, c in enumerate(claims):
        a = r["answers"].get(f"c{i}") or {}
        probs = a.get("probabilities") or {}
        rows.append({"claim": c, "verdict": a.get("choice"), "confidence": a.get("confidence"),
                     "p_contradicted": float(probs.get("contradicted", 0.0) or 0.0)})
    counts = {k: sum(1 for x in rows if x["verdict"] == k) for k in JEV_CLAIM_CRITERIA}
    flagged = [x for x in rows if x["p_contradicted"] >= JEV_CONTRADICT_FLAG
               or x["verdict"] == "contradicted" or x["verdict"] is None]
    clean = (not flagged) and counts["unaddressed"] <= JEV_UNADDRESSED_SHARE * len(rows)
    out = {"claims": rows, "clean": clean, "flagged": len(flagged), "counts": counts}
    out.update(_jev_usage(r))
    return out


# ---- Cerebras live draft ---------------------------------------------------
def cerebras_preview(key, question, arrived, n_total, today, file_names=(),
                     model=CEREBRAS_PREVIEW_MODEL):
    """One fast preview. With no answers yet it is a quick first take; after
    that, where the arrived answers are converging. Never seen by the
    Synthesizer."""
    client = OpenAI(api_key=key, base_url=CEREBRAS_URL, timeout=30.0, max_retries=0)
    note = ""
    if file_names:
        note = (f"\n\n(The user attached {', '.join(file_names)}. You cannot see attachments; "
                "do not guess what they contain.)")
    if not arrived:
        system = ("You give the user a fast first take while a council of stronger AI models "
                  "works on their question. In at most 140 words, sketch where a good answer "
                  "is likely to go. Plain prose or a few short bullets. Do not pretend to "
                  "certainty and do not mention being a preview.")
        user = f"{today}\n\nQuestion: {question}{note}"
    else:
        per = min(PREVIEW_ANSWER_CHARS, max(600, PREVIEW_TOTAL_CHARS // len(arrived)))
        blocks = "\n\n".join(f"Answer {i + 1}:\n{_clip_context_text(t, per)}"
                             for i, t in enumerate(arrived))
        system = ("Independent AI models are answering the user's question; some answers are "
                  "in. Write a short working draft (at most 230 words) of where the answer is "
                  "heading: what the answers agree on, any real disagreement, and what is still "
                  "open. Use two or three bold mini-headings with short bullets. Never name "
                  "models or answer numbers. Do not mention being a preview.")
        user = (f"{today}\n\nQuestion: {question}{note}\n\n{len(arrived)} of {n_total} answers "
                f"so far (excerpts):\n\n{blocks}")
    t0 = time.perf_counter()
    kw = {"model": model, "max_completion_tokens": PREVIEW_MAX_TOKENS,
          "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    try:
        r = client.chat.completions.create(reasoning_effort="low", **kw)
    except Exception as e:
        if "reasoning" not in str(e).lower():
            raise
        r = client.chat.completions.create(**kw)
    text = _content_text(r.choices[0].message.content).strip()
    usage = getattr(r, "usage", None)
    itok = getattr(usage, "prompt_tokens", None)
    otok = getattr(usage, "completion_tokens", None)
    return {"text": text, "secs": time.perf_counter() - t0, "in_tok": itok, "out_tok": otok,
            "cost": cost_of(model, itok, otok), "model": model}


def _preview_loop(reg, job_id, plan):
    """Background preview: a first take at once, then a refresh each time the
    arrived answers cross a quarter of the expected count, at most
    PREVIEW_MAX_CALLS calls. Stops when the real answer starts streaming, the
    run ends, or synthesis has begun and nothing new arrived."""
    pv = plan["preview"]
    total = max(1, int(pv.get("expected") or 1))
    thresholds = sorted({max(1, -(-total * k // 4)) for k in (1, 2, 3, 4)})
    calls, last_n = 0, -1
    while calls < PREVIEW_MAX_CALLS:
        with reg["lock"]:
            job = reg["jobs"].get(job_id)
            if job is None or job.get("status") != "running" or job.get("cancel_requested"):
                return
            arrived = list((job.get("arrived") or {}).values())
            phase = job.get("phase", 1)
            streaming = any(job.get("stream", {}).values())
        if streaming:
            return
        n = len(arrived)
        nxt = next((t for t in thresholds if t > max(last_n, 0)), None)
        due = calls == 0 or (n > last_n and ((nxt is not None and n >= nxt) or phase >= 2))
        if not due:
            if phase >= 2:
                return
            time.sleep(0.4)
            continue
        try:
            r = cerebras_preview(pv["key"], plan.get("prompt", ""), arrived, total,
                                 pv.get("today", ""), pv.get("files") or ())
        except Exception as e:
            _bg_update(reg, job_id, preview_error=f"{type(e).__name__}: {str(e)[:160]}")
            return
        calls, last_n = calls + 1, n
        with reg["lock"]:
            job = reg["jobs"].get(job_id)
            if job is None:
                return
            if r["text"]:
                job["preview"] = {"text": r["text"], "after": n, "of": total, "at": time.time()}
            job.setdefault("preview_calls", []).append(
                {k: r.get(k) for k in ("secs", "cost", "in_tok", "out_tok", "model")})
        if phase >= 2:
            return


# ---- report cards ----------------------------------------------------------
def _median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def model_report_stats(entries):
    """Measured source behaviour from saved chats, keyed by (provider,
    model_id, settings) -- n answers, failures, late arrivals, median time
    (with and without web), median cost. Missing-key errors are not counted
    as model failures."""
    raw = {}
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        for provider, msgs in (e.get("threads") or {}).items():
            for m in msgs or []:
                if not (isinstance(m, dict) and m.get("role") == "assistant"):
                    continue
                if str(m.get("content") or "").startswith("No key for"):
                    continue
                k = (provider, m.get("model_id") or "", m.get("settings") or "")
                s = raw.setdefault(k, {"n": 0, "fail": 0, "late": 0, "secs": [],
                                       "secs_web": [], "cost": []})
                s["n"] += 1
                if m.get("late") or m.get("dropped"):
                    s["late"] += 1
                if m.get("err"):
                    if not m.get("dropped"):
                        s["fail"] += 1
                    continue
                if m.get("web_fallback"):
                    continue
                (s["secs_web"] if m.get("web") else s["secs"]).append(float(m.get("secs") or 0))
                if m.get("cost") is not None:
                    s["cost"].append(float(m["cost"]))
    out = {}
    for k, s in raw.items():
        out[k] = {"n": s["n"], "fail": s["fail"], "late": s["late"],
                  "secs": _median(s["secs"]), "secs_web": _median(s["secs_web"]),
                  "cost": _median(s["cost"])}
    return out


def seat_report_stats(entries):
    raw = {}
    jev = {"claim_checks": 0, "compared": 0, "agreed": 0, "would_hold": 0,
           "answers_read": 0, "flagged_answers": 0}
    helpers = {}

    def add(key, m):
        if not isinstance(m, dict):
            return
        rec_ = raw.setdefault(key, {"n": 0, "fail": 0, "secs": [], "cost": []})
        rec_["n"] += 1
        if m.get("err"):
            rec_["fail"] += 1
            return
        rec_["secs"].append(float(m.get("secs") or 0))
        if m.get("cost") is not None:
            rec_["cost"].append(float(m["cost"]))

    for e in entries or []:
        if not isinstance(e, dict):
            continue
        for msgs in (e.get("threads") or {}).values():
            for m in msgs or []:
                jv = m.get("jev") if isinstance(m, dict) else None
                if isinstance(jv, dict) and jv.get("verdict"):
                    jev["answers_read"] += 1
                    jev["flagged_answers"] += jv["verdict"] != "answers"
        rec = e.get("synth") if isinstance(e.get("synth"), dict) else {}
        turns = {t.get("turn_id"): t for t in rec.get("turns") or [] if isinstance(t, dict)}
        for m in rec.get("thread") or []:
            if not (isinstance(m, dict) and m.get("role") == "assistant"):
                continue
            prov = (turns.get(m.get("turn_id"), {}).get("evaluator_provider")
                    or rec.get("provider") or "?")
            role = "Synthesizer (solo reply)" if m.get("solo") else "Synthesizer"
            first = m.get("pre_revision") if m.get("revised") else m
            add((role, prov, (first or {}).get("model_id") or m.get("model_id") or ""), first)
            adj = m.get("adjudicator")
            if isinstance(adj, dict):
                add(("Adjudicator", adj.get("provider") or "?", adj.get("model_id") or ""), adj)
            for h in m.get("helpers") or []:
                agg = helpers.setdefault(h.get("name", "Helper"), {"runs": 0, "cost": 0.0})
                agg["runs"] += 1
                agg["cost"] += float(h.get("cost") or 0.0)
            jv = m.get("jev")
            if isinstance(jv, dict):
                jev["would_hold"] += len(jv.get("would_hold") or jv.get("held_out") or [])
                claims = jv.get("claims") or {}
                if claims.get("clean") is not None:
                    jev["claim_checks"] += 1
                    verdict = adj.get("material_defects") if isinstance(adj, dict) else None
                    if verdict is not None:
                        jev["compared"] += 1
                        jev["agreed"] += (claims["clean"] is True) == (verdict is False)
    seats = {k: {"n": v["n"], "fail": v["fail"], "secs": _median(v["secs"]),
                 "cost": _median(v["cost"])} for k, v in raw.items()}
    return seats, jev, helpers


def report_card_for(stats, provider, model_id, settings=""):
    """The stats line for one card: this exact setup when it has history,
    else the model across all its settings."""
    exact = stats.get((provider, model_id, settings))
    if exact and exact["n"]:
        return dict(exact, scope="this setup")
    rows = [v for (p, mid, _s), v in stats.items() if p == provider and mid == model_id]
    if not rows:
        return None
    n = sum(r["n"] for r in rows)
    weighted = lambda key: _median([r[key] for r in rows for _ in range(max(1, r["n"]))
                                    if r[key] is not None])
    return {"n": n, "fail": sum(r["fail"] for r in rows), "late": sum(r["late"] for r in rows),
            "secs": weighted("secs"), "secs_web": weighted("secs_web"),
            "cost": weighted("cost"), "scope": "all settings"}


def report_card_text(card):
    if not card or not card.get("n"):
        return "📊 No saved runs with this model yet."
    bits = [f"{card['n']} answer{'s' if card['n'] != 1 else ''} ({card['scope']})"]
    if card.get("secs") is not None:
        bits.append(f"typical {card['secs']:.0f}s")
    if card.get("secs_web") is not None:
        bits.append(f"{card['secs_web']:.0f}s with web")
    if card.get("cost") is not None:
        bits.append(f"~${card['cost']:.4f}")
    if card.get("fail"):
        bits.append(f"{100.0 * card['fail'] / card['n']:.0f}% failed")
    if card.get("late"):
        bits.append(f"{card['late']} late")
    return "📊 Your runs: " + " · ".join(bits)


def run_parallel(jobs, label):
    start_t = time.perf_counter()
    status = st.status(f"{label}…", expanded=True)
    prog = status.empty()
    done_lines = []
    results = {}
    with ThreadPoolExecutor() as ex:
        fut_name = {ex.submit(run_one, j.get("provider", n), j["key"], j["messages"], j["cfg"], j["web"]): n
                    for n, j in jobs.items()}
        pending = set(fut_name)
        while pending:
            completed, pending = wait(pending, timeout=0.4, return_when=FIRST_COMPLETED)
            for fut in completed:
                n = fut_name[fut]; results[n] = fut.result(); r = results[n]
                prov = r.get("provider", n)
                if r.get("credit_err"):
                    st.session_state[f"quota_err_{prov}"] = True
                elif not r.get("err"):
                    st.session_state.pop(f"quota_err_{prov}", None)
                badge = (" \U0001F310" if r.get("web") else "") + \
                        (" 🔎" if jobs[n].get("pages") else "")
                warn = " ⚠️\U0001F4B3" if r.get("credit_err") else (" ⚠️" if r.get("err") else "")
                done_lines.append(f"✅ {ICON.get(n,'')} {n}{badge} — {r['secs']:.1f}s{warn}")
            elapsed = time.perf_counter() - start_t
            waiting = [n for n in jobs if n not in results]
            body = "  \n".join(done_lines)
            if waiting:
                body += ("  \n" if done_lines else "") + "⏳ " + ", ".join(waiting) + " — thinking…"
            prog.markdown(body)
            status.update(label=f"{label} ⏱ {elapsed:.1f}s · {len(results)}/{len(jobs)} done", expanded=True)
    total_t = time.perf_counter() - start_t
    status.update(label=f"✅ {label} — done in {total_t:.1f}s", state="complete", expanded=False)
    return results

def _bg_reg():
    """MAIN THREAD ONLY. The rerun-surviving job registry: {"lock":…, "jobs":{}}."""
    reg = st.session_state.get("_bg_reg")
    if reg is None:
        reg = {"lock": threading.Lock(), "jobs": {}}
        st.session_state["_bg_reg"] = reg
    return reg

def _bg_get(job_id):
    reg = _bg_reg()
    with reg["lock"]:
        j = reg["jobs"].get(job_id)
        if not j:
            return None
        snapshot = dict(j)
        snapshot["progress"] = [dict(item) for item in j.get("progress", [])]
        snapshot["stream"] = {k: "".join(v) for k, v in (j.get("stream") or {}).items()}
        if j.get("preview"):
            snapshot["preview"] = dict(j["preview"])
        return snapshot                       # caller never mutates live state

def _bg_update(reg, job_id, **kw):
    """Called from the worker thread — takes the registry as an argument rather
    than reaching for session_state, which a non-script thread must never do."""
    with reg["lock"]:
        job = reg["jobs"].get(job_id)
        if job is None:
            return
        one = kw.pop("done_one", None)
        if one is not None:
            nm, r = one
            job["progress"].append({"name": nm, "secs": r.get("secs", 0.0),
                                    "cost": r.get("cost"),
                                    "err": bool(r.get("err")),
                                    "credit": bool(r.get("credit_err")),
                                    "web": bool(r.get("web"))})
        job.update(kw)

def _bg_cancel_requested(reg, job_id):
    """Worker-safe cancellation check. A missing job means its result is unwanted."""
    with reg["lock"]:
        job = reg["jobs"].get(job_id)
        return job is None or bool(job.get("cancel_requested"))

def _bg_mark_cancelled(reg, job_id):
    _bg_update(reg, job_id, status="cancelled", cancelled_at=time.time())

def _bg_worker(reg, job_id, plan):
    executors = []
    t_start = time.time()
    srcs = plan["sources"]
    results, arrived = {}, {}
    memos = {}
    memo_jobs = {k: plan[k] for k in ("synth_memo", "adj_memo") if plan.get(k)}
    stragglers = {}                       # future -> (kind, name) after synthesis began
    helper_futs = {}                      # future -> (kind, name): Jev calls
    jev = plan.get("jev") or None
    jev_out = {"mode": (jev or {}).get("mode"), "checks": {}, "router": None,
               "claims": None, "calls": [], "errors": [], "held_out": [],
               "hold_skipped": [], "would_hold": [], "audit_skipped": False}
    helper_pool = None
    good = {}
    router_fut = [None]

    def _shutdown_all():
        for ex in executors:
            ex.shutdown(wait=False, cancel_futures=True)
        executors.clear()

    def _cancelled():
        if _bg_cancel_requested(reg, job_id):
            _shutdown_all()
            _bg_mark_cancelled(reg, job_id)
            return True
        return False

    def _job_of(kind, n):
        return memo_jobs[n] if kind == "memo" else srcs[n]

    def _jev_snapshot():
        return {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                for k, v in jev_out.items()}

    def _record(fut, kind, n, late=False):
        job = _job_of(kind, n)
        try:
            r = fut.result()
        except Exception as e:                  # belt & braces: run_one already catches
            r = {"text": f"Error from {n}: {e}", "secs": 0.0,
                 "in_tok": None, "out_tok": None, "cost": None,
                 "web": job["web"], "provider": job.get("provider", n),
                 "credit_err": False, "err": True}
        if late:
            r = dict(r)
            r["late"] = True
        ok = not r.get("err") and str(r.get("text") or "").strip()
        extra = {}
        if ok and not late:
            arrived[n] = r["text"]
            extra["arrived"] = dict(arrived)
        if kind == "memo":
            memos[n] = r
            label = "Synthesizer memo" if n == "synth_memo" else "Adjudicator memo"
        else:
            results[n] = r
            label = n
        _bg_update(reg, job_id, done_one=(label, r), **extra)
        if ok and not late and jev and helper_pool is not None:
            f = helper_pool.submit(jev_check_answer, jev["key"], plan.get("prompt", ""),
                                   r["text"], jev.get("today", ""), bool(job.get("web")))
            helper_futs[f] = ("check", n)

    def _take_helpers(done):
        changed = False
        for f in done:
            if f not in helper_futs:
                continue
            kind, n = helper_futs.pop(f)
            changed = True
            try:
                res = f.result()
            except Exception as e:
                jev_out["errors"].append(f"{kind}: {str(e)[:160]}")
                continue
            jev_out["calls"].append({k: res.get(k) for k in ("secs", "cost", "in_tok",
                                                               "out_tok", "model")})
            if kind == "check":
                jev_out["checks"][n] = {k: res.get(k) for k in ("verdict", "confidence", "stale")}
            elif kind == "router":
                jev_out["router"] = {k: res.get(k) for k in ("needs_web", "preset", "depth_score",
                                                             "depth_confidence", "needs_council")}
            elif kind == "claims":
                jev_out["claims"] = {k: res.get(k) for k in ("claims", "clean", "flagged",
                                                             "counts", "note")}
        if changed:
            _bg_update(reg, job_id, jev=_jev_snapshot())

    def _wait_for(future):
        """Wait for one call while still recording late sources and Jev
        results as they land. False when the run was stopped."""
        while True:
            if _cancelled():
                return False
            watch = {future} | set(stragglers) | set(helper_futs)
            done, _ = wait(watch, timeout=0.2, return_when=FIRST_COMPLETED)
            for f in done:
                if f in stragglers:
                    kind, n = stragglers.pop(f)
                    _record(f, kind, n, late=True)
            _take_helpers(done)
            if future.done():
                return True

    def _gather_fresh(pack_names):
        fresh = plan.get("fresh") or {}
        if not fresh.get("keys"):
            return set(), ""
        mode = fresh.get("mode", "web")
        want = {n for n in pack_names if srcs[n].get("pack_web")}
        reason = "web search is on" if want else "web search is off"
        if mode == "always":
            want, reason = set(pack_names), "always on"
        elif mode == "jev" and len(want) < len(pack_names) and router_fut[0] is not None:
            until = time.time() + JEV_ROUTE_WAIT_S
            while not router_fut[0].done() and time.time() < until:
                if _cancelled():
                    return None
                wait([router_fut[0]], timeout=0.2)
            if router_fut[0].done():
                _take_helpers([router_fut[0]])
            nw = (jev_out.get("router") or {}).get("needs_web")
            if isinstance(nw, (int, float)) and nw >= JEV_FRESH_THRESHOLD:
                want, reason = set(pack_names), f"Jev: current facts needed ({nw:.2f})"
            elif not want:
                reason = (f"Jev: not needed ({nw:.2f})" if isinstance(nw, (int, float))
                          else "Jev had no read in time")
        if not want:
            _bg_update(reg, job_id, fresh={"status": "skipped", "reason": reason})
            return set(), ""
        _bg_update(reg, job_id, fresh={"status": "searching", "reason": reason,
                                       "for": sorted(want)})
        ex = ThreadPoolExecutor(max_workers=1)
        executors.append(ex)
        fut = ex.submit(fetch_fresh_sources, fresh["keys"], fresh.get("query", ""),
                        fresh.get("depth", "advanced"))
        while not fut.done():
            if _cancelled():
                return None
            wait([fut], timeout=0.2)
        ex.shutdown(wait=False)
        executors.remove(ex)
        try:
            pack = fut.result()
        except Exception as e:                  # fetch_fresh_sources never raises; belt & braces
            pack = {"service": None, "results": [], "secs": 0.0, "errors": [str(e)[:160]],
                    "calls": []}
        ok = bool(pack.get("results"))
        info = {"status": "done" if ok else "failed", "reason": reason,
                "service": pack.get("service"), "n": len(pack.get("results") or []),
                "secs": pack.get("secs") or max([c.get("secs") or 0
                                                 for c in pack.get("calls") or []] or [0]),
                "errors": list(pack.get("errors") or []),
                "counts": dict(pack.get("counts") or {}), "both": pack.get("both", 0),
                "results": [{k: g.get(k, "") for k in ("title", "url", "site", "date",
                                                       "found_by", "text")}
                            for g in fresh_pages_given(pack)],
                "found": len(pack.get("results") or []),
                "for": sorted(want) if ok else []}
        _bg_update(reg, job_id, fresh=info, fresh_calls=list(pack.get("calls") or []))
        return (want, format_fresh_sources(pack)) if ok else (set(), "")

    def _call(slot, p, messages):
        """One sequential evaluator-phase call; streamed into job["stream"]
        [slot] when the provider supports it. None when the run was stopped."""
        ex = ThreadPoolExecutor(max_workers=1)
        executors.append(ex)
        if slot and p.get("provider") in (plan.get("stream") or ()):
            fut = ex.submit(_run_streaming, reg, job_id, slot, p["provider"], p["key"],
                            messages, p["cfg"], p["web"])
        else:
            fut = ex.submit(run_one, p["provider"], p["key"], messages, p["cfg"], p["web"])
        if not _wait_for(fut):
            return None
        ex.shutdown(wait=True)
        executors.remove(ex)
        return fut.result()

    try:
        if jev:
            helper_pool = ThreadPoolExecutor(max_workers=4)
            executors.append(helper_pool)
            f = helper_pool.submit(jev_route, jev["key"], plan.get("prompt", ""),
                                   bool(jev.get("follow_up")), jev.get("previous_question", ""),
                                   bool(jev.get("has_files")))
            helper_futs[f] = ("router", None)
            router_fut[0] = f
        if plan.get("preview"):
            threading.Thread(target=_preview_loop, args=(reg, job_id, plan), daemon=True).start()

        if srcs or memo_jobs:
            src_ex = ThreadPoolExecutor(max_workers=max(1, len(srcs) + len(memo_jobs)))
            executors.append(src_ex)
            pack_names = [n for n, j in srcs.items() if j.get("pack")]
            futs = {src_ex.submit(run_one, j["provider"], j["key"], j["messages"],
                                  j["cfg"], j["web"]): ("source", n)
                    for n, j in srcs.items() if n not in pack_names}
            futs.update({src_ex.submit(run_one, j["provider"], j["key"], j["messages"],
                                       j["cfg"], j["web"]): ("memo", n)
                        for n, j in memo_jobs.items()})
            if pack_names:
                gathered = _gather_fresh(pack_names)
                if gathered is None:
                    return
                given, fresh_text = gathered
                for n in pack_names:
                    j = srcs[n]
                    msgs = (_with_fresh_sources(j["messages"], fresh_text)
                            if n in given else j["messages"])
                    futs[src_ex.submit(run_one, j["provider"], j["key"], msgs,
                                       j["cfg"], j["web"])] = ("source", n)
            p2_plan = plan.get("phase2") or {}
            quorum = plan.get("quorum") if p2_plan and p2_plan.get("kind") != "solo" else None
            quorum_at = None
            pending = set(futs)
            while pending:
                if _cancelled():
                    return
                completed, pending = wait(pending, timeout=0.2,
                                          return_when=FIRST_COMPLETED)
                for fut in completed:
                    kind, n = futs[fut]
                    _record(fut, kind, n)
                _take_helpers([f for f in list(helper_futs) if f.done()])
                if pending and quorum:
                    go, quorum_at = quorum_ready(time.time(), t_start, len(futs), len(arrived),
                                                 p2_plan.get("min_good", 1), quorum_at, quorum)
                    if go:
                        for f in pending:
                            stragglers[f] = futs[f]
                        pending = set()
            executors.remove(src_ex)
            # Stragglers keep running on their own threads; never block on them.
            src_ex.shutdown(wait=not stragglers)
            if stragglers:
                _bg_update(reg, job_id, early_start=True,
                           late_names=[_job_of(k, n).get("provider", n) if k == "memo" else n
                                       for k, n in stragglers.values()])

        if _cancelled():
            return
        _bg_update(reg, job_id, phase1=dict(results), phase=2, memos=dict(memos),
                  phase1_done_at=time.time())

        # Phase 2 is assembled HERE (not on the main thread) because it needs the
        # Phase-1 answers. build_synth_prompt / build_research_prompt are pure.
        p2, out2, out3, out4 = plan.get("phase2"), None, None, None
        good = {n: r["text"] for n, r in results.items()
                if not r.get("err") and str(r["text"] or "").strip()}
        for r in memos.values():
            if not r.get("err") and str(r["text"] or "").strip():
                good[r["provider"]] = r["text"]
        min_good = (p2 or {}).get("min_good", 1)
        if jev and jev.get("mode") == "active":
            until = time.time() + 4.0
            while (any(kind == "check" for kind, _n in helper_futs.values())
                   and time.time() < until):
                if _cancelled():
                    return
                done, _ = wait(set(helper_futs), timeout=0.2, return_when=FIRST_COMPLETED)
                _take_helpers(done)
            hold = []
            for ck, check in jev_out["checks"].items():
                pk = memos[ck]["provider"] if ck in memos else ck
                if jev_should_hold(check) and pk in good:
                    hold.append(pk)
            if hold and len(good) - len(hold) >= max(min_good, 1):
                for pk in hold:
                    good.pop(pk, None)
                jev_out["held_out"] = hold
            elif hold:
                jev_out["hold_skipped"] = hold
            _bg_update(reg, job_id, jev=_jev_snapshot())
        if p2 and len(good) >= p2.get("min_good", 1):
            if p2["kind"] == "research":
                body, legend = build_research_prompt(
                    plan["prompt"], good, p2["web"], plan.get("context", ""))
            elif p2["kind"] == "solo":
                body = build_solo_followup_prompt(
                    plan["prompt"], plan.get("context", ""), p2["web"])
                legend = {}
            else:
                body, legend = build_synth_prompt(
                    plan["prompt"], good, p2["web"], plan.get("context", ""),
                    p2.get("preset", "Light"))
            # Sources that answered but are not in the pool (failed, or set
            # aside by Active Jev). Stragglers still running are not named:
            # the Synthesizer has nothing to say about them.
            missing = [n for n in srcs if n in results and n not in good]
            if missing:
                body += ("\n\nNote: answers from " + ", ".join(missing) +
                         " failed and are not included - clearly identify that omission in "
                         "the visible response and work with the successful sources.")
            if _cancelled():
                return
            msg = {"role": "user", "content": body, "files": p2.get("files") or [],
                   "turn_id": plan.get("turn_id", 0)}
            if plan.get("phase4"):
                msg["cache"] = True
            msg["display"] = plan["prompt"]
            r2 = _call("answer", p2, [msg])
            if r2 is None:
                return
            visible, state = split_state_capsule(r2.get("text", ""))
            visible, ledger2 = split_contribution_ledger(visible)
            r2 = dict(r2)
            r2["text"] = visible
            if not r2.get("err") and not visible.strip():
                r2["text"] = (
                    f"Error from {p2['provider']}: the evaluator returned no visible answer."
                )
                r2["err"] = True
            if _cancelled():
                return
            out2 = {"msg": msg, "result": r2, "legend": legend, "state": state,
                    "contributions": parse_contribution_ledger(ledger2, legend)}
            _bg_update(reg, job_id, done_one=(p2["label_key"], r2),
                      phase2_done_at=time.time())

            claim_fut = None
            if (jev and helper_pool is not None and p2["kind"] == "synth"
                    and not r2.get("err") and len(good) >= 1):
                claim_fut = helper_pool.submit(jev_claim_check, jev["key"], plan["prompt"],
                                               visible, good, legend)
                helper_futs[claim_fut] = ("claims", None)
            p3 = plan.get("phase3")
            skip_audit = False
            if p3 and not r2.get("err") and claim_fut is not None and jev.get("mode") == "active":
                if not _wait_for(claim_fut):
                    return
                if (jev_out.get("claims") or {}).get("clean") is True:
                    skip_audit = True
                    jev_out["audit_skipped"] = True
                    _bg_update(reg, job_id, audit_skipped=True, jev=_jev_snapshot())

            if p3 and not r2.get("err") and not skip_audit:
                body3 = build_adjudicator_prompt(
                    plan["prompt"], visible, good, legend, p3.get("preset", "Light"))
                msg3 = {"role": "user", "content": body3,
                        "turn_id": plan.get("turn_id", 0)}
                r3 = _call(None, p3, [msg3])
                if r3 is None:
                    return
                report, material_defects = split_adjudicator_verdict(r3.get("text", ""))
                r3 = dict(r3)
                r3["text"] = report
                if not r3.get("err") and not report.strip():
                    r3["text"] = (
                        f"Error from {p3['provider']}: the Adjudicator returned no visible report."
                    )
                    r3["err"] = True
                if _cancelled():
                    return
                out3 = {"msg": msg3, "result": r3, "material_defects": material_defects}
                # material_defects is surfaced onto the job immediately so
                # _bg_progress_rows can show Phase 4 as active vs. honestly
                # skipped while the run is still in progress.
                _bg_update(reg, job_id, done_one=(p3["label_key"], r3),
                          material_defects=material_defects, phase3_done_at=time.time())

                p4 = plan.get("phase4")
                if p4 and material_defects is True:
                    if _cancelled():
                        return
                    body4 = build_disposition_prompt(
                        plan["prompt"], visible, report, good, legend,
                        p4.get("preset", "Light"), follow_on=True)
                    msg4 = {"role": "user", "content": body4,
                            "turn_id": plan.get("turn_id", 0)}
                    first = _with_today([msg])[0]
                    r4 = _call("revision", p4,
                               [first, {"role": "assistant", "content": visible}, msg4])
                    if r4 is None:
                        return
                    visible4, state4 = split_state_capsule(r4.get("text", ""))
                    visible4, ledger4 = split_contribution_ledger(visible4)
                    r4 = dict(r4)
                    r4["text"] = visible4
                    if not r4.get("err") and not visible4.strip():
                        r4["text"] = (
                            f"Error from {p4['provider']}: the revision returned no visible answer."
                        )
                        r4["err"] = True
                    if _cancelled():
                        return
                    out4 = {"msg": msg4, "result": r4, "state": state4,
                            "contributions": parse_contribution_ledger(ledger4, legend)}
                    _bg_update(reg, job_id, done_one=(p4.get("label_key", "Revision"), r4))
        if _cancelled():
            return
        # Quick Jev calls still in flight (a shadow claim check, a late
        # answer check) get a short window to land; nothing waits longer.
        until = time.time() + 10.0
        while helper_futs and time.time() < until:
            if _cancelled():
                return
            done, _ = wait(set(helper_futs), timeout=0.3, return_when=FIRST_COMPLETED)
            _take_helpers(done)
        for f in [f for f in stragglers if f.done()]:
            kind, n = stragglers.pop(f)
            _record(f, kind, n, late=True)
        for f, (kind, n) in list(stragglers.items()):
            job = _job_of(kind, n)
            who = job.get("provider", n)
            dropped = {"text": (f"{who} was still answering when the final answer was ready, "
                                "so Conclave stopped waiting for it. The provider may still "
                                "finish and bill this request."),
                       "secs": time.time() - t_start, "in_tok": None, "out_tok": None,
                       "cost": None, "web": job["web"], "provider": who,
                       "credit_err": False, "err": True, "late": True, "dropped": True}
            if kind == "memo":
                memos[n] = dropped
            else:
                results[n] = dropped
        stragglers.clear()
        if jev:
            jev_out["would_hold"] = [n for n, c in jev_out["checks"].items()
                                     if jev_should_hold(c)]
        if helper_pool is not None:
            helper_pool.shutdown(wait=False)
        _bg_update(reg, job_id, status="done", phase1=results, memos=memos,
                  phase2=out2, phase3=out3, phase4=out4, good_n=len(good),
                  finished=time.time(), jev=_jev_snapshot() if jev else None)
    except Exception as e:
        _shutdown_all()
        _bg_update(reg, job_id, status="error", error=f"{type(e).__name__}: {e}")


def build_speed_plan(plan):
    extra = {"stream": list(STREAM_PROVIDERS)}
    p2 = plan.get("phase2") or {}
    if (p2 and p2.get("kind") != "solo"
            and st.session_state.get("k_quorum_mode", QUORUM_MODES[0]) == QUORUM_MODES[0]):
        extra["quorum"] = {"enabled": True, "fraction": QUORUM_FRACTION,
                           "deadline": SOURCE_DEADLINES.get(current_preset_name(), 300)}
    keys = load_keys()
    expected = (len(plan.get("sources") or {})
                + sum(1 for k in ("synth_memo", "adj_memo") if plan.get(k)))
    files = [f.get("name", "file") for f in ((plan.get("user_msg") or {}).get("files") or [])]
    today = _today_line()
    if (keys.get(HELPER_KEYS["Cerebras"]) and st.session_state.get("k_live_preview", True)
            and p2.get("kind") in ("synth", "research") and expected):
        extra["preview"] = {"key": keys[HELPER_KEYS["Cerebras"]], "expected": expected,
                            "files": files, "today": today}
    jmode = st.session_state.get("k_jev_mode", JEV_MODES[1])
    previous = ""
    if plan.get("continuation"):
        record = st.session_state.get("research" if plan.get("mode") == "research"
                                      else "synth") or {}
        turns = record.get("turns") or []
        previous = turns[-1].get("question", "") if turns else ""
    if keys.get(HELPER_KEYS["Jev"]) and jmode != "Off":
        extra["jev"] = {"key": keys[HELPER_KEYS["Jev"]],
                        "mode": "active" if jmode == "Active" else "shadow",
                        "follow_up": bool(plan.get("continuation")),
                        "previous_question": previous, "has_files": bool(files),
                        "today": today}
    search_keys = search_keys_now(keys)
    if search_keys and any(j.get("pack") for j in (plan.get("sources") or {}).values()):
        mode = FRESH_MODE_CODES.get(st.session_state.get("k_fresh_mode"), "jev")
        if mode == "jev" and "jev" not in extra:
            mode = "web"                 # no Jev this run: fall back to the web switch
        extra["fresh"] = {"keys": search_keys, "mode": mode,
                          "depth": TAVILY_DEPTHS.get(st.session_state.get("k_tavily_depth"),
                                                     "advanced"),
                          "query": search_query_for(plan.get("prompt", ""), previous)}
    return extra


def bg_start(plan):
    """Launch a run in the background and remember its id in session_state."""
    reg = _bg_reg()
    try:
        plan = {**plan, **build_speed_plan(plan)}
    except Exception:
        pass                    # a helper problem must never block a run
    job_id = f"{plan['mode']}-{time.time():.6f}"
    with reg["lock"]:
        reg["jobs"][job_id] = {"status": "running", "plan": plan, "phase": 1,
                               "progress": [], "phase1": {}, "phase2": None,
                               "started": time.time(), "error": None, "good_n": 0,
                               "cancel_requested": False}
    # daemon=True so a stray run can never hold the process open on shutdown.
    threading.Thread(target=_bg_worker, args=(reg, job_id, plan), daemon=True).start()
    st.session_state["bg_job_id"] = job_id
    return job_id

def bg_active():
    """True while a run is in flight (used to disable Send and show progress)."""
    jid = st.session_state.get("bg_job_id")
    if not jid:
        return False
    job = _bg_get(jid)
    return bool(job and job["status"] == "running")

def bg_request_stop():
    """MAIN-THREAD callback: ask the worker to stop at its next safe boundary."""
    jid = st.session_state.get("bg_job_id")
    if not jid:
        return
    reg = _bg_reg()
    with reg["lock"]:
        job = reg["jobs"].get(jid)
        if job and job.get("status") == "running":
            job["cancel_requested"] = True
            job["cancel_requested_at"] = time.time()

def commit_result_accounting(results, fallback_to_label=False, provider_fallback=None):
    """MAIN THREAD ONLY: account for one group without merging phase labels."""
    for label, result in results.items():
        provider = result.get("provider", label if fallback_to_label else provider_fallback)
        if not provider:
            continue
        if result.get("credit_err"):
            st.session_state[f"quota_err_{provider}"] = True
        elif not result.get("err"):
            st.session_state.pop(f"quota_err_{provider}", None)
    add_cost(results)


def commit_background_phase(job, plan, phase):
    """Commit a completed evaluator phase once the job is claimed by the UI."""
    output, config = job.get(phase), plan.get(phase)
    if output and config:
        commit_result_accounting({config["label_key"]: output["result"]},
                                 provider_fallback=config["provider"])
    return output, config


def bg_commit_if_done():
    """MAIN THREAD ONLY: commit a completed initial or continuation council run."""
    jid = st.session_state.get("bg_job_id")
    if not jid:
        return
    job = _bg_get(jid)
    if job is None:
        st.session_state.pop("bg_job_id", None)
        return
    if job["status"] == "running":
        return
    reg = _bg_reg()
    with reg["lock"]:
        reg["jobs"].pop(jid, None)
    st.session_state.pop("bg_job_id", None)

    plan = job["plan"]
    mode = plan["mode"]
    restore_key = plan.get("restore_form_key")
    if job["status"] in ("cancelled", "error"):
        try:
            _spent = live_cost_so_far(job)["total"]
            if _spent:
                add_cost({"_stopped_run": {"cost": _spent}})
        except Exception:
            pass
    if job["status"] == "cancelled":
        if restore_key:
            st.session_state[f"_pending_{restore_key}"] = plan.get("prompt", "")
        st.session_state[f"_bg_warn_{mode}"] = (
            "Run stopped. No further Conclave work will start, and completed results were "
            "discarded. A provider request that was already accepted may still finish and "
            "be billed. Your prompt is ready to edit and resend."
        )
        return
    if job["status"] == "error":
        if restore_key:
            st.session_state[f"_pending_{restore_key}"] = plan.get("prompt", "")
        st.session_state[f"_bg_error_{mode}"] = job.get("error") or "unknown error"
        return

    results = job["phase1"]
    meta = plan["src_meta"]
    turn_id = plan.get("turn_id", 0)
    continuation = bool(plan.get("continuation"))
    if continuation:
        _sid_key = "res_session_id" if mode == "research" else "session_id"
        _run_sid = plan.get("session_id")
        if _run_sid and st.session_state.get(_sid_key) != _run_sid:
            _home = next((h for h in (load_history() + load_history_archive())
                          if h.get("ts") == _run_sid
                          and h.get("mode", "synthesis") == mode), None)
            if _home is None:
                st.session_state[f"_bg_error_{mode}"] = (
                    "The chat this answer belongs to could not be found, so the answer "
                    "was not saved. Nothing else was changed.")
                return
            load_session_into_state(_home)
            st.session_state[f"_bg_warn_{mode}"] = (
                "You opened another chat while this answer was being written, so "
                "Conclave switched back to the chat the question was asked in.")
    thread_key = "res_threads" if mode == "research" else "threads"
    threads = st.session_state.get(thread_key, {}) if continuation else {}
    for name in plan["sources"]:
        if name not in results:
            continue
        source_user = dict(plan["user_msg"])
        source_user["turn_id"] = turn_id
        threads.setdefault(name, []).append(source_user)
        _src_msg = assistant_msg(results[name], meta[name]["model_id"],
                                 meta[name]["settings"], turn_id)
        _fresh = job.get("fresh") or {}
        if name in (_fresh.get("for") or []) and _fresh.get("status") == "done":
            _src_msg["fresh_sources"] = _fresh.get("service")
        _check = ((job.get("jev") or {}).get("checks") or {}).get(name)
        if _check:
            _src_msg["jev"] = dict(_check, held=name in ((job.get("jev") or {}).get("held_out") or []))
        threads[name].append(_src_msg)

    # Commit spend/quota in execution order; memos stay separate from Sources.
    commit_result_accounting(results, fallback_to_label=True)
    memos = job.get("memos") or {}
    commit_result_accounting(memos)
    helpers = helper_cost_rows(job)
    if helpers:
        add_cost({h["name"]: h for h in helpers})
    jev_info = job.get("jev") or None
    p2out, p2plan = commit_background_phase(job, plan, "phase2")
    p3out, p3plan = commit_background_phase(job, plan, "phase3")
    p4out, p4plan = commit_background_phase(job, plan, "phase4")

    st.session_state[thread_key] = threads
    record_key = "research" if mode == "research" else "synth"
    record = st.session_state.get(record_key) if continuation else None
    if not continuation:
        if mode == "research":
            st.session_state["res_prompt"] = plan["prompt"]
            st.session_state["res_session_id"] = plan["session_id"]
            st.session_state["res_source_chats"] = {}
            st.session_state["res_source_promotions"] = []
            st.session_state["res_session_pinned"] = False
        else:
            st.session_state["prompt"] = plan["prompt"]
            st.session_state["session_id"] = plan["session_id"]
            st.session_state["source_chats"] = {}
            st.session_state["source_promotions"] = []
            st.session_state["session_pinned"] = False
        st.session_state.pop(record_key, None)

    if p2out and p2plan:
        result2 = p2out["result"]
        previous_memory = (record or {}).get("memory", "")

        revision_ok = bool(p4out and p4plan and not p4out["result"].get("err"))
        final_result = p4out["result"] if revision_ok else result2
        final_plan = p4plan if revision_ok else p2plan
        final_state = (p4out.get("state") if revision_ok else None) or p2out.get("state")
        memory = final_state or fallback_state_capsule(
            previous_memory, plan["prompt"],
            "" if result2.get("err") else result2.get("text", ""),
        )
        evaluator_message = assistant_msg(
            final_result, final_plan["model_id"], final_plan["settings"], turn_id)
        if p2plan.get("kind") == "solo":
            evaluator_message["solo"] = True
        contributions = ((p4out or {}).get("contributions") if revision_ok else None) \
            or p2out.get("contributions") or {}
        if contributions:
            evaluator_message["contributions"] = contributions
        if revision_ok:
            evaluator_message["revised"] = True
            evaluator_message["pre_revision"] = {
                "cost": result2.get("cost"), "secs": result2.get("secs"),
                "in_tok": result2.get("in_tok"), "out_tok": result2.get("out_tok"),
                "model_id": p2plan["model_id"], "settings": p2plan["settings"],
            }
        elif p4out and p4out["result"].get("err"):
            evaluator_message["revision_failed"] = True
            _r4 = p4out["result"]
            evaluator_message["failed_revision"] = {
                "cost": _r4.get("cost"), "secs": _r4.get("secs"),
                "in_tok": _r4.get("in_tok"), "out_tok": _r4.get("out_tok"),
                "model_id": p4plan["model_id"] if p4plan else "",
                "settings": p4plan["settings"] if p4plan else "",
            }
        if job.get("started") and job.get("finished"):
            evaluator_message["turn_secs"] = job["finished"] - job["started"]
        if job.get("started") and job.get("phase1_done_at"):
            evaluator_message["synth_started_after"] = job["phase1_done_at"] - job["started"]
        _late = [n for n, r in results.items() if r.get("late")]
        if _late:
            evaluator_message["late_sources"] = _late
        if helpers:
            evaluator_message["helpers"] = helpers
        if job.get("fresh"):
            evaluator_message["fresh_sources"] = dict(job["fresh"])
        if jev_info:
            evaluator_message["jev"] = {k: v for k, v in jev_info.items() if k != "calls"}
        if p3out and p3plan:
            evaluator_message["adjudicator"] = {
                **assistant_msg(p3out["result"], p3plan["model_id"], p3plan["settings"], turn_id),
                "material_defects": p3out.get("material_defects"),
                "provider": p3plan["provider"],
            }
        if mode == "synthesis":
            eval_threads = st.session_state.get("eval_threads", {}) if continuation else {}
            for memo_key, role_label in (("synth_memo", "Synthesizer"),
                                         ("adj_memo", "Adjudicator")):
                memo_plan = plan.get(memo_key)
                memo_result = memos.get(memo_key)
                if not memo_plan or memo_result is None:
                    continue
                memo_provider = memo_result.get("provider", memo_plan.get("provider"))
                memo_mid = model_id_of(memo_provider, memo_plan.get("cfg", {}))
                memo_settings = settings_str(memo_provider, memo_plan.get("cfg", {}),
                                             memo_result.get("web", False))
                eval_threads.setdefault(role_label, []).append(
                    assistant_msg(memo_result, memo_mid, memo_settings, turn_id))
            st.session_state["eval_threads"] = eval_threads
        turn_detail = {
            "turn_id": turn_id,
            "question": plan["prompt"],
            "source_names": list(plan["sources"]),
            "failed_sources": [n for n, r in results.items()
                               if r.get("err") and not r.get("dropped")],
            "late_sources": [n for n, r in results.items() if r.get("late")],
            "held_out": list((jev_info or {}).get("held_out") or []),
            "legend": p2out["legend"],
            "evaluator_provider": p2plan["provider"],
            "evaluator_model_id": p2plan["model_id"],
            "contributions": contributions,
        }
        if record:
            record.update({
                "label": p2plan["label"], "provider": p2plan["provider"],
                "model_id": p2plan["model_id"], "legend": p2out["legend"],
                "memory": memory,
            })
            record.setdefault("thread", []).extend([p2out["msg"], evaluator_message])
            record.setdefault("turns", []).append(turn_detail)
        else:
            record = {
                "label": p2plan["label"], "provider": p2plan["provider"],
                "model_id": p2plan["model_id"], "legend": p2out["legend"],
                "memory": memory, "turns": [turn_detail],
                "thread": [p2out["msg"], evaluator_message],
            }
            if plan.get("initial_council"):
                record["initial_council"] = plan["initial_council"]
        st.session_state[record_key] = record
        if mode == "synthesis" and record.get("initial_council"):
            st.session_state["initial_council"] = record["initial_council"]
        if continuation:
            st.session_state[f"_scroll_to_latest_{mode}"] = turn_id

        if not result2.get("err"):
            promoted_ids = set(plan.get("promotion_ids") or [])
            promotions_key = ("res_source_promotions" if mode == "research"
                              else "source_promotions")
            branches_key = ("res_source_chats" if mode == "research"
                            else "source_chats")
            for item in st.session_state.get(promotions_key, []):
                if item.get("id") in promoted_ids:
                    item["status"] = "integrated"
                    item["turn_id"] = turn_id
            for branch in st.session_state.get(branches_key, {}).values():
                for message in branch:
                    if message.get("promotion_id") in promoted_ids:
                        message["promoted_turn"] = turn_id
        if mode == "synthesis":
            st.session_state["sp_view"] = "⭐ Answer"
    elif p2plan:
        if continuation and record:
            record.setdefault("thread", []).append({
                "role": "user", "content": plan["prompt"], "display": plan["prompt"],
                "files": plan["user_msg"].get("files", []), "turn_id": turn_id,
            })
        if mode == "research":
            st.session_state["_bg_warn_research"] = (
                "Fewer than two sources answered successfully - nothing to compare. "
                "Check keys/errors in the source answers below.")
        else:
            st.session_state["_bg_warn_synthesis"] = (
                "Every source answer failed, so there was nothing to synthesize. Open 🧩 The Sources to see each error (often a missing key or credit).")
            if not continuation:
                st.session_state["sp_view"] = "\U0001F9E9 The Sources"

    persist_session(mode)

def helper_cost_rows(job):
    rows = []
    fresh_calls = job.get("fresh_calls") or []
    for icon, name, calls in (
            ("👀", f"Live draft · Cerebras {CEREBRAS_PREVIEW_MODEL}", job.get("preview_calls") or []),
            ("🧭", "Jev checks · TypeSafe", (job.get("jev") or {}).get("calls") or []),
            ("🔎", "Fresh sources · " + " + ".join(dict.fromkeys(c.get("model", "") for c in fresh_calls)),
             fresh_calls)):
        if not calls:
            continue
        def _sum(k):
            vals = [c.get(k) for c in calls if c.get(k) is not None]
            return sum(vals) if vals else None
        rows.append({"role": "assistant", "name": name, "icon": icon, "content": "",
                     "cost": _sum("cost"), "secs": _sum("secs") or 0.0,
                     "in_tok": _sum("in_tok"), "out_tok": _sum("out_tok"),
                     "model_id": calls[-1].get("model") or "",
                     "settings": f"{len(calls)} call{'s' if len(calls) != 1 else ''}",
                     "err": False, "web": False, "helper": True})
    return rows


def bg_flash(mode):
    """Show (once) any warning/error left by a completed background run. Keyed by
    mode so a Synthesis warning can't surface on the Research tab, or vice versa."""
    w = st.session_state.pop(f"_bg_warn_{mode}", None)
    if w: st.warning(w)
    e = st.session_state.pop(f"_bg_error_{mode}", None)
    if e: st.error(f"The background run failed: {e}")

# The progress panel is a FRAGMENT so it can refresh itself on a timer WITHOUT
# re-running the whole page (a full rerun every second would fight the user).
# `compact` renders the always-visible sidebar version; only that one triggers
# the app-wide rerun on completion, so a finished run commits even from another
# tab. Never render this when no job is active — run_every would poll forever.
def _bg_progress_rows(job):
    plan = job["plan"]
    done_by_name = {item["name"]: item for item in job.get("progress", [])}
    job_started = job.get("started")
    rows = []
    for name, source in plan["sources"].items():
        meta = plan.get("src_meta", {}).get(name, {})
        shared = {"model_id": meta.get("model_id", source.get("cfg", {}).get("model_id", "")),
                  "settings": meta.get("settings", "")}
        done = done_by_name.get(name)
        if done:
            rows.append({**done, "label": name, "icon": ICON.get(name, ""),
                         "state": "error" if done["err"] else "done", **shared})
        else:
            fresh = job.get("fresh") or {}
            reading = (source.get("pack") and fresh.get("status") == "done"
                       and name in (fresh.get("for") or []))
            if job.get("phase", 1) >= 2:
                verb = "still answering — the answer started without it"
            elif source.get("pack") and fresh.get("status") in (None, "searching"):
                verb = "waiting for fresh web sources"
            elif reading:
                verb = "reading the fresh sources & thinking"
            else:
                verb = "searching & thinking" if source.get("web") else "thinking"
            rows.append({"name": name, "label": name, "icon": ICON.get(name, ""),
                         "state": "active", "web": bool(source.get("web")),
                         "pages": bool(reading),
                         "verb": verb, "started_at": job_started, **shared})

    for memo_key, role_label, done_key in (
            ("synth_memo", "Synthesizer", "Synthesizer memo"),
            ("adj_memo", "Adjudicator", "Adjudicator memo")):
        memo_job = plan.get(memo_key)
        if not memo_job:
            continue
        provider = memo_job.get("provider", "")
        icon = ICON.get(provider, "\U0001F512")
        label = f"{provider} ({role_label})" if provider else f"{role_label} (independent answer)"
        shared = {"model_id": model_id_of(provider, memo_job.get("cfg", {})),
                  "settings": settings_str(provider, memo_job.get("cfg", {}),
                                           memo_job.get("web", False))}
        done = done_by_name.get(done_key)
        if done:
            rows.append({**done, "label": label, "icon": icon,
                         "state": "error" if done["err"] else "done", **shared})
        else:
            rows.append({"name": done_key, "label": label, "icon": icon,
                         "state": "active", "web": False,
                         "verb": ("still writing — the answer started without it"
                                  if job.get("phase", 1) >= 2 else
                                  "writing its own independent answer"),
                         "started_at": job_started, **shared})

    phase2 = plan.get("phase2")
    if phase2:
        key = phase2["label_key"]
        done = done_by_name.get(key)
        label = f"{key} · {phase2['label']}"
        shared = {"model_id": phase2.get("model_id", ""),
                  "settings": phase2.get("settings", "")}
        if done:
            rows.append({**done, "label": label, "icon": ICON.get(key, "⭐"),
                         "state": "error" if done["err"] else "done", **shared})
        elif job.get("phase", 1) >= 2:
            rows.append({"name": key, "label": label, "icon": ICON.get(key, "⭐"),
                         "state": "active", "web": bool(phase2.get("web")),
                         "verb": ("evaluating sources" if phase2["kind"] == "research"
                                  else "continuing the conversation"
                                  if phase2["kind"] == "solo"
                                  else "synthesizing sources"),
                         "started_at": job.get("phase1_done_at", job_started), **shared})
        else:
            rows.append({"name": key, "label": label, "icon": ICON.get(key, "⭐"),
                         "state": "queued", "web": bool(phase2.get("web")),
                         "verb": ("queued until most sources are in"
                                  if plan.get("quorum") else "queued until sources finish"),
                         **shared})

    phase3 = plan.get("phase3")
    if phase3:
        key = phase3["label_key"]
        done = done_by_name.get(key)
        label = f"{key} audit · {phase3['label']}"
        shared = {"model_id": phase3.get("model_id", ""),
                  "settings": phase3.get("settings", "")}
        phase2_done = phase2 and done_by_name.get(phase2["label_key"])
        if done:
            rows.append({**done, "label": label, "icon": "⚖️",
                         "state": "error" if done["err"] else "done", **shared})
        elif job.get("audit_skipped"):
            rows.append({"name": key, "label": label, "icon": "⚖️",
                         "state": "skipped", "web": False,
                         "verb": "skipped — Jev's claim check found nothing contradicted",
                         **shared})
        elif phase2_done and not phase2_done.get("err"):
            rows.append({"name": key, "label": label, "icon": "⚖️",
                         "state": "active", "web": bool(phase3.get("web")),
                         "verb": "auditing the draft",
                         "started_at": job.get("phase2_done_at", job_started), **shared})
        else:
            rows.append({"name": key, "label": label, "icon": "⚖️",
                         "state": "queued", "web": bool(phase3.get("web")),
                         "verb": "queued until the draft is ready", **shared})

    phase4 = plan.get("phase4")
    if phase4:
        key = phase4["label_key"]
        done = done_by_name.get(key)
        label = f"{key} · {phase4['label']}"
        shared = {"model_id": phase4.get("model_id", ""),
                  "settings": phase4.get("settings", "")}
        phase3_done = phase3 and done_by_name.get(phase3["label_key"])
        material_defects = job.get("material_defects")
        if done:
            rows.append({**done, "label": label, "icon": "✏️",
                         "state": "error" if done["err"] else "done", **shared})
        elif job.get("audit_skipped"):
            rows.append({"name": key, "label": label, "icon": "✏️",
                         "state": "skipped", "web": False,
                         "verb": "skipped — no audit was needed", **shared})
        elif phase3_done and not phase3_done.get("err") and material_defects is True:
            rows.append({"name": key, "label": label, "icon": "✏️",
                         "state": "active", "web": bool(phase4.get("web")),
                         "verb": "revising the answer",
                         "started_at": job.get("phase3_done_at", job_started), **shared})
        elif phase3_done and not phase3_done.get("err") and material_defects is not None:
            rows.append({"name": key, "label": label, "icon": "✏️",
                         "state": "skipped", "web": False,
                         "verb": "skipped — no material defects found", **shared})
        else:
            rows.append({"name": key, "label": label, "icon": "✏️",
                         "state": "queued", "web": bool(phase4.get("web")),
                         "verb": "queued until the audit finishes", **shared})
    return rows

def _bg_progress_html(rows):
    """Render compact, animated status rows without creating shifting widgets."""
    parts = ['<div class="conclave-run-list">']
    for row in rows:
        label = html.escape(f"{row.get('icon', '')} {row['label']}")
        web = (" · web" if row.get("web") else "") + (" · 🔎 pages" if row.get("pages") else "")
        if row["state"] == "active":
            started_at = row.get("started_at")
            elapsed = f" · {time.time() - started_at:.0f}s" if started_at else ""
            settings = f" · {row['settings']}" if row.get("settings") else ""
            detail = html.escape(f"{row['verb']}{settings}{web}{elapsed}…")
            marker = (f'<span class="conclave-run-marker conclave-run-seal" role="status" '
                      f'aria-label="Active">{seal_mark_html(15, loading=True)}</span>')
        elif row["state"] == "queued":
            detail = html.escape(row["verb"])
            marker = '<span class="conclave-run-marker conclave-run-queued-marker">○</span>'
        elif row["state"] == "skipped":
            detail = html.escape(row["verb"])
            marker = '<span class="conclave-run-marker conclave-run-queued-marker">–</span>'
        else:
            cost = (f" · ~${row['cost']:.4f}" if row.get("cost") is not None else "")
            detail = html.escape(("failed" if row["state"] == "error" else "finished") +
                                 f" · {row.get('secs', 0.0):.1f}s{cost}{web}")
            marker = ('<span class="conclave-run-marker conclave-run-error-marker">!</span>'
                      if row["state"] == "error" else
                      '<span class="conclave-run-marker conclave-run-done-marker">✓</span>')
        parts.append(
            f'<div class="conclave-run-row conclave-run-{row["state"]}">{marker}'
            f'<span class="conclave-run-copy"><strong>{label}</strong>'
            f'<span>{detail}</span></span></div>')
    parts.append("</div>")
    return "".join(parts)

@st.fragment(run_every=0.6)
def bg_refresh(show_progress=False):
    """One active timer: refresh progress here, or just detect completion away."""
    jid = st.session_state.get("bg_job_id")
    job = _bg_get(jid) if jid else None
    if job is None:
        return
    if job["status"] != "running":
        st.rerun(scope="app")
        return
    if show_progress:
        bg_progress()


def bg_progress():
    """Render a progress snapshot; bg_refresh owns the only timer."""
    jid = st.session_state.get("bg_job_id")
    job = _bg_get(jid) if jid else None
    if job is None:
        return

    stopping = bool(job.get("cancel_requested"))
    elapsed = time.time() - job["started"]
    rows = _bg_progress_rows(job)
    expect = len(rows)
    finished = sum(1 for row in rows if row["state"] in ("done", "error", "skipped"))
    if stopping:
        st.warning("⏹ **Stopping this run.** No new Conclave work will start. An API request "
                   "already accepted by a provider may still finish and be billed.")
    else:
        st.markdown(f'<div class="conclave-loading-banner">{seal_mark_html(44, loading=True)}'
                    "</div>", unsafe_allow_html=True)
        st.info(f"⏳ **Working in the background — {finished}/{expect} done · {elapsed:.0f}s.** "
                "Navigate anywhere you like; this keeps running and the answer will appear "
                "here when it's ready.")
    st.markdown(_bg_progress_html(rows), unsafe_allow_html=True)
    st.progress(min(finished / max(expect, 1), 1.0))
    render_live_panel(job, rows)


def live_timeline_bars(job, rows, now=None):
    now = now or time.time()
    t0 = job.get("started") or now
    plan = job.get("plan") or {}
    phase_start = {}
    for key, stamp in (("phase2", "phase1_done_at"), ("phase3", "phase2_done_at"),
                       ("phase4", "phase3_done_at")):
        ph = plan.get(key)
        if ph and job.get(stamp):
            phase_start[ph.get("label_key")] = job[stamp] - t0
    bars = []
    for r in rows:
        state = r.get("state")
        if state in ("queued", "skipped"):
            continue
        if state == "active":
            began = r.get("started_at") or t0
            start, secs = began - t0, max(0.0, now - began)
            color = "var(--conclave-accent,#2fd4c6)"
        else:
            start = phase_start.get(r.get("name"), 0.0)
            secs = float(r.get("secs") or 0.0)
            color = "#c86d5a" if state == "error" else "#8494a6"
        bars.append({"label": f"{r.get('icon', '')} {r.get('label', '')}".strip()[:44],
                     "start": max(0.0, start), "secs": secs, "color": color})
    return bars, max(0.0, now - t0)


def live_cost_so_far(job):
    progress = job.get("progress") or []
    models = sum(float(p.get("cost") or 0.0) for p in progress)
    helpers = 0.0
    for calls in (job.get("preview_calls") or [], (job.get("jev") or {}).get("calls") or [],
                  job.get("fresh_calls") or []):
        helpers += sum(float(c.get("cost") or 0.0) for c in calls)
    return {"models": models, "helpers": helpers, "total": models + helpers,
            "finished": len(progress),
            "unknown": sum(1 for p in progress if p.get("cost") is None and not p.get("err"))}


def render_live_panel(job, rows=None):
    spent = live_cost_so_far(job)
    expected = len([r for r in (rows or []) if r.get("state") != "skipped"]) or None
    st.markdown(
        f"💵 **Cost so far: ${spent['total']:.4f}**"
        + (f" · {spent['finished']} of {expected} calls finished" if expected else "")
        + f" · models ${spent['models']:.4f} · helpers ${spent['helpers']:.4f}",
        help="Adds each call's cost the moment it finishes, so it climbs as the council "
             "answers. Calls still running are billed when they finish; free models count "
             "as $0. Helpers are the small extras (live preview, Jev's checks, shared web search); the full breakdown appears under the answer.")
    if rows:
        bars, total = live_timeline_bars(job, rows)
        if bars:
            st.caption("🕒 **Live timeline** — one bar per call; a bar keeps growing until its call finishes (red = failed)")
            st.markdown(_timeline_html(bars, total), unsafe_allow_html=True)
    fresh = job.get("fresh") or {}
    if fresh:
        st.caption(fresh_sources_line(fresh))
    if job.get("early_start") and job.get("late_names"):
        st.caption("⏩ The answer started without waiting for "
                   + ", ".join(job["late_names"]) + " — their answers still appear under "
                   "Sources when they land, labeled late.")
    streams = job.get("stream") or {}
    revision = live_answer_text(streams.get("revision", ""))
    answer = live_answer_text(streams.get("answer", ""))
    preview = job.get("preview") or {}
    if revision or answer:
        with st.container(border=True, key="live_answer_box"):
            st.caption("✏️ **Revising the answer — live**" if revision else
                       "✍️ **The answer, as it's being written**")
            st.markdown(revision or answer)
    elif preview.get("text"):
        with st.container(border=True, key="live_draft_box"):
            src = ("a quick first take" if not preview.get("after") else
                   f"from {preview['after']} of {preview.get('of', '?')} answers so far")
            st.caption(f"👀 **Live draft** · Cerebras GPT-OSS 120B · {src} — a fast preview, "
                       "not the council's answer, which replaces it")
            st.markdown(preview["text"])
    elif job.get("preview_error"):
        st.caption(f"👀 Live draft unavailable this run ({job['preview_error'][:120]}).")
    router = (job.get("jev") or {}).get("router") or {}
    if router:
        st.caption("🧭 " + jev_router_text(router))


def fresh_sources_line(fresh):
    status, reason = fresh.get("status"), fresh.get("reason") or ""
    if status == "searching":
        return f"🔎 Searching Tavily + Brave for current web pages for the 🔎 sources… ({reason})"
    if status == "done":
        counts = fresh.get("counts") or {}
        found = (" · ".join(f"{svc} {n}" for svc, n in counts.items())
                 + (f" · {fresh.get('both', 0)} in both" if len(counts) > 1 else ""))
        return (f"🔎 **Fresh sources** · {fresh.get('n', 0)} pages "
                + (f"({found}) " if found else f"via {fresh.get('service')} ")
                + f"in {fresh.get('secs', 0):.1f}s ({reason}) — given to "
                + ", ".join(fresh.get("for") or []))
    if status == "failed":
        why = "; ".join(fresh.get("errors") or [])[:160]
        return ("🔎 Fresh sources unavailable this run" + (f" ({why})" if why else "")
                + " — the sources set to read them answered from their own knowledge.")
    return f"🔎 No fresh sources this run — {reason}."


def render_fresh_sources(m, key_suffix=""):
    fresh = m.get("fresh_sources")
    if not isinstance(fresh, dict) or fresh.get("status") not in ("done", "failed"):
        return
    results = fresh.get("results") or []
    label = (f"🔎 Web sources given to the models · {len(results)} pages via "
             f"{fresh.get('service')}" if results else
             "🔎 Web sources · unavailable this turn")
    with st.expander(label, expanded=False):
        st.caption(fresh_sources_line(fresh))
        found = fresh.get("found")
        if isinstance(found, int) and found > len(results):
            st.caption(f"{found} pages came back; the first {len(results)} fit the reading "
                       "limit and were given to the models.")
        show = any(r.get("text") for r in results) and st.toggle(
            "Show the exact excerpts the models read", key=f"fresh_show_{key_suffix}",
            value=False)
        for i, r in enumerate(results, 1):
            title = (r.get("title") or r.get("site") or "page").replace("[", "(").replace("]", ")")
            by = r.get("found_by") or []
            st.markdown(f"**{i}. [{title}]({r.get('url')})** — {r.get('site', '')}"
                        + (f" · {r['date']}" if r.get("date") else "")
                        + (f" · found by {' + '.join(by)}" if by else ""))
            if show and r.get("text"):
                st.caption(html.escape(" ".join(str(r["text"]).split())))


def fresh_source_stats(entries):
    turns = both_turns = 0
    found = {}
    shared = secs = cost = 0.0
    for e in entries or []:
        for rec_key in ("synth", "research"):
            rec = e.get(rec_key) if isinstance(e, dict) else None
            for m in (rec or {}).get("thread") or [] if isinstance(rec, dict) else []:
                fr = m.get("fresh_sources") if isinstance(m, dict) else None
                if not isinstance(fr, dict) or fr.get("status") != "done":
                    continue
                turns += 1
                counts = fr.get("counts") or {}
                for svc, n in counts.items():
                    found[svc] = found.get(svc, 0) + int(n or 0)
                if len(counts) > 1:
                    both_turns += 1
                    shared += float(fr.get("both") or 0)
                secs += float(fr.get("secs") or 0)
                for h in m.get("helpers") or []:
                    if str(h.get("name", "")).startswith("Fresh sources"):
                        cost += float(h.get("cost") or 0)
    return {"turns": turns, "both_turns": both_turns, "found": found,
            "shared": shared, "secs": secs, "cost": cost}


def fresh_stats_line(stats):
    """One plain line for the report cards."""
    if not stats.get("turns"):
        return ""
    t = stats["turns"]
    per = " · ".join(f"{svc} {n / t:.1f} pages" for svc, n in stats["found"].items())
    line = f"🔎 **Web sources so far:** {t} turns · {per} on average"
    if stats.get("both_turns"):
        bt = stats["both_turns"]
        smaller = min((stats["found"].get(s, 0) for s in ("Tavily", "Brave")), default=0)
        pct = (100.0 * stats["shared"] / smaller) if smaller else 0.0
        line += (f" · {stats['shared'] / bt:.1f} pages found by both per turn "
                 f"({pct:.0f}% of the smaller engine's results)")
    return line + f" · ~{stats['secs'] / t:.1f}s and ~${stats['cost'] / t:.3f} a turn"


def jev_router_text(router):
    """One plain line for Jev's read of a question."""
    bits = []
    if router.get("preset"):
        bits.append(f"looks like a **{router['preset']}** question")
    nw = router.get("needs_web")
    if isinstance(nw, (int, float)):
        bits.append(f"current information {'needed' if nw >= 0.5 else 'not needed'} ({nw:.2f})")
    nc = router.get("needs_council")
    if isinstance(nc, (int, float)):
        bits.append(f"full council {'worth it' if nc >= 0.5 else 'probably not needed'} ({nc:.2f})")
    return "Jev's read: " + " · ".join(bits) if bits else "Jev's read: no verdict"

def render_pending_question():
    jid = st.session_state.get("bg_job_id")
    job = _bg_get(jid) if jid else None
    if not job:
        return
    user_msg = job["plan"].get("user_msg")
    if not user_msg:
        return
    with st.chat_message("user"):
        st.markdown(user_text_of(user_msg))
        render_user_files(user_msg)

def render_stop_run_button(key):
    """Render one consistent Stop control for the active background run."""
    jid = st.session_state.get("bg_job_id")
    job = _bg_get(jid) if jid else None
    if not job or job.get("status") != "running":
        return
    stopping = bool(job.get("cancel_requested"))
    st.button("⏹ Stopping…" if stopping else "⏹ Stop run", key=key,
              use_container_width=True, disabled=stopping,
              on_click=bg_request_stop,
              help=("Stops the run: nothing new starts and partial results are thrown away. "
                    "A provider request already accepted may still finish and be billed."))

def add_cost(results):
    total = sum(float(result.get("cost") or 0.0) for result in results.values())
    st.session_state["session_cost"] = float(
        st.session_state.get("session_cost", 0.0) or 0.0) + total

def assistant_msg(r, model_id, settings, turn_id=None):
    """Package a run_one result as a thread message with its own per-turn metadata."""
    msg = {"role": "assistant", "content": r["text"], "secs": r["secs"],
           "in_tok": r["in_tok"], "out_tok": r["out_tok"], "cost": r["cost"],
           "web": r.get("web", False), "model_id": r.get("model_used") or model_id,
           "settings": settings,
           "err": bool(r.get("err")), "credit_err": bool(r.get("credit_err"))}
    if r.get("model_used"):
        msg["settings"] = f"{settings} · answered by {r['model_used']} (this model can't search)"
    if r.get("web_fallback"):
        msg["web_fallback"] = True
        if r.get("web_fallback_reason"):
            msg["web_fallback_reason"] = r["web_fallback_reason"]
    if r.get("speed_used"):
        msg["speed_used"] = r["speed_used"]
    for flag in ("late", "dropped"):
        if r.get(flag):
            msg[flag] = True
    if r.get("reasoning"):
        msg["reasoning"] = r["reasoning"]
    if r.get("cache"):
        msg["cache"] = dict(r["cache"])
    if turn_id is not None:
        msg["turn_id"] = turn_id
    return msg

def meta_caption(m):
    cap = f"{m.get('model_id','')} · {m.get('settings','')}"
    if m.get("web"): cap += " · \U0001F310 web"
    elif m.get("web_fallback") and m.get("web_fallback_reason") == "unavailable":
        cap += " · \U0001F310 web search isn't switched on for this account → answered without web"
    elif m.get("web_fallback"): cap += " · \U0001F310 search timed out → answered without web"
    if m.get("speed_used"):
        cap += f" · ⚡ ran at {m['speed_used']}"
    if isinstance(m.get("fresh_sources"), str):
        cap += f" · 🔎 {m['fresh_sources']} pages"
    cap += f" · ⏱ {m.get('secs', 0):.1f}s"
    if m.get("in_tok") is not None: cap += f" · \U0001F522 {m['in_tok'] + (m.get('out_tok') or 0):,} tok"
    if m.get("cost") is not None:   cap += f" · \U0001F4B5 ~${m['cost']:.4f}"
    if isinstance(m.get("cache"), dict) and m["cache"].get("read"):
        cap += f" · ♻️ {m['cache']['read']:,} tokens from cache"
    if m.get("late") and not m.get("dropped"):
        cap += " · ⏩ arrived after the answer began — not in this turn's synthesis"
    jv = m.get("jev")
    if isinstance(jv, dict) and jv.get("verdict"):
        conf = jv.get("confidence")
        conf = f" {conf:.2f}" if isinstance(conf, (int, float)) else ""
        cap += (f" · 🧭 set aside by Jev ({jv['verdict']}{conf})" if jv.get("held")
                else f" · 🧭 Jev: {jv['verdict']}{conf}")
        if isinstance(jv.get("stale"), (int, float)) and jv["stale"] >= 0.7:
            cap += " · may rely on outdated knowledge"
    return cap

def _assistant_for_turn(thread, turn_id):
    messages = [m for m in (thread or []) if m.get("role") == "assistant"]
    if not isinstance(turn_id, int):
        return None
    messages = [m for m in messages if m.get("turn_id") == turn_id]
    return messages[-1] if messages else None

def turn_cost_snapshot(threads, evaluator_message, evaluator_label, eval_threads=None):
    turn_id = (evaluator_message or {}).get("turn_id")
    rows = []
    ordered_names = [name for name in MODEL_ORDER if name in (threads or {})]
    ordered_names += [name for name in (threads or {}) if name not in ordered_names]
    for name in ordered_names:
        message = _assistant_for_turn(threads[name], turn_id)
        if message is not None:
            rows.append({"name": name, "message": message, "evaluator": False,
                        "icon": ICON.get(name, "")})
    if evaluator_message is not None:
        _solo_suffix = " (solo reply — no sources this turn)" if evaluator_message.get("solo") else ""
        rows.append({"name": evaluator_label + _solo_suffix, "message": evaluator_message,
                     "evaluator": True, "icon": "⭐"})
        pre = evaluator_message.get("pre_revision") if evaluator_message.get("revised") else None
        if pre is not None:
            rows.append({"name": f"{evaluator_label} (pre-revision draft)",
                        "message": pre, "evaluator": True, "icon": "⭐"})
        _failed_rev = evaluator_message.get("failed_revision")
        if _failed_rev is not None:
            rows.append({"name": f"{evaluator_label} (revision attempt — failed)",
                        "message": _failed_rev, "evaluator": True, "icon": "⭐"})
        adjudicator = evaluator_message.get("adjudicator")
        if adjudicator is not None:
            rows.append({"name": "Adjudicator audit", "message": adjudicator,
                        "evaluator": True,
                        "icon": ICON.get(adjudicator.get("provider", ""), "⚖️")})
        for role_label in ("Synthesizer", "Adjudicator"):
            memo_msg = _assistant_for_turn((eval_threads or {}).get(role_label), turn_id)
            if memo_msg is not None:
                rows.append({"name": f"{role_label} memo", "message": memo_msg,
                            "evaluator": True, "icon": "\U0001F512"})
        for helper in evaluator_message.get("helpers") or []:
            rows.append({"name": helper.get("name", "Helper"), "message": helper,
                         "evaluator": False, "helper": True, "icon": helper.get("icon", "•")})
    source_total = sum(float(row["message"].get("cost") or 0.0)
                       for row in rows if not row["evaluator"] and not row.get("helper"))
    evaluator_total = sum(float(row["message"].get("cost") or 0.0)
                          for row in rows if row["evaluator"])
    helper_total = sum(float(row["message"].get("cost") or 0.0)
                       for row in rows if row.get("helper"))
    return {
        "turn_id": turn_id,
        "rows": rows,
        "source_total": source_total,
        "evaluator_total": evaluator_total,
        "helper_total": helper_total,
        "total": source_total + evaluator_total + helper_total,
        "total_secs": (evaluator_message or {}).get("turn_secs"),
    }

def conversation_cost_snapshot(threads, evaluator_thread, evaluator_label, eval_threads=None):
    """Aggregate the exact source membership and recorded costs across a conversation."""
    evaluator_messages = [m for m in (evaluator_thread or [])
                          if m.get("role") == "assistant"]
    turns = [turn_cost_snapshot(threads, message, evaluator_label, eval_threads)
             for message in evaluator_messages]
    source_totals = {}
    source_secs = {}
    for name, thread in (threads or {}).items():
        source_totals[name] = sum(
            float(message.get("cost") or 0.0) for message in thread
            if message.get("role") == "assistant")
        source_secs[name] = sum(
            float(message.get("secs") or 0.0) for message in thread
            if message.get("role") == "assistant")
    evaluator_total = sum(t["evaluator_total"] for t in turns)
    helper_total = sum(t.get("helper_total", 0.0) for t in turns)
    source_total = sum(source_totals.values())
    turn_secs = [t["total_secs"] for t in turns if t["total_secs"] is not None]
    evaluator_secs = sum(
        float(row["message"].get("secs") or 0.0)
        for t in turns for row in t["rows"]
        if row["evaluator"] and not row["name"].endswith("memo"))
    return {
        "turns": turns,
        "source_totals": source_totals,
        "source_secs": source_secs,
        "evaluator_secs": evaluator_secs,
        "source_total": source_total,
        "evaluator_total": evaluator_total,
        "helper_total": helper_total,
        "total": source_total + evaluator_total + helper_total,
        "total_secs": sum(turn_secs) if turn_secs else None,
        "total_secs_partial": len(turn_secs) < len(turns),
    }

def render_turn_cost_breakdown(threads, evaluator_message, evaluator_label,
                               fallback_turn=None, eval_threads=None):
    snapshot = turn_cost_snapshot(threads, evaluator_message, evaluator_label, eval_threads)
    turn_id = snapshot["turn_id"]
    if isinstance(turn_id, int):
        turn_label = f"Turn {turn_id + 1}"
    elif fallback_turn is not None:
        turn_label = f"Turn {fallback_turn}"
    else:
        turn_label = "This turn"
    with st.expander(f"\U0001F4B5 {turn_label} cost · ~${snapshot['total']:.4f}",
                     expanded=False):
        for row in snapshot["rows"]:
            st.caption(f"{row['icon']} **{row['name']}** — {meta_caption(row['message'])}")
        st.caption(f"Sources: ~${snapshot['source_total']:.4f} · "
                   f"{evaluator_label}: ~${snapshot['evaluator_total']:.4f}"
                   + (f" · Helpers: ~${snapshot['helper_total']:.4f}"
                      if snapshot.get("helper_total") else ""))
        if snapshot["total_secs"] is not None:
            st.caption(f"⏱ **Total time: {snapshot['total_secs']:.0f}s** "
                       "(wall-clock for this turn, not a sum of each model's own time)")
        st.caption("Estimated from the token counts each provider reported. Per-search web fees some providers add may not be included.")

def render_conversation_costs(threads, evaluator_record, evaluator_label,
                              heading="\U0001F4CA Run stats & true cost", eval_threads=None):
    evaluator_thread = (evaluator_record or {}).get("thread", [])
    snapshot = conversation_cost_snapshot(threads, evaluator_thread, evaluator_label, eval_threads)
    with st.expander(heading, expanded=False):
        st.caption(f"{len(snapshot['turns'])} completed council turn"
                   f"{'s' if len(snapshot['turns']) != 1 else ''}")
        for name in MODEL_ORDER:
            if name in snapshot["source_totals"]:
                _t = snapshot["source_secs"].get(name) or 0.0
                st.caption(f"{ICON.get(name, '')} **{name} source total** — "
                           f"~${snapshot['source_totals'][name]:.4f} · {_t:.1f}s")
        st.caption(f"⭐ **{evaluator_label} total** — ~${snapshot['evaluator_total']:.4f} · "
                   f"{snapshot['evaluator_secs']:.1f}s")
        st.caption(f"Sources subtotal: ~${snapshot['source_total']:.4f} · "
                   f"{evaluator_label} subtotal: ~${snapshot['evaluator_total']:.4f}"
                   + (f" · Helpers (live draft, Jev, shared web search): ~${snapshot['helper_total']:.4f}"
                      if snapshot.get("helper_total") else ""))
        st.markdown(f"\U0001F4B5 **True total: ~${snapshot['total']:.4f}** across this conversation")
        if snapshot["total_secs"] is not None:
            _partial = " (some earlier turns predate this stat)" if snapshot["total_secs_partial"] else ""
            st.markdown(f"⏱ **Total time: {snapshot['total_secs']:.0f}s** across this "
                        f"conversation{_partial}")
        if snapshot["turns"]:
            st.divider()
            for index, turn in enumerate(snapshot["turns"], start=1):
                sources = [row["name"] for row in turn["rows"]
                           if not row["evaluator"] and not row.get("helper")]
                _secs = f" · {turn['total_secs']:.0f}s" if turn["total_secs"] is not None else ""
                st.caption(f"Turn {index} · {', '.join(sources) or 'No successful sources'} · "
                           f"~${turn['total']:.4f}{_secs}")
        st.caption("Estimates from reported token counts. The session spend below also counts your private one-on-one chats with single sources.")

def _split_after_heading(content, headings):
    """Split content at the first line whose words match one accepted heading
    form. The stored message is never changed. Matching heading words rather
    than loose substrings keeps ordinary prose mentions of a heading visible
    in the body. Returns (body, details); details is None when no heading
    with following content is present."""
    text = str(content or "")
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        words = re.findall(r"[a-z0-9]+", line.casefold())
        if words in headings:
            details = "".join(lines[index + 1:]).strip()
            if details:
                return "".join(lines[:index]).rstrip(), details
    return text, None

def split_source_weighing(content):
    """Split only the explicit synthesis Part 2 heading from a response."""
    expected = ["how", "i", "weighed", "the", "sources"]
    return _split_after_heading(content, [expected, ["part", "2", *expected],
                                          ["part", "ii", *expected]])

def split_disposition_response(content):
    """Split the optional 'Response to the Adjudicator' section from a
    REVISED Synthesizer answer (mandatory disposition + one controlled
    revision pass, architecture review Phase 4). MUST run BEFORE
    split_source_weighing at render time -- this section is the LAST one in
    the revision prompt's requested layout, so split_source_weighing's own
    "everything after the heading" logic would otherwise swallow it into
    the weighing section. A message that never went through a revision (no
    such heading present) round-trips completely unchanged."""
    expected = ["response", "to", "the", "adjudicator"]
    return _split_after_heading(content, [expected, ["part", "3", *expected]])

def render_synthesized_content(content):
    """Render synthesis prose with its audit note collapsed by default.

    Also peels off a REVISED answer's 'Response to the Adjudicator' section
    into its own collapsed disclosure -- order matters here, see
    split_disposition_response's docstring for why it must run first."""
    content, disposition = split_disposition_response(content)
    answer, weighing = split_source_weighing(content)
    if answer:
        st.markdown(answer)
    if weighing is not None:
        with st.expander("How I weighed the sources", expanded=False):
            st.markdown(weighing)
    if disposition is not None:
        with st.expander("✏️ Response to the Adjudicator's review", expanded=False):
            st.markdown(disposition)

def render_reasoning(m, provider):
    text = (m or {}).get("reasoning")
    kind, note = REASONING_EXPOSURE.get(provider, ("private", "not returned by this provider"))
    if text:
        with st.expander(f"🧠 Reasoning — as returned by {provider}'s API", expanded=False):
            st.caption(f"Verbatim from the provider ({note}). Not part of the answer; "
                       "Conclave adds, trims, or rewrites nothing here.")
            st.markdown(text)
    elif (m or {}).get("err"):
        return
    elif kind == "private":
        st.caption(f"🧠 No reasoning to show — {note}.")
    else:
        st.caption(f"🧠 No reasoning returned this call — {provider}'s API sends it only when "
                   "the model reasons (or this answer was saved before Conclave kept reasoning).")

def reasoning_coverage_caption(names):
    """One line above the source answers: who returns reasoning, who doesn't."""
    yes = [n for n in names if REASONING_EXPOSURE.get(n, ("private",))[0] == "returns"]
    no = [n for n in names if n not in yes]
    parts = []
    if yes:
        parts.append("returned by " + ", ".join(f"{ICON.get(n, '')} {n}" for n in yes))
    if no:
        parts.append("kept private by " + ", ".join(f"{ICON.get(n, '')} {n}" for n in no))
    return ("🧠 Some providers show their model's reasoning and some keep it private — " + " · ".join(parts)
            + ". Each answer below says which; Conclave never makes reasoning up.")

def _letter_mentions(text, legend):
    """Count 'Answer A' / bare-letter citations in the Synthesizer's own
    weighing note, decoded through the legend -> {provider: count}."""
    counts = {}
    letters = set((legend or {}).keys())
    for found in re.finditer(r"\b(?:answers?\s+)?([A-Z])\b", text or ""):
        letter = found.group(1)
        if letter in letters:
            name = legend[letter]
            counts[name] = counts.get(name, 0) + 1
    return counts

def _timeline_html(bars, total):
    total = max(total or 0.0, max((b["start"] + b["secs"] for b in bars), default=0.0), 0.1)
    rows = []
    for b in bars:
        left = 100.0 * b["start"] / total
        width = max(0.6, 100.0 * b["secs"] / total)
        rows.append(
            "<div style='display:flex;align-items:center;gap:.5rem;margin:.15rem 0;'>"
            f"<div style='flex:0 0 11rem;font-size:.8rem;white-space:nowrap;overflow:hidden;"
            f"text-overflow:ellipsis'>{html.escape(b['label'])}</div>"
            "<div style='flex:1;position:relative;height:.9rem;background:var(--conclave-field,#2a2f38);"
            "border-radius:4px;overflow:hidden'>"
            f"<div style='position:absolute;left:{left:.1f}%;width:{width:.1f}%;top:0;bottom:0;"
            f"background:{b['color']};border-radius:4px'></div></div>"
            f"<div style='flex:0 0 3.4rem;font-size:.75rem;text-align:right'>{b['secs']:.0f}s</div>"
            "</div>")
    return ("<div style='margin:.2rem 0 .4rem'>" + "".join(rows)
            + f"<div style='font-size:.72rem;opacity:.7;text-align:right'>0s → {total:.0f}s</div></div>")

def render_run_report(threads, m, evaluator_record, evaluator_label, fallback_turn=None,
                      eval_threads=None):
    snapshot = turn_cost_snapshot(threads, m, evaluator_label, eval_threads)
    turn_id = snapshot["turn_id"]
    turn_label = (f"Turn {turn_id + 1}" if isinstance(turn_id, int)
                  else f"Turn {fallback_turn}" if fallback_turn is not None else "This turn")
    turn_detail = next((t for t in (evaluator_record or {}).get("turns", [])
                        if t.get("turn_id") == turn_id), {})
    legend = turn_detail.get("legend") or (evaluator_record or {}).get("legend") or {}
    contributions = m.get("contributions") or turn_detail.get("contributions") or {}
    with st.expander(f"📊 Run report · {turn_label}", expanded=False):
        # ---- 1. timeline ----
        st.markdown("**Timeline** — the sources (and, with adjudication on, both judges' private first takes) run side by side from the start; the "
                    "Synthesizer begins once most of them are in (or all of them, if you chose "
                    "to wait); the audit follows the draft; a revision follows the audit. Bars "
                    "are each call's own recorded time placed in that order.")
        parallel = [r for r in snapshot["rows"]
                    if (not r["evaluator"] or r["name"].endswith(" memo")) and not r.get("helper")]
        p_end = max((float(r["message"].get("secs") or 0) for r in parallel), default=0.0)
        bars = []
        for r in parallel:
            bars.append({"label": f"{r['icon']} {r['name']}", "start": 0.0,
                         "secs": float(r["message"].get("secs") or 0),
                         "color": "var(--conclave-accent,#2fd4c6)" if not r["evaluator"] else "#8494a6"})
        cursor = float(m.get("synth_started_after") or p_end)
        pre = m.get("pre_revision") if m.get("revised") else None
        draft_secs = float((pre or m).get("secs") or 0)
        bars.append({"label": f"⭐ {evaluator_label}" + (" draft" if pre else ""),
                     "start": cursor, "secs": draft_secs, "color": "#e8b04a"})
        cursor += draft_secs
        adj = m.get("adjudicator")
        if adj:
            a_secs = float(adj.get("secs") or 0)
            bars.append({"label": f"{ICON.get(adj.get('provider', ''), '⚖️')} Adjudicator audit",
                         "start": cursor, "secs": a_secs, "color": "#c86d5a"})
            cursor += a_secs
        if pre:
            r_secs = float(m.get("secs") or 0)
            bars.append({"label": f"⭐ {evaluator_label} revision", "start": cursor,
                         "secs": r_secs, "color": "#e8b04a"})
            cursor += r_secs
        st.markdown(_timeline_html(bars, snapshot.get("total_secs")), unsafe_allow_html=True)
        if snapshot.get("total_secs"):
            st.caption(f"Measured wall-clock for the turn: {snapshot['total_secs']:.0f}s "
                       f"(the bars end at {cursor:.0f}s; the rest is time spent waiting and saving).")
        if m.get("late_sources"):
            st.caption("⏩ Started without waiting for " + ", ".join(m["late_sources"])
                       + " — their bars run past the Synthesizer's start; their answers are in "
                       "Sources but not in this turn's synthesis.")
        if m.get("solo"):
            st.caption("Solo reply — no sources or audit this turn, so the sections below "
                       "don't apply.")
            render_jev_report(m.get("jev"), m)
            return
        # ---- 2. influence ----
        st.markdown("**Who shaped the answer** — counted from the Synthesizer's own "
                    "*How I weighed the sources* note and its list of what each source uniquely added, decoded "
                    "from the anonymous letters (A, B, C…) it saw back to provider names.")
        content, _disp = split_disposition_response(m.get("content", ""))
        _answer, weighing = split_source_weighing(content)
        mentions = _letter_mentions(weighing or "", legend)
        names = [n for n in MODEL_ORDER if n in legend.values()]
        names += [n for n in legend.values() if n not in names]
        if not names:
            st.caption("No letter legend was stored for this turn.")
        for n in names:
            cnt = mentions.get(n, 0)
            bullets = contributions.get(n)
            survived = ("no unique contribution survived" if bullets == [] else
                        f"{len(bullets)} unique contribution{'s' if len(bullets) != 1 else ''}"
                        if bullets else "ledger not recorded")
            bar = "▮" * min(cnt, 12) + ("…" if cnt > 12 else "")
            st.caption(f"{ICON.get(n, '')} **{n}** — cited {cnt}× {bar} · {survived}")
        if contributions:
            with st.expander("🧾 What each source uniquely contributed", expanded=False):
                for n in names:
                    bullets = contributions.get(n)
                    if bullets is None:
                        continue
                    if bullets:
                        st.markdown(f"**{ICON.get(n, '')} {n}**\n" +
                                    "\n".join(f"- {b}" for b in bullets))
                    else:
                        st.markdown(f"**{ICON.get(n, '')} {n}** — {LEDGER_NOTHING}")
                st.caption("Written by the Synthesizer while it wrote the answer (it saw only letters), then "
                           "decoded here. It travels with every follow-up so the "
                           "conversation keeps each source's voice on record.")
        if adj:
            st.markdown("**Audit**")
            verdict = adj.get("material_defects")
            v_txt = ("material defects flagged" if verdict is True else
                     "no material defects" if verdict is False else
                     "verdict block missing")
            if adj.get("err"):
                v_txt = "audit call failed"
            outcome = ("revision ran and replaced the draft" if m.get("revised") else
                       "revision attempted but failed — original draft shown" if m.get("revision_failed") else
                       "no revision needed" if verdict is False else "no revision")
            st.caption(f"⚖️ {ICON.get(adj.get('provider', ''), '')} {adj.get('provider', 'Adjudicator')}: "
                       f"{v_txt} · {outcome}")
            if _disp:
                low = _disp.lower()
                counts = {"accepted": len(re.findall(r"\baccept", low)),
                          "rejected": len(re.findall(r"\breject", low)),
                          "qualified": len(re.findall(r"\bqualif", low)),
                          "escalated": len(re.findall(r"\bescalat", low))}
                st.caption("How the Synthesizer handled the audit's points (rough word counts in its reply to the "
                           "review — approximate): " +
                           " · ".join(f"{k} {v}" for k, v in counts.items()))
        # ---- 4. retention ----
        src_chars = sum(len(r["message"].get("content", "")) for r in snapshot["rows"]
                        if not r["evaluator"] and not r["message"].get("err"))
        ans_chars = len(_answer or "")
        if src_chars:
            kept = [n for n, b in contributions.items() if b]
            st.markdown("**Retention**")
            st.caption(f"{src_chars:,} characters of source answers → {ans_chars:,} in the "
                       f"final answer ({100.0 * ans_chars / src_chars:.0f}%)"
                       + (f" · {len(kept)} of {len(contributions)} sources left a unique mark"
                          if contributions else ""))
        render_jev_report(m.get("jev"), m, legend)
        st.caption("Everything above is measured or counted from this turn's stored records; "
                   "none of it is a model's hidden reasoning.")


def render_jev_report(jv, m, legend=None):
    if not jv:
        return
    active = jv.get("mode") == "active"
    st.markdown("**🧭 Jev decision checks** (a tiny, cheap judging helper) — " +
                ("Active: acted only on clear verdicts" if active
                 else "Shadow: recorded only, nothing was changed"))
    if jv.get("router"):
        st.caption(jev_router_text(jv["router"]))
    checks = jv.get("checks") or {}
    if checks:
        flagged = [f"{n} ({c.get('verdict')} {c.get('confidence') or 0:.2f})"
                   for n, c in checks.items() if c.get("verdict") not in (None, "answers")]
        st.caption(f"Answer checks: {len(checks)} read · "
                   + (("flagged: " + "; ".join(flagged)) if flagged else "every answer was a real attempt"))
    if jv.get("held_out"):
        st.caption("Set aside before synthesis: " + ", ".join(jv["held_out"]))
    elif jv.get("would_hold") and not active:
        st.caption("Active mode would have set aside: " + ", ".join(jv["would_hold"]))
    if jv.get("hold_skipped"):
        st.caption("Kept despite a flag (setting it aside would leave too few answers): "
                   + ", ".join(jv["hold_skipped"]))
    claims = jv.get("claims") or {}
    if claims.get("claims"):
        counts = claims.get("counts") or {}
        st.caption(f"Claim check: {len(claims['claims'])} claims — "
                   + " · ".join(f"{k} {counts.get(k, 0)}" for k in JEV_CLAIM_CRITERIA)
                   + (" → **clean**" if claims.get("clean") else " → **needs the audit**"))
        adj = m.get("adjudicator") or {}
        verdict = adj.get("material_defects")
        if jv.get("audit_skipped"):
            st.caption("⚖️ The Adjudicator's audit was skipped on Jev's clean verdict.")
        elif verdict is not None and claims.get("clean") is not None:
            agree = (claims["clean"] is True) == (verdict is False)
            st.caption(f"Adjudicator found material defects: {'yes' if verdict else 'no'} → "
                       f"Jev {'agreed' if agree else 'disagreed'}.")
        with st.expander("Claims Jev checked", expanded=False):
            for row in claims["claims"]:
                pc = row.get("p_contradicted") or 0.0
                mark = "⚠️ " if pc >= JEV_CONTRADICT_FLAG else ""
                st.markdown(f"- {mark}**{row.get('verdict') or '?'}** "
                            f"({(row.get('confidence') or 0):.2f}) — {row.get('claim', '')}")
    elif claims.get("note"):
        st.caption(f"Claim check skipped: {claims['note']}.")
    if jv.get("errors"):
        st.caption("Jev calls that failed: " + "; ".join(jv["errors"][:4]))

def render_adjudicator_review(m):
    review = m.get("adjudicator")
    if not review and (m.get("jev") or {}).get("audit_skipped"):
        st.caption("⚖️ No audit this turn — Jev's claim check found nothing in the answer "
                   "contradicted by the sources (Active mode). See the run report for the claims.")
        return
    if not review:
        return
    if review.get("err"):
        st.caption("⚖️ The Adjudicator's review did not complete this turn — the "
                   "Synthesizer's answer above is unaffected.")
        return
    material = review.get("material_defects")
    if material is True:
        note = "flagged material issues" + (" — addressed below" if m.get("revised") else "")
    elif material is False:
        note = "found no material issues"
    else:
        note = "reviewed the draft"
    with st.expander(f"⚖️ Adjudicator's review — {note}", expanded=False):
        st.caption(meta_caption(review))
        st.markdown(review["content"])
        render_reasoning(review, review.get("provider", ""))
        if material is True and m.get("revision_failed"):
            st.caption("⚠️ The Synthesizer's revision pass did not complete — the answer above "
                       "is its original, pre-revision draft.")

def api_msgs(thread):
    """Thread -> plain messages for the API (content + files, no meta)."""
    return [{"role": m["role"], "content": m["content"], "files": m.get("files", [])} for m in thread]

def _clip_context_text(text, limit):
    """Bound long context while preserving both its framing and newest detail."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    if limit < 120:
        return text[:limit]
    head = max(40, int(limit * 0.42))
    tail = limit - head - 35
    return text[:head].rstrip() + "\n...[older detail compacted]...\n" + text[-tail:].lstrip()

def split_state_capsule(content):
    """Remove the evaluator's factual memory capsule from user-visible output."""
    text = str(content or "")
    pattern = re.compile(
        re.escape(STATE_BEGIN) + r"\s*(.*?)\s*" + re.escape(STATE_END),
        flags=re.DOTALL,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return text.strip(), ""
    match = matches[-1]
    state = _clip_context_text(match.group(1), STATE_CAPSULE_LIMIT)
    visible = (text[:match.start()] + text[match.end():]).strip()
    return visible, state

def split_contribution_ledger(content):
    text = str(content or "")
    pattern = re.compile(re.escape(LEDGER_BEGIN) + r"\s*(.*?)\s*" + re.escape(LEDGER_END),
                         flags=re.DOTALL)
    matches = list(pattern.finditer(text))
    if not matches:
        return text.strip(), ""
    match = matches[-1]
    visible = (text[:match.start()] + text[match.end():]).strip()
    return visible, match.group(1).strip()

def parse_contribution_ledger(ledger_text, legend):
    """'A: fact one; fact two' lines -> {provider: [bullets]} through the
    turn's own letter legend. Letters the legend doesn't know are skipped;
    a letter marked 'nothing unique' maps to an empty list (recorded, so the
    report can say the source added nothing that survived)."""
    out = {}
    for raw in str(ledger_text or "").splitlines():
        line = raw.strip().lstrip("-*•").strip()
        found = re.match(r"^(?:answer\s+)?([A-Za-z])\s*[:\-\u2013\u2014]\s*(.*)$", line,
                         flags=re.IGNORECASE)
        if not found:
            continue
        letter, body = found.group(1).upper(), found.group(2).strip()
        name = (legend or {}).get(letter)
        if not name:
            continue
        if not body or body.lower().rstrip(".") == LEDGER_NOTHING:
            out[name] = []
            continue
        bullets = [b.strip(" .") for b in re.split(r"\s*;\s*", body) if b.strip(" .")]
        out[name] = [b for b in bullets if b.lower() != LEDGER_NOTHING][:4]
    return out

def contribution_ledger_instruction(legend):
    letters = ", ".join(sorted(legend))
    return (
        "After the continuity record, append a contribution ledger between the exact markers "
        f"{LEDGER_BEGIN} and {LEDGER_END}: one line per answer letter ({letters}), in the form "
        "`A: phrase; phrase` — two to four short phrases naming what that answer UNIQUELY "
        "contributed and that survived into your final answer (a fact, figure, caveat, angle, "
        f"or correction no other answer supplied). Write `A: {LEDGER_NOTHING}` when it added "
        "nothing the others lacked. Letters only, no prose, no source names. Conclave removes "
        "this before display."
    )

def contributions_context_section(record):
    """The newest turns' decoded ledgers, for build_working_context."""
    lines = []
    turns = [t for t in (record or {}).get("turns", []) if t.get("contributions")]
    for t in turns[-LEDGER_TURNS_IN_CONTEXT:]:
        parts = []
        for name, bullets in t["contributions"].items():
            parts.append(f"{name}: " + ("; ".join(bullets) if bullets else LEDGER_NOTHING))
        q = _clip_context_text(t.get("question", ""), 90)
        lines.append(f"Turn {t.get('turn_id', 0) + 1} ({q}) — " + " · ".join(parts))
    return "\n".join(lines)

def split_adjudicator_verdict(content):
    """Remove the Adjudicator's machine-parseable verdict block from its
    user-visible report and report whether it found a material defect.
    Returns (visible_report, material_defects) where material_defects is
    True/False when the model filled in the block, or None when it omitted
    or malformed it -- None means "unknown," never silently treated as "no."
    """
    text = str(content or "")
    pattern = re.compile(
        re.escape(ADJ_VERDICT_BEGIN) + r"\s*(.*?)\s*" + re.escape(ADJ_VERDICT_END),
        flags=re.DOTALL,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return text.strip(), None
    match = matches[-1]
    block = match.group(1)
    visible = (text[:match.start()] + text[match.end():]).strip()
    found = re.search(r"material_defects\s*:\s*(yes|no)", block, flags=re.IGNORECASE)
    material_defects = (found.group(1).lower() == "yes") if found else None
    return visible, material_defects

def fallback_state_capsule(previous, question, answer):
    """Keep continuity when a provider omits the requested state markers."""
    parts = []
    if previous:
        parts.append("Prior durable state:\n" + str(previous))
    if question:
        parts.append("Latest user turn:\n" + str(question))
    if answer:
        parts.append("Latest council conclusion:\n" + _clip_context_text(answer, 1800))
    return _clip_context_text("\n\n".join(parts), STATE_CAPSULE_LIMIT)

def build_working_context(record, original_prompt, promotions=None,
                          preset_name="Light"):
    """Create bounded neutral context for one new council turn.

    Full threads remain untouched in local history. This projection is the only
    conversation context sent to providers, keeping repeated council turns from
    growing without bound or giving one source access to another source's private
    branch conversation.
    """
    record = record or {}
    budget = CONTEXT_CHAR_BUDGET.get(preset_name, CONTEXT_CHAR_BUDGET["Custom"])
    sections = []
    if original_prompt:
        sections.append("Original objective:\n" + _clip_context_text(original_prompt, 1600))
    memory = record.get("memory")
    if memory:
        sections.append("Durable council state:\n" + _clip_context_text(memory, 4200))
    ledger = contributions_context_section(record)
    if ledger:
        sections.append("Source contributions on record (what each independent source "
                        "uniquely added, per turn):\n"
                        + _clip_context_text(ledger, max(1200, budget // 4)))

    recent = []
    for message in record.get("thread", []):
        if message.get("role") == "user" and message.get("display"):
            recent.append("User: " + str(message["display"]))
        elif message.get("role") == "assistant":
            recent.append("Council: " + str(message.get("content", "")))
    if recent:
        recent_text = "\n\n".join(recent[-4:])
        sections.append("Recent visible turns:\n" + _clip_context_text(recent_text, budget // 2))

    selected = [p for p in (promotions or []) if p.get("status", "pending") == "pending"]
    if selected:
        insights = "\n\n".join(
            f"Selected insight {index + 1}:\n{item.get('content', '')}"
            for index, item in enumerate(selected)
        )
        sections.append("User-selected private-branch insights:\n" +
                        _clip_context_text(insights, min(4000, budget // 3)))

    if not sections:
        return ""
    return _clip_context_text("\n\n".join(sections), budget)

def build_source_turn_message(question, context="", continuation=False, research=False):
    """Give every source the same explicit, turn-aware independent job contract."""
    workspace_duty = (
        "Develop a rigorous research contribution: distinguish established facts from "
        "uncertainty, assess evidence, surface important disagreements, and identify material "
        "gaps or caveats."
        if research else
        "Develop the strongest useful standalone answer you can: reason carefully, preserve "
        "important nuance, and include every relevant fact or recommendation you can support."
    )
    turn_duty = (
        "This is a follow-up turn in an ongoing conversation. Use the shared context only to "
        "maintain continuity, then answer the current user turn as the controlling request. "
        "Update, correct, or extend prior conclusions when warranted; do not merely repeat the "
        "earlier answer or restart the conversation."
        if continuation else
        "This is the initial turn. Analyze the user's request from first principles and establish "
        "a complete, well-supported foundation for any later follow-up."
    )
    parts = [
        "You are an independent source for Conclave. Your job is to provide one high-quality "
        "source answer for a separate Synthesizer or research evaluator. Work independently, "
        "answer directly and accurately, and never assume another source will repair omissions. "
        "Do not discuss council mechanics or defer the work to the evaluator.",
        workspace_duty,
        turn_duty,
    ]
    if context:
        parts.append("Shared conversation context (factual summary, not another source answer):\n" + context)
    parts.append("Current user turn:\n" + str(question or ""))
    return "\n\n".join(parts)

def build_independent_memo_prompt(question, context="", continuation=False):
    """The locked independent-memo contract (architecture review Phase 2C).
    Both the Synthesizer and Adjudicator answer the user's actual question on
    their own, before seeing sources, each other, or any draft -- so each
    role's own first take becomes just one more fallible, anonymized
    contribution in the evidence pool, never privileged ground truth. Shares
    build_source_turn_message's structure/style deliberately -- same idea
    (independent job contract) applied to a different role. Returns plain
    text; the caller wraps it as one user-role message."""
    turn_duty = (
        "This is a follow-up turn. Use the shared context only to maintain continuity, then "
        "answer the current user turn as the controlling request on your own -- do not simply "
        "restate an earlier answer."
        if continuation else
        "This is the initial turn. Analyze the user's request from first principles."
    )
    parts = [
        "Answer the user's question independently, before seeing any other contribution, "
        "source answer, or draft. Produce a compact memo: your proposed conclusion, the "
        "material claims it rests on, your reasoning, unique considerations, genuine "
        "uncertainty, supporting evidence, and likely failure points. This memo is a fallible "
        "hypothesis, not privileged ground truth -- it will be anonymized and reassessed "
        "alongside other evidence later. Do not reference your future role, mention that this "
        "is a memo, or attempt to anticipate what other models will say.",
        turn_duty,
    ]
    if context:
        parts.append("Shared conversation context (factual summary, not another source answer):\n" + context)
    parts.append("Current user turn:\n" + str(question or ""))
    return "\n\n".join(parts)

def next_turn_id(record):
    ids = []
    for turn in (record or {}).get("turns", []):
        if isinstance(turn.get("turn_id"), int):
            ids.append(turn["turn_id"])
    for message in (record or {}).get("thread", []):
        if isinstance(message.get("turn_id"), int):
            ids.append(message["turn_id"])
    return max(ids, default=-1) + 1

def eval_provider_of(prefix):
    return st.session_state.get(f"{prefix}_provider", "Claude")

def eval_label(prefix):
    p = eval_provider_of(prefix)
    if p == "Claude":  return f"Claude {st.session_state.get(f'{prefix}_cla_model', 'Opus 5.5')}"
    if p == "ChatGPT":
        _lbl = st.session_state.get(f"{prefix}_gpt_model", "Astra (flagship)")
        _ver = chatgpt_version_of(CHATGPT_MODELS.get(_lbl, MODEL_GPT))
        return f"ChatGPT GPT-{_ver} {_lbl.split()[0]}"
    if p == "Gemini":
        if prefix == "k_res":
            return f"Gemini {SYNTH_GEMINI_LABEL}"
        return f"Gemini {st.session_state.get(f'{prefix}_gem_model', 'Pro (3.1)')}"
    if p == "DeepSeek":
        model = st.session_state.get(f"{prefix}_ds_model", "V4.1 Flash (fast · vision)")
        return f"DeepSeek {model.split(' (')[0]}"
    if p == "Qwen":
        model = st.session_state.get(f"{prefix}_qwen_model", "Max 3.8 (flagship)")
        return f"Qwen {model.split(' (')[0]}"
    if p == "Mistral":
        model = st.session_state.get(f"{prefix}_mistral_model", "Medium 3.5 (advanced)")
        return f"Mistral {model.split(' (')[0]}"
    if p == "Kimi":
        model = st.session_state.get(f"{prefix}_kimi_model", "K3.1 Pro (flagship)")
        return f"Kimi {model.split(' (')[0]}"
    if p == "Meta":
        model = st.session_state.get(f"{prefix}_meta_model", "Spark 1.3 (balanced)")
        return f"Meta Muse {model.split(' (')[0]}"
    if p == "MiniMax":
        model = st.session_state.get(f"{prefix}_mm_model", "M3 (flagship)")
        return f"MiniMax {model.split(' (')[0]}"
    if p == "NVIDIA":
        model = st.session_state.get(f"{prefix}_nv_model", "Nemotron 3 Ultra (flagship)")
        return f"NVIDIA {model.split(' (')[0]}"
    if p == "GLM":
        model = st.session_state.get(f"{prefix}_glm_model", "GLM-5.3 (flagship)")
        return f"Z.ai {model.split(' (')[0]}"
    if p == "MiMo":
        model = st.session_state.get(f"{prefix}_mimo_model", "V2.6 Pro (flagship)")
        return f"Xiaomi MiMo {model.split(' (')[0]}"
    return f"Grok {GROK_VER}"

def evaluator_action_label(prefix, role="Synthesizer"):
    """Compact, live model identity for composer settings buttons."""
    provider = eval_provider_of(prefix)
    lead = "⭐" if role == "Synthesizer" else "\U0001F52C"
    web = " · \U0001F310" if st.session_state.get(f"{prefix}_web") else ""
    return f"{lead} {role} · {ICON.get(provider, '')} {eval_label(prefix)}{web}"

def _wsync(app_key, ns="w", after_change=None):
    def _cb():
        st.session_state[app_key] = st.session_state[f"{ns}_{app_key}"]
        if after_change:
            after_change()
    return _cb

def _reseed(wk, value):
    """Refresh an ALREADY-MOUNTED controlled widget from its app key, and report
    whether it did.

    Once a keyed widget has state, Streamlit ignores its index=/value= default —
    so a mirror widget that stays mounted (the "sc" side-chat dials live in an
    always-rendered popover) showed a STALE value after the same app key changed
    elsewhere (sidebar card, preset button). Writing the app key's value onto the
    widget key before instantiation fixes that.

    Returns True when the widget key already existed (state now drives it, so the
    caller must NOT also pass index=/value= — doing both is what triggers
    Streamlit's "created with a default value but also had its value set via the
    Session State API" warning on every rerun, and also violates this project's
    own rule about never passing value=/index= to keyed widgets). Returns False
    on first mount, where the default IS how the app key reaches the widget —
    that path is exactly the documented dialog first-mount fix, so it must stay.
    """
    if wk in st.session_state:
        st.session_state[wk] = value
        return True
    return False

def c_selectbox(label, options, app_key, ns="w", after_change=None, **kw):
    wk = f"{ns}_{app_key}"
    cur = st.session_state.get(app_key)
    if wk in st.session_state:
        if cur in options:
            st.session_state[wk] = cur          # refresh from the app key
        return st.selectbox(label, options, key=wk,
                            on_change=_wsync(app_key, ns, after_change), **kw)
    idx = options.index(cur) if cur in options else 0   # first mount: default carries it
    return st.selectbox(label, options, index=idx, key=wk,
                        on_change=_wsync(app_key, ns, after_change), **kw)

def c_toggle(label, app_key, ns="w", after_change=None, **kw):
    wk = f"{ns}_{app_key}"
    val = bool(st.session_state.get(app_key, False))
    if _reseed(wk, val):
        return st.toggle(label, key=wk,
                         on_change=_wsync(app_key, ns, after_change), **kw)
    return st.toggle(label, value=val, key=wk,
                     on_change=_wsync(app_key, ns, after_change), **kw)

def c_checkbox(label, app_key, ns="w", **kw):
    wk = f"{ns}_{app_key}"
    val = bool(st.session_state.get(app_key, False))
    if _reseed(wk, val):
        return st.checkbox(label, key=wk, on_change=_wsync(app_key, ns), **kw)
    return st.checkbox(label, value=val, key=wk, on_change=_wsync(app_key, ns), **kw)

def c_number(label, app_key, ns="w", **kw):
    wk = f"{ns}_{app_key}"
    val = int(st.session_state.get(app_key, 1))
    if _reseed(wk, val):
        return st.number_input(label, key=wk, on_change=_wsync(app_key, ns), **kw)
    return st.number_input(label, value=val, key=wk, on_change=_wsync(app_key, ns), **kw)

def c_segmented(label, options, app_key, ns="w", after_change=None, **kw):
    wk = f"{ns}_{app_key}"
    cur = st.session_state.get(app_key)
    if wk in st.session_state:
        if cur in options:
            st.session_state[wk] = cur
        return st.segmented_control(label, options, key=wk,
                                    on_change=_wsync(app_key, ns, after_change), **kw)
    default = cur if cur in options else options[0]
    return st.segmented_control(label, options, default=default, key=wk,
                                on_change=_wsync(app_key, ns, after_change), **kw)

def _sync_source_claude_search_cap():
    st.session_state["k_cla_maxsearch"] = recommended_claude_search_cap(
        st.session_state.get("k_cla_model", "Sonnet 5.5"),
        st.session_state.get("k_cla", "Medium"),
    )

def _sync_evaluator_claude_search_cap(prefix):
    st.session_state[f"{prefix}_maxsearch"] = recommended_claude_search_cap(
        st.session_state.get(f"{prefix}_cla_model", "Opus 5.5"),
        st.session_state.get(f"{prefix}_cla_eff", "High"),
    )

def _claude_search_recommendation_caption(prefix):
    model = st.session_state.get(f"{prefix}_cla_model", "Opus 5.5")
    effort = st.session_state.get(f"{prefix}_cla_eff", "High")
    cap = recommended_claude_search_cap(model, effort)
    st.caption(f"Conclave recommends {cap} for {model} · {effort}. Changing either "
               "refreshes the cap; you can still override it.")

_HAIKU_THINKING_CAPTION = ("Haiku has no effort dial; the thinking box turns on its manual "
                           "extended thinking (8,000-token budget).")
_DEEPSEEK_CAPTION = ("V4.1 Flash reads "
                     "images. DeepSeek can't search the web itself (its search tool was removed on "
                     "2026-09-10). As a source it reads the shared 🔎 Tavily + Brave pages while web search is on, or sits that run out "
                     "without them — same as Mistral and NVIDIA. V4 Pro left "
                     "the menu: since 2026-09-14 DeepSeek routes it to V4.1 Flash.")

def _gemini_minimal_caption(model_key, level_key):
    _mid = GEMINI_MODELS.get(st.session_state.get(model_key), "")
    if (st.session_state.get(level_key) == "Minimal" and _mid not in GEMINI_MINIMAL_MODELS):
        st.caption("Minimal thinking is Flash-Lite only — this model runs at Standard.")

def _astra_instant_caption(model_key, effort_key):
    if (CHATGPT_MODELS.get(st.session_state.get(model_key)) in CHATGPT_NO_NONE
            and st.session_state.get(effort_key) == "Instant"):
        st.caption("This model has no Instant tier (reasoning can't be turned off) — "
                   "this call runs at Low effort.")

def source_setting_summary(n):
    """One-line current-intensity label for a source's side-chat dial popover."""
    if n == "ChatGPT":
        model = st.session_state["k_gpt_model"].split()[0]
        return f"⚙️ {model} · {st.session_state['k_gpt']} · {st.session_state['k_gpt_speed']}"
    if n == "Claude":
        cm = st.session_state["k_cla_model"]
        if CLAUDE_MODELS[cm] in CLAUDE_NO_DIALS:
            return f"⚙️ {cm}"
        return f"⚙️ {cm} · {st.session_state['k_cla']}"
    if n == "Gemini":
        return f"⚙️ {st.session_state['k_gem']} · {st.session_state['k_gem_t']}"
    if n == "DeepSeek":
        thinking = "Thinking" if st.session_state["k_ds_think"] else "Direct"
        effort = f" · {st.session_state['k_ds_effort']}" if st.session_state["k_ds_think"] else ""
        return f"⚙️ {st.session_state['k_ds_model']} · {thinking}{effort}"
    if n == "Qwen":
        _qe = st.session_state.get("k_qwen_effort", "Default")
        if (st.session_state["k_qwen_think"] and _qe != "Default"
                and QWEN_MODELS[st.session_state["k_qwen_model"]] in QWEN_GRADED_EFFORT_MODELS):
            thinking = f"Thinking · {_qe}"
        else:
            thinking = f"Thinking · {st.session_state['k_qwen_budget']:,} tok" if st.session_state["k_qwen_think"] else "Direct"
        return f"⚙️ {st.session_state['k_qwen_model']} · {thinking}"
    if n == "Mistral":
        return f"⚙️ {st.session_state['k_mistral_model']} · reasoning {st.session_state['k_mistral_eff']}"
    if n == "Kimi":
        _km = st.session_state["k_kimi_model"]
        _kt = (f"reasoning {st.session_state['k_kimi_effort']}" if KIMI_MODELS[_km] in KIMI_ALWAYS_THINK
               else ("Thinking" if st.session_state["k_kimi_think"] else "Direct"))
        return f"⚙️ {_km} · {_kt}"
    if n == "Meta":
        return f"⚙️ {st.session_state['k_meta_model']} · reasoning {st.session_state['k_meta_eff']}"
    if n == "MiniMax":
        _mt = ("Adaptive thinking" if (st.session_state["k_mm_think"] or
                                       MINIMAX_MODELS[st.session_state["k_mm_model"]] not in MINIMAX_THINK_OFF_OK)
               else "Direct")
        return f"⚙️ {st.session_state['k_mm_model']} · {_mt} · {st.session_state.get('k_mm_speed', 'Standard')}"
    if n == "NVIDIA":
        _nt = f"Thinking · {st.session_state['k_nv_budget']:,} tok" if st.session_state["k_nv_think"] else "Direct"
        return f"⚙️ {st.session_state['k_nv_model']} · {_nt}"
    if n == "GLM":
        _gt = (f"reasoning {st.session_state['k_glm_eff']}"
               if GLM_MODELS[st.session_state['k_glm_model']] in GLM_ALWAYS_THINK
               else ("Thinking" if st.session_state["k_glm_think"] else "Direct"))
        return f"⚙️ {st.session_state['k_glm_model']} · {_gt}"
    if n == "MiMo":
        return (f"⚙️ {st.session_state['k_mimo_model']} · "
                f"{'Thinking' if st.session_state['k_mimo_think'] else 'Direct'}")
    return f"⚙️ Mode: {st.session_state['k_grok']}"

def render_source_intensity_dials(n, ns="sc"):
    """Render provider n's real effort/model dials as controlled widgets under
    namespace `ns`, writing back to the sidebar card's app keys."""
    if n == "ChatGPT":
        c_selectbox("GPT model", list(CHATGPT_MODELS.keys()), "k_gpt_model", ns=ns)
        c_selectbox("Reasoning effort", list(CHATGPT_EFFORT.keys()), "k_gpt", ns=ns)
        c_selectbox("Processing speed", list(CHATGPT_SPEED.keys()), "k_gpt_speed", ns=ns)
        st.caption(CHATGPT_SPEED_DESC[st.session_state["k_gpt_speed"]])
        _astra_instant_caption("k_gpt_model", "k_gpt")
    elif n == "Claude":
        c_selectbox("Model", list(CLAUDE_MODELS.keys()), "k_cla_model", ns=ns,
                    after_change=_sync_source_claude_search_cap)
        _mid = CLAUDE_MODELS[st.session_state["k_cla_model"]]
        _nod = _mid in CLAUDE_NO_DIALS
        c_selectbox("Effort", list(CLAUDE_EFFORT.keys()), "k_cla", ns=ns, disabled=_nod,
                    after_change=_sync_source_claude_search_cap)
        _forced = claude_thinking_forced(_mid, CLAUDE_EFFORT[st.session_state["k_cla"]])
        c_checkbox("Adaptive thinking", "k_cla_think", ns=ns,
                   disabled=(_mid in CLAUDE_ALWAYS_THINK or _forced))
        if _nod:
            st.caption(_HAIKU_THINKING_CAPTION)
        elif _mid in CLAUDE_ALWAYS_THINK:
            st.caption(f"{st.session_state['k_cla_model']} always thinks — effort sets how hard.")
        elif _forced:
            st.caption("This model requires thinking at Extra/Max effort — drop to High to turn it off.")
    elif n == "Gemini":
        c_selectbox("Model", list(GEMINI_MODELS.keys()), "k_gem", ns=ns)
        c_selectbox("Thinking level", list(GEMINI_THINK.keys()), "k_gem_t", ns=ns)
        _gemini_minimal_caption("k_gem", "k_gem_t")
    elif n == "DeepSeek":
        c_selectbox("Model", list(DEEPSEEK_MODELS.keys()), "k_ds_model", ns=ns,
                    after_change=_sync_source_deepseek_web)
        c_toggle("Thinking", "k_ds_think", ns=ns)
        c_selectbox("Reasoning effort", list(DEEPSEEK_EFFORT.keys()), "k_ds_effort",
                    ns=ns, disabled=not st.session_state["k_ds_think"])
        st.caption(_DEEPSEEK_CAPTION)
    elif n == "Qwen":
        c_selectbox("Model", list(QWEN_MODELS.keys()), "k_qwen_model", ns=ns,
                    after_change=_sync_source_qwen_web)
        c_toggle("Thinking", "k_qwen_think", ns=ns)
        _qgraded = QWEN_MODELS[st.session_state["k_qwen_model"]] in QWEN_GRADED_EFFORT_MODELS
        c_selectbox("Reasoning effort (3.8 series)", list(QWEN_EFFORT.keys()), "k_qwen_effort",
                    ns=ns, disabled=not (st.session_state["k_qwen_think"] and _qgraded))
        c_number("Thinking budget (tokens)", "k_qwen_budget", ns=ns, min_value=500,
                max_value=32000, step=500,
                disabled=not st.session_state["k_qwen_think"] or
                (_qgraded and st.session_state.get("k_qwen_effort", "Default") != "Default"))
    elif n == "Mistral":
        c_selectbox("Model", list(MISTRAL_MODELS.keys()), "k_mistral_model", ns=ns)
        c_selectbox("Reasoning effort", list(MISTRAL_EFFORT.keys()), "k_mistral_eff", ns=ns,
                    disabled=MISTRAL_MODELS[st.session_state["k_mistral_model"]] not in MISTRAL_REASONING_MODELS)
    elif n == "Kimi":
        c_selectbox("Model", list(KIMI_MODELS.keys()), "k_kimi_model", ns=ns)
        if KIMI_MODELS[st.session_state["k_kimi_model"]] in KIMI_ALWAYS_THINK:
            c_selectbox("Reasoning effort", list(KIMI_EFFORT.keys()), "k_kimi_effort", ns=ns)
        else:
            c_toggle("Thinking", "k_kimi_think", ns=ns)
    elif n == "Meta":
        c_selectbox("Model", list(META_MODELS.keys()), "k_meta_model", ns=ns)
        c_selectbox("Reasoning effort", list(META_EFFORT.keys()), "k_meta_eff", ns=ns)
    elif n == "MiniMax":
        c_selectbox("Model", list(MINIMAX_MODELS.keys()), "k_mm_model", ns=ns)
        c_toggle("Adaptive thinking", "k_mm_think", ns=ns,
                 disabled=MINIMAX_MODELS[st.session_state["k_mm_model"]] not in MINIMAX_THINK_OFF_OK)
        c_selectbox("Processing speed", list(MINIMAX_SPEED.keys()), "k_mm_speed", ns=ns)
        st.caption(MINIMAX_SPEED_DESC[st.session_state.get("k_mm_speed", "Standard")])
    elif n == "NVIDIA":
        c_selectbox("Model", list(NVIDIA_MODELS.keys()), "k_nv_model", ns=ns)
        c_toggle("Thinking", "k_nv_think", ns=ns)
        c_number("Reasoning budget (tokens)", "k_nv_budget", ns=ns, min_value=1000,
                 max_value=28000, step=1000, disabled=not st.session_state["k_nv_think"])
    elif n == "GLM":
        c_selectbox("Model", list(GLM_MODELS.keys()), "k_glm_model", ns=ns)
        if GLM_MODELS[st.session_state["k_glm_model"]] in GLM_ALWAYS_THINK:
            c_selectbox("Reasoning effort", list(GLM_EFFORT.keys()), "k_glm_eff", ns=ns)
        else:
            c_toggle("Thinking", "k_glm_think", ns=ns)
    elif n == "MiMo":
        c_selectbox("Model", list(MIMO_MODELS.keys()), "k_mimo_model", ns=ns)
        c_toggle("Thinking", "k_mimo_think", ns=ns)
    else:  # Grok
        c_selectbox("Mode", GROK_MODES, "k_grok", ns=ns)

def synth_locked_provider():
    record = st.session_state.get("synth")
    if not record or st.session_state.get("_synth_unlocked"):
        return None
    return record.get("provider") or None

def _enforce_synth_lock():
    """Re-assert the lock on every run (a loaded chat, a stale picker
    value, or any non-UI path that moved k_syn_provider)."""
    locked = synth_locked_provider()
    if locked and locked in EVAL_PROVIDERS and st.session_state.get("k_syn_provider") != locked:
        active_tier = current_preset_name()
        st.session_state["k_syn_provider"] = locked
        _evaluator_provider_changed("k_syn", active_tier)
        _enforce_adjudicator_cross_provider(active_tier)

def _unlock_synth_provider():
    st.session_state["_synth_unlocked"] = True

def _set_synth_provider(provider):
    locked = synth_locked_provider()
    if locked and provider != locked:
        return
    active_tier = current_preset_name()
    st.session_state["k_syn_provider"] = provider
    _evaluator_provider_changed("k_syn", active_tier)
    _enforce_adjudicator_cross_provider(active_tier)

def _next_provider_other_than(forbidden):
    """First MODEL_ORDER entry that isn't `forbidden` -- deterministic,
    predictable fallback for the cross-provider constraint below."""
    for p in MODEL_ORDER:
        if p != forbidden:
            return p
    return MODEL_ORDER[0]  # unreachable with six providers; defensive only

def _enforce_adjudicator_cross_provider(active_tier=None):
    if st.session_state.get("k_adj_provider") == st.session_state.get("k_syn_provider"):
        if active_tier is None:
            active_tier = current_preset_name()
        st.session_state["k_adj_provider"] = _next_provider_other_than(
            st.session_state["k_syn_provider"])
        _evaluator_provider_changed("k_adj", active_tier)

def _set_adjudicator_provider(provider):
    """Sidebar Adjudicator picker strip's on_click -- mirrors
    _set_synth_provider: retunes the Adjudicator itself to the active preset
    tier, then handles the cross-provider bump in the other direction if the
    new choice collides with the current Synthesizer."""
    active_tier = current_preset_name()
    st.session_state["k_adj_provider"] = provider
    _evaluator_provider_changed("k_adj", active_tier)
    if st.session_state.get("k_syn_provider") == provider:
        if synth_locked_provider() == provider:
            st.session_state["k_adj_provider"] = _next_provider_other_than(provider)
            _evaluator_provider_changed("k_adj", active_tier)
            return
        st.session_state["k_syn_provider"] = _next_provider_other_than(provider)
        _evaluator_provider_changed("k_syn", active_tier)

def _evaluator_provider_changed(prefix, active_tier):
    """Shared reaction to the evaluator provider changing, for role `prefix`
    ("k_syn"/"k_fu"/"k_res"/"k_adj") -- used by both the settings dialogs'
    provider selectbox and the sidebar's compact Synthesizer/Adjudicator
    picker strips, so every entry point behaves identically. `active_tier`
    must be captured BEFORE the provider actually switches (see callers)."""
    provider = st.session_state.get(f"{prefix}_provider", "Claude")
    if prefix in ("k_syn", "k_adj", "k_fu") and active_tier in PRESETS:
        # The named preset is an effort tier, not a provider choice. Preserve
        # the newly selected provider and tune its native controls to the tier
        # that was active before this change.
        _apply_by_prefix = {
            "k_syn": _apply_synth_preset,
            "k_adj": _apply_adjudicator_preset,
            "k_fu": _apply_followup_preset,
        }
        _apply_by_prefix[prefix](active_tier, provider)
    if provider in NO_WEB_PROVIDERS:
        st.session_state[f"{prefix}_web"] = False
    elif prefix in ("k_syn", "k_fu", "k_adj") and st.session_state.get("k_web_all"):
        st.session_state[f"{prefix}_web"] = (
            provider != "Qwen" or _evaluator_qwen_web_supported(prefix))
    if provider == "DeepSeek":
        _sync_evaluator_deepseek_web_toggle(prefix)
    if provider == "Claude":
        _sync_evaluator_claude_search_cap(prefix)

def render_evaluator_controls(prefix, show_provider_picker=True):
    """Provider selector + that provider's real dials (controlled widgets — see
    above). Claude also gets a model sub-selector. Returns the provider.
    `show_provider_picker=False` skips the "Model" provider selectbox itself --
    used when an outer picker (the sidebar's Synthesizer icon strip) already
    covers that choice, so the two controls don't sit redundantly stacked."""
    sync_cap = lambda: _sync_evaluator_claude_search_cap(prefix)
    active_tier = current_preset_name() if prefix in ("k_syn", "k_fu") else "Custom"
    if show_provider_picker:
        p = c_selectbox("Model", EVAL_PROVIDERS, f"{prefix}_provider",
                        after_change=lambda: _evaluator_provider_changed(prefix, active_tier))
    else:
        p = st.session_state.get(f"{prefix}_provider", "Claude")
    if p == "Claude":
        sel = c_selectbox("Claude model", list(CLAUDE_MODELS.keys()), f"{prefix}_cla_model",
                          after_change=sync_cap)
        mid = CLAUDE_MODELS[sel]
        no_dials = mid in CLAUDE_NO_DIALS
        c_selectbox("Effort", list(CLAUDE_EFFORT.keys()), f"{prefix}_cla_eff",
                    disabled=no_dials, after_change=sync_cap)
        forced = claude_thinking_forced(
            mid, CLAUDE_EFFORT[st.session_state.get(f"{prefix}_cla_eff", "High")])
        c_checkbox("Adaptive thinking", f"{prefix}_cla_think",
                   disabled=(mid in CLAUDE_ALWAYS_THINK or forced))
        if no_dials:
            st.caption(_HAIKU_THINKING_CAPTION)
        elif mid in CLAUDE_ALWAYS_THINK:
            st.caption(f"{sel} always thinks — effort sets how hard.")
        elif forced:
            st.caption("This model requires thinking at Extra/Max effort — drop to High to turn it off.")
    elif p == "ChatGPT":
        c_selectbox("GPT model", list(CHATGPT_MODELS.keys()), f"{prefix}_gpt_model")
        c_selectbox("Reasoning effort", list(CHATGPT_EFFORT.keys()), f"{prefix}_gpt_eff")
        c_selectbox("Processing speed", list(CHATGPT_SPEED.keys()), f"{prefix}_gpt_speed")
        st.caption(CHATGPT_SPEED_DESC[st.session_state.get(f"{prefix}_gpt_speed", "Standard")])
        _astra_instant_caption(f"{prefix}_gpt_model", f"{prefix}_gpt_eff")
    elif p == "Gemini":
        if prefix == "k_res":
            st.caption(f"Gemini {SYNTH_GEMINI_LABEL} — the flagship reasoning model.")
        else:
            c_selectbox("Model", list(GEMINI_MODELS.keys()), f"{prefix}_gem_model")
        c_selectbox("Thinking level", list(GEMINI_THINK.keys()), f"{prefix}_gem_t")
        if prefix != "k_res":
            _gemini_minimal_caption(f"{prefix}_gem_model", f"{prefix}_gem_t")
    elif p == "DeepSeek":
        c_selectbox("DeepSeek model", list(DEEPSEEK_MODELS.keys()), f"{prefix}_ds_model",
                    after_change=lambda: _sync_evaluator_deepseek_web(prefix))
        c_toggle("Thinking", f"{prefix}_ds_think")
        c_selectbox("Reasoning effort", list(DEEPSEEK_EFFORT.keys()), f"{prefix}_ds_effort",
                    disabled=not st.session_state.get(f"{prefix}_ds_think", True))
        st.caption(_DEEPSEEK_CAPTION)
    elif p == "Qwen":
        c_selectbox("Qwen model", list(QWEN_MODELS.keys()), f"{prefix}_qwen_model",
                    after_change=lambda: _sync_evaluator_qwen_web(prefix))
        c_toggle("Thinking", f"{prefix}_qwen_think")
        _qgraded = QWEN_MODELS[st.session_state.get(f"{prefix}_qwen_model", "Max 3.8 (flagship)")] in QWEN_GRADED_EFFORT_MODELS
        _qthink = st.session_state.get(f"{prefix}_qwen_think", True)
        c_selectbox("Reasoning effort (3.8 series)", list(QWEN_EFFORT.keys()), f"{prefix}_qwen_effort",
                    disabled=not (_qthink and _qgraded))
        c_number("Thinking budget (tokens)", f"{prefix}_qwen_budget", min_value=500,
                 max_value=32000, step=500,
                 disabled=not _qthink or
                 (_qgraded and st.session_state.get(f"{prefix}_qwen_effort", "Default") != "Default"))
        st.caption("Images supported; PDFs text-only. The 3.8 series takes a graded effort "
                   "(Default keeps the token budget instead) and can web-verify ($0.573 per "
                   "1,000 searches); the 3.7 models cannot search.")
        if (st.session_state.get(f"{prefix}_web") and
                not _evaluator_qwen_web_supported(prefix)):
            st.caption("⚠️ Web verify needs a 3.8 Qwen model — this seat will not search.")
    elif p == "Mistral":
        c_selectbox("Mistral model", list(MISTRAL_MODELS.keys()), f"{prefix}_mistral_model")
        _mm = MISTRAL_MODELS[st.session_state.get(f"{prefix}_mistral_model", "Medium 3.5 (advanced)")]
        c_selectbox("Reasoning effort", list(MISTRAL_EFFORT.keys()), f"{prefix}_mistral_eff",
                    disabled=_mm not in MISTRAL_REASONING_MODELS)
        st.caption("Large 4, Medium 3.5 and Small 4 take a reasoning dial; Large 3 has none. "
                   "Mistral can't search the web itself; images on Large 4, Medium 3.5 and "
                   "Large 3; PDFs read through Mistral's Document QnA.")
    elif p == "Kimi":
        c_selectbox("Kimi model", list(KIMI_MODELS.keys()), f"{prefix}_kimi_model")
        _kmid = KIMI_MODELS[st.session_state.get(f"{prefix}_kimi_model", "K3.1 Pro (flagship)")]
        if _kmid in KIMI_ALWAYS_THINK:
            c_selectbox("Reasoning effort", list(KIMI_EFFORT.keys()), f"{prefix}_kimi_effort")
        else:
            c_toggle("Thinking", f"{prefix}_kimi_think")
        st.caption("K3 always reasons (effort dial); K2.6 toggles thinking. Images yes; "
                   "PDFs read through Moonshot's Files API; web "
                   "verification uses Moonshot's web search API ($0.002 per search).")
    elif p == "Meta":
        c_selectbox("Muse model", list(META_MODELS.keys()), f"{prefix}_meta_model")
        c_selectbox("Reasoning effort", list(META_EFFORT.keys()), f"{prefix}_meta_eff")
        st.caption("Wonder 1.0 (preview) is Meta's most capable model; Spark 1.3 the balanced one. Reasoning dial minimal → max; images and "
                   "PDFs supported; web verification uses Meta's own search "
                   "($2.50 per 1,000 searches on top of tokens) and returns a reasoning summary.")
    elif p == "MiniMax":
        c_selectbox("MiniMax model", list(MINIMAX_MODELS.keys()), f"{prefix}_mm_model")
        _mmid = MINIMAX_MODELS[st.session_state.get(f"{prefix}_mm_model", "M3 (flagship)")]
        c_toggle("Adaptive thinking", f"{prefix}_mm_think",
                 disabled=_mmid not in MINIMAX_THINK_OFF_OK)
        c_selectbox("Processing speed", list(MINIMAX_SPEED.keys()), f"{prefix}_mm_speed")
        st.caption(MINIMAX_SPEED_DESC[st.session_state.get(f"{prefix}_mm_speed", "Standard")])
        st.caption("M3 can switch adaptive thinking off. Images yes, "
                   "PDFs text-only; web verification uses MiniMax's own search "
                   "(beta, $0.01 per request).")
    elif p == "NVIDIA":
        c_selectbox("Nemotron model", list(NVIDIA_MODELS.keys()), f"{prefix}_nv_model")
        c_toggle("Thinking", f"{prefix}_nv_think")
        c_number("Reasoning budget (tokens)", f"{prefix}_nv_budget", min_value=1000,
                 max_value=28000, step=1000,
                 disabled=not st.session_state.get(f"{prefix}_nv_think", True))
        st.caption("NVIDIA's hosted catalog is a free trial (~40 requests/min, no billing). "
                   "No web search; images only on Nano Omni; the budget caps how long it reasons.")
    elif p == "GLM":
        c_selectbox("GLM model", list(GLM_MODELS.keys()), f"{prefix}_glm_model")
        _gmid = GLM_MODELS[st.session_state.get(f"{prefix}_glm_model", "GLM-5.3 (flagship)")]
        if _gmid in GLM_ALWAYS_THINK:
            c_selectbox("Reasoning effort", list(GLM_EFFORT.keys()), f"{prefix}_glm_eff")
        else:
            c_toggle("Thinking", f"{prefix}_glm_think")
        st.caption("GLM-5.3 and 5.3 Flash always reason (Low/High/Max dial). Web verification uses Z.ai's hosted search ($0.01 per use). "
                   "Images on 5.3 Flash only.")
    elif p == "MiMo":
        c_selectbox("MiMo model", list(MIMO_MODELS.keys()), f"{prefix}_mimo_model")
        c_toggle("Thinking", f"{prefix}_mimo_think")
        st.caption("Xiaomi MiMo V2.6: thinking on or off (no effort dial). Web verification uses "
                   "MiMo's hosted search ($5 per 1,000 searches; switch on the Web Search Plugin "
                   "in the MiMo console first). Reads images.")
    else:  # Grok
        c_selectbox("Mode", GROK_MODES, f"{prefix}_grok_mode")
    return p

def evaluator_job(prefix, messages, web, max_searches=5, *, keys_now=None):
    """Build (provider, model_id, cfg, job) from the chosen provider's native dials."""
    p = eval_provider_of(prefix)
    if p == "Claude":
        mid = CLAUDE_MODELS[st.session_state.get(f"{prefix}_cla_model", "Opus 5.5")]
        cfg = {"model_id": mid, "effort": CLAUDE_EFFORT[st.session_state.get(f"{prefix}_cla_eff", "High")],
               "thinking": st.session_state.get(f"{prefix}_cla_think", True), "max_searches": max_searches}
    elif p == "ChatGPT":
        mid = CHATGPT_MODELS[st.session_state.get(f"{prefix}_gpt_model", "Astra (flagship)")]
        cfg = {"model_id": mid,
               "effort": CHATGPT_EFFORT[st.session_state.get(f"{prefix}_gpt_eff", "High")],
               "speed": st.session_state.get(f"{prefix}_gpt_speed", "Standard")}
    elif p == "Gemini":
        mid = (SYNTH_GEMINI if prefix == "k_res" else
               GEMINI_MODELS[st.session_state.get(f"{prefix}_gem_model", "Pro (3.1)")])
        cfg = {"model_id": mid, "level": GEMINI_THINK[st.session_state.get(f"{prefix}_gem_t", "Extended")]}
    elif p == "DeepSeek":
        mid = DEEPSEEK_MODELS[st.session_state.get(f"{prefix}_ds_model", "V4.1 Flash (fast · vision)")]
        cfg = {"model_id": mid,
               "thinking": st.session_state.get(f"{prefix}_ds_think", True),
               "effort": DEEPSEEK_EFFORT[st.session_state.get(f"{prefix}_ds_effort", "Max")]}
    elif p == "Qwen":
        mid = QWEN_MODELS[st.session_state.get(f"{prefix}_qwen_model", "Max 3.8 (flagship)")]
        cfg = {"model_id": mid,
               "thinking": st.session_state.get(f"{prefix}_qwen_think", True),
               "budget": st.session_state.get(f"{prefix}_qwen_budget", 8000),
               "effort": QWEN_EFFORT.get(st.session_state.get(f"{prefix}_qwen_effort", "Default"))}
    elif p == "Mistral":
        mid = MISTRAL_MODELS[st.session_state.get(f"{prefix}_mistral_model", "Medium 3.5 (advanced)")]
        cfg = {"model_id": mid,
               "effort": MISTRAL_EFFORT[st.session_state.get(f"{prefix}_mistral_eff", "Medium")]}
    elif p == "Kimi":
        mid = KIMI_MODELS[st.session_state.get(f"{prefix}_kimi_model", "K3.1 Pro (flagship)")]
        cfg = {"model_id": mid,
               "thinking": st.session_state.get(f"{prefix}_kimi_think", True),
               "effort": KIMI_EFFORT[st.session_state.get(f"{prefix}_kimi_effort", "High")]}
    elif p == "Meta":
        mid = META_MODELS[st.session_state.get(f"{prefix}_meta_model", "Spark 1.3 (balanced)")]
        cfg = {"model_id": mid,
               "effort": META_EFFORT[st.session_state.get(f"{prefix}_meta_eff", "Medium")]}
    elif p == "MiniMax":
        mid = MINIMAX_MODELS[st.session_state.get(f"{prefix}_mm_model", "M3 (flagship)")]
        cfg = {"model_id": mid, "thinking": st.session_state.get(f"{prefix}_mm_think", True),
               "speed": st.session_state.get(f"{prefix}_mm_speed", "Standard")}
    elif p == "NVIDIA":
        mid = NVIDIA_MODELS[st.session_state.get(f"{prefix}_nv_model", "Nemotron 3 Ultra (flagship)")]
        cfg = {"model_id": mid, "thinking": st.session_state.get(f"{prefix}_nv_think", True),
               "budget": st.session_state.get(f"{prefix}_nv_budget", 16000)}
    elif p == "GLM":
        mid = GLM_MODELS[st.session_state.get(f"{prefix}_glm_model", "GLM-5.3 (flagship)")]
        cfg = {"model_id": mid, "thinking": st.session_state.get(f"{prefix}_glm_think", True),
               "effort": GLM_EFFORT[st.session_state.get(f"{prefix}_glm_eff", "High")]}
    elif p == "MiMo":
        mid = MIMO_MODELS[st.session_state.get(f"{prefix}_mimo_model", "V2.6 Pro (flagship)")]
        cfg = {"model_id": mid, "thinking": st.session_state.get(f"{prefix}_mimo_think", True)}
    else:  # Grok
        mid = MODEL_GROK
        cfg = {"mode": st.session_state.get(f"{prefix}_grok_mode", "Expert")}
    effective_web = bool(
        web and p not in NO_WEB_PROVIDERS and (p != "DeepSeek" or deepseek_web_supported(mid))
        and (p != "Qwen" or qwen_web_supported(mid))
    )
    job = {"provider": p, "key": (load_keys() if keys_now is None else keys_now).get(KEY_MAP[p], ""),
           "messages": messages, "cfg": cfg, "web": effective_web}
    return p, mid, cfg, job

def build_memo_job(prefix, question, context, continuation, *, keys_now=None):
    memo_text = build_independent_memo_prompt(question, context, continuation)
    p = eval_provider_of(prefix)
    return {"provider": p, "key": (load_keys() if keys_now is None else keys_now).get(KEY_MAP[p], ""),
            "messages": [{"role": "user", "content": memo_text}],
            "cfg": dict(CFG[p]), "web": False}

def keyed_sources(names, keys_now):
    have = [n for n in names if keys_now.get(KEY_MAP[n])]
    return have, [n for n in names if n not in have]


def build_source_jobs(names, keys_now, source_msg):
    """Build one run plan's source-call jobs plus per-source caption metadata.

    Caption metadata is resolved NOW, on the main thread, because
    settings_str/model_id_of read the live config."""
    sources, src_meta = {}, {}
    for n in names:
        pack = n in PACK_SOURCES
        web = WEB[n]
        sources[n] = {"provider": n, "key": keys_now.get(KEY_MAP[n], ""),
                      "messages": [source_msg], "cfg": CFG[n], "web": web}
        if pack:
            sources[n]["pack"] = True
            sources[n]["pack_web"] = bool(PACK_SOURCES[n])
        src_meta[n] = {"model_id": model_id_of(n, CFG[n]),
                       "settings": settings_str(n, CFG[n], web)}
    return sources, src_meta

def build_eval_phase(prefix, label_key, resolved=None, *, keys_now=None, **extra):
    """Build the shared shape of one evaluator-phase job (Synthesizer,
    Researcher, Adjudicator, Revision) for a run plan. `resolved` accepts an
    already-resolved (provider, model_id, cfg, job) tuple so a phase can
    deliberately reuse another phase's dial -- the Revision pass is the same
    role revising its own draft, not a new evaluator."""
    if resolved is None:
        resolved = evaluator_job(prefix, [], st.session_state[f"{prefix}_web"],
                                 st.session_state[f"{prefix}_maxsearch"], keys_now=keys_now)
    provider, model_id, cfg, job = resolved
    phase = {"provider": provider, "model_id": model_id, "cfg": cfg,
             "key": job["key"], "web": job["web"],
             "label": eval_label(prefix),
             "settings": settings_str(provider, cfg, job["web"]),
             "label_key": label_key, "preset": current_preset_name()}
    phase.update(extra)
    return phase


def snapshot_initial_council():
    _parked_now = set(st.session_state.get("_web_parked") or [])
    return {"sources": {k: bool(st.session_state.get(k)) or (_EN_KEY_TO_NAME.get(k) in _parked_now)
                        for k in _EN_KEYS_ALL},
            "k_syn_provider": st.session_state.get("k_syn_provider"),
            "k_adj_provider": st.session_state.get("k_adj_provider"),
            "k_adjudicator_on": st.session_state.get("k_adjudicator_on"),
            "adjudication": bool(adjudicator_stack_active())}

def _restore_initial_council():
    """on_change of the 🏛️ Full-council toggle: when it turns ON, put back
    exactly the council the initial question ran with. Turning it OFF
    leaves the current selections alone (solo replies ignore them anyway).
    A user who tweaks cards after flipping it on is respected -- this only
    runs at the moment of flipping."""
    snap = st.session_state.get("initial_council")
    if not st.session_state.get("k_full_council") or not snap:
        return
    for k, v in (snap.get("sources") or {}).items():
        st.session_state[k] = bool(v)
    if snap.get("k_syn_provider"):
        st.session_state["k_syn_provider"] = snap["k_syn_provider"]
    if snap.get("k_adj_provider"):
        st.session_state["k_adj_provider"] = snap["k_adj_provider"]
    st.session_state["k_adjudicator_on"] = bool(snap.get("adjudication"))
    for _pfx in ("k_syn", "k_adj"):
        _prov = st.session_state.get(f"{_pfx}_provider")
        if _prov in NO_WEB_PROVIDERS:
            st.session_state[f"{_pfx}_web"] = False
        elif _prov == "DeepSeek":
            _sync_evaluator_deepseek_web_toggle(_pfx)

def inherit_followup_from_initial():
    """Start continuation chat from the exact Synthesizer setup used this run.

    The follow-up controls remain independent after this copy, so the user can
    deliberately make later turns lighter without the app silently changing the
    provider or model between the initial answer and the first continuation.
    """
    for suffix in (
        "provider", "cla_model", "cla_eff", "cla_think", "gpt_model",
        "gpt_eff", "gpt_speed", "gem_model", "gem_t", "ds_model", "ds_think",
        "ds_effort", "grok_mode", "qwen_model", "qwen_think", "qwen_budget",
        "qwen_effort",
        "mistral_model", "mistral_eff", "kimi_model", "kimi_think", "kimi_effort",
        "meta_model", "meta_eff", "mm_model", "mm_speed",
        "mm_think", "nv_model", "nv_think", "nv_budget",
        "glm_model", "glm_think", "glm_eff", "mimo_model", "mimo_think",
        "web", "maxsearch",
    ):
        source_key = f"k_syn_{suffix}"
        if source_key in st.session_state:
            st.session_state[f"k_fu_{suffix}"] = st.session_state[source_key]

# ---- Synthesizer prompt ----
def state_capsule_instruction(research=False):
    focus = ("research objective, established facts, adjudicated findings, unresolved "
             "questions, and evidence" if research else
             "user goals, established facts, decisions, preferences, unresolved questions, "
             "and evidence")
    return (
        "After all user-visible content, append a compact factual continuity record between "
        f"the exact markers {STATE_BEGIN} and {STATE_END}. Preserve {focus} needed for the "
        "next turn. Do not include private reasoning, source identities, prose critique, or a "
        "transcript. Keep it under 500 words. Conclave removes this record before display."
    )

def build_synth_prompt(question, answers, web_on, context="", preset="Light"):
    order = list(answers.keys())
    random.shuffle(order)
    legend = {chr(65 + i): nm for i, nm in enumerate(order)}
    lead = "Current user turn" if context else "Original question"
    target = "current user turn" if context else "original question"
    turn_duty = (
        "This is a follow-up. Treat the current user turn as the controlling request while "
        "preserving relevant established context. Evaluate the fresh source answers specifically "
        "for this follow-up, revise earlier conclusions when evidence warrants it, and do not "
        "merely restate the prior synthesis."
        if context else
        "This is the initial synthesis. Establish a complete, accurate baseline answer that "
        "directly resolves the user's original request and supports useful follow-up."
    )
    parts = (["Conversation context:\n" + context + "\n"] if context else [])
    parts += [f"{lead}:\n{question}\n",
             f"Below are {len(order)} anonymized answers to that question from different AI assistants, "
             f"labeled {', '.join(legend.keys())}:\n"]
    for label, nm in legend.items():
        parts.append(f"--- Answer {label} ---\n{answers[nm]}\n")
    verify = " and, where useful, verify contested or time-sensitive claims with web search" if web_on else ""
    parts.append(
        "\nYou are the Synthesizer. Your output is one direct, self-contained answer to the "
        f"{target} — not a comparative review, leaderboard, or recap of which model said what. "
        "Before drafting, privately evaluate every candidate answer at the claim level: which "
        "claims are supported, contradicted, redundant, uniquely useful, uncertain, or "
        "irrelevant. Resolve conflicts by evidence quality, recency, relevance, and reasoning — "
        "never by majority vote, writing style, or provider identity. Use those verdicts, not "
        "one favored answer, as the factual substrate of your answer. You are not a summary "
        "engine: make your own independent, reasoned judgment and reach the conclusion you "
        "believe is best supported, even when no source states it directly or the sources "
        "mostly disagree.\n"
        "Your own knowledge is an additional input, not privileged ground truth — its authority "
        "depends on evidence quality, recency, relevance, and reasoning, not on whether a claim "
        "originated with you. In descending priority: current verifiable primary evidence; "
        "strong reasoning that genuinely converges across independent answers (agreement alone "
        "is not proof); an accurate unique contribution from a single answer (a correct minority "
        "finding is not weaker for being a minority); your own stable background knowledge; then "
        "unsupported inference or extrapolation, clearly labeled as such. Clearly distinguish "
        "well-supported conclusions from judgment calls, inference, or uncertainty. You are "
        "responsible for the accuracy, completeness, clarity, and practical usefulness of the "
        "final answer.\n"
        + turn_duty + "\n"
        + (
            "Before drafting, build an explicit internal claim ledger: for every material claim, "
            "privately record its normalized statement, which answer letters support or dispute "
            "it, its relationship (agreement, conflict, unique contribution, or Synthesizer-"
            "added), the underlying evidence, a verdict (include, qualify, exclude, or "
            "unresolved), your confidence with a brief reason, and why it matters to this "
            "question. Give every claim a verdict before drafting, and account for every answer "
            "letter somewhere in your reasoning even if it added nothing unique. Keep the ledger "
            "internal — do not show it to the user.\n"
            if preset in ("Thorough", "Deep") else
            "Before drafting, privately work through the claims involved: for each material one, "
            "note what it says, which answers support or dispute it, whether it's agreement, "
            "conflict, a unique contribution, or something you're adding yourself, and whether it "
            "belongs in the answer. Keep this reasoning internal — do not show it to the user.\n"
        )
        + "Method:\n"
        "1. Evaluate every claim for accuracy and authenticity. Cross-check the answers against each other"
        + verify + ". Discard anything wrong, unsupported, or hallucinated.\n"
        "2. Preserve every materially relevant, decision-useful accurate detail from ANY answer — "
        "a detail is material if omitting it could change the user's understanding, decision, "
        "safety, next action, or confidence, not merely because it is accurate. Remove "
        "duplication and match depth to the user's question, stakes, and requested format rather "
        "than padding the answer with everything correct.\n"
        "3. Resolve contradictions by evidence and reasoning, never by majority vote or writing style.\n"
        "4. Where the answers fall short, add your own knowledge and reasoning to fill the gaps. "
        "Take a position when the user asks for an opinion, recommendation, or decision; do not "
        "hide behind a neutral recap of what the sources said.\n"
        "Begin with a concise, specific Markdown heading tailored to this question and the substance "
        "of your answer. Never use a generic heading such as 'Part 1', 'The Enhanced Answer', "
        "'Definitive Answer', or 'Response'. Follow it with a complete, self-contained, superbly "
        f"organized answer to the {target}. Do not include meta-commentary about the source "
        "answers in the main answer; it should stand on its own.\n"
        "Never narrate your process anywhere in the output. Do not open with, or ever "
        "include, statements about what you are about to do — no 'I'll evaluate these "
        "answers', 'Let me search to verify', 'First I will compare', or anything "
        "similar. All evaluation, verification, and searching happens silently before you "
        "write; the user sees only the finished answer, which starts at its heading and "
        "proceeds straight into substance.\n"
        "If web search is unavailable, fails, times out, or returns nothing useful, proceed "
        "silently on the evidence and your own knowledge — never mention the search problem "
        "in the answer.\n"
        "After a horizontal rule (---), add a short section titled '**How I weighed the sources**' "
        "describing concretely what you did: which answers (by letter only) contributed what, what you "
        "corrected or discarded and why, and where you added beyond them. Keep it brief.\n"
        "The answers are anonymized on purpose. Refer to them only by letter. If one happens to resemble "
        "your own style, scrutinize it harder, not softer.")
    parts.append("\n" + state_capsule_instruction(research=False))
    parts.append(contribution_ledger_instruction(legend))
    return "\n".join(parts), legend

def build_solo_followup_prompt(question, context="", web_on=False):
    verify = (" Where useful, verify contested or time-sensitive claims with web search."
              if web_on else "")
    parts = []
    if context:
        parts.append("Conversation context:\n" + context + "\n")
    parts.append(f"Current user turn:\n{question}\n")
    parts.append(
        "\nYou are the Synthesizer for Conclave, continuing a conversation whose earlier "
        "answers you built by reconciling multiple independent AI sources. For THIS follow-up "
        "the user has chosen to continue with you alone — no fresh source answers were "
        "gathered. You carry the full working context above: the original objective, the "
        "durable factual state your earlier synthesis established, the recent visible "
        "turns, and a ledger of what each independent source uniquely contributed in "
        "earlier turns — use that ledger to recall who raised what, and to bring a "
        "dropped point back into play when the user asks about it. Flow naturally into "
        "the conversation as the same voice that wrote those answers.\n"
        "Continuing alone does not lower the bar — keep synthesizing. Cross-check any new "
        "claim (the user's or your own) against what this conversation has already "
        "established and against your own reliable knowledge." + verify + " Update, correct, "
        "or extend earlier conclusions when warranted, and say so plainly when you do. Your "
        "own knowledge remains an input whose authority depends on evidence quality, recency, "
        "relevance, and reasoning — clearly distinguish well-supported conclusions from "
        "judgment calls, inference, or genuine uncertainty. Take a position when the user "
        "asks for an opinion, recommendation, or decision.\n"
        "Treat the current user turn as the controlling request. Answer it directly and "
        "completely, at a depth matched to what it actually asks — a quick factual follow-up "
        "deserves a quick factual answer, not a re-synthesis of the whole conversation. Do "
        "not restate the prior answer unless the user asks for that, and do not discuss "
        "council mechanics, sources, or this instruction.\n"
        "Never narrate your process. Do not open with, or ever include, statements about "
        "what you are about to do ('I'll check...', 'Let me verify...'); any checking "
        "happens silently before you write, and your reply begins directly with its "
        "substance."
        + (" If web search is unavailable, fails, or times out, proceed silently on "
           "what you know — never mention the search problem in the reply." if web_on else ""))
    parts.append("\n" + state_capsule_instruction(research=False))
    return "\n".join(parts)

def build_adjudicator_prompt(question, draft, answers, legend, preset="Light"):
    parts = [
        f"Original question:\n{question}\n",
        f"Below are the same {len(legend)} anonymized answers the Synthesizer was given, "
        f"labeled {', '.join(legend.keys())}:\n",
    ]
    for label, nm in legend.items():
        parts.append(f"--- Answer {label} ---\n{answers[nm]}\n")
    parts.append(f"--- The Synthesizer's finished draft ---\n{draft}\n")
    parts.append(
        "\nYou are the Adjudicator — the council's final quality gate. The draft you are "
        "auditing is otherwise what the user will read verbatim: nothing after you checks it, "
        "so a defect you miss ships, and a defect you invent triggers a needless paid revision. "
        "Your judgment carries the same weight as the Synthesizer's. "
        "Independently audit the Synthesizer's draft against the "
        "evidence above. You are not writing your own answer and must never draft a competing "
        "one or tell the user what you would have written instead. Check specifically for: "
        "claims in the draft that are not actually supported by the cited evidence or any other "
        "answer above; evidence the draft mischaracterizes, contradicts, or drops when it was "
        "material to the answer; factual errors you can verify against the evidence or your own "
        "reliable knowledge; and internal contradictions or unresolved gaps in the draft's own "
        "reasoning. Do not flag stylistic preferences, immaterial omissions, or places where you "
        "would simply have written it differently.\n"
        "The evidence is anonymized on purpose, exactly as the Synthesizer saw it — one of these "
        "letters is your own independent first take on this question, generated before you saw "
        "any of this. Treat it exactly like any other answer; do not privilege or second-guess "
        "it because it might be yours.\n"
        + (
            "For every defect you find, list it explicitly: the claim in the draft, what is "
            "wrong with it, which answer letter(s) the evidence comes from, and how serious it "
            "is — material (could change the user's understanding, decision, safety, or "
            "confidence) or minor. If you find none, say so plainly and stop there.\n"
            if preset in ("Thorough", "Deep") else
            "Describe what you found in plain prose, focused on anything material — issues that "
            "could change the user's understanding, decision, safety, or confidence. If you find "
            "nothing material, say so plainly and briefly.\n"
        )
        + "You never rewrite, complete, or override the draft yourself — return findings only."
    )
    parts.append(
        "\nEnd your response with exactly this block on its own lines, filled in truthfully "
        f"based on whether you found ANY material defect above:\n{ADJ_VERDICT_BEGIN}\n"
        "material_defects: yes|no\n"
        f"{ADJ_VERDICT_END}"
    )
    return "\n".join(parts)

def build_disposition_prompt(question, draft, defect_report, answers, legend, preset="Light",
                             follow_on=False):
    if follow_on:
        parts = ["The answers and your draft are above. "
                 f"--- The Adjudicator's independent review of that draft ---\n{defect_report}\n"]
    else:
        parts = [
            f"Original question:\n{question}\n",
            f"Below are the same {len(legend)} anonymized answers you originally evaluated, "
            f"labeled {', '.join(legend.keys())}:\n",
        ]
        for label, nm in legend.items():
            parts.append(f"--- Answer {label} ---\n{answers[nm]}\n")
        parts.append(f"--- Your own finished draft ---\n{draft}\n")
        parts.append(f"--- The Adjudicator's independent review of that draft ---\n{defect_report}\n")
    parts.append(
        "\nYou are the Synthesizer. The Adjudicator — a genuinely different provider, auditing "
        "independently — has reviewed your draft against the same evidence above and found "
        "material defects. You must resolve every finding it raised, checked against the real "
        "evidence above rather than accepted purely on the Adjudicator's word: "
        "**accept and correct** (the finding is right — fix it), "
        "**reject with evidence** (the finding is wrong — the original draft is supported by "
        "specific evidence; cite it), "
        "**qualify** (partially right — soften, caveat, or narrow the claim rather than remove "
        "it entirely), or "
        "**escalate as unresolved** (a genuine, evidence-based disagreement that cannot be "
        "settled from what's available — say so plainly to the user rather than silently "
        "picking a side).\n"
        + (
            "For every finding the Adjudicator raised, list it explicitly: the finding, your "
            "disposition, and your reasoning with letter citations where relevant.\n"
            if preset in ("Thorough", "Deep") else
            "Work through the findings in plain prose, covering every one the Adjudicator "
            "raised and your reasoning.\n"
        )
        + "Then produce your COMPLETE revised final answer, incorporating every disposition "
        "above — corrected where warranted, defended where the evidence supports your original "
        "draft, qualified or flagged as unresolved where genuinely uncertain. This replaces your "
        "previous answer entirely; the user will only see this version, not the original draft.\n"
        "Begin with a concise, specific Markdown heading tailored to this question and the "
        "substance of your answer (never a generic heading such as 'Part 1', 'Revised Answer', "
        "or 'Response'). Follow it with the complete, self-contained, superbly organized answer "
        "— do not include meta-commentary about the review process in the main answer; it "
        "should stand on its own.\n"
        "After a horizontal rule (---), add a short section titled '**How I weighed the "
        "sources**' describing concretely what you did, exactly as you did the first time.\n"
        "After a second horizontal rule (---), add a section titled '**Response to the "
        "Adjudicator**' with your dispositions from above."
    )
    parts.append("\n" + state_capsule_instruction(research=False))
    parts.append(contribution_ledger_instruction(legend))
    return "\n".join(parts)

# ---- Research Synthesizer prompt (the evaluation engine — separate from synthesis) ----
def build_research_prompt(question, answers, web_on, context=""):
    order = list(answers.keys())
    random.shuffle(order)
    legend = {chr(65 + i): nm for i, nm in enumerate(order)}
    lead = "Current research turn" if context else "Original question"
    turn_duty = (
        "This is a follow-up research turn. Treat the current question as the controlling scope, "
        "preserve relevant established findings, identify what the new source evidence changes or "
        "confirms, and do not simply repeat the prior evaluation."
        if context else
        "This is the initial research evaluation. Establish a rigorous baseline map of the "
        "evidence, agreements, disagreements, uncertainties, and blind spots."
    )
    parts = (["Research conversation context:\n" + context + "\n"] if context else [])
    parts += [f"{lead}:\n{question}\n",
             f"Below are {len(order)} anonymized answers to that question from different AI assistants, "
             f"labeled {', '.join(legend.keys())}:\n"]
    for label, nm in legend.items():
        parts.append(f"--- Answer {label} ---\n{answers[nm]}\n")
    verify = " Verify contested claims with web search where useful." if web_on else ""
    parts.append(
        "\nYou are a rigorous research evaluator. Produce a deep comparative analysis of these answers "
        "for research purposes — the goal is understanding their differences, quality, and reliability, "
        "not producing a merged answer. You are responsible for accurate adjudication, explicit "
        "uncertainty, and a useful evidence map." + verify + "\n" + turn_duty + "\n"
        "Use exactly these sections:\n"
        "1. **CONSENSUS** — claims all or most answers agree on, each with a confidence level.\n"
        "2. **CONTRADICTIONS & ADJUDICATION** — every point of disagreement, however small. For each, "
        "adjudicate by evidence and reasoning: which letter is most likely correct, and why.\n"
        "3. **UNIQUE CONTRIBUTIONS** — details or insights only one answer provided; assess whether each "
        "is accurate and valuable, or dubious.\n"
        "4. **STRENGTH RANKING** — rank the answers from strongest to weakest on accuracy, evidence, "
        "completeness, and reasoning quality, with concrete justification for each placement.\n"
        "5. **BLIND SPOTS** — important considerations ALL of them missed.\n"
        "6. **CONFIDENCE TABLE** — a markdown table: Claim/Topic · Verdict · Confidence (High/Med/Low) · "
        "Supporting answers (letters).\n"
        "Judge ONLY accuracy, evidence, and reasoning quality — never writing style or tone. The answers "
        "are anonymized on purpose; refer to them only by letter, and scrutinize harder any answer that "
        "resembles your own style.")
    parts.append("\n" + state_capsule_instruction(research=True))
    return "\n".join(parts), legend

def _strip_files_deep(o):
    """Deep copy with attachment BYTES removed (names/kinds kept) for history."""
    if isinstance(o, dict):
        o2 = {k: _strip_files_deep(v) for k, v in o.items()}
        if "files" in o2:
            o2["files"] = [dict({"name": f.get("name", ""), "kind": f.get("kind", ""),
                                 "stripped": True},
                                **({"prepared": f["prepared"]} if f.get("prepared") else {}))
                           for f in (o2["files"] or [])]
        return o2
    if isinstance(o, list): return [_strip_files_deep(x) for x in o]
    return o

def persist_session(mode="synthesis"):
    if mode == "research":
        sid = st.session_state.get("res_session_id")
        if not sid: return
        entry = {"ts": sid, "mode": "research",
                 "prompt": st.session_state.get("res_prompt", ""),
                 "threads": st.session_state.get("res_threads", {}),
                 "research": st.session_state.get("research"),
                 "source_chats": st.session_state.get("res_source_chats", {}),
                 "source_promotions": st.session_state.get(
                     "res_source_promotions", [])}
    else:
        sid = st.session_state.get("session_id")
        if not sid: return
        entry = {"ts": sid, "mode": "synthesis",
                 "prompt": st.session_state.get("prompt", ""),
                 "threads": st.session_state.get("threads", {}),
                 "synth": st.session_state.get("synth"),
                 "source_chats": st.session_state.get("source_chats", {}),
                 "source_promotions": st.session_state.get("source_promotions", []),
                 "eval_threads": st.session_state.get("eval_threads", {})}
    # Match on (ts, mode), not ts alone, for compatibility with older entries
    # whose second-resolution ids could collide across the two independent tabs.
    clean_entry = _strip_files_deep(entry)
    _archived = None
    if os.path.exists(HISTORY_ARCHIVE_FILE):
        _archived = next((e for e in load_history_archive()
                          if _history_key(e) == (sid, mode)), None)
    def _upsert(h):
        old = next((e for e in h
                    if e.get("ts") == sid and e.get("mode", "synthesis") == mode), None) or _archived
        if old and old.get("pinned"):
            clean_entry["pinned"] = True
        # The opens counter (Most frequented) survives re-saves the same way.
        if old and old.get("opens"):
            clean_entry["opens"] = old["opens"]
        if old and old.get("last_open"):
            clean_entry["last_open"] = old["last_open"]
        h = [e for e in h
             if not (e.get("ts") == sid and e.get("mode", "synthesis") == mode)]
        h.insert(0, clean_entry)
        return h
    update_history(_upsert)
    if _archived is not None:
        update_history_archive(lambda h: [e for e in h if _history_key(e) != (sid, mode)])

def v2_entry_to_session(h):
    """Convert an old v2 history entry (single-shot results) to v3 threads."""
    threads = {}
    for n, r in h.get("results", {}).items():
        threads[n] = [{"role": "user", "content": h.get("prompt", "")},
                      {"role": "assistant", "content": r.get("text", ""),
                       "secs": r.get("secs", 0), "in_tok": r.get("in_tok"),
                       "out_tok": r.get("out_tok"), "cost": r.get("cost"),
                       "web": r.get("web", False),
                       "model_id": h.get("model_id", {}).get(n, ""),
                       "settings": h.get("settings", {}).get(n, "")}]
    return threads

def _land_on_latest_answer(record, mode):
    thread = (record or {}).get("thread") or []
    last_turn = next((m.get("turn_id") for m in reversed(thread)
                      if m.get("role") == "assistant"
                      and m.get("turn_id") is not None), None)
    if last_turn is not None:
        st.session_state[f"_scroll_to_latest_{mode}"] = last_turn

def load_session_into_state(h):
    """Load a saved chat into ITS tab (synthesis or research) and switch there."""
    mode = h.get("mode", "synthesis")
    if mode == "research":
        st.session_state["res_threads"]    = h.get("threads", {})
        st.session_state["research"]       = h.get("research")
        st.session_state["res_source_chats"] = h.get("source_chats", {})
        st.session_state["res_source_promotions"] = h.get("source_promotions", [])
        st.session_state["res_prompt"]     = h.get("prompt", "")
        st.session_state["res_session_id"] = h["ts"]
        st.session_state["res_session_pinned"] = bool(h.get("pinned"))
        _land_on_latest_answer(h.get("research"), "research")
        st.session_state["main_view"]      = VIEW_RES
    else:
        if "threads" in h:                               # v3 entry
            st.session_state["threads"] = h["threads"]
            st.session_state["synth"]   = h.get("synth")
            st.session_state["initial_council"] = (h.get("synth") or {}).get("initial_council")
            st.session_state["source_chats"] = h.get("source_chats", {})
            st.session_state["source_promotions"] = h.get("source_promotions", [])
            st.session_state["eval_threads"] = h.get("eval_threads", {})
        else:                                            # v2 back-compat
            st.session_state["threads"] = v2_entry_to_session(h)
            st.session_state["synth"]   = None
            st.session_state["initial_council"] = None
            st.session_state["source_chats"] = {}
            st.session_state["source_promotions"] = []
            st.session_state["eval_threads"] = {}
        st.session_state["prompt"]     = h.get("prompt", "")
        st.session_state["session_id"] = h["ts"]
        st.session_state["session_pinned"] = bool(h.get("pinned"))
        st.session_state.pop("_synth_unlocked", None)
        _land_on_latest_answer(h.get("synth"), "synthesis")
        st.session_state["main_view"] = VIEW_SYNTH
        st.session_state["sp_view"] = ("⭐ Answer" if st.session_state["synth"]
                                       else "\U0001F9E9 The Sources")
    for n in list(ICON.keys()) + EVAL_MEMO_AUDIO_NAMES:
        st.session_state.pop(f"audio_{n}", None)
        st.session_state.pop(f"audio_err_{n}", None)   # else a stale TTS error
    bump_chat_opens(h["ts"], mode)

def new_chat():
    """Start a FRESH chat in the active chat workspace, or Synthesis elsewhere.

    The chat being replaced is
    already in history (persist_session runs on every send), so nothing is lost —
    this only clears the on-screen state so the next send opens a new session.
    Used as an on_click callback, so it must not call st.rerun() (see the sidebar
    button rules) — Streamlit's own post-callback rerun is enough."""
    active_view = st.session_state.get("main_view")
    if active_view == VIEW_RES:
        for k in ("res_threads", "research", "res_session_id",
                  "res_session_pinned", "res_prompt",
                  "res_source_chats", "res_source_promotions"):
            st.session_state.pop(k, None)
        st.session_state["res_prompt_text"] = ""
    else:
        for k in ("threads", "synth", "session_id", "session_pinned", "prompt",
                  "source_chats", "source_promotions", "eval_threads", "initial_council",
                  "_synth_unlocked"):
            st.session_state.pop(k, None)
        st.session_state["prompt_text"] = ""
        st.session_state["sp_view"] = "⭐ Answer"
        st.session_state["main_view"] = VIEW_SYNTH
    for k in [k for k in list(st.session_state) if str(k).startswith("txt_fu_")]:
        st.session_state.pop(k, None)
    for n in list(ICON.keys()) + EVAL_MEMO_AUDIO_NAMES:
        st.session_state.pop(f"audio_{n}", None)
        st.session_state.pop(f"audio_err_{n}", None)

def toggle_pin_session(ts, mode="synthesis"):
    resulting_pinned = None
    def _flip(h):
        nonlocal resulting_pinned
        for e in h:
            if e.get("ts") == ts and e.get("mode", "synthesis") == mode:
                if e.get("pinned"):
                    e.pop("pinned", None)
                    resulting_pinned = False
                else:
                    e["pinned"] = True
                    resulting_pinned = True
        return h
    update_history(_flip)
    if os.path.exists(HISTORY_ARCHIVE_FILE):
        update_history_archive(_flip)
    session_id_key = "res_session_id" if mode == "research" else "session_id"
    pinned_key = "res_session_pinned" if mode == "research" else "session_pinned"
    if (resulting_pinned is not None
            and st.session_state.get(session_id_key) == ts):
        st.session_state[pinned_key] = resulting_pinned

CHAT_TOPIC_ICONS = [
    ("\U0001F3AE", ("wow ", "world of warcraft", "warcraft", "diablo", "game", "gaming",
                    "quest", "raid", "shaman", "paladin", "rpg", "playstation", "xbox",
                    "nintendo", "steam ", "counter-strike", "fortnite", "zelda")),
    ("\U0001F4AA", ("workout", "gym", "fitness", "exercise", "training", "muscle",
                    "protein", "cardio", "strength", "endurance", "biomarker")),
    ("\U0001FA7A", ("health", "doctor", "medical", "blood", "lab result", "symptom",
                    "diagnos", "medicine", "hospital", "insurance", "therapy", "ct ",
                    "mri", "x-ray", "vitamin", "diet ")),
    ("\U0001F4B0", ("stock", "invest", "finance", "financial", "money", "budget",
                    "tax", "salary", "mortgage", "crypto", "401k", "retirement",
                    "share", "portfolio", "expense")),
    ("\U0001F4BB", ("code", "coding", "python", "programming", "software", "api ",
                    "app ", "streamlit", "github", "developer", "database", "script",
                    "claude code", "codex", "debugging")),
    ("\U0001F5A5", ("pc ", "gpu", "cpu", "rtx", "ram ", "computer build", "rig",
                    "monitor", "overclock", "hardware", "motherboard", "keyboard")),
    ("\U0001F916", ("ai ", " ai", "artificial intelligence", "chatgpt", "claude",
                    "gemini", "grok", "llm", "model comparison", "openai", "anthropic",
                    "deepseek", "qwen", "mistral", "kimi", "moonshot", "minimax",
                    "nemotron", "muse spark", "glm", "zhipu", "z.ai")),
    ("\U0001F4DC", ("history", "historical", "ancient", "war ", "wwii", "century",
                    "colonial", "revolution", "medieval", "rome ", "roman", "spartan",
                    "1700", "1800", "1900")),
    ("✝", ("jesus", "bible", "god ", "church", "faith", "christian", "religion",
                "prayer", "biblical", "divine")),
    ("\U0001F37D", ("recipe", "cook", "food", "meal", "dinner", "restaurant",
                    "nutrition", "pizza", "protein shake", "breakfast")),
    ("✈", ("travel", "trip", "flight", "vacation", "hotel", "itinerary",
                "airport", "destination")),
    ("\U0001F3B5", ("music", "song", "album", "band", "guitar", "playlist", "lyric",
                    "concert", "aerosmith", "marilyn manson")),
    ("⚖", ("legal", "law ", "lawyer", "court", "contract", "lawsuit", "rights",
                "trial", "attorney")),
    ("\U0001F4F0", ("news", "election", "politic", "president", "government", "iran",
                    "israel", "ukraine", "conflict", "military", "congress")),
    ("\U0001F468\U0000200D\U0001F469\U0000200D\U0001F467", (
        "family", "kid", "child", "baby", "parent", "wife", "husband", "marriage",
        "pregnan", "son ", "daughter")),
    ("\U0001F697", ("car ", "truck", "vehicle", "engine", "tire", "trade-in",
                    "dealership", "driving")),
    ("\U0001F4BC", ("job", "career", "resume", "interview", "linkedin", "work ",
                    "salary negotiation", "recruiter", "application", "supervisor")),
    ("\U0001F3E0", ("house", "home ", "hvac", "plumbing", "renovation", "repair",
                    "furnace", "countertop", "appliance", "roof")),
    ("\U0001F52C", ("science", "physics", "chemistry", "biology", "space ", "nasa",
                    "quantum", "evolution", "climate")),
]

def answer_heading(text):
    """The Markdown heading a synthesized answer opens with ("# The War of
    1812: Causes, Buildup, and Legacy" -> "The War of 1812: Causes, Buildup,
    and Legacy"), or None when the answer doesn't start with one."""
    seen = 0
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if seen and level > 1:
                return None   # a section heading after some prose -- not the title
            title = stripped.lstrip("#").strip().strip("*").strip()
            return title or None
        # tolerate a stray opening line or two (older answers that narrated
        # before their heading) -- scan the first few non-empty lines
        seen += 1
        if seen >= 4:
            return None
    return None

def chat_display_title(entry):
    record = entry.get("research") if entry.get("mode") == "research" else entry.get("synth")
    thread = (record or {}).get("thread") if isinstance(record, dict) else None
    for m in thread or []:
        if m.get("role") == "assistant":
            heading = answer_heading(m.get("content"))
            if heading:
                return heading
            break
    return " ".join(str(entry.get("prompt", "") or "").split())

def current_chat_title():
    """The open chat's display title from live session state (sidebar
    header), same rule as chat_display_title."""
    if st.session_state.get("main_view") == VIEW_RES:
        record, prompt = st.session_state.get("research"), st.session_state.get("res_prompt")
    else:
        record, prompt = st.session_state.get("synth"), st.session_state.get("prompt")
    for m in ((record or {}).get("thread") if isinstance(record, dict) else None) or []:
        if m.get("role") == "assistant":
            heading = answer_heading(m.get("content"))
            if heading:
                return heading
            break
    return " ".join(str(prompt or "").split())

def chat_topic_icon(title, mode="synthesis"):
    """Best-effort topic icon for one saved chat's title. Case-insensitive
    substring match against CHAT_TOPIC_ICONS (first group wins); the
    workspace's own mark is the honest fallback when nothing matches."""
    text = f" {str(title or '').lower()} "
    for icon, needles in CHAT_TOPIC_ICONS:
        if any(needle in text for needle in needles):
            return icon
    return "\U0001F52C" if mode == "research" else "⭐"

def bump_chat_opens(ts, mode="synthesis"):
    def _bump(h):
        for e in h:
            if e.get("ts") == ts and e.get("mode", "synthesis") == mode:
                e["opens"] = int(e.get("opens") or 0) + 1
                e["last_open"] = datetime.now().isoformat(timespec="seconds")
        return h
    update_history(_bump)

def chat_day_group(ts):
    try:
        days_ago = (datetime.now().date() - datetime.fromisoformat(str(ts)).date()).days
    except (ValueError, TypeError):
        return "Undated"
    if days_ago <= 0:
        return "Today"
    if days_ago == 1:
        return "Yesterday"
    if days_ago == 2:
        return "Two days ago"
    if days_ago == 3:
        return "Three days ago"
    if days_ago <= 7:
        return "Previous 7 days"
    for limit, label in ((14, "Older than 7 days"), (30, "Older than 14 days"),
                         (60, "Older than 30 days"), (90, "Older than 60 days"),
                         (180, "Older than 90 days"), (365, "Older than 6 months")):
        if days_ago <= limit:
            return label
    return "Older than a year"

def delete_session(ts, mode="synthesis"):
    """Remove a chat from history; clear whichever tab currently shows it.
    Matches on (ts, mode) — see persist_session — so a same-second entry in
    the OTHER tab is never deleted or cleared alongside the intended one."""
    update_history(lambda h: [e for e in h
                              if not (e.get("ts") == ts and
                                      e.get("mode", "synthesis") == mode)])
    if os.path.exists(HISTORY_ARCHIVE_FILE):
        update_history_archive(lambda h: [e for e in h if _history_key(e) != (ts, mode)])
    if mode == "research" and st.session_state.get("res_session_id") == ts:
        for k in ("res_threads", "research", "res_session_id", "res_prompt",
                  "res_source_chats", "res_source_promotions"):
            st.session_state.pop(k, None)
    elif mode != "research" and st.session_state.get("session_id") == ts:
        for k in ("threads", "synth", "session_id", "prompt",
                  "source_chats", "source_promotions", "eval_threads"):
            st.session_state.pop(k, None)

MODEL_PICKER_SPECS = {
    "Claude": ("k_cla_model", "cla_model", CLAUDE_MODELS),
    "ChatGPT": ("k_gpt_model", "gpt_model", CHATGPT_MODELS),
    "Gemini": ("k_gem", "gem_model", GEMINI_MODELS),
    "Meta": ("k_meta_model", "meta_model", META_MODELS),
    "DeepSeek": ("k_ds_model", "ds_model", DEEPSEEK_MODELS),
    "Qwen": ("k_qwen_model", "qwen_model", QWEN_MODELS),
    "GLM": ("k_glm_model", "glm_model", GLM_MODELS),
    "NVIDIA": ("k_nv_model", "nv_model", NVIDIA_MODELS),
    "Mistral": ("k_mistral_model", "mistral_model", MISTRAL_MODELS),
    "Kimi": ("k_kimi_model", "kimi_model", KIMI_MODELS),
    "MiniMax": ("k_mm_model", "mm_model", MINIMAX_MODELS),
    "MiMo": ("k_mimo_model", "mimo_model", MIMO_MODELS),
}

DEFAULTS = {
    "k_gpt_model": "Sol (advanced)", "k_gpt": "Low", "k_gpt_speed": "Standard",
    "k_preset_custom": False,
    "k_cla_model": "Sonnet 5.5", "k_cla": "Medium", "k_cla_think": True,
    "k_gem": "Flash-Lite (3.5)", "k_gem_t": "Balanced",
    "k_ds_model": "V4.1 Flash (fast · vision)", "k_ds_think": True, "k_ds_effort": "High",
    "k_grok": "Fast",
    "k_qwen_model": "Flash 3.8 (economy)", "k_qwen_think": True, "k_qwen_budget": 4000, "k_qwen_effort": "Default",
    "k_mistral_model": "Small 4 (economy)", "k_mistral_eff": "Low",
    "k_kimi_model": "K2.6 (balanced)", "k_kimi_think": False, "k_kimi_effort": "Low",
    "k_meta_model": "Spark 1.3 (balanced)", "k_meta_eff": "Low",
    "k_mm_model": "M3 (flagship)", "k_mm_speed": "Standard", "k_mm_think": True,
    "k_nv_model": "Nemotron 3.5 Lightning (economy)", "k_nv_think": True, "k_nv_budget": 4000,
    "k_glm_model": "GLM-5.3 Flash (economy)", "k_glm_think": True, "k_glm_eff": "Low",
    "k_mimo_model": "V2.6 Flash (economy)", "k_mimo_think": True,
    "k_en_gpt": True, "k_en_cla": True, "k_en_gem": True,
    "k_en_ds": True, "k_en_grok": True, "k_en_qwen": False,
    "k_en_mistral": False, "k_en_kimi": False,
    "k_en_meta": False, "k_en_mm": False, "k_en_nv": False, "k_en_glm": False,
    "k_en_mimo": False,
    "k_web_gpt": False, "k_web_cla": False, "k_web_gem": False, "k_web_meta": False,
    "k_web_glm": False,
    "k_web_ds": False, "k_web_grok": False, "k_web_qwen": False,
    "k_web_kimi": False, "k_web_mm": False,
    "k_web_mimo": False,
    "k_web_all": False,
    "prompt_text": "", "view_mode": "Tabs (full width)",
    "src_subview": "Source models", "eval_view_mode": "Tabs (full width)",
    "k_v_gpt": "onyx", "k_v_cla": "nova", "k_v_gem": "fable",
    "k_v_ds": "shimmer", "k_v_grok": "echo", "k_v_qwen": "coral",
    "k_v_mistral": "ash", "k_v_kimi": "sage", "k_v_meta": "cedar", "k_v_mm": "ballad", "k_v_nv": "verse",
    "k_v_glm": "marin", "k_v_mimo": "coral",
    "k_v_syn": "alloy", "k_v_res": "alloy", "k_v_adj": "alloy",
    "k_cla_maxsearch": 3,
    # ⭐ Synthesizer (INITIAL answer): matches the Light run preset by default.
    "k_syn_provider": "Claude", "k_syn_cla_model": "Sonnet 5.5", "k_syn_cla_eff": "Medium",
    "k_syn_cla_think": True, "k_syn_gpt_model": "Sol (advanced)",
    "k_syn_gpt_eff": "High", "k_syn_gpt_speed": "Standard",
    "k_syn_gem_model": "Flash (3.8)", "k_syn_gem_t": "Standard",
    "k_syn_ds_model": "V4.1 Flash (fast · vision)", "k_syn_ds_think": True,
    "k_syn_ds_effort": "High", "k_syn_grok_mode": "Expert",
    "k_syn_qwen_model": "Plus 3.7 (balanced)", "k_syn_qwen_think": True, "k_syn_qwen_budget": 4000, "k_syn_qwen_effort": "Default",
    "k_syn_mistral_model": "Large 3 (balanced)", "k_syn_mistral_eff": "Medium",
    "k_syn_kimi_model": "K2.6 (balanced)", "k_syn_kimi_think": True, "k_syn_kimi_effort": "Low",
    "k_syn_meta_model": "Spark 1.3 (balanced)", "k_syn_meta_eff": "Low", "k_syn_mm_model": "M3 (flagship)", "k_syn_mm_speed": "Standard", "k_syn_mm_think": True,
    "k_syn_nv_model": "Nemotron 3 Super (balanced)", "k_syn_nv_think": True, "k_syn_nv_budget": 8000,
    "k_syn_glm_model": "GLM-5.3 (flagship)", "k_syn_glm_think": True, "k_syn_glm_eff": "Low",
    "k_syn_mimo_model": "V2.6 Pro (flagship)", "k_syn_mimo_think": True,
    "k_syn_web": False, "k_syn_maxsearch": 3, "k_autosynth": True,
    "k_adjudicator_on": False,
    # ⭐ Synthesizer (FOLLOW-UP chat): initialized to the initial Synthesizer and
    # refreshed from the exact setup used for every new answer. It becomes fully
    # independent afterwards so the user can intentionally make later turns lighter.
    "k_fu_provider": "Claude", "k_fu_cla_model": "Sonnet 5.5", "k_fu_cla_eff": "Medium",
    "k_fu_cla_think": True, "k_fu_gpt_model": "Sol (advanced)",
    "k_fu_gpt_eff": "High", "k_fu_gpt_speed": "Standard",
    "k_fu_gem_model": "Flash (3.8)", "k_fu_gem_t": "Standard",
    "k_fu_ds_model": "V4.1 Flash (fast · vision)", "k_fu_ds_think": True,
    "k_fu_ds_effort": "High", "k_fu_grok_mode": "Expert",
    "k_fu_qwen_model": "Plus 3.7 (balanced)", "k_fu_qwen_think": True, "k_fu_qwen_budget": 4000, "k_fu_qwen_effort": "Default",
    "k_fu_mistral_model": "Large 3 (balanced)", "k_fu_mistral_eff": "Medium",
    "k_fu_kimi_model": "K2.6 (balanced)", "k_fu_kimi_think": True, "k_fu_kimi_effort": "Low",
    "k_fu_meta_model": "Spark 1.3 (balanced)", "k_fu_meta_eff": "Low", "k_fu_mm_model": "M3 (flagship)", "k_fu_mm_speed": "Standard", "k_fu_mm_think": True,
    "k_fu_nv_model": "Nemotron 3 Super (balanced)", "k_fu_nv_think": True, "k_fu_nv_budget": 8000,
    "k_fu_glm_model": "GLM-5.3 (flagship)", "k_fu_glm_think": True, "k_fu_glm_eff": "Low",
    "k_fu_mimo_model": "V2.6 Pro (flagship)", "k_fu_mimo_think": True,
    "k_fu_web": False, "k_fu_maxsearch": 3,
    "k_res_provider": "Claude", "k_res_cla_model": "Opus 5.5", "k_res_cla_eff": "High",
    "k_res_cla_think": True, "k_res_gpt_model": "Astra (flagship)",
    "k_res_gpt_eff": "High", "k_res_gpt_speed": "Standard", "k_res_gem_t": "Extended",
    "k_res_ds_model": "V4.1 Flash (fast · vision)", "k_res_ds_think": True,
    "k_res_ds_effort": "Max", "k_res_grok_mode": "Expert",
    "k_res_qwen_model": "Max 3.8 (flagship)", "k_res_qwen_think": True, "k_res_qwen_budget": 16000, "k_res_qwen_effort": "Default",
    "k_res_mistral_model": "Medium 3.5 (advanced)", "k_res_mistral_eff": "High",
    "k_res_kimi_model": "K3.1 Pro (flagship)", "k_res_kimi_think": True, "k_res_kimi_effort": "High",
    "k_res_meta_model": "Spark 1.3 (balanced)", "k_res_meta_eff": "High",
    "k_res_mm_model": "M3 (flagship)", "k_res_mm_speed": "Standard", "k_res_mm_think": True,
    "k_res_nv_model": "Nemotron 3 Ultra (flagship)", "k_res_nv_think": True, "k_res_nv_budget": 24000,
    "k_res_glm_model": "GLM-5.3 (flagship)", "k_res_glm_think": True, "k_res_glm_eff": "High",
    "k_res_mimo_model": "V2.6 Pro (flagship)", "k_res_mimo_think": True,
    "k_res_web": False, "k_res_maxsearch": 7, "res_prompt_text": "",
    "k_adj_provider": "ChatGPT", "k_adj_cla_model": "Opus 5.5", "k_adj_cla_eff": "High",
    "k_adj_cla_think": True, "k_adj_gpt_model": "Sol (advanced)",
    "k_adj_gpt_eff": "Low", "k_adj_gpt_speed": "Standard",
    "k_adj_gem_model": "Flash (3.8)", "k_adj_gem_t": "Standard",
    "k_adj_ds_model": "V4.1 Flash (fast · vision)", "k_adj_ds_think": True,
    "k_adj_ds_effort": "High", "k_adj_grok_mode": "Expert",
    "k_adj_qwen_model": "Plus 3.7 (balanced)", "k_adj_qwen_think": True, "k_adj_qwen_budget": 4000, "k_adj_qwen_effort": "Default",
    "k_adj_mistral_model": "Large 3 (balanced)", "k_adj_mistral_eff": "Medium",
    "k_adj_kimi_model": "K2.6 (balanced)", "k_adj_kimi_think": True, "k_adj_kimi_effort": "Low",
    "k_adj_meta_model": "Spark 1.3 (balanced)", "k_adj_meta_eff": "Low", "k_adj_mm_model": "M3 (flagship)", "k_adj_mm_speed": "Standard", "k_adj_mm_think": True,
    "k_adj_nv_model": "Nemotron 3 Super (balanced)", "k_adj_nv_think": True, "k_adj_nv_budget": 8000,
    "k_adj_glm_model": "GLM-5.3 (flagship)", "k_adj_glm_think": True, "k_adj_glm_eff": "Low",
    "k_adj_mimo_model": "V2.6 Pro (flagship)", "k_adj_mimo_think": True,
    "k_adj_web": False, "k_adj_maxsearch": 3,
    "main_view": "⭐ Synthesis", "sp_view": "⭐ Answer",
    "nonce_main": 0, "nonce_mic": 0, "nonce_res": 0, "nonce_res_mic": 0,
    "k_hist_sort": "Newest first", "k_hist_search": "",
    "k_hist_page_sort": "Newest first", "k_hist_page_search": "",
    "k_hist_page_mode": "All chats", "k_hist_include_archive": False,
    "k_context_mode": "Automatic (run preset)",
    "k_page_width": "Narrow", "k_text_size": "Compact", "k_side_text": "Default",
    "k_theme_mode": "Dark", "k_accent": "Slate Mono",
    "k_full_council": False,
    "k_quorum_mode": "Start when most are in (faster)",
    "k_live_preview": True,
    "k_jev_mode": "Shadow (record only)",
    "k_fresh_mode": "When web is on, or Jev says the question needs current facts",
    "k_pack_gpt": False, "k_pack_cla": False, "k_pack_gem": False, "k_pack_ds": False,
    "k_pack_grok": False, "k_pack_qwen": False, "k_pack_mistral": False,
    "k_pack_kimi": False, "k_pack_meta": False, "k_pack_mm": False, "k_pack_nv": False,
    "k_pack_glm": False, "k_pack_mimo": False,
    "k_use_tavily": True, "k_use_brave": True,
    "k_tavily_depth": "Advanced (2 credits — richer excerpts)",
}
if ALL_SOURCES_ON_FOR_TESTING:
    for _k in _EN_KEYS_ALL:
        DEFAULTS[_k] = True
if ADJUDICATION_ON_FOR_TESTING:
    DEFAULTS["k_adjudicator_on"] = True
for _k, _v in DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v
# Normalize every native model picker before widgets or CFG index its value.
_RETIRED_MODEL_LABELS = {
    "Claude": {"Fable 5": "Fable 5.1", "Opus 5": "Opus 5.5", "Sonnet 5": "Sonnet 5.5",
               "Haiku 4.5": "Haiku 5.5"},
    "ChatGPT": {"Sol (flagship)": "Sol (advanced)", "Terra (balanced)": "Sol (advanced)"},
    "Gemini": {"Flash (3.7)": "Flash (3.8)", "Flash (3.6)": "Flash (3.8)"},
    "Meta": {"Spark 1.2 (flagship)": "Spark 1.3 (balanced)",
             "Spark 1.2 (prior)": "Spark 1.3 (balanced)",
             "Spark 1.1 (prior)": "Spark 1.3 (balanced)",
             "Spark 1.3 (flagship)": "Spark 1.3 (balanced)"},
    "Kimi": {"K3 (flagship)": "K3.1 Pro (flagship)"},
    "Qwen": {"Flash 3.6 (economy)": "Flash 3.8 (economy)"},
    "Mistral": {"Medium 3.5 (flagship)": "Medium 3.5 (advanced)"},
    "NVIDIA": {"Nemotron 3 Nano (economy)": "Nemotron 3.5 Lightning (economy)"},
    "GLM": {"GLM-5 Turbo (balanced)": "GLM-5.3 Flash (economy)",
            "GLM-4.7 (economy)": "GLM-5.3 Flash (economy)"},
    "MiniMax": {"M2.7 (balanced)": "M3 (flagship)", "M2.7 highspeed (fast)": "M3 (flagship)"},
    "DeepSeek": {"V4 Flash (fast)": "V4.1 Flash (fast · vision)",
                 "V4 Pro (flagship)": "V4.1 Flash (fast · vision)",
                 "V4 Flash Vision (experimental)": "V4.1 Flash (fast · vision)"},
}


def migrate_model_labels(state, defaults, specs, aliases):
    """Preserve valid selections; migrate known labels or use a valid default."""
    for provider, (source_key, suffix, options) in specs.items():
        keys = [source_key] + [f"{prefix}_{suffix}" for prefix in
                               ("k_syn", "k_fu", "k_adj", "k_res")]
        for key in keys:
            current = state.get(key)
            if current in options:
                continue
            successor = aliases.get(provider, {}).get(current)
            default = defaults.get(key)
            state[key] = (successor if successor in options else
                          default if default in options else next(iter(options)))


migrate_model_labels(st.session_state, DEFAULTS, MODEL_PICKER_SPECS,
                     _RETIRED_MODEL_LABELS)
st.session_state.setdefault("source_chats", {})
st.session_state.setdefault("source_promotions", [])
st.session_state.setdefault("res_source_chats", {})
st.session_state.setdefault("res_source_promotions", [])
if st.session_state.get("main_view") == "\U0001F4DC Golden Document":
    st.session_state["main_view"] = VIEW_DOC
if st.session_state.get("main_view") == VIEW_ABOUT:
    st.session_state["main_view"] = VIEW_SETTINGS
    st.session_state["_show_about_dialog"] = True
for _k in DEFAULTS:
    st.session_state[_k] = st.session_state[_k]
for _k in [k for k in list(st.session_state) if str(k).startswith("txt_fu_")]:
    st.session_state[_k] = st.session_state[_k]

def _navigate(view_name):
    """Navigation callback shared by the sidebar and both Conclave brand controls.

    Also remembers the view being LEFT (not the one being entered) as
    `_prev_view`, purely so render_back_button() below has somewhere honest
    to send the user -- a real one-step history, not a guess."""
    current = st.session_state.get("main_view")
    if current and current != view_name:
        st.session_state["_prev_view"] = current
    st.session_state["main_view"] = view_name

def render_back_button(key):
    st.button("← Back", key=key, on_click=_navigate,
              args=(st.session_state.get("_prev_view") or VIEW_SYNTH,))

def _open_about_dialog(focus=None):
    st.session_state["_show_about_dialog"] = True
    st.session_state["_about_focus"] = focus

def _reset_session_cost():
    st.session_state["session_cost"] = 0.0

def qwen_best_web_model(current_label):
    if not QWEN_WEB_MODELS:
        return current_label
    if "flash" in current_label.lower():
        return "Flash 3.8 (economy)"
    return "Max 3.8 (flagship)"

def _sync_source_qwen_web():
    """Model changed to a 3.7 while web was on -> web off (honest)."""
    if not _source_qwen_web_supported():
        st.session_state["k_web_qwen"] = False

def _sync_source_qwen_web_toggle():
    """Web turned on with a 3.7 model -> switch to the nearest 3.8 (moves
    the preset to Custom, honestly, exactly like DeepSeek's rescue)."""
    if st.session_state.get("k_web_qwen") and not _source_qwen_web_supported():
        st.session_state["k_qwen_model"] = qwen_best_web_model(st.session_state.get("k_qwen_model", ""))

def _sync_evaluator_qwen_web(prefix):
    if not _evaluator_qwen_web_supported(prefix):
        st.session_state[f"{prefix}_web"] = False

def _sync_evaluator_qwen_web_toggle(prefix):
    if st.session_state.get(f"{prefix}_web") and not _evaluator_qwen_web_supported(prefix):
        st.session_state[f"{prefix}_qwen_model"] = qwen_best_web_model(
            st.session_state.get(f"{prefix}_qwen_model", ""))

def _source_qwen_web_supported():
    return qwen_web_supported(QWEN_MODELS[st.session_state.get("k_qwen_model", "Max 3.8 (flagship)")])

def _evaluator_qwen_web_supported(prefix):
    return qwen_web_supported(QWEN_MODELS[st.session_state.get(f"{prefix}_qwen_model", "Max 3.8 (flagship)")])

def _source_deepseek_web_supported():
    model_id = DEEPSEEK_MODELS[st.session_state.get("k_ds_model", "V4.1 Flash (fast · vision)")]
    return deepseek_web_supported(model_id)

def _evaluator_deepseek_web_supported(prefix):
    label = st.session_state.get(f"{prefix}_ds_model", "V4.1 Flash (fast · vision)")
    return deepseek_web_supported(DEEPSEEK_MODELS[label])

def _sync_source_deepseek_web():
    if not _source_deepseek_web_supported():
        st.session_state["k_web_ds"] = False

def _sync_evaluator_deepseek_web(prefix):
    if not _evaluator_deepseek_web_supported(prefix):
        st.session_state[f"{prefix}_web"] = False

def _sync_source_deepseek_web_toggle():
    if st.session_state.get("k_web_ds") and not _source_deepseek_web_supported():
        st.session_state["k_ds_model"] = deepseek_best_web_model()

def _sync_evaluator_deepseek_web_toggle(prefix):
    if st.session_state.get(f"{prefix}_web") and not _evaluator_deepseek_web_supported(prefix):
        st.session_state[f"{prefix}_ds_model"] = deepseek_best_web_model()


def _flip_source_web(provider):
    key = _SRC_WEB_KEY[provider]
    st.session_state[key] = not bool(st.session_state.get(key))
    if provider == "DeepSeek":
        _sync_source_deepseek_web_toggle()
    if provider == "Qwen":
        _sync_source_qwen_web_toggle()
    if st.session_state[key] and provider in WEB_TIER_FLOOR:
        _note_web_floor_crossed([provider])

def _sync_source_deepseek_web_toggle_with_floor_note():
    _sync_source_deepseek_web_toggle()
    if st.session_state.get("k_web_ds"):
        _note_web_floor_crossed(["DeepSeek"])

def _flip_source_active(provider):
    key = _NAME_TO_EN_KEY[provider]
    st.session_state[key] = not bool(st.session_state.get(key))

def source_card_controls(provider):
    def _pages():
        ready = _SEARCH_READY if "_SEARCH_READY" in globals() else shared_search_ready()
        key = PACK_KEYS[provider]
        on = ready and bool(st.session_state.get(key))
        st.button("🔎", key=f"srcpack_{provider}", type="primary" if on else "secondary",
                  use_container_width=True, disabled=not ready,
                  on_click=_flip_flag, args=(key,),
                  help=(f"Tavily + Brave pages for {provider}: "
                        f"{'ON — click to turn off' if on else 'off — click to turn on'}. "
                        "One shared search per turn; the pages are added to this source's "
                        "question." if ready else
                        "Tavily + Brave pages: add a Tavily or Brave key in Settings ▸ API keys "
                        "(or switch an engine on under Speed & helpers)."))
    def _web():
        if provider in NO_WEB_PROVIDERS:
            st.button("🌐", key=f"srcweb_{provider}", disabled=True, use_container_width=True,
                      help=f"{provider} has no web search of its own — use 🔎 for the Tavily "
                           "+ Brave pages.")
        else:
            on = bool(st.session_state.get(_SRC_WEB_KEY[provider]))
            _help = (f"{provider}'s own web search: "
                     f"{'ON — click to turn off' if on else 'off — click to turn on'}.")
            if provider in WEB_TIER_FLOOR and _web_tier_too_low(provider):
                _help += (f" ⏱️💸 Below its recommended {WEB_TIER_FLOOR[provider]} floor: "
                          f"{WEB_FLOOR_EVIDENCE[provider]}.")
            st.button("🌐", key=f"srcweb_{provider}", type="primary" if on else "secondary",
                      use_container_width=True, on_click=_flip_source_web, args=(provider,),
                      help=_help)
    def _active():
        en_key = _NAME_TO_EN_KEY[provider]
        parked = source_parked(provider)
        on = bool(st.session_state.get(en_key)) and not parked
        st.button("ON" if on else "OFF", key=f"srcon_{provider}",
                  type="primary" if on else "secondary", use_container_width=True,
                  disabled=parked, on_click=_flip_source_active, args=(provider,),
                  help=("Sitting out while web search is on — it can't search the web itself. Turn on its 🔎 pages to bring it back."
                        if parked else
                        f"{provider} as a source: {'ON — click to deactivate' if on else 'off — click to activate'}."))
    return (_pages, _web, _active)

def _flip_evaluator_web(prefix):
    key = f"{prefix}_web"
    st.session_state[key] = not bool(st.session_state.get(key))
    if st.session_state.get(f"{prefix}_provider") == "DeepSeek":
        _sync_evaluator_deepseek_web_toggle(prefix)
    if st.session_state.get(f"{prefix}_provider") == "Qwen":
        _sync_evaluator_qwen_web_toggle(prefix)

def _flip_flag(key):
    st.session_state[key] = not bool(st.session_state.get(key))

def evaluator_card_controls(role, prefix, on_key):
    def _web():
        prov = st.session_state.get(f"{prefix}_provider")
        if prov in NO_WEB_PROVIDERS:
            st.button("🌐", key=f"evalweb_{role}", disabled=True, use_container_width=True,
                      help=f"{prov} can't search the web, so this seat can't web-verify.")
        else:
            on = bool(st.session_state.get(f"{prefix}_web"))
            st.button("🌐", key=f"evalweb_{role}", type="primary" if on else "secondary",
                      use_container_width=True, on_click=_flip_evaluator_web, args=(prefix,),
                      help=f"Web verification for the {role}: {'ON — click to turn off' if on else 'off — click to turn on'}.")
    def _on():
        on = bool(st.session_state.get(on_key))
        st.button("ON" if on else "OFF", key=f"evalon_{role}",
                  type="primary" if on else "secondary", use_container_width=True,
                  on_click=_flip_flag, args=(on_key,),
                  help=(f"{role}: {'ON — click to turn off' if on else 'off — click to turn on'}."
                        + ((" (Defaults ON at every preset right now.)"
                            if ADJUDICATION_ON_FOR_TESTING else
                            " (Adjudication defaults on at Thorough/Deep.)")
                           if role == "Adjudicator" else
                           " (Off: you get only the sources' own answers, with no combined "
                           "answer.)")))
    return (_web, _on)

def _jev_suggest(text_key, slot="_jev_suggestion", follow_up=False):
    text = (st.session_state.get(text_key) or "").strip()
    if not text:
        st.session_state[slot] = {"error": "Type a question first, then ask Jev."}
        return
    previous = ""
    if follow_up:
        turns = (st.session_state.get("synth") or {}).get("turns") or []
        previous = turns[-1].get("question", "") if turns else st.session_state.get("prompt", "")
    try:
        r = jev_route(load_keys().get(HELPER_KEYS["Jev"], ""), text, follow_up=follow_up,
                      previous_question=previous,
                      has_files=bool(st.session_state.get("_staged_main")))
    except Exception as e:
        st.session_state[slot] = {"error": f"Jev couldn't answer ({str(e)[:120]})."}
        return
    r["follow_up"] = follow_up
    st.session_state[slot] = r
    if r.get("cost"):
        st.session_state["session_cost"] = float(st.session_state.get("session_cost", 0.0) or 0.0) + r["cost"]


def _apply_jev_suggestion(slot="_jev_suggestion"):
    sug = st.session_state.pop(slot, None) or {}
    if sug.get("preset") in PRESETS and sug["preset"] != current_preset_name():
        _apply_preset(sug["preset"])
    nw = sug.get("needs_web")
    if isinstance(nw, (int, float)):
        want = nw >= 0.5
        if bool(st.session_state.get("k_web_all")) != want:
            st.session_state["k_web_all"] = want
            _set_all_source_web()
    nc = sug.get("needs_council")
    if sug.get("follow_up") and isinstance(nc, (int, float)):
        want = nc >= 0.5
        if bool(st.session_state.get("k_full_council")) != want:
            st.session_state["k_full_council"] = want
            if want:
                _restore_initial_council()      # same as flipping the toggle on


def _dismiss_jev_suggestion(slot="_jev_suggestion"):
    st.session_state.pop(slot, None)


def jev_helpers_ready():
    return (st.session_state.get("k_jev_mode") != "Off"
            and bool(load_keys().get(HELPER_KEYS["Jev"])))


def render_jev_suggest_button(key, text_key, slot="_jev_suggestion", follow_up=False, label="💡"):
    if not jev_helpers_ready():
        return
    st.button(label, key=key, use_container_width=True, on_click=_jev_suggest,
              args=(text_key, slot, follow_up), disabled=bg_active(),
              help="Ask Jev (a tiny, fast helper model) which preset and whether web search suit what you've typed"
                   + (", and whether this follow-up needs the full council" if follow_up else "")
                   + " (about half a second, a tiny fraction of a cent). Nothing changes "
                     "unless you click Apply.")


def render_jev_suggestion(slot="_jev_suggestion"):
    sug = st.session_state.get(slot)
    if not sug:
        return
    if sug.get("error"):
        st.caption(f"💡 {sug['error']}")
        return
    nw = sug.get("needs_web")
    web_txt = ("" if not isinstance(nw, (int, float)) else
               f" → web search **{'on' if nw >= 0.5 else 'off'}**")
    nc = sug.get("needs_council")
    council_txt = ("" if not (sug.get("follow_up") and isinstance(nc, (int, float))) else
                   f" → **{'full council' if nc >= 0.5 else 'Synthesizer alone'}**")
    c1, c2, c3 = st.columns([7, 1.3, 0.6], gap="small", vertical_alignment="center")
    with c1:
        st.caption(f"💡 Jev suggests **{sug.get('preset') or current_preset_name()}**"
                   f"{web_txt}{council_txt} · "
                   + jev_router_text(sug).replace("Jev's read: ", ""))
    with c2:
        st.button("Apply", key=f"jev_apply{slot}", use_container_width=True,
                  on_click=_apply_jev_suggestion, args=(slot,))
    with c3:
        st.button("✕", key=f"jev_dismiss{slot}", on_click=_dismiss_jev_suggestion,
                  args=(slot,), help="Dismiss the suggestion.")


def _toggle_web_all():
    st.session_state["k_web_all"] = not bool(st.session_state.get("k_web_all"))
    _set_all_source_web()

def stage_uploads(up_key, stage_key):
    current = st.session_state.get(up_key)
    if current:
        sig = [(getattr(f, "file_id", None) or getattr(f, "name", str(f)), getattr(f, "size", None)) for f in current]
        if st.session_state.get(stage_key + "_sig") == sig and st.session_state.get(stage_key):
            staged = st.session_state[stage_key]
        else:
            staged = pack_files(current)
            st.session_state[stage_key + "_sig"] = sig
    else:
        staged = st.session_state.get(stage_key) or []
    st.session_state[stage_key] = staged
    st.session_state.setdefault("_uploaders_mounted_now", set()).add(up_key)
    return staged

ATTACH_THUMB_MAX_B64 = 1_500_000   # ~1.1 MB image; larger ones get an icon chip


def _clear_attachments(stage_key, nonce_key):
    st.session_state[nonce_key] = st.session_state.get(nonce_key, 0) + 1
    st.session_state[stage_key] = []
    st.session_state.pop(stage_key + "_sig", None)


def render_attachment_strip(files, stage_key=None, nonce_key=None, key=None):
    if not files:
        return
    chips = []
    for f in files:
        if isinstance(f, dict):
            name = html.escape(f.get("name") or "file")
            mime = f.get("mime") or ""
            b64 = f.get("b64") if f.get("kind") == "image" else None
        else:
            name = html.escape(getattr(f, "name", "file"))
            mime = getattr(f, "type", "") or ""
            b64 = base64.b64encode(f.getvalue()).decode() if mime.startswith("image/") else None
        if b64 and len(b64) > ATTACH_THUMB_MAX_B64:
            b64 = None
            chips.append(f'<span class="conclave-att-chip">'
                         f'<span class="att-ico">🖼️</span><span>{name}</span></span>')
            continue
        if b64:
            chips.append(f'<span class="conclave-att-chip">'
                         f'<img src="data:{mime};base64,{b64}" alt=""/>'
                         f'<span>{name}</span></span>')
        else:
            icon = "📄" if name.lower().endswith(".pdf") else "📝"
            chips.append(f'<span class="conclave-att-chip">'
                         f'<span class="att-ico">{icon}</span><span>{name}</span></span>')
    if stage_key and nonce_key and key:
        _strip, _x = st.columns([12, 1], gap="small", vertical_alignment="center")
        with _strip:
            st.markdown(f'<div class="conclave-att-strip">'
                        f'<span class="conclave-att-count">📎 {len(files)}</span>'
                        f'{"".join(chips)}</div>', unsafe_allow_html=True)
        with _x:
            st.button("✕", key=key, on_click=_clear_attachments, args=(stage_key, nonce_key),
                      help="Remove the attached file(s) from this message.")
        return
    st.markdown(f'<div class="conclave-att-strip">'
                f'<span class="conclave-att-count">📎 {len(files)}</span>'
                f'{"".join(chips)}</div>', unsafe_allow_html=True)

def render_web_search_button(key):
    """The composer's global web-search control as a lit/unlit button.
    Reads the SAME k_web_all flag the Council page's switch drives (kept
    honest every run by _sync_web_all_flag), so the two can never disagree."""
    on = bool(st.session_state.get("k_web_all"))
    pages = shared_search_ready() and any(st.session_state.get(k) for k in PACK_KEYS.values())
    st.button("🌐🔎 Web" if pages else "🌐 Web", key=key,
              type="primary" if on else "secondary", use_container_width=True,
              on_click=_toggle_web_all,
              help=("Web search is ON for every supported source, the Synthesizer, and the "
                    "Adjudicator — click to turn it off." if on else
                    "Click to turn web search ON for every supported source, the "
                    "Synthesizer, and the Adjudicator. Off by default: faster, cheaper; "
                    "turn it on for time-sensitive questions."))

def render_council_button(key):
    """One click from the composer to the 🏛️ Council page, labeled with the
    live council digest."""
    _cs, _csyn, _cadj, _cadj_name = council_summary()
    _cpreset = current_preset_name()
    st.button(f"🏛️ {len(_cs)} · ⭐ {_csyn}" + (f" · ⚖️ {_cadj_name}" if _cadj else "")
              + f" · 🎚️ {_cpreset}",
              key=key, use_container_width=True,
              on_click=_navigate, args=(VIEW_COUNCIL,),
              help=(f"Open the Council page to choose who answers and how hard they work. Preset: {_cpreset}. Sources now: "
                    + (", ".join(_cs) if _cs else "no sources active")
                    + f" → ⭐ {_csyn} synthesizes"
                    + (f" → ⚖️ {_cadj_name} adjudicates" if _cadj else "") + "."))

def _set_all_source_web():
    enabled = bool(st.session_state.get("k_web_all", False))
    for key in ("k_web_gpt", "k_web_cla", "k_web_gem", "k_web_meta", "k_web_glm", "k_web_mimo",
                "k_web_grok", "k_web_qwen", "k_web_kimi", "k_web_mm"):
        st.session_state[key] = enabled
    st.session_state["k_web_ds"] = False
    ready = shared_search_ready()
    for prov, pack_key in PACK_KEYS.items():
        by_pages = enabled and ready and pages_by_default(prov)
        st.session_state[pack_key] = by_pages
        if by_pages and _SRC_WEB_KEY.get(prov):
            st.session_state[_SRC_WEB_KEY[prov]] = False
    if st.session_state.get("k_web_qwen") and not _source_qwen_web_supported():
        st.session_state["k_web_qwen"] = False
    if enabled:
        _note_web_floor_crossed(list(WEB_TIER_FLOOR))
    for prefix in ("k_syn", "k_fu", "k_adj"):
        provider = st.session_state.get(f"{prefix}_provider")
        # No-web providers never get a seat web flag; a Qwen seat whose model
        # cannot search stays off rather than being moved to another model.
        if provider in NO_WEB_PROVIDERS:
            continue
        if provider == "Qwen" and not _evaluator_qwen_web_supported(prefix):
            st.session_state[f"{prefix}_web"] = False
            continue
        st.session_state[f"{prefix}_web"] = enabled

def _render_role_provider_picker(role_key, options, on_pick, label_help, disabled=False):
    wk = f"pick_{role_key}"
    cur = st.session_state.get(role_key, options[0])
    def _changed():
        on_pick(st.session_state[wk])
    kwargs = dict(key=wk, on_change=_changed, help=label_help,
                  format_func=lambda p: f"{ICON.get(p, '')} {p}", disabled=disabled)
    if wk in st.session_state:
        st.session_state[wk] = cur if cur in options else options[0]
        st.selectbox("Provider", options, **kwargs)
    else:
        st.selectbox("Provider", options,
                     index=options.index(cur) if cur in options else 0, **kwargs)

def render_initial_synth_panel(show_provider_picker=True):
    st.caption("On your first send the sources answer in parallel, then this model weighs "
               "them all, applies its own knowledge and judgment, and writes one enhanced, "
               "definitive answer. From then on it simply continues the conversation with "
               "you — carrying everything established so far and still synthesizing every "
               "new claim — while **🏛️ Full council this reply** (by the follow-up box) "
               "re-runs the whole council for any reply you choose.")
    _locked = synth_locked_provider()
    _render_role_provider_picker(
        "k_syn_provider",
        [p for p in EVAL_PROVIDERS if p != st.session_state.get("k_adj_provider")],
        _set_synth_provider,
        "Which provider synthesizes. The current Adjudicator isn't offered here — "
        "the two roles must be different providers; change the Adjudicator's own "
        "provider first if you want it synthesizing instead.",
        disabled=bool(_locked))
    if _locked:
        st.caption(f"🔒 Locked to {ICON.get(_locked, '')} **{_locked}** for this conversation "
                   "— it carries this chat's memory (its earlier answers, its running notes, "
                   "and its record of what each source contributed). Model, effort, "
                   "web verify, and the run preset stay adjustable.")
        st.button("🔓 Change provider anyway", key="unlock_synth_provider",
                  on_click=_unlock_synth_provider,
                  help="Hands the rest of this conversation to a different provider. It "
                       "receives the same written context but did not build it — expect "
                       "a change of voice. Starting a new chat is the cleaner path.")
    c_toggle("Auto-synthesize on send", "k_autosynth",
             help="On: after the sources answer, the Synthesizer writes one combined answer. "
                  "Off: you only get the sources' own answers to compare — cheaper for quick checks.")
    p = render_evaluator_controls("k_syn", show_provider_picker=show_provider_picker)
    c_toggle("\U0001F310 Web verify", "k_syn_web", disabled=(p in NO_WEB_PROVIDERS),
             after_change=lambda: (_sync_evaluator_deepseek_web_toggle("k_syn"),
                                   _sync_evaluator_qwen_web_toggle("k_syn")),
             help="One extra pass: the Synthesizer fact-checks contested or time-sensitive "
                  "claims itself before writing the definitive answer. DeepSeek, the 3.7 Qwen models, "
                  "Mistral and NVIDIA cannot search, so this stays off for them.")
    if p == "Claude":
        c_number("Max web searches", "k_syn_maxsearch", min_value=1, max_value=20, step=1,
                 disabled=not st.session_state["k_syn_web"],
                 help="Applies when web verify is on. Claude needs this cap; "
                      "other providers self-limit.")
        _claude_search_recommendation_caption("k_syn")
    st.caption("The Synthesizer never answers as a regular source. With adjudication on, it "
               "also writes its own first take before seeing the others (mixed in, unlabeled, with "
               "the answers it weighs), then writes the final answer.")

# Same definition-order requirement as render_initial_synth_panel above: the
# sidebar calls this directly at top-level script execution, right below the
# Synthesizer panel, so it must also be defined before st.set_page_config().
def render_adjudicator_panel(show_provider_picker=True):
    st.caption("Checks the Synthesizer's draft against the source answers before you "
               "see it and lists any real problems; if it finds one, the Synthesizer revises the answer once.")
    _render_role_provider_picker(
        "k_adj_provider",
        [p for p in EVAL_PROVIDERS if p != st.session_state.get("k_syn_provider")],
        _set_adjudicator_provider,
        "Which provider audits. The current Synthesizer isn't offered here — the "
        "two roles must be different providers, so the audit is a genuinely "
        "independent second read, not the same lineage checking itself.")
    c_toggle("⚖️ Adjudication active this run", "k_adjudicator_on",
             help="On: the Adjudicator checks the Synthesizer's draft before you see it (both "
                  "judges also write a private first take, and a fix-up pass runs if real problems turn up). "
                  "Off: a plain Synthesizer answer — faster and cheaper. "
                  + ("Every preset currently defaults this ON; "
                     if ADJUDICATION_ON_FOR_TESTING else
                     "Named presets set this by default — off at Fast/Light/Balanced, on at "
                     "Thorough/Deep — ")
                  + "you can override it either direction here, for any preset.")
    p = render_evaluator_controls("k_adj", show_provider_picker=show_provider_picker)
    c_toggle("\U0001F310 Web verify", "k_adj_web", disabled=(p in NO_WEB_PROVIDERS),
             after_change=lambda: (_sync_evaluator_deepseek_web_toggle("k_adj"),
                                   _sync_evaluator_qwen_web_toggle("k_adj")),
             help="Lets the Adjudicator fact-check contested claims itself while auditing. DeepSeek, "
                  "the 3.7 Qwen models, Mistral and NVIDIA cannot search, so this stays off for them.")
    if p == "Claude":
        c_number("Max web searches", "k_adj_maxsearch", min_value=1, max_value=20, step=1,
                 disabled=not st.session_state["k_adj_web"],
                 help="Applies when web verify is on. Claude needs this cap; "
                      "other providers self-limit.")
        _claude_search_recommendation_caption("k_adj")
    st.caption("Always a different provider from the Synthesizer, so the check is independent — "
               "the Synthesizer's provider isn't offered in the list above.")

st.set_page_config(page_title="Conclave", page_icon=BRAND_MARK, layout="wide")

def install_send_shortcut():
    """Install one parent-page Ctrl/Cmd+Enter bridge for every text composer.

    Streamlit text areas do not expose a native submit shortcut. The bridge
    identifies the keyed composer that owns the focused textarea, blurs it so
    its latest value is committed, then clicks that composer's visible button.
    """
    st.html("""
<script>
(() => {
  const doc = document;
  if (doc.__conclaveSendShortcutInstalled) return;
  doc.__conclaveSendShortcutInstalled = true;

  const keyedAncestor = (element) => {
    let node = element;
    while (node && node !== doc.body) {
      for (const className of node.classList || []) {
        if (className.startsWith("st-key-")) return className;
      }
      node = node.parentElement;
    }
    return "";
  };
  const visibleButton = (selector) =>
    [...doc.querySelectorAll(selector)].find((button) => {
      const rect = button.getBoundingClientRect();
      return !button.disabled && rect.width > 0 && rect.height > 0;
    });

  doc.addEventListener("keydown", (event) => {
    
    if (event.key !== "Enter" || event.shiftKey || event.altKey || event.repeat) return;
    const textarea = event.target;
    if (!(textarea instanceof HTMLTextAreaElement)) return;

    const owner = keyedAncestor(textarea);
    let buttonSelector = "";
    if (owner === "st-key-prompt_text") {
      buttonSelector = ".st-key-send_synth button";
    } else if (owner === "st-key-res_prompt_text") {
      buttonSelector = ".st-key-run_res_btn button";
    } else {
      const followUp = owner.match(/^st-key-txt_(.+)_\\d+$/);
      if (followUp) buttonSelector = `.st-key-sendbtn_${followUp[1]} button`;
    }
    if (!buttonSelector || !visibleButton(buttonSelector)) return;

    event.preventDefault();
    event.stopPropagation();
    textarea.blur();
    window.setTimeout(() => visibleButton(buttonSelector)?.click(), 50);
  }, true);

  
  const composerBox = () =>
    [...doc.querySelectorAll('.st-key-prompt_text textarea, [class*="st-key-txt_"] textarea')]
      .find((t) => { const r = t.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  let lastFocused = null;
  const autofocus = () => {
    const box = composerBox();
    if (!box || box === lastFocused) return;
    const active = doc.activeElement;
    if (active && active !== doc.body && active.tagName !== "IFRAME" && active !== box) return;
    lastFocused = box;
    box.focus({ preventScroll: true });
  };
  new MutationObserver(() => window.setTimeout(autofocus, 80))
    .observe(doc.body, { childList: true, subtree: true });
  window.setTimeout(autofocus, 300);

  
  const sleep = (ms) => new Promise((r) => window.setTimeout(r, ms));
  const visibleFileInput = () =>
    [...doc.querySelectorAll('input[type="file"]')].find((i) => {
      const box = i.closest('[data-testid="stFileUploader"]') || i.parentElement;
      const r = box.getBoundingClientRect(); return r.width > 0 && r.height > 0;
    });
  const attachButtonFor = (textarea) => {
    let node = textarea;
    while (node && node !== doc.body) {
      const btn = [...node.querySelectorAll("button")].find((b) => /Attach/.test(b.innerText));
      if (btn) return btn;
      node = node.parentElement;
    }
    return null;
  };
  const handToUploader = async (textarea, files) => {
    const btn = attachButtonFor(textarea);
    if (!btn) return;
    let input = visibleFileInput();
    if (!input) {
      btn.click();
      for (let i = 0; i < 40 && !input; i++) { await sleep(100); input = visibleFileInput(); }
    }
    if (!input) return;
    const dt = new DataTransfer();
    for (const f of files) dt.items.add(f);
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const composerTextarea = (target) =>
    target instanceof HTMLTextAreaElement && keyedAncestor(target).match(
      /^st-key-(prompt_text|res_prompt_text|txt_.+)$/) ? target : null;
  doc.addEventListener("paste", (event) => {
    const textarea = composerTextarea(event.target);
    const files = [...(event.clipboardData?.files || [])];
    if (!textarea || !files.length) return;
    event.preventDefault();
    handToUploader(textarea, files);
  }, true);
  doc.addEventListener("dragover", (event) => {
    if (composerTextarea(event.target) && event.dataTransfer?.types?.includes("Files")) {
      event.preventDefault();
    }
  }, true);
  doc.addEventListener("drop", (event) => {
    const textarea = composerTextarea(event.target);
    const files = [...(event.dataTransfer?.files || [])];
    if (!textarea || !files.length) return;
    event.preventDefault();
    handToUploader(textarea, files);
  }, true);
})();
</script>
""", unsafe_allow_javascript=True)


def install_run_control_hover_titles():
    _current = current_preset_name()
    titles = {
        ".st-key-run_preset_choice": preset_dial_help_text(_current),
        ".st-key-global_k_web_all": web_toggle_help_text(_current),
    }
    st.html(f"""
<script>
(() => {{
  const doc = document;
  doc.__conclaveRunTitles = {json.dumps(titles)};
  const apply = () => {{
    const titles = doc.__conclaveRunTitles || {{}};
    for (const sel of Object.keys(titles)) {{
      const el = doc.querySelector(sel);
      if (el && el.title !== titles[sel]) el.title = titles[sel];
    }}
  }};
  apply();
  if (!doc.__conclaveRunTitlesInstalled) {{
    doc.__conclaveRunTitlesInstalled = true;
    new MutationObserver(apply).observe(doc.body, {{childList: true, subtree: true}});
  }}
}})();
</script>
""", unsafe_allow_javascript=True)

install_send_shortcut()
# install_run_control_hover_titles() is called AFTER the sidebar block below
# -- it reads current_preset_name(), which (like the preset tables it uses)
# is defined between here and there in top-to-bottom script order.

# A finished background run is committed HERE — before anything renders, and on
# EVERY script run — so results land whichever tab you happen to be on when the
# run completes, and the sidebar/history below already reflect them.
bg_commit_if_done()

st.session_state["_uploaders_mounted_prev"] = st.session_state.get("_uploaders_mounted_now") or set()
st.session_state["_uploaders_mounted_now"] = set()
def _seat_key_fallback():
    if st.session_state.get("synth") or st.session_state.get("_seat_fallback_done"):
        return
    keys = load_keys()
    have = [p for p in MODEL_ORDER if keys.get(KEY_MAP[p])]
    if not have:
        return
    st.session_state["_seat_fallback_done"] = True
    moved = False
    if st.session_state.get("k_syn_provider") not in have:
        _set_synth_provider(have[0])
        moved = True
    others = [p for p in have if p != st.session_state.get("k_syn_provider")]
    if st.session_state.get("k_adj_provider") not in have and others:
        _set_adjudicator_provider(others[0])
        moved = True
    return moved


_enforce_synth_lock()
inherit_followup_from_initial()

def _size_index(state_key, table):
    opts = list(table.keys())
    cur = st.session_state.get(state_key, "Default")
    return opts.index(cur) if cur in opts else opts.index("Default")

def _step_text_size(state_key, delta):
    """− / + stepper shared by the header (k_text_size) and sidebar
    (k_side_text) size buttons -- clamps at the ends of the table."""
    table = TEXT_SIZES if state_key == "k_text_size" else SIDEBAR_TEXT_SCALES
    opts = list(table.keys())
    i = _size_index(state_key, table) + delta
    st.session_state[state_key] = opts[max(0, min(len(opts) - 1, i))]

THEME_SWITCH_ICONS = {"System": "🖥️", "Light": "☀️", "Dark": "🌙"}

with st.container(key="conclave_header"):
    _spacer_col, _theme_col = st.columns([6.4, 1.0], gap=None,
                                         vertical_alignment="center")
    with _theme_col:
        with st.container(key="hdr_text_stepper"):
            _tm, _tp = st.columns(2, gap=None)
            with _tm:
                st.button("−", key="hdr_txt_minus", on_click=_step_text_size,
                          args=("k_text_size", -1), help="Smaller text in the center area",
                          disabled=_size_index("k_text_size", TEXT_SIZES) == 0)
            with _tp:
                st.button("＋", key="hdr_txt_plus", on_click=_step_text_size,
                          args=("k_text_size", 1), help="Larger text in the center area",
                          disabled=_size_index("k_text_size", TEXT_SIZES)
                                   == len(TEXT_SIZES) - 1)
        c_segmented("Appearance", ["System", "Light", "Dark"], "k_theme_mode",
                    ns="hdr", format_func=lambda v: THEME_SWITCH_ICONS[v],
                    label_visibility="collapsed", required=True,
                    help="Appearance: 🖥️ automatic (day from sunrise to sunset), ☀️ always day, or 🌙 always night.")

def synth_preset_settings(preset_name, provider=None):
    """Native initial-Synthesizer settings for one provider at one run tier."""
    provider = provider or st.session_state.get("k_syn_provider", "Claude")
    return SYNTH_PRESETS.get(preset_name, {}).get(provider, {})

def adj_preset_settings(preset_name, provider=None):
    """Native Adjudicator settings for one provider at one run tier."""
    provider = provider or st.session_state.get("k_adj_provider", "ChatGPT")
    return ADJ_PRESETS.get(preset_name, {}).get(provider, {})

def followup_preset_settings(preset_name, provider=None):
    """Native follow-up-Synthesizer settings for one provider at one run tier.

    Reuses SYNTH_PRESETS itself (the initial Synthesizer's own table) with its
    k_syn_ keys renamed to k_fu_ -- the follow-up Synthesizer must use the
    EXACT same per-provider tier dials as the initial one, same discipline
    ADJ_PRESETS already follows relative to SYNTH_PRESETS. No separate
    FU_PRESETS table: keeping one source of truth per tier avoids a second
    place these dials could silently drift apart.
    """
    provider = provider or st.session_state.get("k_fu_provider", "Claude")
    src = SYNTH_PRESETS.get(preset_name, {}).get(provider, {})
    return {key.replace("k_syn_", "k_fu_", 1): value for key, value in src.items()}

def current_preset_name():
    """Which provider-preserving source/Synthesizer/Adjudicator tier is live,
    or Custom."""
    if st.session_state.get("k_preset_custom", False):
        return "Custom"
    for name, cfg in PRESETS.items():
        combined = {**cfg, **synth_preset_settings(name), **adj_preset_settings(name)}
        if all(st.session_state.get(k) == v for k, v in combined.items()):
            return name
    return "Custom"

def adjudicator_default_for_preset(preset_name):
    if ADJUDICATION_ON_FOR_TESTING:
        return True
    return preset_name in ("Thorough", "Deep")

def adjudicator_stack_active():
    return bool(st.session_state.get("k_adjudicator_on", False))

def _apply_synth_preset(preset_name, provider=None):
    """Apply a named tier to the selected Synthesizer provider without
    changing provider."""
    for key, value in synth_preset_settings(preset_name, provider).items():
        st.session_state[key] = value

def _apply_adjudicator_preset(preset_name, provider=None):
    """Apply a named tier to the selected Adjudicator provider without
    changing provider."""
    for key, value in adj_preset_settings(preset_name, provider).items():
        st.session_state[key] = value

def _apply_followup_preset(preset_name, provider=None):
    for key, value in followup_preset_settings(preset_name, provider).items():
        st.session_state[key] = value

def _apply_preset(preset_name):
    """Apply a named full-run tier, or pin the current manual configuration."""
    if preset_name == "Custom":
        st.session_state["k_preset_custom"] = True
        return
    if preset_name in PRESETS:
        st.session_state["k_preset_custom"] = False
        for key, value in PRESETS[preset_name].items():
            st.session_state[key] = value
        _apply_synth_preset(preset_name)
        _apply_adjudicator_preset(preset_name)
        _apply_followup_preset(preset_name)
        st.session_state["k_adjudicator_on"] = adjudicator_default_for_preset(preset_name)
        for key, value in PRESET_SOURCES.get(preset_name, {}).items():
            st.session_state[key] = value
        _table = PRESET_SOURCES.get(preset_name, {})
        st.session_state["_web_parked"] = [
            prov for prov in (st.session_state.get("_web_parked") or [])
            if _table.get(_NAME_TO_EN_KEY.get(prov), True)]

def _preset_dial_changed(widget_key):
    choice = st.session_state.get(widget_key)
    if choice in PRESETS:
        _apply_preset(choice)

def render_preset_dial(widget_key, **kw):
    active = current_preset_name()
    if active in PRESETS:
        st.session_state["_last_named_preset"] = active
    value = active if active in PRESETS else st.session_state.get("_last_named_preset", "Light")
    if widget_key in st.session_state:
        st.session_state[widget_key] = value
        return st.segmented_control("Council effort", list(PRESETS), key=widget_key,
                                    on_change=_preset_dial_changed, args=(widget_key,), **kw)
    return st.segmented_control("Council effort", list(PRESETS), default=value,
                                key=widget_key,
                                on_change=_preset_dial_changed, args=(widget_key,), **kw)

def _toggle_card(card_id):
    ok = f"card_open_{card_id}"
    st.session_state[ok] = not st.session_state.get(ok, False)

def render_card(card_id, label, body_fn, controls=None):
    """A collapsible card whose open/closed state WE own (session_state), keyed
    by a stable card_id rather than the (live, changing) label text. Unlike
    st.expander -- which resets to collapsed whenever its label text changes, or
    when the widget briefly disappears and reappears (e.g. a source card hidden
    by the synthesizer switch) -- this stays exactly as the user left it. Uses
    an on_click callback (not "mutate + st.rerun()") -- see the presets comment
    above for why: an explicit st.rerun() here was found to wipe unsent prompt
    text elsewhere on the page."""
    ok = f"card_open_{card_id}"
    st.session_state.setdefault(ok, False)
    arrow = "▾" if st.session_state[ok] else "▸"
    if controls is None:
        st.button(f"{arrow} {label}", key=f"card_btn_{card_id}", use_container_width=True,
                  on_click=_toggle_card, args=(card_id,))
    else:
        _cc = st.columns([10.0 - 0.8 * len(controls)] + [0.8] * len(controls),
                         gap="small", vertical_alignment="center")
        with _cc[0]:
            st.button(f"{arrow} {label}", key=f"card_btn_{card_id}", use_container_width=True,
                      on_click=_toggle_card, args=(card_id,))
        for _col, _render in zip(_cc[1:], controls):
            with _col:
                _render()
    if st.session_state[ok]:
        with st.container(border=True):
            body_fn()

MODEL_LIST_ENDPOINTS = {
    "Grok": "https://api.x.ai/v1", "DeepSeek": "https://api.deepseek.com",
    "Qwen": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    "Mistral": "https://api.mistral.ai/v1", "Kimi": "https://api.moonshot.ai/v1",
    "Meta": "https://api.meta.ai/v1", "MiniMax": "https://api.minimax.io/v1",
    "NVIDIA": "https://integrate.api.nvidia.com/v1", "GLM": "https://api.z.ai/api/paas/v4/",
    "MiMo": MIMO_URL,
}
MODEL_FAMILY_PREFIX = {
    "ChatGPT": "gpt-", "Claude": "claude-", "Gemini": "gemini-", "Grok": "grok-",
    "Meta": "muse-", "DeepSeek": "deepseek", "Kimi": "kimi-", "Qwen": "qwen3",
    "MiMo": "mimo-", "GLM": "glm-", "MiniMax": "minimax-", "Mistral": "mistral-",
    "NVIDIA": "nvidia/nemotron",
}
MODEL_CHECK_SKIP = ("embed", "tts", "audio", "realtime", "transcri", "image", "moderation",
                    "whisper", "rerank", "ocr", "guard", "safety")


def offered_model_ids(provider):
    """Every model ID Conclave offers for one provider."""
    if provider == "Grok":
        return [MODEL_GROK]
    return list(MODEL_PICKER_SPECS[provider][2].values())


def compare_model_lists(offered, listed, family, alternates=None):
    """(offered IDs missing from the company's list, new same-family IDs).
    Dated snapshots of a model already offered are not reported as new."""
    names = [str(x)[len("models/"):] if str(x).startswith("models/") else str(x) for x in listed]
    lower = {n.lower() for n in names}
    alternates = alternates or {}
    missing = [m for m in offered
               if m.lower() not in lower and not (alternates.get(m) and alternates[m].lower() in lower)]
    offered_l = [o.lower() for o in offered] + [a.lower() for a in alternates.values()]
    new = sorted({n for n in names
                  if family and n.lower().startswith(family)
                  and n.lower() not in offered_l
                  and not any(n.lower().startswith(o + "-") for o in offered_l)
                  and not any(w in n.lower() for w in MODEL_CHECK_SKIP)})
    return missing, new


def list_provider_models(provider, key):
    """The model IDs a company's own list endpoint returns."""
    if provider == "ChatGPT":
        return [m.id for m in OpenAI(api_key=key, timeout=20.0, max_retries=0).models.list()]
    if provider == "Claude":
        return [m.id for m in Anthropic(api_key=key, timeout=20.0, max_retries=0).models.list(limit=1000)]
    if provider == "Gemini":
        return [m.name for m in genai.Client(api_key=key).models.list()]
    client = OpenAI(base_url=MODEL_LIST_ENDPOINTS[provider], api_key=key,
                    timeout=20.0, max_retries=0)
    return [m.id for m in client.models.list()]


def run_model_check(keys):
    """One result per provider: nokey / error (class name only) / ok."""
    def one(provider):
        key = keys.get(KEY_MAP[provider], "")
        if not key:
            return provider, {"status": "nokey"}
        try:
            listed = list_provider_models(provider, key)
        except Exception as e:
            return provider, {"status": "error", "error": type(e).__name__}
        missing, new = compare_model_lists(
            offered_model_ids(provider), listed, MODEL_FAMILY_PREFIX.get(provider, ""),
            MISTRAL_ID_ALTERNATES if provider == "Mistral" else None)
        return provider, {"status": "ok", "missing": missing, "new": new}
    with ThreadPoolExecutor(max_workers=8) as ex:
        return dict(ex.map(one, MODEL_ORDER))


def render_model_check():
    st.markdown("##### Check models")
    st.caption("AI companies retire and release models often. This asks each company's "
               "own model list (free, using your saved keys) whether every model Conclave "
               "uses still exists, and which new ones have appeared. It never changes "
               "anything: prices, effort levels and web search aren't in those lists, so "
               "updates are made by hand (see MODEL_MAINTENANCE.md).")
    if st.button("🔄 Check models now", key="model_check_btn"):
        with st.spinner("Asking each company for its current model list…"):
            st.session_state["_model_check"] = {
                "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "results": run_model_check(load_keys())}
    res = st.session_state.get("_model_check")
    if not res:
        return
    st.caption(f"Checked {res['at']}.")
    lines = []
    for provider in MODEL_ORDER:
        r = res["results"].get(provider, {})
        head = f"{ICON.get(provider, '')} **{provider}** — "
        if r.get("status") == "nokey":
            lines.append(head + "no key saved, not checked")
        elif r.get("status") == "error":
            lines.append(head + f"couldn't check ({r.get('error')}); this company may not "
                         "offer a model list")
        else:
            bits = []
            if r.get("missing"):
                bits.append("⚠️ not in the company's list (retired or renamed?): "
                            + ", ".join(f"`{m}`" for m in r["missing"]))
            if r.get("new"):
                shown = r["new"][:12]
                more = f" and {len(r['new']) - 12} more" if len(r["new"]) > 12 else ""
                bits.append("🆕 new: " + ", ".join(f"`{m}`" for m in shown) + more)
            lines.append(head + (" · ".join(bits) if bits else "✅ every model Conclave uses is listed"))
    st.markdown("\n".join(f"- {line}" for line in lines))


def render_api_keys_panel(expanded=False):
    with st.expander("\U0001F511 API Keys (Saved Locally)", expanded=expanded):
        saved = load_keys()
        _keys_file_problem = keys_decrypt_failed()
        _recovery_notice = st.session_state.pop("_key_recovery_notice", None)
        if _recovery_notice:
            st.success("Replacement keys saved and encrypted for this Windows login. "
                       f"The previous unreadable file was preserved locally as `{_recovery_notice}`.")
        if _keys_file_problem:
            st.warning("⚠️ The saved key file could not be read safely (it may be malformed, "
                       "pywin32 may be missing/broken, or it may come from another Windows "
                       "login). The empty boxes below are NOT proof there are no keys. Saving "
                       "is disabled until you explicitly confirm recovery below.")
        entered_keys = {}
        for _title, _blurb, _fields, _ncols in API_KEY_GROUPS:
            st.markdown(f"**{_title}**")
            st.caption(_blurb)
            _cols = st.columns(_ncols)
            for _i, (_name, _label, _help) in enumerate(_fields):
                with _cols[_i % _ncols]:
                    entered_keys[_name] = st.text_input(_label, type="password",
                                                        value=saved.get(_name, ""),
                                                        help=_help)
                    st.caption(provider_links_caption(_name, short=True))
        if not _DPAPI:
            st.error(f"\U0001F513 {KEYS_UNENCRYPTED_MESSAGE}")
        if st.button("\U0001F4BE Save All Keys", type="primary",
                     disabled=_keys_file_problem or not _DPAPI):
            try:
                save_keys(entered_keys)
                st.success("Keys saved and encrypted for this Windows login.")
            except Exception as save_error:
                # Name the exception CLASS only — enough to tell a lock
                # timeout from a disk error without risking key material
                # from an SDK/OS message reaching the UI.
                st.error("The keys could not be saved. The existing key file was left "
                         f"unchanged. ({type(save_error).__name__})")
            # No st.rerun() here: the boxes already show what was just typed and
            # saved, so nothing stale needs a forced refresh -- and an explicit
            # rerun from this handler would risk the same prompt-wiping bug
            # documented above.
        if _keys_file_problem:
            st.markdown("**Recover key storage**")
            st.caption("Use this only after entering the replacement keys above. Conclave will "
                       "keep the unreadable file as a local backup before replacing it.")
            _confirm_recovery = st.checkbox(
                "I understand this replaces the active key file with the entered keys.",
                key="confirm_key_recovery",
            )
            if st.button(
                "Replace unreadable file with entered keys",
                key="replace_unreadable_keys",
                type="primary",
                disabled=not (_confirm_recovery and any(entered_keys.values()) and _DPAPI),
            ):
                try:
                    _backup_name = replace_unreadable_keys(entered_keys)
                except Exception as recovery_error:
                    st.error("Recovery could not be completed. The previous key file was "
                             f"preserved. ({type(recovery_error).__name__})")
                else:
                    st.session_state["_key_recovery_notice"] = _backup_name
                    st.session_state.pop("confirm_key_recovery", None)
                    st.rerun()
        st.caption("\U0001F512 Encrypted (Windows login)" if _DPAPI
                   else "\U0001F513 Saving disabled — `pip install pywin32` to encrypt keys")

# ================= SIDEBAR =================
_hist_all = load_history()
_alert_log = st.session_state.setdefault("_history_alert_log", [])
for _history_notice in _history_alerts:
    if _history_notice not in _alert_log:
        _alert_log.append(_history_notice)
for _history_notice in _alert_log:
    st.warning(_history_notice)


def any_source_web_on():
    return any(st.session_state.get(k) for k in _SOURCE_WEB_KEYS)

def saved_search_services(keys=None):
    """The search engines with a key saved, whether switched on or not."""
    keys = load_keys() if keys is None else keys
    return {svc: keys[k] for svc, k in SEARCH_KEYS.items() if keys.get(k)}

def web_split_caption():
    ready = _SEARCH_READY if "_SEARCH_READY" in globals() else shared_search_ready()
    if not ready:
        return ("🔎 Tavily + Brave pages: off — add a Tavily or Brave key in Settings ▸ API "
                "keys. Sources that can't search sit out while web is on.")
    active = [p for p in MODEL_ORDER if st.session_state.get(PROVIDERS[p]["enabled_key"])]
    pages = [p for p in active if st.session_state.get(PACK_KEYS[p])]
    own = [p for p in active if PROVIDERS[p]["web_key"]
           and st.session_state.get(PROVIDERS[p]["web_key"])]
    engines = " + ".join(search_keys_now()) or "no engine switched on"
    return (f"🔎 **{engines} pages:** " + (", ".join(pages) or "nobody")
            + "  ·  🌐 **own search:** " + (", ".join(own) or "nobody"))

def search_keys_now(keys=None):
    return {svc: k for svc, k in saved_search_services(keys).items()
            if st.session_state.get(f"k_use_{svc.lower()}", True)}

def shared_search_ready(keys=None):
    return bool(search_keys_now(keys))

def uses_shared_sources(provider, ready=None):
    if not (shared_search_ready() if ready is None else ready):
        return False
    return bool(st.session_state.get(PACK_KEYS.get(provider, ""), False))

def _park_no_web_sources():
    parked = set(st.session_state.get("_web_parked") or [])
    for prov in list(parked):
        if prov in WEB_TIER_FLOOR:
            st.session_state[_NAME_TO_EN_KEY[prov]] = True
            parked.discard(prov)
    if any_source_web_on():
        for prov, en_key in _NO_WEB_EN_KEYS.items():
            if _no_web_gets_pages(prov):
                if prov in parked:
                    st.session_state[en_key] = True
                    parked.discard(prov)
                continue
            if st.session_state.get(en_key):
                st.session_state[en_key] = False
                parked.add(prov)
        st.session_state["_web_parked"] = sorted(parked)
    elif parked:
        for prov in parked:
            st.session_state[_NO_WEB_EN_KEYS[prov]] = True
        st.session_state["_web_parked"] = []

WEB_TIER_FLOOR = {"Grok": "Balanced"}
WEB_FLOOR_EVIDENCE = {
    "Grok": "its own web search took 25–48 s at ~$0.13 per answer — about 4× the rest of a "
            "council combined",
}

def _web_tier_too_low(provider):
    floor = WEB_TIER_FLOOR.get(provider)
    if not floor:
        return False
    tiers = list(PRESETS)
    active = current_preset_name()
    if active not in tiers:
        active = st.session_state.get("_last_named_preset", "Light")
    return tiers.index(active) < tiers.index(floor)

def web_floor_offenders():
    return [prov for prov in WEB_TIER_FLOOR
            if st.session_state.get(_SRC_WEB_KEY[prov]) and _web_tier_too_low(prov)]

def web_floor_warning_text(providers=None):
    """One plain sentence per offender, for the banner / toast / hover."""
    providers = web_floor_offenders() if providers is None else providers
    if not providers:
        return ""
    active = current_preset_name()
    lines = []
    for prov in providers:
        lines.append(f"**{prov}** web search at **{active}** will take longer and cost more — "
                     f"{WEB_FLOOR_EVIDENCE[prov]}. Recommended at "
                     f"{WEB_TIER_FLOOR[prov]} and above; it stays on because you chose it.")
    return "\n\n".join(lines)

def render_web_floor_warning():
    text = web_floor_warning_text()
    if text:
        st.warning("⏱️💸 " + text)

def _note_web_floor_crossed(providers):
    """Called from the web-flip callbacks: remember which providers just
    crossed below their floor so the script body can toast once."""
    crossed = [p for p in providers if _web_tier_too_low(p)]
    if crossed:
        st.session_state["_web_floor_toast"] = crossed

def flush_web_floor_toast():
    """Script-body half of the one-time toast (callbacks can't render)."""
    crossed = st.session_state.pop("_web_floor_toast", None)
    if crossed:
        st.toast(web_floor_warning_text(crossed).replace("**", ""), icon="⏱️")

def _no_web_gets_pages(provider):
    if not shared_search_ready():
        return False
    if st.session_state.get(PACK_KEYS.get(provider, ""), False):
        return True
    return FRESH_MODE_CODES.get(st.session_state.get("k_fresh_mode")) == "always"


def source_parked(provider):
    return (any_source_web_on() and provider in NO_WEB_PROVIDERS
            and not _no_web_gets_pages(provider))

def _sync_web_all_flag():
    _park_no_web_sources()
    _ready = shared_search_ready()
    _global_web_keys = []
    for _p, _spec in PROVIDERS.items():
        if _ready and pages_by_default(_p):
            _global_web_keys.append(PACK_KEYS[_p])
        elif _p == "Qwen" and not _source_qwen_web_supported():
            continue
        elif _spec["web_key"]:
            _global_web_keys.append(_spec["web_key"])
    for _eval_prefix in ("k_syn", "k_adj"):
        _ep = st.session_state.get(f"{_eval_prefix}_provider")
        if _ep in NO_WEB_PROVIDERS:
            continue
        if _ep == "DeepSeek" and not _evaluator_deepseek_web_supported(_eval_prefix):
            continue
        if _ep == "Qwen" and not _evaluator_qwen_web_supported(_eval_prefix):
            continue
        _global_web_keys.append(f"{_eval_prefix}_web")
    st.session_state["k_web_all"] = all(st.session_state[key] for key in _global_web_keys)

def council_summary():
    """(active source names, synthesizer provider, adjudication on) -- the
    one-line digest the sidebar's Council entry and the composer show."""
    if st.session_state.get("main_view") == VIEW_RES:
        syn = st.session_state.get("k_res_provider", "Claude"); adj = None
    else:
        syn = st.session_state.get("k_fu_provider" if st.session_state.get("synth")
                                   else "k_syn_provider", "Claude")
        adj = st.session_state.get("k_adj_provider", "ChatGPT")
    adj_on = bool(adj) and adjudicator_stack_active()
    eval_active = (st.session_state.get("main_view") == VIEW_RES
                   or bool(st.session_state.get("k_autosynth")))
    excluded = set()
    if eval_active:
        excluded.add(syn)
        if adj_on:
            excluded.add(adj)
    keys = _NAME_TO_EN_KEY
    srcs = [n for n in MODEL_ORDER
            if st.session_state.get(keys[n]) and n not in excluded]
    return srcs, syn, adj_on, (adj if adj_on else None)

def render_source_model_picker(provider, label="Model", **kwargs):
    """Render a source's model choices from the same registry used for migration."""
    key, _, options = MODEL_PICKER_SPECS[provider]
    return st.selectbox(label, list(options), key=key, **kwargs)


def source_card_label(provider):
    """Summarize native source dials with a shared quota badge."""
    _badge = "\U0001F4B3 " if st.session_state.get(f"quota_err_{provider}") else ""
    if provider == "Claude":
        _cm = st.session_state["k_cla_model"]
        _lbl = (f"{_badge}{ICON['Claude']} **Claude {_cm}**"
                + ("" if CLAUDE_MODELS[_cm] in CLAUDE_NO_DIALS else f" — {st.session_state['k_cla']}")
                )
        return _lbl
    elif provider == "ChatGPT":
        _gpt_short = st.session_state["k_gpt_model"].split()[0]
        _gpt_ver = chatgpt_version_of(CHATGPT_MODELS[st.session_state["k_gpt_model"]])
        _lbl = (f"{_badge}\U0001F4AC **ChatGPT {_gpt_ver} {_gpt_short}** — "
                f"{st.session_state['k_gpt']} · {st.session_state['k_gpt_speed']}"
                )
        return _lbl
    elif provider == "Gemini":
        _lbl = (f"{_badge}{ICON['Gemini']} **Gemini {st.session_state['k_gem']}** — {st.session_state['k_gem_t']}"
                )
        return _lbl
    elif provider == "Grok":
        _lbl = (f"{_badge}{ICON['Grok']} **Grok {GROK_VER}** — {st.session_state['k_grok']}"
                + (" · ⏱️💸 web below recommended tier"
                   if "Grok" in web_floor_offenders() else ""))
        return _lbl
    elif provider == "Meta":
        _lbl = (f"{_badge}{ICON['Meta']} **Meta Muse {st.session_state['k_meta_model']}** — "
                f"reasoning {st.session_state['k_meta_eff']}")
        return _lbl
    elif provider == "DeepSeek":
        _ds_model = st.session_state["k_ds_model"]
        _ds_mode = (f"thinking · {st.session_state['k_ds_effort']}"
                    if st.session_state["k_ds_think"] else "direct")
        _lbl = (f"{_badge}{ICON['DeepSeek']} **DeepSeek {_ds_model}** — {_ds_mode}"
                + (" · ⏸ sits out (web on · turn on 🔎)" if source_parked("DeepSeek") else ""))
        return _lbl
    elif provider == "Qwen":
        _qwen_model = st.session_state["k_qwen_model"]
        _qwen_eff = st.session_state.get("k_qwen_effort", "Default")
        if (st.session_state["k_qwen_think"] and _qwen_eff != "Default"
                and QWEN_MODELS[_qwen_model] in QWEN_GRADED_EFFORT_MODELS):
            _qwen_mode = f"thinking · {_qwen_eff}"
        else:
            _qwen_mode = (f"thinking · {st.session_state['k_qwen_budget']:,} tok"
                          if st.session_state["k_qwen_think"] else "direct")
        _lbl = (f"{_badge}{ICON['Qwen']} **Qwen {_qwen_model}** — {_qwen_mode}"
                + (" · 🌐 needs a 3.8 model"
                   if st.session_state.get("k_web_qwen") and not _source_qwen_web_supported()
                   else ""))
        return _lbl
    elif provider == "MiMo":
        return (f"{_badge}{ICON['MiMo']} **Xiaomi MiMo {st.session_state['k_mimo_model']}** — "
                f"{'thinking' if st.session_state['k_mimo_think'] else 'direct'}")
    elif provider == "GLM":
        _glm_id = GLM_MODELS[st.session_state["k_glm_model"]]
        _glm_mode = (f"reasoning {st.session_state['k_glm_eff']}" if _glm_id in GLM_ALWAYS_THINK
                     else ("thinking" if st.session_state["k_glm_think"] else "direct"))
        _lbl = (f"{_badge}{ICON['GLM']} **Z.ai {st.session_state['k_glm_model']}** — {_glm_mode}")
        return _lbl
    elif provider == "NVIDIA":
        _nv_mode = (f"thinking · {st.session_state['k_nv_budget']:,} tok"
                    if st.session_state["k_nv_think"] else "direct")
        _lbl = (f"{_badge}{ICON['NVIDIA']} **NVIDIA {st.session_state['k_nv_model']}** — {_nv_mode}"
                + (" · ⏸ sits out (web on · turn on 🔎)" if source_parked("NVIDIA") else ""))
        return _lbl
    elif provider == "Mistral":
        _mm_id = MISTRAL_MODELS[st.session_state["k_mistral_model"]]
        _m_mode = (f"reasoning {st.session_state['k_mistral_eff']}"
                   if _mm_id in MISTRAL_REASONING_MODELS else "no reasoning dial")
        _lbl = (f"{_badge}{ICON['Mistral']} **Mistral {st.session_state['k_mistral_model']}** — {_m_mode}"
                + (" · ⏸ sits out (web on · turn on 🔎)" if source_parked("Mistral") else ""))
        return _lbl
    elif provider == "Kimi":
        _km_id = KIMI_MODELS[st.session_state["k_kimi_model"]]
        _k_mode = (f"reasoning {st.session_state['k_kimi_effort']}" if _km_id in KIMI_ALWAYS_THINK
                   else ("thinking" if st.session_state["k_kimi_think"] else "direct"))
        _lbl = f"{_badge}{ICON['Kimi']} **Kimi {st.session_state['k_kimi_model']}** — {_k_mode}"
        return _lbl
    elif provider == "MiniMax":
        _mm_id = MINIMAX_MODELS[st.session_state["k_mm_model"]]
        _mm_mode = ("adaptive thinking" if (st.session_state["k_mm_think"] or
                                            _mm_id not in MINIMAX_THINK_OFF_OK) else "direct")
        if st.session_state.get("k_mm_speed", "Standard") != "Standard":
            _mm_mode += f" · {st.session_state['k_mm_speed'].lower()}"
        _lbl = f"{_badge}{ICON['MiniMax']} **MiniMax {st.session_state['k_mm_model']}** — {_mm_mode}"
        return _lbl
    raise ValueError(f"Unknown provider: {provider}")


def render_source_claude():
    render_source_model_picker(
        "Claude", on_change=_sync_source_claude_search_cap,
        help="Haiku = fastest/cheapest · Sonnet = daily driver · "
             "Opus 5.5 = heavyweight · Fable 5.1 = Anthropic's most capable (priciest).")
    _cla_mid = CLAUDE_MODELS[st.session_state["k_cla_model"]]
    _cla_no_dials = _cla_mid in CLAUDE_NO_DIALS
    st.selectbox("Effort", list(CLAUDE_EFFORT.keys()), key="k_cla",
                 disabled=_cla_no_dials, on_change=_sync_source_claude_search_cap)
    _cla_forced = claude_thinking_forced(_cla_mid, CLAUDE_EFFORT[st.session_state["k_cla"]])
    st.checkbox("Adaptive thinking", key="k_cla_think",
                disabled=(_cla_mid in CLAUDE_ALWAYS_THINK or _cla_forced))
    if _cla_no_dials:
        st.caption(_HAIKU_THINKING_CAPTION)
    elif _cla_mid in CLAUDE_ALWAYS_THINK:
        st.caption(f"{st.session_state['k_cla_model']} always thinks — effort sets how hard. "
                   "Fable 5.1 is the priciest model here.")
    elif _cla_forced:
        st.caption("This model requires thinking at Extra/Max effort — drop to High to turn it off.")
    st.toggle("\U0001F310 Web search", key="k_web_cla")
    st.number_input(
        "Max web searches", min_value=1, max_value=20, step=1, key="k_cla_maxsearch",
        disabled=not st.session_state["k_web_cla"],
        help="Caps how many times Claude can search per query (when its web search is on). "
             "Lower = faster & cheaper. Claude is the one model that needs this — the others self-limit.")
    _source_recommended = recommended_claude_search_cap(
        st.session_state["k_cla_model"], st.session_state["k_cla"])
    st.caption(f"Conclave recommends {_source_recommended} for "
               f"{st.session_state['k_cla_model']} · {st.session_state['k_cla']}. "
               "Changing either refreshes the cap; you can still override it.")
    st.toggle("Active source", key=PROVIDERS["Claude"]["enabled_key"],
              help="On: Claude answers your questions alongside the other sources. It sits out automatically while it holds the Synthesizer seat (or the Adjudicator seat, with adjudication on).")


def render_source_gpt():
    render_source_model_picker(
        "ChatGPT", 'GPT model',
        help="Astra = GPT-6 flagship · Sol 6.1 = advanced (a fifth of Astra's "
             "price) · Luna = GPT-6 economy.")
    st.selectbox("Reasoning effort", list(CHATGPT_EFFORT.keys()), key="k_gpt")
    _astra_instant_caption("k_gpt_model", "k_gpt")
    st.selectbox("Processing speed", list(CHATGPT_SPEED.keys()), key="k_gpt_speed")
    st.caption(CHATGPT_SPEED_DESC[st.session_state["k_gpt_speed"]])
    st.toggle("\U0001F310 Web search", key="k_web_gpt")
    st.toggle("Active source", key=PROVIDERS["ChatGPT"]["enabled_key"],
              help="On: ChatGPT answers your questions alongside the other sources. It sits out automatically while it holds the Synthesizer seat (or the Adjudicator seat, with adjudication on).")


def render_source_gemini():
    render_source_model_picker("Gemini", 'Model')
    st.selectbox("Thinking level", list(GEMINI_THINK.keys()), key="k_gem_t",
                 help="How much Gemini thinks before answering — more is deeper but slower. Minimal works on Flash-Lite only; the other levels "
                      "apply to every model.")
    _gemini_minimal_caption("k_gem", "k_gem_t")
    st.caption("Argon (4) is Google's newest and most capable model (introductory price "
               "$2/$10 until Nov 30, then $4/$20).")
    st.toggle("\U0001F310 Web search", key="k_web_gem",
              help="Google Search grounding: 5,000 free searches a month shared across "
                   "models, then $14 per 1,000 — billed per search query, on top of tokens.")
    st.toggle("Active source", key=PROVIDERS["Gemini"]["enabled_key"],
              help="On: Gemini answers your questions alongside the other sources. It sits out automatically while it holds the Synthesizer seat (or the Adjudicator seat, with adjudication on).")


def render_source_grok():
    st.selectbox("Mode", GROK_MODES, key="k_grok",
                 help="How hard Grok reasons: Fast = low · Balanced = medium · High = Grok's own default · "
                      "Expert = highest. Reasoning can't be switched off.")
    st.toggle("\U0001F310 Web search", key="k_web_grok",
              on_change=lambda: _note_web_floor_crossed(["Grok"]) if st.session_state.get("k_web_grok") else None,
              help="xAI's hosted search bills $5 per 1,000 calls on top of tokens "
                   "Recommended at "
                   f"{WEB_TIER_FLOOR['Grok']} and above — it works below that, but "
                   "slower and pricier (you'll see a warning).")
    if "Grok" in web_floor_offenders():
        st.warning("⏱️💸 " + web_floor_warning_text(["Grok"]))
    st.toggle("Active source", key=PROVIDERS["Grok"]["enabled_key"],
              help="On: Grok answers your questions alongside the other sources. It sits out automatically while it holds the Synthesizer seat (or the Adjudicator seat, with adjudication on).")


def render_source_meta():
    render_source_model_picker("Meta", 'Model')
    st.selectbox("Reasoning effort", list(META_EFFORT.keys()), key="k_meta_eff",
                 help="How hard Muse reasons, Minimal → Max — the same dial on Wonder and Spark 1.3. Higher is deeper but slower and pricier; reasoning can't be switched "
                      "off entirely.")
    st.toggle("\U0001F310 Web search", key="k_web_meta",
              help="Meta's own web search: $2.50 "
                   "per 1,000 searches on top of tokens.")
    st.caption("Muse Wonder (preview, $4/$20) is Meta's "
               "most capable model; Spark 1.3 ($1.25/$4.25) is the everyday one. Images, "
               "video, audio and PDFs in; 1M context. The discounted 'contributor' tier "
               "that trains on your prompts is deliberately not offered.")
    st.toggle("Active source", key=PROVIDERS["Meta"]["enabled_key"],
              help="On: Meta answers your questions alongside the other sources. It sits out automatically while it holds the Synthesizer seat (or the Adjudicator seat, with adjudication on).")


def render_source_deepseek():
    render_source_model_picker("DeepSeek", "Model", on_change=_sync_source_deepseek_web)
    st.toggle("Thinking", key="k_ds_think")
    st.selectbox("Reasoning effort", list(DEEPSEEK_EFFORT.keys()), key="k_ds_effort",
                 disabled=not st.session_state["k_ds_think"])
    st.caption(_DEEPSEEK_CAPTION)
    st.toggle("Active source", key=PROVIDERS["DeepSeek"]["enabled_key"], disabled=source_parked("DeepSeek"),
              help=("Sitting out: web search is on and DeepSeek can't search the web itself. Turn on its 🔎 switch (needs a Tavily or Brave key) to give it the shared pages, or it waits "
                    "until web search is off again." if source_parked("DeepSeek") else
                    "On: DeepSeek answers your questions alongside the other sources. It sits out automatically while it holds the Synthesizer seat (or the Adjudicator seat, with adjudication on)."))


def render_source_qwen():
    render_source_model_picker("Qwen", 'Model', on_change=_sync_source_qwen_web,
                               help="Max 3.8 and Flash 3.8 can web-search; the 3.7 models cannot. "
                                    "Turning on Qwen's own 🌐 search with a 3.7 model switches to the nearest 3.8.")
    st.toggle("Thinking", key="k_qwen_think")
    _qgraded = QWEN_MODELS[st.session_state["k_qwen_model"]] in QWEN_GRADED_EFFORT_MODELS
    st.selectbox("Reasoning effort (3.8 series)", list(QWEN_EFFORT.keys()), key="k_qwen_effort",
                 disabled=not (st.session_state["k_qwen_think"] and _qgraded),
                 help="How hard a Qwen 3.8 model reasons (Low / Medium / Extra). Default "
                      "uses the thinking budget below instead — Qwen accepts one or the "
                      "other, not both. 3.8 models only.")
    st.number_input("Thinking budget (tokens)", min_value=500, max_value=32000, step=500,
                    key="k_qwen_budget",
                    disabled=(not st.session_state["k_qwen_think"]) or
                             (_qgraded and st.session_state["k_qwen_effort"] != "Default"),
                    help="Caps how many tokens Qwen can spend reasoning before it must "
                         "answer. Higher means deeper reasoning, but slower and pricier.")
    st.toggle("\U0001F310 Web search", key="k_web_qwen", on_change=_sync_source_qwen_web_toggle,
              help="Qwen's own search (Max 3.8 / Flash 3.8 only), $0.573 per 1,000 searches. "
                   "Not yet proven in the US region (two earlier tests never searched). The global "
                   "web switch gives Qwen the 🔎 shared pages instead.")
    st.caption("Alibaba Cloud Model Studio, US region. Images supported, PDFs text-only. "
               "Its own web search (🌐, 3.8 models) is on trial and off by default — earlier "
               "tests never searched, so the global web switch gives Qwen the 🔎 pages instead.")
    st.toggle("Active source", key=PROVIDERS["Qwen"]["enabled_key"],
              help="On: Qwen answers your questions alongside the other sources. It sits out automatically while it holds the Synthesizer seat (or the Adjudicator seat, with adjudication on).")


def render_source_glm():
    render_source_model_picker("GLM", 'Model')
    if GLM_MODELS[st.session_state["k_glm_model"]] in GLM_ALWAYS_THINK:
        st.selectbox("Reasoning effort", list(GLM_EFFORT.keys()), key="k_glm_eff",
                     help="GLM-5.3 always reasons; Low / High / Max set how hard "
                          "(Z.ai's default is Max — Conclave always sends this "
                          "explicitly).")
    else:
        st.toggle("Thinking", key="k_glm_think")
    st.toggle("\U0001F310 Web search", key="k_web_glm",
              help="Z.ai's own web search — $0.01 per use on "
                   "top of tokens.")
    st.caption("Z.ai GLM (international platform). 1M context on "
               "GLM-5.3; images on 5.3 Flash only. Z.ai is on the US Commerce Entity "
               "List (a US trade restriction) — fine for everyday questions; avoid sending it sensitive material.")
    st.toggle("Active source", key=PROVIDERS["GLM"]["enabled_key"],
              help="On: GLM answers your questions alongside the other sources. It sits out automatically while it holds the Synthesizer seat (or the Adjudicator seat, with adjudication on).")


def render_source_mimo():
    render_source_model_picker("MiMo", 'Model')
    st.toggle("Thinking", key="k_mimo_think",
              help="MiMo V2.6 thinks by default; off answers directly and faster. There is no "
                   "effort dial in MiMo's API.")
    st.toggle("\U0001F310 Web search", key="k_web_mimo",
              help="MiMo's own web search — $5 per 1,000 searches (up to "
                   f"{MIMO_SEARCH_KEYWORDS} per answer). Switch on the Web Search Plugin in the MiMo "
                   "console first, or MiMo answers without searching.")
    st.caption("Xiaomi MiMo V2.6: 1M context, reads images. Pro is the "
               "flagship ($0.435/$0.87 per million tokens), Flash the economy tier ($0.14/$0.28), "
               "Pro Ultraspeed the same Pro tuned for speed at 10× the price.")
    st.toggle("Active source", key=PROVIDERS["MiMo"]["enabled_key"],
              help="On: MiMo answers your questions alongside the other sources. It sits out automatically while it holds the Synthesizer seat (or the Adjudicator seat, with adjudication on).")


def render_source_nv():
    render_source_model_picker("NVIDIA", 'Model')
    st.toggle("Thinking", key="k_nv_think")
    st.number_input("Reasoning budget (tokens)", key="k_nv_budget", min_value=1000,
                    max_value=28000, step=1000,
                    disabled=not st.session_state["k_nv_think"],
                    help="Caps how many tokens the model may spend reasoning before "
                         "it answers.")
    st.caption("NVIDIA's hosted catalog — a free trial "
               "endpoint (no billing, ~40 requests/min per key, meant for "
               "experimentation), so costs show as $0. Images on Nano Omni only. No web search "
               "of its own — with its 🔎 switch on it reads the shared pages instead.")
    st.toggle("Active source", key=PROVIDERS["NVIDIA"]["enabled_key"], disabled=source_parked("NVIDIA"),
              help=("Sitting out: web search is on and NVIDIA can't search the web itself. Turn on its 🔎 switch (needs a Tavily or Brave key) to give it the shared pages, or it waits "
                    "until web search is off again."
                    if source_parked("NVIDIA") else
                    "On: NVIDIA answers your questions alongside the other sources. It sits out automatically while it holds the Synthesizer seat (or the Adjudicator seat, with adjudication on)."))


def render_source_mistral():
    render_source_model_picker("Mistral", 'Model')
    st.selectbox("Reasoning effort", list(MISTRAL_EFFORT.keys()), key="k_mistral_eff",
                 disabled=MISTRAL_MODELS[st.session_state["k_mistral_model"]] not in MISTRAL_REASONING_MODELS,
                 help="How hard Mistral reasons. Large 4, Medium 3.5 and Small 4 reason on demand (Off = a direct answer). "
                      "Large 3 has no reasoning dial.")
    st.caption("Mistral AI (Paris); Large 4 (preview) is the flagship. No web search of its own — with its 🔎 switch on it reads the shared pages instead; "
               "images on Large 4, Medium 3.5 and Large 3; PDFs read through Document "
               "QnA.")
    st.toggle("Active source", key=PROVIDERS["Mistral"]["enabled_key"], disabled=source_parked("Mistral"),
              help=("Sitting out: web search is on and Mistral can't search the web itself. Turn on its 🔎 switch (needs a Tavily or Brave key) to give it the shared pages, or it waits "
                    "until web search "
                    "is off again." if source_parked("Mistral") else
                    "On: Mistral answers your questions alongside the other sources. It sits out automatically while it holds the Synthesizer seat (or the Adjudicator seat, with adjudication on)."))


def render_source_kimi():
    render_source_model_picker("Kimi", 'Model')
    if KIMI_MODELS[st.session_state["k_kimi_model"]] in KIMI_ALWAYS_THINK:
        st.selectbox("Reasoning effort", list(KIMI_EFFORT.keys()), key="k_kimi_effort",
                     help="K3 always reasons; this sets how hard (Max is its default).")
    else:
        st.toggle("Thinking", key="k_kimi_think")
    st.toggle("\U0001F310 Web search", key="k_web_kimi",
              help="Moonshot's web search API ($0.002 per search on top of tokens); "
                   "Conclave runs each search Kimi asks for.")
    st.caption("Kimi by Moonshot AI. Images supported; PDFs read "
               "through Moonshot's Files API. New accounts start at 3 requests/min "
               "until $10 has been added to the account.")
    st.toggle("Active source", key=PROVIDERS["Kimi"]["enabled_key"],
              help="On: Kimi answers your questions alongside the other sources. It sits out automatically while it holds the Synthesizer seat (or the Adjudicator seat, with adjudication on).")


def render_source_mm():
    render_source_model_picker("MiniMax", 'Model')
    st.toggle("Adaptive thinking", key="k_mm_think",
              disabled=MINIMAX_MODELS[st.session_state["k_mm_model"]] not in MINIMAX_THINK_OFF_OK,
              help="M3 can run with thinking off for faster, cheaper answers.")
    st.selectbox("Processing speed", list(MINIMAX_SPEED.keys()), key="k_mm_speed")
    st.caption(MINIMAX_SPEED_DESC[st.session_state["k_mm_speed"]])
    st.toggle("\U0001F310 Web search", key="k_web_mm",
              help="MiniMax's own web search (beta) — $0.01 per "
                   "request on top of tokens. The thinking switch has no effect while it searches.")
    st.caption("MiniMax (international platform). 1M context on M3; "
               "images on M3, PDFs text-only. Priority speed (above) is the same model served "
               "faster at 1.5× the price.")
    st.toggle("Active source", key=PROVIDERS["MiniMax"]["enabled_key"],
              help="On: MiniMax answers your questions alongside the other sources. It sits out automatically while it holds the Synthesizer seat (or the Adjudicator seat, with adjudication on).")


SOURCE_CARD_RENDERERS = {
    "Claude": render_source_claude,
    "ChatGPT": render_source_gpt,
    "Gemini": render_source_gemini,
    "Grok": render_source_grok,
    "Meta": render_source_meta,
    "DeepSeek": render_source_deepseek,
    "Qwen": render_source_qwen,
    "GLM": render_source_glm,
    "MiMo": render_source_mimo,
    "NVIDIA": render_source_nv,
    "Mistral": render_source_mistral,
    "Kimi": render_source_kimi,
    "MiniMax": render_source_mm,
}


def render_council_page():
    flush_web_floor_toast()
    render_back_button("back_from_council")
    st.markdown("#### 🏛️ Council")
    st.caption("Who answers, who judges, and how hard everyone works. The sources answer side by side, the ⭐ Synthesizer writes one answer from theirs, and the ⚖️ Adjudicator (optional) checks it before you see it. Presets retune every "
               "dial at once; each card stays adjustable afterward. Nothing here runs a "
               "call — changes apply to your next send.")
    # ---- Run presets: one compact, provider-preserving intensity ladder.
    # The radio callback runs before the natural rerun, preserving unsent prompt
    # text just like the former preset buttons. Custom remains the visibly
    # separated manual mode, not a sixth effort tier.
    _active_preset = current_preset_name()
    _any_web_now = any(st.session_state.get(k) for k in _SOURCE_WEB_KEYS)
    _any_pages_now = _SEARCH_READY and any(st.session_state.get(k) for k in PACK_KEYS.values()) \
        if "_SEARCH_READY" in globals() else False
    _preset_web_badge = (("  ·  \U0001F310" if _any_web_now else "")
                         + ("🔎" if _any_pages_now else ""))
    with st.container(key="run_preset_picker"):
        with st.expander(f"Run preset · {_active_preset}{_preset_web_badge}", expanded=True,
                         key="run_preset_expander", type="compact"):
            render_preset_dial("run_preset_choice", label_visibility="collapsed",
                               help=preset_dial_help(_active_preset))
            if _active_preset == "Custom":
                st.caption("⚙ **Custom (manual)** — your own dial changes are live. "
                           "Click any preset above to go back to its settings.")
            else:
                st.caption(f"**{_active_preset}** — {PRESET_DESC[_active_preset]}")
            st.button("⚙ Custom — keep dials exactly as configured",
                      key="preset_custom_btn", use_container_width=True,
                      type="primary" if _active_preset == "Custom" else "secondary",
                      on_click=_apply_preset, args=("Custom",),
                      help=PRESET_DESC["Custom"])

    # One deliberate shortcut for a complete Synthesis turn. Research remains
    # independent because it is a separate workspace with its own cost controls.
    _sync_web_all_flag()
    c_toggle("\U0001F310 Web search for all sources", "k_web_all", ns="global",
             after_change=_set_all_source_web,
             help="Gives every source current web information, plus the Synthesizer and "
                  "Adjudicator their own search. With a Tavily or Brave key, the sources that "
                  "can't search (or whose own search is slow or costly) get the shared pages "
                  "(🔎) instead; the rest search on their own (🌐). Each card's 🔎 and 🌐 "
                  "switches show exactly who gets what. Research remains separate.")
    st.caption(web_split_caption())
    render_web_floor_warning()

    st.divider()
    _syn_provider = st.session_state.get("k_syn_provider", "Claude")
    _autosynth_on = bool(st.session_state.get("k_autosynth"))
    _adj_provider_now = st.session_state.get("k_adj_provider", "ChatGPT")
    _adjudication_on = adjudicator_stack_active()
    with st.expander(f"Evaluators · {int(_autosynth_on) + int(_adjudication_on)} on",
                     expanded=True, key="evaluators_expander", type="compact"):
        if any_source_web_on():
            _seat_warn = [f"{r} ({st.session_state.get(k)})" for r, k in
                          (("Synthesizer", "k_syn_provider"), ("Adjudicator", "k_adj_provider"))
                          if st.session_state.get(k) in NO_WEB_PROVIDERS]
            if _seat_warn:
                st.warning("⚠️ Web search is on, but " + " and ".join(_seat_warn) +
                           " cannot web-verify. Pick a web-capable provider for that seat "
                           "if the question needs current information.")
        _syn_lbl = f"⭐ **Synthesizer** — {ICON.get(_syn_provider, '')} {eval_label('k_syn')}"
        render_card("Synthesizer", _syn_lbl, lambda: render_initial_synth_panel(show_provider_picker=False),
                    controls=evaluator_card_controls("Synthesizer", "k_syn", "k_autosynth"))
        _adj_lbl = f"⚖️ **Adjudicator** — {ICON.get(_adj_provider_now, '')} {eval_label('k_adj')}"
        render_card("Adjudicator", _adj_lbl, lambda: render_adjudicator_panel(show_provider_picker=False),
                    controls=evaluator_card_controls("Adjudicator", "k_adj", "k_adjudicator_on"))

    st.divider()
    _research_sidebar = st.session_state.get("main_view") == VIEW_RES
    if _research_sidebar:
        _syn_provider = st.session_state.get("k_res_provider", "Claude")
        _adj_provider_now = None
    else:
        _syn_provider = st.session_state.get(
            "k_fu_provider" if st.session_state.get("synth") else "k_syn_provider",
            "Claude",
        )
        _adj_provider_now = st.session_state.get("k_adj_provider", "ChatGPT")
    _independent_eval_active = _research_sidebar or st.session_state["k_autosynth"]
    # Read the controlled values before their widgets render below the cards.
    # Streamlit applies widget changes before each rerun, so these are current.
    use_gpt = bool(st.session_state["k_en_gpt"])
    use_cla = bool(st.session_state["k_en_cla"])
    use_gem = bool(st.session_state["k_en_gem"])
    use_ds = bool(st.session_state["k_en_ds"])
    use_grok = bool(st.session_state["k_en_grok"])
    use_qwen = bool(st.session_state["k_en_qwen"])
    use_mistral = bool(st.session_state["k_en_mistral"])
    use_kimi = bool(st.session_state["k_en_kimi"])
    use_meta = bool(st.session_state["k_en_meta"])
    use_mm = bool(st.session_state["k_en_mm"])
    use_nv = bool(st.session_state["k_en_nv"])
    use_glm = bool(st.session_state["k_en_glm"])
    use_mimo = bool(st.session_state["k_en_mimo"])

    _enabled_now = {"ChatGPT": use_gpt, "Claude": use_cla, "Gemini": use_gem,
                    "DeepSeek": use_ds, "Grok": use_grok, "Qwen": use_qwen,
                    "Mistral": use_mistral, "Kimi": use_kimi,
                    "Meta": use_meta, "MiniMax": use_mm, "NVIDIA": use_nv, "GLM": use_glm,
                    "MiMo": use_mimo}
    _excluded_providers = set()
    if _independent_eval_active:
        _excluded_providers.add(_syn_provider)
        if _adj_provider_now and _adjudication_on:
            _excluded_providers.add(_adj_provider_now)
    _src_now = [n for n in MODEL_ORDER if _enabled_now[n] and n not in _excluded_providers]
    _adj_suffix = (f" · ⚖️ {_adj_provider_now} adjudicates."
                   if _adj_provider_now and _adjudication_on else
                   f" · ⚖️ adjudication off" if _adj_provider_now else "")
    st.caption(("Sources this run: " + ", ".join(_src_now) if _src_now else
                "⚠️ No independent sources are active.") +
               (f"  ·  \U0001F52C {_syn_provider} evaluates." if _research_sidebar else
                ((f"  ·  ⭐ {_syn_provider} synthesizes" + _adj_suffix)
                 if st.session_state["k_autosynth"] else "")))

    _dim_rules = "".join(
        f'.st-key-card_btn_{name} button {{ opacity: 0.5; }}\n'
        for name, active in _enabled_now.items() if not active
    )
    _pop_rules = "".join(
        f'.st-key-card_btn_{name} button {{ border-left: 2px solid var(--conclave-accent); }}\n'
        for name, active in _enabled_now.items() if active
    )
    for _role, _role_on in (("Synthesizer", _autosynth_on),
                            ("Adjudicator", _adjudication_on)):
        if _role_on:
            _pop_rules += (f'.st-key-card_btn_{_role} button {{ '
                           'border-left: 2px solid var(--conclave-accent); '
                           'background: rgba(85, 184, 179, 0.12); }\n')
        else:
            _dim_rules += f'.st-key-card_btn_{_role} button {{ opacity: 0.5; }}\n'
    _flag_rules = "".join(
        f'.st-key-card_btn_{_role} button::after {{ {origin_flag_css(PROVIDER_ORIGIN.get(_prov, ""))} }}\n'
        for _role, _prov in (("Synthesizer", _syn_provider), ("Adjudicator", _adj_provider_now)))
    _flag_rules += "".join(
        f'.st-key-card_btn_{_p} button::after {{ {origin_flag_css(_o)} }}\n'
        for _p, _o in PROVIDER_ORIGIN.items())
    st.markdown(f"<style>{_dim_rules}{_pop_rules}{_flag_rules}</style>", unsafe_allow_html=True)

    with st.expander(f"Source models · {len(_src_now)} active", expanded=True,
                     key="source_models_expander", type="compact"):
        _nokey_on = keyed_sources([p for p in MODEL_ORDER
                                   if st.session_state.get(PROVIDERS[p]["enabled_key"])],
                                  load_keys())[1]
        if _nokey_on:
            st.caption("🔑 No key saved for " + ", ".join(_nokey_on) + " — they'll be skipped. "
                       "Add keys in Settings ▸ API keys.")
        st.caption("Each card's switches: 🔎 reads the shared Tavily + Brave web pages · "
                   "🌐 uses its own web search · ON takes part in this run. Open a card for "
                   "its model and effort.")
        for provider in MODEL_ORDER:
            if provider not in _excluded_providers:
                render_card(provider, source_card_label(provider) + _slow_badge(provider),
                            lambda _p=provider: render_source_card_body(_p),
                            controls=source_card_controls(provider))
            if st.session_state.get(f"quota_err_{provider}"):
                st.error(f"\U0001F4B3 **{provider} looks out of credits / over its billing quota.** "
                         f"[Open {provider}'s billing page]({BILLING_LINKS[provider]}) — this clears "
                         "automatically the next time a call to it succeeds.")

    render_speed_settings()
    render_report_cards()


def report_stats():
    """Measured source stats from every saved chat (recent + archive),
    cached until either history file changes."""
    sig = tuple(os.path.getmtime(f) if os.path.exists(f) else 0
                for f in (HISTORY_FILE, HISTORY_ARCHIVE_FILE))
    cached = st.session_state.get("_report_stats")
    if cached and cached[0] == sig:
        return cached[1]
    stats = model_report_stats(list(load_history()) + list(load_history_archive()))
    st.session_state["_report_stats"] = (sig, stats)
    return stats


def _current_card(provider):
    cfg = CFG[provider]
    return report_card_for(report_stats(), provider, model_id_of(provider, cfg),
                           settings_str(provider, cfg, WEB.get(provider, False)))


def _slow_badge(provider):
    try:
        cards = {p: _current_card(p) for p in MODEL_ORDER}
    except Exception:
        return ""
    times = [c["secs"] for c in cards.values() if c and c.get("secs") is not None]
    mine = (cards.get(provider) or {}).get("secs")
    if mine is None or len(times) < 3:
        return ""
    return " 🐢" if mine >= max(45.0, 2 * _median(times)) else ""


def render_source_card_body(provider):
    try:
        st.caption(report_card_text(_current_card(provider)))
    except Exception:
        pass
    _ready = _SEARCH_READY if "_SEARCH_READY" in globals() else shared_search_ready()
    if provider in NO_WEB_PROVIDERS:
        st.caption("🚫🌐 Can't search the web itself — with its 🔎 switch on it reads the shared Tavily + Brave pages; with 🔎 off it sits out while web search is on."
                   if _ready else
                   "🚫🌐 No web search here — this source sits out while web search is on. "
                   "Add a Tavily or Brave key and it can read the 🔎 shared pages.")
    st.toggle("🔎 Tavily + Brave pages", key=PACK_KEYS[provider], disabled=not _ready,
              help="One shared search per turn on Tavily and Brave; the pages are added to "
                   "this source's question. Works beside its own 🌐 search, or instead of it.")
    SOURCE_CARD_RENDERERS[provider]()


def render_speed_settings():
    keys = load_keys()
    with st.expander("⚡ Speed & helpers", expanded=False, key="speed_helpers_expander",
                     type="compact"):
        st.radio("When some sources are slow", QUORUM_MODES, key="k_quorum_mode",
                 help="Start when most are in: the answer begins once about three in four "
                      "sources have answered (plus a short grace for near-finishers), or when "
                      "the preset's time limit passes. Late answers still appear under Sources, "
                      "labeled, and are billed as usual — they just aren't in that turn's "
                      "answer. Wait for every source: slower, but every answer is used.")
        st.caption("Longest wait before the answer starts anyway, by preset: " + " · ".join(f"{k} {v // 60}m{v % 60:02d}s"
                                                for k, v in SOURCE_DEADLINES.items()
                                                if k != "Custom"))
        st.toggle("👀 Live draft while the council works (Cerebras)", key="k_live_preview",
                  disabled=not keys.get(HELPER_KEYS["Cerebras"]),
                  help="A fast model drafts a preview as answers arrive, so there is something "
                       "to read during the wait. It is clearly labeled, never fed to the "
                       "Synthesizer, and replaced by the real answer. About a cent per run.")
        if not keys.get(HELPER_KEYS["Cerebras"]):
            st.caption("Add a Cerebras key in Settings ▸ API keys to turn this on.")
        st.selectbox("🧭 Jev decision checks", JEV_MODES, key="k_jev_mode",
                     disabled=not keys.get(HELPER_KEYS["Jev"]),
                     help="Jev is a tiny, very cheap judging model from TypeSafe. It checks whether a question "
                          "needs current information and how deep it is, whether each source "
                          "answer is a real attempt, and whether the draft's claims are "
                          "supported by the sources. Shadow records every verdict in the run "
                          "report and changes nothing. Active sets aside answers with a clear "
                          "defect and skips the Adjudicator's audit when no claim is "
                          "contradicted. Start in Shadow and compare.")
        if not keys.get(HELPER_KEYS["Jev"]):
            st.caption("Add a TypeSafe key in Settings ▸ API keys to turn this on.")
        st.caption("Claude and ChatGPT stream the answer as it's written; other Synthesizers "
                   "show the live draft until their answer arrives.")
        st.markdown("**🔎 Shared web sources** — one search per turn, both engines at once")
        _saved = saved_search_services(keys)
        _e1, _e2, _e3 = st.columns([1, 1, 2])
        with _e1:
            st.toggle("Tavily", key="k_use_tavily", disabled="Tavily" not in _saved,
                      help="Ranked pages with their most relevant passages. Advanced uses 2 "
                           "credits (1,000 free credits a month), then $0.008 a credit.")
        with _e2:
            st.toggle("Brave", key="k_use_brave", disabled="Brave" not in _saved,
                      help="Brave's own index, more pages with shorter excerpts. $5 of free "
                           "searches a month, then half a cent a search.")
        with _e3:
            st.selectbox("Tavily depth", list(TAVILY_DEPTHS), key="k_tavily_depth",
                         disabled="Tavily" not in _saved)
        _ready = shared_search_ready(keys)
        st.selectbox("When the shared sources are used", FRESH_SOURCE_MODES, key="k_fresh_mode",
                     disabled=not _ready,
                     help="Decides when the shared Tavily + Brave search runs. Both engines search at once, duplicate pages are merged, and every "
                          "source with 🔎 on reads the same pages. With Jev on, "
                          "the middle option also searches whenever Jev reads the question as "
                          "needing current facts, even with web off.")
        if not _saved:
            st.caption("Add a Tavily and/or Brave key in Settings ▸ API keys to turn this on. "
                       "Until then, sources without web search sit out while web is on.")
        elif not _ready:
            st.caption("Both engines are switched off, so nothing uses the shared sources.")
        st.caption("Each source card's **🔎** switch gives that source the pages; **🌐** is "
                   "its own search. The global web switch turns 🔎 on for "
                   + ", ".join(p for p in MODEL_ORDER if pages_by_default(p))
                   + " and 🌐 on for the rest. Why (from the developer's test runs): Grok's own "
                   "search cost 23–27¢ an answer; Kimi and MiniMax searching on their own took "
                   "30 s to 7 minutes; GLM's own search is fast and nearly free; MiMo keeps its "
                   "own search so it can be measured; Qwen's own search is still on trial.")


def report_seat_stats():
    """seat_report_stats over every saved chat, cached like report_stats."""
    sig = tuple(os.path.getmtime(f) if os.path.exists(f) else 0
                for f in (HISTORY_FILE, HISTORY_ARCHIVE_FILE))
    cached = st.session_state.get("_report_seat_stats")
    if cached and cached[0] == sig:
        return cached[1]
    out = seat_report_stats(list(load_history()) + list(load_history_archive()))
    st.session_state["_report_seat_stats"] = (sig, out)
    return out


def report_fresh_stats():
    """fresh_source_stats over every saved chat, cached like report_stats."""
    sig = tuple(os.path.getmtime(f) if os.path.exists(f) else 0
                for f in (HISTORY_FILE, HISTORY_ARCHIVE_FILE))
    cached = st.session_state.get("_report_fresh_stats")
    if cached and cached[0] == sig:
        return cached[1]
    out = fresh_source_stats(list(load_history()) + list(load_history_archive()))
    st.session_state["_report_fresh_stats"] = (sig, out)
    return out


def offered_model_ids():
    """Every model ID a source card can pick right now."""
    ids = {MODEL_GROK}
    for table in (CHATGPT_MODELS, CLAUDE_MODELS, GEMINI_MODELS, DEEPSEEK_MODELS, QWEN_MODELS,
                  MISTRAL_MODELS, KIMI_MODELS, META_MODELS, MINIMAX_MODELS, NVIDIA_MODELS,
                  GLM_MODELS, MIMO_MODELS):
        ids.update(table.values())
    return ids


def render_report_cards():
    stats = report_stats()
    offered = offered_model_ids()
    with st.expander("📊 Model report cards — measured from your runs", expanded=False,
                     key="report_cards_expander", type="compact"):
        rows = []
        for p in MODEL_ORDER:
            mids = sorted({mid for (pp, mid, _s) in stats if pp == p and mid})
            for mid in mids:
                card = report_card_for(stats, p, mid, "")
                if not card:
                    continue
                rows.append({
                    "Source": f"{ICON.get(p, '')} {p}", "Model": mid, "Answers": card["n"],
                    "Typical time (s)": round(card["secs"]) if card.get("secs") is not None else None,
                    "With web (s)": round(card["secs_web"]) if card.get("secs_web") is not None else None,
                    "Typical cost ($)": round(card["cost"], 4) if card.get("cost") is not None else None,
                    "Failed": f"{100.0 * card['fail'] / card['n']:.0f}%" if card["n"] else "",
                    "Late": card.get("late") or 0,
                    "Web search": "no" if p in NO_WEB_PROVIDERS else "yes",
                    "Offered now": "yes" if mid in offered else "retired",
                })
        if not rows:
            st.caption("No saved runs yet — cards fill in as you use Conclave.")
            return
        rows.sort(key=lambda r: (r["Offered now"] != "yes", r["Typical time (s)"] is None,
                                 r["Typical time (s)"] or 0))
        st.dataframe(rows, hide_index=True, use_container_width=True)
        # Slow = at least twice the median of models still offered (and over
        # 45 s), checked separately with and without web search.
        slow = []
        for col, how in (("Typical time (s)", ""), ("With web (s)", " with web")):
            timed = [r for r in rows if r[col] is not None and r["Answers"] >= 2
                     and r["Offered now"] == "yes"
                     and not (how and r["Web search"] == "no")]   # old web runs of no-web sources
            if len(timed) < 3:
                continue
            mid = _median([r[col] for r in timed])
            slow += [f"{r['Source'].split(' ', 1)[-1]} {r['Model']}{how} (~{r[col]}s)"
                     for r in timed if r[col] >= max(45, 2 * mid)]
        if slow:
            st.warning("🐢 Much slower than the rest (twice the median or more): "
                       + "; ".join(slow) + ". With *Start when most are in*, these rarely "
                       "make it into the answer.")
        st.caption("Typical (median) values from answers that actually came back (failed calls, missing keys and timed-out "
                   "searches are excluded from time and cost). No web search: "
                   + ", ".join(NO_WEB_PROVIDERS) + (" — they read the shared web sources "
                   "while web is on." if shared_search_ready() else
                   " — they sit out while web is on."))
        seats, jev, helpers = report_seat_stats()
        if seats:
            st.markdown("**Synthesizer & Adjudicator**")
            seat_rows = [{"Role": role, "Provider": f"{ICON.get(prov, '')} {prov}", "Model": mid,
                          "Calls": v["n"],
                          "Typical time (s)": round(v["secs"]) if v["secs"] is not None else None,
                          "Typical cost ($)": round(v["cost"], 4) if v["cost"] is not None else None,
                          "Failed": f"{100.0 * v['fail'] / v['n']:.0f}%" if v["n"] else ""}
                         for (role, prov, mid), v in seats.items()]
            seat_rows.sort(key=lambda r: (r["Role"], -r["Calls"]))
            st.dataframe(seat_rows, hide_index=True, use_container_width=True)
            st.caption("A revised answer counts its first draft here, so revisions don't inflate "
                       "the Synthesizer's typical time.")
        if jev["answers_read"] or jev["claim_checks"]:
            agree = (f"agreed with the Adjudicator on {jev['agreed']} of {jev['compared']} "
                     "audited turns" if jev["compared"] else "no audited turn to compare yet")
            st.caption(f"🧭 **Jev's record:** {jev['answers_read']} source answers read "
                       f"({jev['flagged_answers']} flagged) · {jev['claim_checks']} claim checks, "
                       f"{agree} · would have set aside {jev['would_hold']} answers. "
                       "Keep it in Shadow until this agreement is consistently high.")
        if helpers:
            st.caption("👀🧭 **Helpers so far:** " + " · ".join(
                f"{name}: {v['runs']} runs, ~${v['cost']:.3f}" for name, v in helpers.items()))
        _fresh_line = fresh_stats_line(report_fresh_stats())
        if _fresh_line:
            st.caption(_fresh_line)

if _seat_key_fallback():
    inherit_followup_from_initial()
_sync_web_all_flag()

with st.sidebar:
    with st.container(key="side_text_stepper"):
        _sm, _sp = st.columns(2, gap=None)
        with _sm:
            st.button("−", key="side_txt_minus", on_click=_step_text_size,
                      args=("k_side_text", -1), help="Smaller chat titles",
                      disabled=_size_index("k_side_text", SIDEBAR_TEXT_SCALES) == 0)
        with _sp:
            st.button("＋", key="side_txt_plus", on_click=_step_text_size,
                      args=("k_side_text", 1), help="Larger chat titles",
                      disabled=_size_index("k_side_text", SIDEBAR_TEXT_SCALES)
                               == len(SIDEBAR_TEXT_SCALES) - 1)
    with st.container(key="sidebar_brand_lockup"):
        _side_mark, _side_brand = st.columns([0.11, 0.89], gap=None,
                                              vertical_alignment="center")
        with _side_mark:
            render_brand_mark(24)
        with _side_brand:
            st.button("Conclave", key="sidebar_brand_home",
                      on_click=_navigate, args=(VIEW_SYNTH,), help="Return to Synthesis")
        st.markdown('<div class="conclave-tagline">Multi-model intelligence</div>',
                    unsafe_allow_html=True)

    _current_view = st.session_state.get("main_view", VIEW_SYNTH)

    _chat_tab_active = st.session_state.get("main_view") in (VIEW_SYNTH, VIEW_RES)
    _new_chat_disabled = bg_active()
    if bg_active():
        _new_chat_help = "Disabled while a run is in progress."
    elif not _chat_tab_active:
        _new_chat_help = "Start a fresh Synthesis chat and return home."
    else:
        _new_chat_help = ("Start a fresh chat in the tab you're on. The current one is "
                          "already saved under Chats — nothing is lost.")
    st.button("➕ New chat", key="new_chat_btn", use_container_width=True,
              type="primary", on_click=new_chat, disabled=_new_chat_disabled,
              help=_new_chat_help)
    st.button("🔍 Search chats", key="nav_search_chats", use_container_width=True,
              type="primary" if _current_view == VIEW_HISTORY else "secondary",
              on_click=_navigate, args=(VIEW_HISTORY,),
              help="Open every saved chat with full-text search, filters, sort, pin, and delete.")

    _view_short = {VIEW_SYNTH: "⭐ Synthesis", VIEW_RES: "\U0001F52C Research",
                   VIEW_COUNCIL: "🏛️ Council",
                   VIEW_DOC: "\U0001F4C4 Document", VIEW_PROJECTS: "▦ Projects",
                   VIEW_LENSES: "◉ Tasks & Lenses", VIEW_HISTORY: "\U0001F558 Chats",
                   VIEW_SETTINGS: "⚙ Settings"}.get(_current_view, "⭐ Synthesis")
    with st.expander(f"Workspaces  ·  {_view_short}", expanded=True,
                     key="workspaces_expander", type="compact"):
        for _nav_label, _nav_view, _nav_key in (
            ("⭐ Synthesis", VIEW_SYNTH, "nav_synthesis"),
            ("▦ Projects (coming soon)", VIEW_PROJECTS, "nav_projects"),
            ("◉ Tasks & Lenses (coming soon)", VIEW_LENSES, "nav_lenses"),
            ("\U0001F52C Research (inactive)", VIEW_RES, "nav_research"),
            ("\U0001F4C4 Document (coming soon)", VIEW_DOC, "nav_document"),
        ):
            st.button(_nav_label, key=_nav_key, use_container_width=True,
                      type="primary" if _current_view == _nav_view else "secondary",
                      on_click=_navigate, args=(_nav_view,),
                      help={
                          VIEW_SYNTH: "The main chat: your question goes to the council, the "
                                      "Synthesizer writes one answer, and follow-ups continue "
                                      "with the Synthesizer.",
                          VIEW_PROJECTS: "Coming next: persistent workspaces that group related "
                                         "chats, sources, and deliverables.",
                          VIEW_LENSES: "Coming later: task modes (Decision, Critical Review, "
                                       "Creative...) that shape how the council approaches work.",
                          VIEW_RES: "A separate workspace: the sources answer fresh and an "
                                    "evaluator compares them -- consensus, contradictions, blind "
                                    "spots -- instead of writing one answer.",
                          VIEW_DOC: "Coming later: the council drafts and the Synthesizer writes "
                                    "a finished document you can export.",
                      }.get(_nav_view))

    _open_chat_title = current_chat_title()
    _chats_label = "\U0001F558 Chats"
    if _open_chat_title:
        _chats_label += (f"  ·  {chat_topic_icon(_open_chat_title)} "
                         f"{_open_chat_title[:22]}{'…' if len(_open_chat_title) > 22 else ''}")
    with st.expander(_chats_label, expanded=True, key="chats_expander", type="compact"):
        _side_all = list(_hist_all) + [h for h in load_history_archive()
                                       if _history_key(h) not in {_history_key(x) for x in _hist_all}]
        _pinned = [h for h in _side_all if h.get("pinned")]
        _recent = [h for h in _side_all if not h.get("pinned")]
        def _freq_fresh(h):
            try:
                _ref = str(h.get("last_open") or h.get("ts"))
                return (datetime.now().date()
                        - datetime.fromisoformat(_ref).date()).days <= 7
            except (ValueError, TypeError):
                return False
        _frequent = sorted((h for h in _recent
                            if int(h.get("opens") or 0) >= 2 and _freq_fresh(h)),
                           key=lambda e: -int(e.get("opens") or 0))[:3]
        _frequent_keys = {(h.get("ts"), h.get("mode", "synthesis")) for h in _frequent}
        _recent_rest = [h for h in _recent
                        if (h.get("ts"), h.get("mode", "synthesis")) not in _frequent_keys]
        _SIDEBAR_RECENT_CAP = len(_recent)

        def _chat_row(_h, _i, _prefix):
            _title = chat_display_title(_h)
            _hicon = chat_topic_icon(f"{_title} {_h.get('prompt', '') or ''}",
                                     _h.get("mode", "synthesis"))
            _label = f"{_hicon} {_title[:96] or '(no prompt)'}"
            st.button(_label, key=f"{_prefix}_{_i}", use_container_width=True,
                      on_click=load_session_into_state, args=(_h,))

        if _pinned:
            st.markdown('<div class="conclave-nav-label">\U0001F4CC Pinned</div>',
                        unsafe_allow_html=True)
            for _i, _h in enumerate(_pinned):
                _chat_row(_h, _i, "sbpin")
        if _frequent:
            st.markdown('<div class="conclave-nav-label">\U0001F525 Most frequented</div>',
                        unsafe_allow_html=True)
            for _i, _h in enumerate(_frequent):
                _chat_row(_h, _i, "sbfreq")
        if not _side_all:
            st.caption("No saved chats yet — your first send will appear here.")
        _last_group = None
        for _i, _h in enumerate(_recent_rest[:_SIDEBAR_RECENT_CAP]):
            _group = chat_day_group(_h.get("ts"))
            if _group != _last_group:
                st.markdown(f'<div class="conclave-nav-label">{_group}</div>',
                            unsafe_allow_html=True)
                _last_group = _group
            _chat_row(_h, _i, "sbhist")
        st.button(f"See all chats  ·  {len(_side_all)}", key="nav_history",
                  use_container_width=True,
                  type="primary" if _current_view == VIEW_HISTORY else "secondary",
                  on_click=_navigate, args=(VIEW_HISTORY,))


    st.divider()
    # Keep cancellation reachable after navigating away, but do not duplicate the
    # centered model rows, elapsed time, or progress bar in the sidebar. The
    # invisible fragment preserves completion commits from every workspace.
    if st.session_state.get("bg_job_id"):
        _sidebar_job = _bg_get(st.session_state.get("bg_job_id"))
        if _sidebar_job and _sidebar_job.get("status") == "running":
            _sidebar_mode = _sidebar_job["plan"]["mode"]
            _sidebar_elapsed = time.time() - _sidebar_job["started"]
            _sidebar_label = "Synthesis" if _sidebar_mode == "synthesis" else "Research"
            _sidebar_target = VIEW_SYNTH if _sidebar_mode == "synthesis" else VIEW_RES
            st.caption(f"⏳ Still working in the background · {_sidebar_elapsed:.0f}s — "
                       "navigating anywhere in Conclave never stops it.")
            if _current_view != _sidebar_target:
                st.button(f"↩ Back to {_sidebar_label}", key="bg_job_back_link",
                         use_container_width=True,
                         on_click=_navigate, args=(_sidebar_target,))
        render_stop_run_button("stop_run_sidebar")


    with st.container(key="sidebar_pinned_footer"):
        render_council_button("ftr_council")
        if bg_active():
            _fj = _bg_get(st.session_state.get("bg_job_id"))
            if _fj and _fj.get("status") == "running":
                _fmode = "Synthesis" if _fj["plan"]["mode"] == "synthesis" else "Research"
                st.caption(f"⏳ {_fmode} run in progress · {time.time() - _fj['started']:.0f}s")
        with st.popover(f"⚙ Conclave · v{APP_VERSION}", use_container_width=True):
            _c_srcs, _c_syn, _c_adj_on, _c_adj = council_summary()
            st.button(f"🏛️ Council · {current_preset_name()} · {len(_c_srcs)} sources · ⭐ {_c_syn}"
                      + (f" · ⚖️ {_c_adj}" if _c_adj_on else ""),
                      key="nav_council", use_container_width=True,
                      type="primary" if _current_view == VIEW_COUNCIL else "secondary",
                      on_click=_navigate, args=(VIEW_COUNCIL,))
            st.button("⚙ Settings & API Keys", key="nav_settings", use_container_width=True,
                      type="primary" if _current_view == VIEW_SETTINGS else "secondary",
                      on_click=_navigate, args=(VIEW_SETTINGS,))
            st.button("ℹ️ About Conclave", key="footer_about", use_container_width=True,
                      on_click=_open_about_dialog)
            st.button("❓ How to use Conclave", key="footer_howto", use_container_width=True,
                      on_click=_open_about_dialog, args=("howto",),
                      help="Opens About Conclave straight at the step-by-step guide.")
            c_selectbox("Page width", list(PAGE_WIDTHS.keys()), "k_page_width", ns="ftr")
            c_selectbox("Appearance", ["System", "Light", "Dark"], "k_theme_mode", ns="ftr")
            c_selectbox("Accent color", list(ACCENT_PRESETS.keys()), "k_accent", ns="ftr")
            st.caption(f"Conclave v{APP_VERSION} · local & private — your keys, "
                       "your machine, your chats.")

install_run_control_hover_titles()

ENABLED = {p: bool(st.session_state[spec["enabled_key"]])
           for p, spec in PROVIDERS.items()}
WEB = {p: bool(st.session_state[spec["web_key"]]) if spec["web_key"] else False
       for p, spec in PROVIDERS.items()}
WEB["Qwen"] = WEB["Qwen"] and _source_qwen_web_supported()
_SEARCH_READY = shared_search_ready()
PACK_SOURCES = {p: bool(st.session_state.get(PACK_KEYS[p]))
                for p in PROVIDERS
                if _SEARCH_READY and (st.session_state.get(PACK_KEYS[p])
                                      or (pages_by_default(p) and not WEB[p]))}

CFG = {
    "ChatGPT": {"model_id": CHATGPT_MODELS[st.session_state["k_gpt_model"]],
                 "effort": CHATGPT_EFFORT[st.session_state["k_gpt"]],
                 "speed": st.session_state["k_gpt_speed"]},
    "Claude":  {"model_id": CLAUDE_MODELS[st.session_state["k_cla_model"]],
                "effort": CLAUDE_EFFORT[st.session_state["k_cla"]],
                "thinking": st.session_state["k_cla_think"],
                "max_searches": st.session_state["k_cla_maxsearch"]},
    "Gemini":  {"model_id": GEMINI_MODELS[st.session_state["k_gem"]],
                "level": GEMINI_THINK[st.session_state["k_gem_t"]]},
    "DeepSeek": {"model_id": DEEPSEEK_MODELS[st.session_state["k_ds_model"]],
                 "thinking": st.session_state["k_ds_think"],
                 "effort": DEEPSEEK_EFFORT[st.session_state["k_ds_effort"]]},
    "Grok":    {"mode": st.session_state["k_grok"]},
    "Qwen":    {"model_id": QWEN_MODELS[st.session_state["k_qwen_model"]],
                "thinking": st.session_state["k_qwen_think"],
                "budget": st.session_state["k_qwen_budget"],
                "effort": QWEN_EFFORT[st.session_state["k_qwen_effort"]]},
    "Mistral": {"model_id": MISTRAL_MODELS[st.session_state["k_mistral_model"]],
                "effort": MISTRAL_EFFORT[st.session_state["k_mistral_eff"]]},
    "Kimi":    {"model_id": KIMI_MODELS[st.session_state["k_kimi_model"]],
                "thinking": st.session_state["k_kimi_think"],
                "effort": KIMI_EFFORT[st.session_state["k_kimi_effort"]]},
    "Meta":    {"model_id": META_MODELS[st.session_state["k_meta_model"]],
                "effort": META_EFFORT[st.session_state["k_meta_eff"]]},
    "MiniMax": {"model_id": MINIMAX_MODELS[st.session_state["k_mm_model"]],
                "thinking": st.session_state["k_mm_think"],
                "speed": st.session_state["k_mm_speed"]},
    "NVIDIA":  {"model_id": NVIDIA_MODELS[st.session_state["k_nv_model"]],
                "thinking": st.session_state["k_nv_think"],
                "budget": st.session_state["k_nv_budget"]},
    "GLM":     {"model_id": GLM_MODELS[st.session_state["k_glm_model"]],
                "thinking": st.session_state["k_glm_think"],
                "effort": GLM_EFFORT[st.session_state["k_glm_eff"]]},
    "MiMo":    {"model_id": MIMO_MODELS[st.session_state["k_mimo_model"]],
                "thinking": st.session_state["k_mimo_think"]},
}

st.markdown("""<style>
html { font-size: 17px; }
:root {
    --conclave-accent: #55b8b3;
    --conclave-action: #246b73;
    --conclave-action-hover: #2d7f85;
    --conclave-teal: #55b8b3;
    --conclave-coral: #d97972;
    --conclave-green: #2f9e68;
    --conclave-base: #eff1f4;
    --conclave-text: #3b434d;
    --conclave-muted: #6f7984;
    --conclave-line: rgba(38, 52, 64, 0.13);
    --conclave-surface: #e6e9ed;
    --conclave-field: #f3f4f6;
    --conclave-card: #f7f8fa;
}
@media (prefers-color-scheme: dark) {
    :root {
        --conclave-base: #0e1117;
        --conclave-text: #d8dee5;
        --conclave-muted: #98a3ad;
        --conclave-line: rgba(216, 222, 229, 0.15);
        --conclave-surface: #141922;
        --conclave-field: #1d222c;
        --conclave-card: #171c24;
    }
}
[data-testid="stAppViewContainer"] {
    background: var(--conclave-base);
}
[data-testid="stMainBlockContainer"] {
    width: 100%;
    max-width: 2400px;
    margin-inline: auto;
    padding-top: 3.55rem;
    padding-left: clamp(0.85rem, 1.5vw, 2rem);
    padding-right: clamp(0.85rem, 1.5vw, 2rem);
    padding-bottom: 4rem;
}
.st-key-conclave_header {
    margin: 0 0 0.2rem;
    padding-bottom: 0;
}
.st-key-main_brand_home button,
.st-key-sidebar_brand_home button {
    justify-content: flex-start;
    border-color: transparent !important;
    background: transparent !important;
    box-shadow: none !important;
    color: var(--conclave-text) !important;
    font-weight: 800;
    letter-spacing: 0;
}
.st-key-main_brand_home button {
    min-height: 2rem;
    width: auto !important;
    padding: 0.08rem 0.1rem;
    font-size: 1.45rem;
}
.st-key-main_brand_home button p {font-size: 1.45rem !important;}
.st-key-sidebar_brand_home button {
    min-height: 1.8rem !important;
    width: auto !important;
    padding: 0.06rem 0.08rem !important;
    font-size: 1.5rem !important;
    line-height: 1 !important;
    margin-bottom: 0;
    transform: translateY(1.5px);
}
.st-key-sidebar_brand_home button p {
    font-size: 1.5rem !important;
    line-height: 1 !important;
}
.st-key-main_brand_home button:hover,
.st-key-sidebar_brand_home button:hover {
    color: var(--conclave-accent) !important;
}
.st-key-sidebar_brand_lockup {margin-bottom: 1.1rem;}
.st-key-conclave_header [data-testid="stCaptionContainer"] p {
    color: var(--conclave-muted);
    font-size: 0.78rem;
}
.st-key-conclave_header [data-testid="stHorizontalBlock"] {align-items: center;}
.st-key-conclave_header [data-testid="stHorizontalBlock"] {
    display: grid !important;
    grid-template-columns: minmax(0, 1fr) max-content !important;
    column-gap: 0 !important;
}


.st-key-conclave_composer textarea, [class*="st-key-composer_"] textarea {
    caret-color: var(--conclave-accent) !important;
}
.conclave-tagline {
    margin: -0.15rem 0 0 0.1rem;
    color: var(--conclave-muted);
    font-size: 0.66rem;
    letter-spacing: 0.04em;
}

.st-key-conclave_home {
    min-height: calc(100vh - 14.5rem);
    justify-content: center;
    
    padding-bottom: calc(11rem + 24vh);
}

.st-key-conclave_home .st-key-conclave_composer {
    width: clamp(42rem, 66%, 100%);
    max-width: 100%;
    margin-left: auto;
    margin-right: auto;
}
/* the control labels never wrap, whatever the width */
.st-key-conclave_composer button p,
[class*="st-key-composer_"] button p,
.st-key-conclave_composer [data-testid="stPopoverButton"] p {
    white-space: nowrap;
}
.st-key-conclave_composer [data-testid="stHorizontalBlock"],
[class*="st-key-composer_"] [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap;
}
.st-key-conclave_composer [data-testid="stColumn"],
[class*="st-key-composer_"] [data-testid="stColumn"] {
    min-width: max-content;
}

.st-key-conclave_composer [data-testid="stColumn"]:has([data-testid="stAudioInput"]),
[class*="st-key-composer_"] [data-testid="stColumn"]:has([data-testid="stAudioInput"]) {
    min-width: 2.4rem !important;
}
.st-key-conclave_composer,
[class*="st-key-composer_"] {
    overflow: clip;
}
/* ...and below ~900px the control row WRAPS inside the pill instead --
   the mic drops to a second line, never off the edge. */
@media (max-width: 900px) {
    .st-key-conclave_composer [data-testid="stHorizontalBlock"],
    [class*="st-key-composer_"] [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap;
    }
}

.conclave-att-strip {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    overflow-x: auto;
    padding: 0.35rem 0.2rem 0.1rem;
    border-top: 1px dashed var(--conclave-line);
}
.conclave-att-count {
    color: var(--conclave-muted);
    font-size: 0.74rem;
    font-weight: 700;
    white-space: nowrap;
}
.conclave-att-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.32rem;
    background: var(--conclave-field);
    border: 1px solid var(--conclave-line);
    border-radius: 9px;
    padding: 0.14rem 0.5rem 0.14rem 0.18rem;
    font-size: 0.72rem;
    white-space: nowrap;
}
.conclave-att-chip img {
    width: 26px;
    height: 26px;
    object-fit: cover;
    border-radius: 6px;
}
.conclave-att-chip .att-ico { font-size: 0.95rem; padding-left: 0.2rem; }
[data-testid="stMainBlockContainer"]:has(.st-key-conclave_home) {
    padding-bottom: 1rem;
}

[data-testid="stSidebarHeader"] {
    height: 2.1rem;
    min-height: 0;
    padding: 0.25rem 0.5rem 0;
}

.st-key-chats_expander [data-testid="stVerticalBlock"] { gap: 0.12rem; }
.st-key-chats_expander [data-testid="stMarkdownContainer"] { overflow: visible; line-height: 1.4; }
.st-key-chats_expander [data-testid="stElementContainer"]:has(.conclave-nav-label) {
    min-height: 2.1rem;
}
.st-key-chats_expander [data-testid="stButton"] { margin-bottom: 0; }
.st-key-chats_expander [data-testid="stHorizontalBlock"] { gap: 0.2rem; }
.st-key-conclave_home .conclave-greeting { margin-top: 0; }

.st-key-conclave_composer [data-testid="InputInstructions"],
[class*="st-key-composer_"] [data-testid="InputInstructions"] {
    display: none !important;
}

.st-key-conclave_composer,
[class*="st-key-composer_"] {
    background: var(--conclave-card);
    border: 1px solid var(--conclave-line);
    border-radius: 20px;
    padding: 0.55rem 0.75rem 0.55rem;
    gap: 0.35rem;
}
.st-key-conclave_composer [data-baseweb="textarea"],
.st-key-conclave_composer [data-baseweb="base-input"],
[class*="st-key-composer_"] [data-baseweb="textarea"],
[class*="st-key-composer_"] [data-baseweb="base-input"] {
    background: transparent !important;
    border-color: transparent !important;
    box-shadow: none !important;
}
.st-key-conclave_composer [data-testid="stTextArea"] textarea,
[class*="st-key-composer_"] [data-testid="stTextArea"] textarea {
    background: transparent !important;
    border-color: transparent !important;
    box-shadow: none !important;
}
.st-key-conclave_composer [data-testid="stPopoverButton"],
[class*="st-key-composer_"] [data-testid="stPopoverButton"],
.st-key-conclave_composer [data-testid="stBaseButton-secondary"],
[class*="st-key-composer_"] [data-testid="stBaseButton-secondary"] {
    border-color: transparent !important;
    background: transparent !important;
    box-shadow: none !important;
}

.st-key-card_btn_ChatGPT button  { border-left-color: #19c37d !important; }
.st-key-card_btn_Claude button   { border-left-color: #d97757 !important; }
.st-key-card_btn_Gemini button   { border-left-color: #4e8df5 !important; }
.st-key-card_btn_Grok button     { border-left-color: #9aa7b5 !important; }
.st-key-card_btn_DeepSeek button { border-left-color: #4d6bfe !important; }
.st-key-card_btn_Qwen button     { border-left-color: #8b5cf6 !important; }
.st-key-card_btn_Mistral button  { border-left-color: #ff7000 !important; }
.st-key-card_btn_Kimi button     { border-left-color: #4f8cff !important; }
.st-key-card_btn_Meta button     { border-left-color: #0866ff !important; }
.st-key-card_btn_MiniMax button  { border-left-color: #ff4d4f !important; }
.st-key-card_btn_NVIDIA button   { border-left-color: #76b900 !important; }
.st-key-card_btn_GLM button      { border-left-color: #00c2ff !important; }
.st-key-card_btn_MiMo button     { border-left-color: #ff6900 !important; }

[class*="st-key-card_btn_"] button { justify-content: flex-start !important; }
[class*="st-key-card_btn_"] button > div {
    justify-content: flex-start !important;
    text-align: left !important;
    flex: 1 1 auto;
    width: auto !important;
}
[class*="st-key-card_btn_"] button p { text-align: left !important; }
[class*="st-key-card_btn_"] button::after {
    margin-left: auto;
    font-size: 0.95em;
    opacity: 0.9;
    
    background-repeat: no-repeat;
    background-position: 0.55rem center;
    background-size: 15px 10.5px;
    padding-left: calc(0.55rem + 20px);
}

[class*="st-key-srcweb_"] button, [class*="st-key-srcon_"] button,
[class*="st-key-evalweb_"] button, [class*="st-key-evalon_"] button {
    min-height: 1.7rem !important;
    padding: 0.05rem 0.25rem !important;
    font-size: 0.78rem;
    justify-content: center !important;
}

[class*="st-key-srcweb_"] button[data-testid="stBaseButton-secondary"],
[class*="st-key-srcon_"] button[data-testid="stBaseButton-secondary"],
[class*="st-key-evalweb_"] button[data-testid="stBaseButton-secondary"],
[class*="st-key-evalon_"] button[data-testid="stBaseButton-secondary"] {
    opacity: 0.45;
}
[class*="st-key-srcweb_"] button[disabled],
[class*="st-key-evalweb_"] button[disabled] { opacity: 0.25; }
[class*="st-key-srcweb_"] button p, [class*="st-key-srcon_"] button p,
[class*="st-key-evalweb_"] button p, [class*="st-key-evalon_"] button p {
    text-align: center !important;
    white-space: nowrap;
}


.st-key-hdr_k_theme_mode {
    position: fixed !important;
    
    top: 4rem;
    right: 1.0rem;
    width: auto !important;
    z-index: 999;
}
.st-key-hdr_k_theme_mode [data-testid="stButtonGroup"] {
    justify-content: flex-end;
}

.st-key-hdr_text_stepper {
    position: fixed !important;
    top: 4rem;
    
    right: var(--conclave-hdr-stepper-right, 11rem);
    width: auto !important;
    z-index: 999;
}
.st-key-hdr_text_stepper [data-testid="stHorizontalBlock"],
.st-key-side_text_stepper [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap;
    gap: 0.15rem;
    justify-content: flex-end;
}
.st-key-hdr_text_stepper [data-testid="stColumn"],
.st-key-side_text_stepper [data-testid="stColumn"] {
    flex: none !important;
    width: auto !important;
    min-width: 0 !important;
}
.st-key-hdr_text_stepper button,
.st-key-side_text_stepper button {
    min-height: 1.7rem !important;
    padding: 0.1rem 0.55rem !important;
    font-size: 0.9rem;
    line-height: 1;
}
.st-key-hdr_text_stepper button[disabled],
.st-key-side_text_stepper button[disabled] { opacity: 0.3; }
[data-testid="stSidebarContent"] { position: relative; }
.st-key-side_text_stepper {
    position: absolute !important;
    top: 0.4rem;
    
    right: 4.2rem;
    width: auto !important;
    z-index: 10;
}
.st-key-hdr_k_theme_mode [data-testid="stBaseButton-segmented_control"],
.st-key-hdr_k_theme_mode [data-testid="stBaseButton-segmented_controlActive"] {
    min-height: 1.7rem;
    min-width: 0 !important;          
    padding: 0.1rem 0.5rem !important;
    font-size: 0.85rem;
    line-height: 1;
}
.st-key-sidebar_brand_lockup [data-testid="stHorizontalBlock"] {
    display: grid !important;
    grid-template-columns: 28px max-content !important;
    column-gap: 0 !important;
    justify-content: start !important;
}
.st-key-conclave_header [data-testid="stColumn"],
.st-key-sidebar_brand_lockup [data-testid="stColumn"] {
    width: auto !important;
    min-width: 0 !important;
}
.st-key-conclave_header [data-testid="stHorizontalBlock"] >
    div[data-testid="stColumn"]:nth-child(3) {
    padding-left: 1rem !important;
}
.st-key-conclave_header [data-testid="stImage"],
section[data-testid="stSidebar"] [data-testid="stImage"] {
    display: flex;
    align-items: center;
    justify-content: flex-start;
}
.st-key-conclave_header img,
section[data-testid="stSidebar"] [data-testid="stImage"] img {
    display: block;
    object-fit: contain;
}

.conclave-mark { position: relative; display: block; }
.conclave-mark svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    display: block;
    overflow: visible;
}

.st-key-conclave_header [data-testid="stMarkdownContainer"]:has(.conclave-mark),
.st-key-sidebar_brand_lockup [data-testid="stMarkdownContainer"]:has(.conclave-mark) {
    margin: 0; padding: 0; line-height: 0;
}
/* (the margin/line-height zeroing above is the whole alignment fix --
   measured within ~1px of the title's centerline in both lockups) */
.conclave-mark .seal-rot {
    
    transform-origin: 50% 50%;
}

.conclave-mark .seal-hone,
.conclave-mark .seal-dot {
    transform-box: fill-box;
    transform-origin: 50% 50%;
}

@keyframes conclave-seal-spin { to { transform: rotate(360deg); } }
@keyframes conclave-seal-hone {
    0%   { opacity: 0.9; transform: scale(3.4); }
    70%  { opacity: 0.4; }
    100% { opacity: 0;   transform: scale(0.5); }
}
@keyframes conclave-seal-pulse {
    0%, 55% { transform: scale(1); }
    80%     { transform: scale(1.3); }
    100%    { transform: scale(1); }
}

.conclave-seal-loading .seal-rot { animation: conclave-seal-spin 0.5s linear infinite; }
.conclave-seal-loading .seal-hone { animation: conclave-seal-hone 1.15s ease-in infinite; }
.conclave-seal-loading .seal-dot  { animation: conclave-seal-pulse 1.15s ease-in infinite; }
.conclave-run-seal {
    width: 1rem;
    height: 1rem;
    display: inline-grid;
    place-items: center;
    margin-top: 0.06rem;
}
.conclave-run-seal .conclave-mark { width: 0.95rem !important; height: 0.95rem !important; }
.conclave-loading-banner {
    display: flex;
    justify-content: center;
    margin: 0.25rem 0 0.5rem;
}
[data-testid="stMainBlockContainer"] h4 {
    margin-top: 0.1rem;
    font-size: 1.18rem;
    color: var(--conclave-text);
}
.conclave-nav-label {
    display: block;
    line-height: 1.4;
    
    padding: 0.7rem 0 0.25rem;
    margin: 0;
    color: var(--conclave-muted);
    font-size: calc(0.68rem * var(--conclave-side-scale, 1));
    font-weight: 750;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}

section[data-testid="stSidebar"] button {
    justify-content: flex-start !important;
}
section[data-testid="stSidebar"] button > div {
    justify-content: flex-start !important;
    text-align: left !important;
    width: 100%;
}
section[data-testid="stSidebar"] button p {
    text-align: left !important;
}

.st-key-new_chat_btn button,
.st-key-nav_projects button,
.st-key-nav_search_chats button,
.st-key-nav_lenses button,
.st-key-nav_history button,
.st-key-nav_synthesis button,
.st-key-nav_research button,
.st-key-nav_document button {
    justify-content: flex-start;
    border-color: transparent !important;
    box-shadow: none !important;
}

.st-key-new_chat_btn button p,
.st-key-nav_projects button p,
.st-key-nav_search_chats button p,
.st-key-nav_lenses button p,
.st-key-nav_history button p,
.st-key-nav_synthesis button p,
.st-key-nav_research button p,
.st-key-nav_document button p {
    text-align: left;
}

[class*="st-key-sbhist_"] button,
[class*="st-key-sbpin_"] button,
[class*="st-key-sbfreq_"] button {
    justify-content: flex-start;
    border-color: transparent !important;
    box-shadow: none !important;
    min-height: 1.5rem !important;
    padding: 0.04rem 0.4rem !important;
}
[class*="st-key-sbhist_"] button p,
[class*="st-key-sbpin_"] button p,
[class*="st-key-sbfreq_"] button p {
    text-align: left;
    font-size: calc(0.78rem * var(--conclave-side-scale, 1));
    
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.st-key-chats_expander [data-testid="stPopoverButton"] {
    min-height: 1.5rem !important;
    padding: 0.04rem 0.3rem !important;
    border-color: transparent !important;
    background: transparent !important;
    opacity: 0.45;
}
.st-key-chats_expander [data-testid="stHorizontalBlock"]:hover [data-testid="stPopoverButton"] {
    opacity: 1;
}
/* The account-style footer popover trigger reads as a quiet full-width menu
   control, not a loud action button. */
.st-key-sidebar_pinned_footer [data-testid="stPopover"] > button {
    justify-content: flex-start;
    border-color: transparent !important;
    box-shadow: none !important;
}

.conclave-greeting {
    margin: 2.6rem 0 1.1rem;
    text-align: center;
    font-size: 1.65rem;
    font-weight: 750;
    color: var(--conclave-text);
    letter-spacing: -0.01em;
}
.st-key-run_preset_picker {
    margin-top: 0.72rem;
}
.st-key-run_preset_picker [data-testid="stExpander"] {
    background: var(--conclave-card);
}
.st-key-run_preset_picker [data-testid="stExpander"] summary {
    min-height: 2.12rem;
    font-size: 0.79rem;
    font-weight: 720;
}
.st-key-run_preset_picker [role="radiogroup"] label {
    padding-block: 0.08rem;
}
.st-key-run_preset_picker [role="radiogroup"] > label:last-child {
    margin-top: 0.34rem;
    padding-top: 0.52rem;
    border-top: 1px solid var(--conclave-line);
}
.st-key-run_preset_picker [data-testid="stCaptionContainer"] p {
    font-size: 0.72rem;
    line-height: 1.3;
}
section[data-testid="stSidebar"] {
    border-right: 1px solid var(--conclave-line);
    background: var(--conclave-surface);
}
section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
    padding-top: 0.2rem;
}
section[data-testid="stSidebar"] hr {
    margin: 0.65rem 0;
    border-color: var(--conclave-line);
}
section[data-testid="stSidebar"] [data-testid="stButton"] {
    margin-bottom: 0.18rem;
}
section[data-testid="stSidebar"] [data-testid="stButton"] > button {
    min-height: 2.08rem;
    padding: 0.27rem 0.58rem;
    border-radius: 6px;
    font-size: 0.79rem;
}
section[data-testid="stSidebar"] [data-testid="stExpander"] {
    border-color: var(--conclave-line);
    border-radius: 6px;
}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
    font-size: 0.72rem;
    line-height: 1.35;
}
button[kind="primary"] {
    border-color: color-mix(in srgb, var(--conclave-action) 78%, #000);
    background: var(--conclave-action);
    color: #f4f8f9;
    font-weight: 700;
}
button[kind="primary"]:hover {
    border-color: var(--conclave-accent);
    background: var(--conclave-action-hover);
    color: #f4f8f9;
}
[data-testid="stBaseButton-secondary"],
[data-testid="stPopover"] > button,
[data-testid="stDownloadButton"] button {
    border-color: var(--conclave-line);
    border-radius: 6px;
    background: var(--conclave-card);
}
[data-testid="stTextArea"] textarea,
[data-testid="stTextInput"] input,
[data-baseweb="select"] > div {
    border-color: var(--conclave-line);
    border-radius: 6px;
    background: var(--conclave-field);
}
[data-testid="stTextArea"] textarea:focus,
[data-testid="stTextInput"] input:focus {
    border-color: var(--conclave-teal);
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--conclave-teal) 48%, transparent);
}
[data-testid="stVerticalBlockBorderWrapper"] {
    border-color: var(--conclave-line) !important;
    border-radius: 8px !important;
    background: var(--conclave-card);
}
div[data-testid="stAudioInput"] {
    width: 100%;
    max-width: 190px;
}
div[data-testid="stAudioInput"] > div {
    min-height: 2.25rem;
    padding: 0.12rem 0.3rem;
    border-color: var(--conclave-line);
    border-radius: 6px;
    background: var(--conclave-card);
}
div[data-testid="stAudioInput"] button {
    width: 2rem;
    height: 2rem;
    min-height: 2rem;
    border-color: color-mix(in srgb, var(--conclave-coral) 55%, var(--conclave-line));
    background: color-mix(in srgb, var(--conclave-coral) 12%, transparent);
    color: var(--conclave-coral);
}
div[data-testid="stAudioInput"]:has(button[aria-label="Record"]) {
    width: 2.5rem;
    max-width: 2.5rem;
    margin-left: auto;
}
div[data-testid="stAudioInput"]:has(button[aria-label="Record"]) > div {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 2.5rem;
    height: 2.5rem;
    min-height: 2.5rem;
    padding: 0;
}
div[data-testid="stAudioInput"]:has(button[aria-label="Record"]) button {
    margin: auto;
}
div[data-testid="stAudioInput"]:has(button[aria-label="Record"])
    > div > div:has(button[aria-label="Record"]) {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 2.5rem !important;
    height: 2.5rem !important;
    margin: 0 !important;
}
div[data-testid="stAudioInput"]:has(button[aria-label="Record"])
    div:has(> [data-testid="stAudioInputWaveSurfer"]),
div[data-testid="stAudioInput"]:has(button[aria-label="Record"])
    [data-testid="stAudioInputWaveformTimeCode"] {
    display: none;
}
[data-testid="stPopover"] button p {white-space: nowrap;}

div[data-baseweb="tooltip"], div[role="tooltip"] {
    position: fixed !important;
    top: auto !important; left: auto !important;
    bottom: 1rem !important; right: 1rem !important;
    transform: none !important;
    max-width: 440px;
    z-index: 99990;
}
section[data-testid="stSidebar"] button p {
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
[data-testid="stChatMessage"] {
    border: 1px solid var(--conclave-line);
    border-radius: 8px;
    background: var(--conclave-card);
}

[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    background: color-mix(in srgb, var(--conclave-accent) 13%, var(--conclave-card));
    border-left: 4px solid var(--conclave-accent);
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
    border-left: 4px solid var(--conclave-line);
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
    box-sizing: border-box;
    width: 100%;
    max-width: min(210ch, 100%);
    margin-inline: auto;
    padding-right: clamp(2rem, 4vw, 3.25rem);
    text-align: left;
    overflow-wrap: anywhere;
}
.st-key-stop_run_sidebar button,
.st-key-stop_run_main_synthesis button,
.st-key-stop_run_main_research button {
    border-color: color-mix(in srgb, var(--conclave-coral) 70%, var(--conclave-line)) !important;
    background: color-mix(in srgb, var(--conclave-coral) 13%, var(--conclave-card)) !important;
    color: #f1a09b !important;
    font-weight: 700;
}
.st-key-stop_run_sidebar button:hover,
.st-key-stop_run_main_synthesis button:hover,
.st-key-stop_run_main_research button:hover {
    border-color: var(--conclave-coral) !important;
    background: color-mix(in srgb, var(--conclave-coral) 20%, var(--conclave-card)) !important;
}
[data-testid="stAppViewContainer"] [data-testid="stMarkdownContainer"],
[data-testid="stAppViewContainer"] [data-testid="stChatMessage"],
[data-testid="stAppViewContainer"] label,
[data-testid="stAppViewContainer"] textarea,
[data-testid="stAppViewContainer"] input {
    color: var(--conclave-text);
}
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] p,
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] li,
[data-testid="stChatMessage"] p,
[data-testid="stChatMessage"] li {
    color: var(--conclave-text) !important;
}
[data-testid="stBaseButton-segmented_controlActive"] {
    border-color: var(--conclave-accent);
    color: var(--conclave-accent) !important;
    background: color-mix(in srgb, var(--conclave-accent) 11%, transparent);
}
[data-testid="stAlert"] {border-radius: 6px;}
.stTabs [data-baseweb="tab-list"],
[data-testid="stSegmentedControl"] {gap: 0.25rem;}
.conclave-run-list {
    display: grid;
    gap: 0.32rem;
    margin: 0.35rem 0 0.5rem;
}
.conclave-run-row {
    display: grid;
    grid-template-columns: 1rem minmax(0, 1fr);
    align-items: start;
    gap: 0.55rem;
    min-height: 1.35rem;
    font-size: 0.88rem;
    line-height: 1.3;
}
.conclave-run-copy {min-width: 0;}
.conclave-run-copy strong,
.conclave-run-copy span {display: block; overflow-wrap: anywhere;}
.conclave-run-copy span {color: rgba(128, 128, 128, 0.95); font-size: 0.79rem;}
.conclave-thinking-spinner {
    box-sizing: border-box;
    width: 0.82rem;
    height: 0.82rem;
    margin-top: 0.12rem;
    border: 2px solid rgba(128, 128, 128, 0.28);
    border-top-color: var(--primary-color, #ff4b4b);
    border-radius: 50%;
    animation: conclave-spin 0.8s linear infinite;
}
.conclave-run-marker {
    display: inline-grid;
    place-items: center;
    width: 0.9rem;
    height: 0.9rem;
    margin-top: 0.08rem;
    font-size: 0.72rem;
    font-weight: 700;
}
.conclave-run-done-marker {color: var(--conclave-green);}
.conclave-run-error-marker {color: var(--conclave-coral);}
.conclave-run-queued {opacity: 0.58;}
.conclave-run-queued-marker {color: rgba(128, 128, 128, 0.9);}
section[data-testid="stSidebar"] .conclave-run-row {font-size: 0.8rem;}
section[data-testid="stSidebar"] .conclave-run-copy span {font-size: 0.73rem;}
@keyframes conclave-spin {to {transform: rotate(360deg);}}
@media (prefers-reduced-motion: reduce) {
    .conclave-thinking-spinner {animation: none; border-top-color: currentColor;}
}
@media (max-width: 780px) {
    [data-testid="stMainBlockContainer"] {
        padding: 3.45rem 0.85rem 3rem;
    }
    .st-key-conclave_header {margin-bottom: 0.8rem;}
    div[data-testid="stHorizontalBlock"] {flex-wrap: wrap; gap: 0.45rem;}
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
        flex: 1 1 8rem !important;
        width: auto !important;
        min-width: min(8rem, 100%) !important;
    }
    .st-key-conclave_header [data-testid="stHorizontalBlock"] {
        display: grid !important;
        grid-template-columns: minmax(0, 1fr) max-content !important;
        gap: 0 !important;
    }
    .st-key-sidebar_brand_lockup [data-testid="stHorizontalBlock"] {
        display: grid !important;
        grid-template-columns: 28px max-content !important;
        gap: 0 !important;
        justify-content: start !important;
    }
    .st-key-conclave_header [data-testid="stHorizontalBlock"] > div[data-testid="stColumn"],
    .st-key-sidebar_brand_lockup [data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
        flex: none !important;
        width: auto !important;
        min-width: 0 !important;
    }
    div[data-testid="stColumn"]:has(div[data-testid="stAudioInput"]:has(button[aria-label="Record"])) {
        flex: 0 0 2.5rem !important;
        width: 2.5rem !important;
        min-width: 2.5rem !important;
        margin-left: auto;
    }
}


[data-testid="stSidebarContent"] [data-testid="stLayoutWrapper"]:has(> .st-key-sidebar_pinned_footer) {
    position: sticky;
    bottom: 0;
    z-index: 5;
    margin-top: auto;
}

[data-testid="stSidebarUserContent"] { padding-bottom: 0 !important; }
[data-testid="stSidebarContent"] [data-testid="stLayoutWrapper"]:has(> .st-key-sidebar_pinned_footer) {
    padding-bottom: 0.35rem;
    background: var(--conclave-surface);
}
.st-key-sidebar_pinned_footer {
    position: static;
    
    background: var(--conclave-card);
    border: 1px solid var(--conclave-line);
    border-radius: 10px;
    padding: 0.45rem 0.5rem 0.4rem;
    box-shadow: 0 -6px 18px rgba(0, 0, 0, 0.18);
    gap: 0.25rem;
}
.st-key-sidebar_pinned_footer .st-key-ftr_council button {
    border-color: transparent !important;
    background: transparent !important;
    box-shadow: none !important;
    min-height: 1.7rem;
    padding: 0.1rem 0.35rem;
}
.st-key-sidebar_pinned_footer .st-key-ftr_council button p { white-space: nowrap; }
.st-key-sidebar_pinned_footer [data-testid="stCaptionContainer"] p {
    font-size: 0.72rem;
    white-space: nowrap;
    padding-left: 0.35rem;
}


/* Type: the operating system's modern UI face (Segoe UI Variable on
   Windows 11) -- no network font fetch. Code keeps a monospace face. */
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"], button, input, textarea, select {
    font-family: "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif !important;
    -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }
code, pre, kbd, samp { font-family: "Cascadia Code", Consolas, ui-monospace, monospace !important; }

/* Sidebar: one tight rhythm (nav rows ~39px, chat rows ~32px). */
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0.25rem; }
section[data-testid="stSidebar"] [data-testid="stButton"] { margin-bottom: 0 !important; }
section[data-testid="stSidebar"] [data-testid="stButton"] button { min-height: 2.05rem !important; height: 2.05rem; padding: 0 0.65rem !important; border-radius: 8px; font-weight: 500; transition: background-color 120ms ease, color 120ms ease; }
section[data-testid="stSidebar"] [data-testid="stButton"] button p { font-size: 0.84rem; line-height: 1.2; letter-spacing: 0; }
.st-key-nav_search_chats button, .st-key-nav_synthesis button, .st-key-nav_projects button, .st-key-nav_lenses button, .st-key-nav_research button, .st-key-nav_document button, .st-key-nav_history button { background: transparent; }
section[data-testid="stSidebar"] [data-testid="stButton"] button:hover { background: color-mix(in srgb, var(--conclave-text) 7%, transparent) !important; }
section[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] { background: color-mix(in srgb, var(--conclave-accent) 16%, transparent) !important; border-color: transparent !important; color: var(--conclave-text) !important; }
section[data-testid="stSidebar"] .st-key-new_chat_btn button { border: 1px solid var(--conclave-line) !important; }
.st-key-nav_projects button, .st-key-nav_lenses button, .st-key-nav_research button, .st-key-nav_document button { opacity: 0.62; }
section[data-testid="stSidebar"] [data-testid="stExpander"] { border: none !important; background: transparent !important; }
section[data-testid="stSidebar"] [data-testid="stExpander"] details { border: none !important; }
section[data-testid="stSidebar"] [data-testid="stExpander"] summary { padding: 0.35rem 0.35rem !important; min-height: 0 !important; }
section[data-testid="stSidebar"] [data-testid="stExpander"] summary p { font-size: 0.74rem !important; font-weight: 650; letter-spacing: 0.04em; color: var(--conclave-muted) !important; }
section[data-testid="stSidebar"] [data-testid="stExpanderDetails"] { padding: 0.1rem 0 0.4rem !important; }
.conclave-nav-label { padding: 0.8rem 0.65rem 0.2rem !important; font-size: calc(0.68rem * var(--conclave-side-scale, 1)) !important; }
section[data-testid="stSidebar"] :is([class*="st-key-sbhist_"], [class*="st-key-sbpin_"], [class*="st-key-sbfreq_"]) button { height: calc(1.85rem * var(--conclave-side-scale, 1)) !important; min-height: calc(1.85rem * var(--conclave-side-scale, 1)) !important; }

section[data-testid="stSidebar"] :is([class*="st-key-sbhist_"], [class*="st-key-sbpin_"], [class*="st-key-sbfreq_"]) button p { font-size: calc(0.84rem * var(--conclave-side-scale, 1)) !important; }
section[data-testid="stSidebar"] .st-key-chats_expander [data-testid="stVerticalBlock"] { gap: 0.05rem; }

/* Composer: a touch more air and a clean focus ring. */
.st-key-conclave_composer, [class*="st-key-composer_"] { border-radius: 18px !important; padding: 0.55rem 0.7rem 0.45rem !important; }
.st-key-conclave_composer textarea, [class*="st-key-composer_"] textarea { font-size: 1rem !important; line-height: 1.5 !important; }
.st-key-conclave_composer:focus-within, [class*="st-key-composer_"]:focus-within { box-shadow: 0 0 0 1px color-mix(in srgb, var(--conclave-accent) 55%, transparent), 0 6px 24px rgba(0,0,0,0.18) !important; }
.conclave-greeting { font-weight: 650 !important; letter-spacing: -0.02em !important; }
[data-testid="stMainBlockContainer"] button { border-radius: 10px; font-weight: 550; }

/* Answers: reading rhythm -- line height, paragraph and list spacing,
   heading scale, tables, quotes, softer cards. */
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] p, [data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] li { line-height: 1.65; }
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] p { margin: 0 0 0.85em; }
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] li { margin: 0.2em 0; }
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] :is(ul, ol) { margin: 0.2em 0 0.9em; padding-left: 1.4em; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] :is(h1, h2, h3, h4) { line-height: 1.25; letter-spacing: -0.012em; font-weight: 650; margin: 1.4em 0 0.5em; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h1 { font-size: 1.55rem; margin-top: 0.2em; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h2 { font-size: 1.25rem; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h3 { font-size: 1.08rem; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] table { border-collapse: collapse; margin: 0.6em 0 1.1em; font-size: 0.93em; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] :is(th, td) { padding: 0.45em 0.75em; border-bottom: 1px solid var(--conclave-line); }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] blockquote { border-left: 3px solid var(--conclave-line); margin: 0.8em 0; padding: 0.1em 0 0.1em 1em; color: var(--conclave-muted); }
[data-testid="stChatMessage"] { border-radius: 14px !important; padding: 1.1rem 1.25rem !important; margin-bottom: 0.9rem; }
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] { border-radius: 10px !important; border-color: var(--conclave-line) !important; }
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] summary p { font-size: 0.88rem; font-weight: 550; }
[data-testid="stCaptionContainer"] p { line-height: 1.45; }

/* Fixed top-right controls get a backing so scrolled text never shows through. */
.st-key-hdr_k_theme_mode, .st-key-hdr_text_stepper { background: color-mix(in srgb, var(--conclave-base, #0f131a) 82%, transparent); backdrop-filter: blur(8px); border-radius: 10px; padding: 2px; }

/* ✕ beside the attachment strip: small and quiet. */
[class*="st-key-attclr_"] button { min-height: 1.7rem !important; height: 1.7rem; padding: 0 0.5rem !important; border-color: transparent !important; background: transparent !important; opacity: 0.7; }
[class*="st-key-attclr_"] button:hover { opacity: 1; }
.conclave-att-strip { margin: 0 !important; padding-bottom: 0.25rem !important; }
/* Streamlit gives every markdown block a -1rem bottom margin, which pushed
   the strip 17px past the composer's clipped edge. */
[data-testid="stMarkdownContainer"]:has(> .conclave-att-strip) { margin-bottom: 0 !important; }
.st-key-conclave_composer, [class*="st-key-composer_"] { padding-bottom: 0.7rem !important; }


:root { --conclave-hairline: color-mix(in srgb, var(--conclave-line) 62%, transparent); }

/* Frame: sidebar edge, header strip, dividers. */
section[data-testid="stSidebar"] { border-right: 1px solid var(--conclave-hairline) !important; }
[data-testid="stHeader"] { border-bottom: none !important; box-shadow: none !important; }
hr, [data-testid="stMarkdownContainer"] hr { border: none !important; border-top: 1px solid var(--conclave-hairline) !important; margin: 1rem 0 !important; }

/* Every card-like surface: chat turns, expanders, bordered containers,
   composers, dialogs, popovers, the side panel's footer box. */
[data-testid="stChatMessage"] { border: 1px solid var(--conclave-hairline) !important; border-radius: 10px !important; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) { border-left: 2px solid var(--conclave-accent) !important; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) { border-left: 1px solid var(--conclave-hairline) !important; }
[data-testid="stExpander"] details, [data-testid="stMainBlockContainer"] [data-testid="stExpander"], [data-testid="stDialog"] [data-testid="stExpander"] { border-color: var(--conclave-hairline) !important; border-radius: 8px !important; }
[data-testid="stVerticalBlockBorderWrapper"] { border-color: var(--conclave-hairline) !important; border-radius: 10px !important; }
.st-key-conclave_composer, [class*="st-key-composer_"] { border: 1px solid var(--conclave-hairline) !important; border-radius: 14px !important; }
.st-key-conclave_composer:focus-within, [class*="st-key-composer_"]:focus-within { box-shadow: 0 0 0 1px color-mix(in srgb, var(--conclave-accent) 45%, transparent) !important; }
[data-testid="stDialog"] [role="dialog"], [data-testid="stPopoverBody"] { border: 1px solid var(--conclave-hairline) !important; border-radius: 12px !important; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.22) !important; }
.st-key-sidebar_pinned_footer { border: 1px solid var(--conclave-hairline) !important; border-radius: 8px !important; box-shadow: none !important; }

/* Controls: hairline outlines, one radius, lighter weight. */
[data-testid="stBaseButton-secondary"], [data-testid="stPopoverButton"], [data-testid="stBaseButton-tertiary"], [data-testid="stDownloadButton"] button { border-color: var(--conclave-hairline) !important; }
[data-testid="stMainBlockContainer"] button, [data-testid="stDialog"] button { border-radius: 8px !important; font-weight: 500 !important; }
[data-testid="stTextAreaRootElement"], [data-testid="stTextInputRootElement"], [data-testid="stNumberInputContainer"], [data-testid="stSelectbox"] [role="group"] { border-color: var(--conclave-hairline) !important; }
.st-key-conclave_composer [data-testid="stTextAreaRootElement"], [class*="st-key-composer_"] [data-testid="stTextAreaRootElement"] { border-color: transparent !important; }
[data-testid="stButtonGroup"] button { border-color: var(--conclave-hairline) !important; }
[data-baseweb="tab-list"] { gap: 0.15rem; }
[data-baseweb="tab-highlight"] { height: 2px !important; }
[data-baseweb="tab-border"] { height: 1px !important; background: var(--conclave-hairline) !important; }
[data-testid="stAlert"] { border-radius: 8px !important; }

/* Council page cards: the accent edge on active sources and live seats
   stays, at 2px instead of 3-4px; the card outline goes to a hairline. */
[class*="st-key-card_btn_"] button { border-top-color: var(--conclave-hairline) !important; border-right-color: var(--conclave-hairline) !important; border-bottom-color: var(--conclave-hairline) !important; }

/* Answer tables and quotes: hairlines. */
[data-testid="stMarkdownContainer"] :is(th, td) { border-bottom: 1px solid var(--conclave-hairline) !important; }
[data-testid="stMarkdownContainer"] blockquote { border-left: 2px solid var(--conclave-line) !important; }

/* Type: lighter, tighter headings and a calmer greeting -- same font. */
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] :is(h1, h2, h3, h4) { font-weight: 600 !important; letter-spacing: -0.015em !important; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h1 { font-size: 1.45rem !important; }
[data-testid="stMainBlockContainer"] :is(h1, h2, h3) { font-weight: 600 !important; letter-spacing: -0.015em; }
.conclave-greeting { font-weight: 600 !important; letter-spacing: -0.025em !important; }
[data-testid="stExpander"] summary p { font-weight: 500 !important; }
</style>""", unsafe_allow_html=True)

_page_width_choice = st.session_state.get("k_page_width", "Wide")
_text_size_choice = st.session_state.get("k_text_size", "Default")
_side_text_choice = st.session_state.get("k_side_text", "Default")
st.markdown(
    f'<style>html {{ font-size: {TEXT_SIZES.get(_text_size_choice, "17px")} !important; }}\n'
    f'[data-testid="stSidebarContent"] {{ zoom: '
    f'{17.0 / float(TEXT_SIZES.get(_text_size_choice, "17px")[:-2]):.4f}; }}\n'
    f':root {{ --conclave-side-scale: {SIDEBAR_TEXT_SCALES.get(_side_text_choice, "1")}; }}\n'
    f'[data-testid="stMainBlockContainer"] {{ '
    f'max-width: {PAGE_WIDTHS.get(_page_width_choice, "2400px")} '
    f'!important; }}\n'
    f'[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {{ '
    f'max-width: {PAGE_PROSE_WIDTHS.get(_page_width_choice, "100%")} '
    f'!important; }}</style>',
    unsafe_allow_html=True,
)

_theme_mode = st.session_state.get("k_theme_mode", "System")
_accent_choice = st.session_state.get("k_accent", "Conclave Teal")
_appearance_css = ""
if _theme_mode in THEME_TOKENS:
    _tokens = "".join(f"{k}: {v}; " for k, v in THEME_TOKENS[_theme_mode].items())
    _appearance_css += (
        f":root {{ {_tokens}}}\n"
        + STREAMLIT_THEME_REANCHOR_CSS
    )
elif _theme_mode == "System":
    _day_tokens = "".join(f"{k}: {v}; " for k, v in THEME_TOKENS["Light"].items())
    _night_tokens = "".join(f"{k}: {v}; " for k, v in THEME_TOKENS["Dark"].items())
    _appearance_css += (
        f"html.conclave-day {{ {_day_tokens}}}\n"
        f":where(html.conclave-day) {{\n{STREAMLIT_THEME_REANCHOR_CSS}}}\n"
        f"html.conclave-night {{ {_night_tokens}}}\n"
    )
if _accent_choice in ACCENT_PRESETS and ACCENT_PRESETS[_accent_choice]:
    _accent_tokens = "".join(f"{k}: {v}; "
                             for k, v in ACCENT_PRESETS[_accent_choice].items())
    _appearance_css += f":root {{ {_accent_tokens}}}\n"
if _appearance_css:
    st.markdown(f"<style>{_appearance_css}</style>", unsafe_allow_html=True)
st.html(SOLAR_THEME_JS, unsafe_allow_javascript=True)


def render_research_eval_panel():
    st.caption("The Research evaluator compares and adjudicates the source answers — its own "
               "model, fully separate from the Synthesizer.")
    p = render_evaluator_controls("k_res")
    c_toggle("\U0001F310 Web verify", "k_res_web", disabled=(p in NO_WEB_PROVIDERS),
             after_change=lambda: (_sync_evaluator_deepseek_web_toggle("k_res"),
                                   _sync_evaluator_qwen_web_toggle("k_res")),
             help="Lets the evaluator fact-check claims. Off by default to keep research "
                  "cheap. DeepSeek, the 3.7 Qwen models, Mistral and NVIDIA cannot search, so this "
                  "stays off for them.")
    if p == "Claude":
        c_number("Max web searches", "k_res_maxsearch", min_value=1, max_value=20, step=1,
                 disabled=not st.session_state["k_res_web"],
                 help="Applies when web verify is on. Claude needs this cap; "
                      "other providers self-limit.")
        _claude_search_recommendation_caption("k_res")
    st.caption("The evaluator always remains independent and never answers as a source.")

@st.dialog("\U0001F52C Research evaluator")
def research_settings_dialog():
    render_research_eval_panel()
    if st.button("✅ Done", type="primary", use_container_width=True, key="done_res"):
        st.rerun()

def _render_side_chat_web_button(provider, key):
    if provider in NO_WEB_PROVIDERS:
        st.button("🌐 Web", key=key, disabled=True, use_container_width=True,
                  help=f"{provider} can't search the web itself — use 🔎 Pages for the shared Tavily + Brave pages.")
        return
    on = bool(st.session_state.get(_SRC_WEB_KEY[provider]))
    _help = (f"Web search for {provider} in this private chat (and on its Council card): "
             f"{'ON — click to turn off' if on else 'off — click to turn on'}.")
    if provider in WEB_TIER_FLOOR and _web_tier_too_low(provider):
        _help += f" ⏱️💸 {WEB_FLOOR_EVIDENCE[provider]}."
    if provider == "Qwen" and not _source_qwen_web_supported():
        _help += " Turning it on switches the model to the nearest 3.8 (the 3.7 models can't search)."
    st.button("🌐 Web", key=key, type="primary" if on else "secondary",
              use_container_width=True, on_click=_flip_source_web, args=(provider,), help=_help)

def _render_side_chat_pages_button(provider, key):
    ready = shared_search_ready()
    on = ready and bool(st.session_state.get(PACK_KEYS[provider]))
    st.button("🔎 Pages", key=key, type="primary" if on else "secondary",
              use_container_width=True, disabled=not ready,
              on_click=_flip_flag, args=(PACK_KEYS[provider],),
              help=(f"Tavily + Brave pages for {provider} in this private chat (and on its "
                    f"Council card): {'ON — click to turn off' if on else 'off — click to turn on'}."
                    if ready else "Add a Tavily or Brave key in Settings ▸ API keys."))

def follow_up_form(form_key, label, settings_kind=None, provider=None):
    nonce_key = f"nonce_{form_key}"
    st.session_state.setdefault(nonce_key, 0)
    txt_key = f"txt_{form_key}_{st.session_state[nonce_key]}"
    upn_key = f"nonce_up_{form_key}"
    st.session_state.setdefault(upn_key, 0)
    up_key = f"up_{form_key}_{st.session_state[nonce_key]}_{st.session_state[upn_key]}"
    mic_nonce_key = f"nonce_mic_{form_key}"
    st.session_state.setdefault(mic_nonce_key, 0)
    mic_key = f"mic_{form_key}_{st.session_state[mic_nonce_key]}"

    # Apply a transcript captured on the previous run, before the box renders.
    pend_key = f"_pending_{form_key}"
    if pend_key in st.session_state:
        st.session_state[txt_key] = st.session_state.pop(pend_key)

    with st.container(key=f"composer_{form_key}"):
        txt = st.text_area(label, height=68, key=txt_key,
                           placeholder="Ask a follow-up…  (Enter sends · Shift+Enter for a new line)")
        c1, c2, c3, c4, c5 = st.columns([1.15, 1.0, 2.3, 1.35, 1.0], gap="small",
                                         vertical_alignment="center")
        with c1:
            sent = st.button("➤ Send", key=f"sendbtn_{form_key}", type="primary",
                             use_container_width=True, disabled=bg_active(),
                             help="Wait for the run in progress to finish." if bg_active() else None)
        with c2:
            staged = stage_uploads(up_key, f"_staged_{form_key}")
            n_att = len(staged)
            with st.popover(f"\U0001F4CE Attach ({n_att})" if n_att else "\U0001F4CE Attach files"):
                st.file_uploader("Files for this follow-up", type=ATTACH_TYPES,
                                 accept_multiple_files=True, key=up_key)
        with c3:
            if provider and settings_kind not in ("synth", "research"):
                _render_side_chat_pages_button(provider, f"pagesbtn_{form_key}")
            if settings_kind == "synth":
                render_council_button(f"council_{form_key}")
            elif settings_kind == "research":
                if st.button(evaluator_action_label("k_res", "Evaluator"),
                             use_container_width=True, key=f"settings_{form_key}",
                             help="Open Research evaluator settings"):
                    research_settings_dialog()
        with c4:
            if settings_kind in ("synth", "research"):
                render_web_search_button(f"webbtn_{form_key}")
                flush_web_floor_toast()
            elif provider:
                _render_side_chat_web_button(provider, f"webbtn_{form_key}")
                flush_web_floor_toast()
        if settings_kind in ("synth", "research"):
            render_web_floor_warning()
        with c5:
            # Same behaviour as the main composer: finishing a recording auto-
            # transcribes and APPENDS to whatever is already typed (never overwrites).
            audio_fu = st.audio_input("Dictate", label_visibility="collapsed", key=mic_key)
            if audio_fu is not None:
                with st.spinner("Transcribing…"):
                    _t, _e = transcribe(audio_fu.getvalue())
                st.session_state[mic_nonce_key] += 1        # fresh (empty) mic next run
                if _e:
                    st.session_state[f"_dictate_err_{form_key}"] = _e
                else:
                    _cur = st.session_state.get(txt_key, "") or ""
                    _sep = "" if (not _cur or _cur.endswith((" ", "\n"))) else " "
                    st.session_state[pend_key] = _cur + _sep + (_t or "")
                st.rerun()
        render_attachment_strip(st.session_state.get(f"_staged_{form_key}"),
                                f"_staged_{form_key}", upn_key, f"attclr_{form_key}")
    _fu_err = st.session_state.pop(f"_dictate_err_{form_key}", None)
    if _fu_err:
        st.error(f"Dictation failed: {_fu_err}")

    if settings_kind == "synth":
        if jev_helpers_ready():
            _jc1, _jc2 = st.columns([5, 1.6], gap="small", vertical_alignment="center")
            with _jc2:
                render_jev_suggest_button(f"jev_suggest_{form_key}", txt_key,
                                          slot="_jev_suggestion_fu", follow_up=True,
                                          label="💡 Ask Jev")
            render_jev_suggestion("_jev_suggestion_fu")
        st.toggle("\U0001F3DB️ Full council this reply", key="k_full_council",
                  on_change=_restore_initial_council,
                  help="Off (default): your follow-up goes to the Synthesizer alone — it "
                       "carries the whole conversation's context and keeps applying its "
                       "synthesizing judgment, at a fraction of a full run's cost. On: this "
                       "follow-up re-runs the complete council — every active source answers "
                       "fresh, then the Synthesizer (and the Adjudicator, when active) "
                       "evaluates them all. Costs roughly as much as the original question. "
                       "Turning it on also brings back the sources and judges from your first question; it stays on until you turn it off.")
        if st.session_state.get("k_full_council"):
            _fc_who = [n for n in MODEL_ORDER if ENABLED.get(n)
                       and n != eval_provider_of("k_fu")
                       and not (adjudicator_stack_active()
                                and n == st.session_state.get("k_adj_provider"))]
            st.caption("\U0001F3DB️ Next follow-up runs the full council: "
                       + (", ".join(_fc_who) if _fc_who else "⚠️ no sources active")
                       + f" → ⭐ {eval_provider_of('k_fu')} synthesizes"
                       + (f" → ⚖️ {st.session_state.get('k_adj_provider')} adjudicates"
                          if adjudicator_stack_active() else "") + ".")
        else:
            st.caption(f"⭐ Next follow-up goes to the Synthesizer alone "
                       f"({eval_provider_of('k_fu')}) — full conversation context, "
                       "fraction of the cost. Flip the toggle above for a full council pass.")

    if sent:
        files = list(st.session_state.get(f"_staged_{form_key}") or [])
        if (txt or "").strip() or files:
            st.session_state[nonce_key] += 1
            st.session_state.pop(f"_staged_{form_key}", None)
            return (txt or "").strip() or "(see attached files)", files
    return None, None

def _keep_rejected_followup(form_key, question, files, error):
    st.session_state[f"_pending_{form_key}"] = (
        "" if question == "(see attached files)" else question)
    if files:
        st.session_state[f"_staged_{form_key}"] = list(files)
    st.session_state[f"_fu_error_{form_key}"] = error
    st.rerun()


def _show_rejected_followup(form_key):
    error = st.session_state.pop(f"_fu_error_{form_key}", None)
    if error:
        st.error(error)


def start_council_followup(mode, question, files, form_key):
    research_mode = mode == "research"
    record = st.session_state.get("research" if research_mode else "synth")
    if not record:
        return "There is no completed council answer to continue yet."

    prefix = "k_res" if research_mode else "k_fu"
    evaluator_provider = eval_provider_of(prefix)
    full_council = research_mode or bool(st.session_state.get("k_full_council"))
    adjudicator_on = full_council and (not research_mode) and adjudicator_stack_active()
    if full_council:
        excluded = {evaluator_provider}
        if adjudicator_on:
            excluded.add(st.session_state["k_adj_provider"])
        sources = [name for name in MODEL_ORDER
                   if ENABLED.get(name) and name not in excluded]
        minimum = 2 if research_mode else 1
        if len(sources) < minimum:
            noun = "two independent sources" if research_mode else "one independent source"
            also = (f" and {st.session_state['k_adj_provider']} the Adjudicator"
                    if adjudicator_on else "")
            return (f"This turn needs at least {noun}. Activate another model; the selected "
                    f"{evaluator_provider} evaluator{also} always remain{'s' if research_mode else ''} "
                    "outside the source council.")
    else:
        sources = []
        minimum = 0

    keys_now = load_keys()
    if not any(keys_now.values()):
        return "No keys found - open Settings & API Keys."
    if sources:
        sources, _nokey = keyed_sources(sources, keys_now)
        if len(sources) < minimum:
            return ("Not enough active source models have a saved key for this turn. "
                    "Add keys in Settings ▸ API keys for: " + ", ".join(_nokey) + ".")

    promotions_key = ("res_source_promotions" if research_mode
                      else "source_promotions")
    promotions = st.session_state.get(promotions_key, [])
    original = st.session_state.get("res_prompt" if research_mode else "prompt", "")
    context_tier = current_preset_name()
    context_mode = st.session_state.get("k_context_mode", "Automatic (run preset)")
    if context_mode == "Lean":
        context_tier = "Fast"
    elif context_mode == "Expanded":
        context_tier = "Thorough"
    context = build_working_context(record, original, promotions, context_tier)
    source_api_msg = {
        "role": "user",
        "content": build_source_turn_message(
            question, context, continuation=True, research=research_mode),
        "files": files,
    }
    display_msg = {"role": "user", "content": question, "display": question,
                   "files": files}
    turn_id = next_turn_id(record)
    web_key = f"{prefix}_web"
    max_key = f"{prefix}_maxsearch"
    eval_resolved = evaluator_job(
        prefix, [], st.session_state[web_key], st.session_state[max_key], keys_now=keys_now)
    label_key = "Researcher" if research_mode else "Synthesizer"
    phase2 = build_eval_phase(
        prefix, label_key, resolved=eval_resolved,
        kind=("research" if research_mode else
              ("synth" if full_council else "solo")),
        files=files, min_good=minimum)
    synth_memo = adj_memo = phase3 = phase4 = None
    if adjudicator_on:
        synth_memo = build_memo_job("k_fu", question, context, True, keys_now=keys_now)
        adj_memo = build_memo_job("k_adj", question, context, True, keys_now=keys_now)
        phase3 = build_eval_phase("k_adj", "Adjudicator", keys_now=keys_now)
        phase4 = build_eval_phase(prefix, "Revision", resolved=eval_resolved)
    pending_ids = [item.get("id") for item in promotions
                   if item.get("status", "pending") == "pending" and item.get("id")]
    source_jobs, src_meta = build_source_jobs(sources, keys_now, source_api_msg)
    bg_start({
        "mode": mode,
        "continuation": True,
        "turn_id": turn_id,
        "prompt": question,
        "context": context,
        "user_msg": display_msg,
        "session_id": st.session_state.get(
            "res_session_id" if research_mode else "session_id"),
        "restore_form_key": form_key,
        "promotion_ids": pending_ids,
        "sources": source_jobs,
        "src_meta": src_meta,
        "phase2": phase2,
        "synth_memo": synth_memo,
        "adj_memo": adj_memo,
        "phase3": phase3,
        "phase4": phase4,
    })
    return None

def _private_source_state_keys(mode):
    """Return the isolated branch/promotion stores for one council workspace."""
    if mode == "research":
        return "res_source_chats", "res_source_promotions"
    return "source_chats", "source_promotions"

def _promote_source_answer(mode, provider, branch_index):
    """Queue one private answer without disturbing any visible composer draft."""
    chats_key, promotions_key = _private_source_state_keys(mode)
    branch = st.session_state.setdefault(chats_key, {}).setdefault(provider, [])
    if branch_index < 0 or branch_index >= len(branch):
        return
    message = branch[branch_index]
    if message.get("role") != "assistant" or message.get("err") or message.get("promoted"):
        return
    promotion_id = uuid.uuid4().hex
    message["promoted"] = True
    message["promotion_id"] = promotion_id
    st.session_state.setdefault(promotions_key, []).append({
        "id": promotion_id, "provider": provider,
        "content": message.get("content", ""), "status": "pending",
        "created": datetime.now().isoformat(timespec="seconds"),
    })
    persist_session(mode)

def render_private_source_branch(n, latest_source_answer, council_record, mode):
    """Render one provider-only branch without mutating the council transcript."""
    chats_key, _ = _private_source_state_keys(mode)
    branch = st.session_state.setdefault(chats_key, {}).setdefault(n, [])
    st.caption(
        f"Private one-on-one chat with {n}. It doesn't affect the council unless you "
        "click **Bring this insight into the next council turn** under one of its answers."
    )
    with st.popover(source_setting_summary(n), use_container_width=True):
        st.caption(f"{n}'s settings for this private chat. Changes also update its "
                   "card on the Council page.")
        render_source_intensity_dials(n, ns=f"sc_{mode}")

    for branch_index, branch_message in enumerate(branch):
        if branch_message["role"] == "user":
            with st.chat_message("user"):
                st.markdown(user_text_of(branch_message))
                render_user_files(branch_message)
            continue

        with st.chat_message("assistant", avatar=ICON.get(n)):
            st.caption(meta_caption(branch_message))
            st.markdown(branch_message["content"])
        if branch_message.get("promoted"):
            promoted_turn = branch_message.get("promoted_turn")
            st.caption(
                "Queued for the next council turn." if promoted_turn is None
                else f"Included in council turn {promoted_turn + 1}."
            )
        elif not branch_message.get("err"):
            st.button(
                "Bring this insight into the next council turn",
                key=f"promote_{mode}_{n}_{branch_index}",
                use_container_width=True, on_click=_promote_source_answer,
                args=(mode, n, branch_index),
            )

    form_key = f"branch_{mode}_{n}"
    fu, fu_files = follow_up_form(form_key, f"Your private follow-up to {n}", provider=n)
    if not fu:
        return

    if branch:
        branch_user = {"role": "user", "content": fu,
                       "display": fu, "files": fu_files}
    else:
        original_key = "res_prompt" if mode == "research" else "prompt"
        branch_context = build_working_context(
            council_record, st.session_state.get(original_key, ""), [],
            current_preset_name())
        branch_seed = (
            "You are continuing in a private branch. This exchange is isolated "
            "from the main council unless the user later promotes an answer.\n\n"
            f"Shared council context:\n{branch_context}\n\n"
            f"Your latest council answer:\n{latest_source_answer}\n\n"
            f"Private user turn:\n{fu}"
        )
        branch_user = {"role": "user", "content": branch_seed,
                       "display": fu, "files": fu_files}
    branch.append(branch_user)
    _msgs, _web, _fresh_svc = api_msgs(branch), WEB[n], None
    if PACK_SOURCES.get(n):
        with st.spinner("🔎 Gathering fresh web sources…"):
            _pack = fetch_fresh_sources(
                search_keys_now(), search_query_for(fu, st.session_state.get(
                    "res_prompt" if mode == "research" else "prompt", "")),
                TAVILY_DEPTHS.get(st.session_state.get("k_tavily_depth"), "advanced"))
        if _pack.get("calls"):
            add_cost({"Fresh sources · " + " + ".join(c["model"] for c in _pack["calls"]): {
                "cost": _pack.get("cost"), "secs": _pack.get("secs") or 0.0,
                "in_tok": None, "out_tok": None, "err": False, "provider": "Fresh sources"}})
        if _pack.get("results"):
            _msgs = _with_fresh_sources(_msgs, format_fresh_sources(_pack))
            _fresh_svc = _pack["service"]
    job = {n: {"provider": n,
               "key": load_keys().get(KEY_MAP[n], ""),
               "messages": _msgs,
               "cfg": CFG[n], "web": _web, "pages": bool(_fresh_svc)}}
    result = run_parallel(job, f"{ICON.get(n, '')} {n} is thinking")
    branch.append(assistant_msg(
        result[n], model_id_of(n, CFG[n]), settings_str(n, CFG[n], _web)))
    if _fresh_svc:
        branch[-1]["fresh_sources"] = _fresh_svc
    add_cost(result)
    st.session_state.pop(f"audio_{n}", None)
    persist_session(mode)
    st.rerun()

# ================= shared render helpers =================
def render_audio(name):
    if st.session_state.get(f"audio_{name}"):
        st.audio(st.session_state[f"audio_{name}"], format="audio/mp3")
    if st.session_state.get(f"audio_err_{name}"):
        st.caption("⚠️ " + st.session_state[f"audio_err_{name}"])

def render_read_aloud_controls(name, text, key_suffix, cache_key=None):
    """Put playback and its provider-specific voice choice beside the response.

    cache_key defaults to `name` (unchanged for every pre-existing caller) --
    pass it explicitly when `name` has more than one independently-readable
    text visible in the app at once (see speak()'s docstring)."""
    cache_key = cache_key or name
    action_col, voice_col = st.columns([5, 1.35], gap="small",
                                       vertical_alignment="center")
    with action_col:
        if st.button("\U0001F50A Read aloud", key=f"say_{key_suffix}"):
            with st.spinner("Generating audio..."):
                speak(name, text, cache_key)
    with voice_col:
        voice_key = VOICE_KEY[name]
        with st.popover("Voice", use_container_width=True,
                        help=f"Choose the read-aloud voice for {name}'s answers. "
                             "All the options below are OpenAI text-to-speech "
                             "voice styles, available for reading aloud any "
                             "model's response -- they aren't tied to ChatGPT, "
                             "Claude, Gemini, or any other specific provider."):
            st.selectbox(f"{name} voice", VOICES, key=voice_key)
            st.caption("All voices are OpenAI text-to-speech -- a style choice, "
                       "not tied to which model wrote this answer.")
    render_audio(cache_key)

def render_session_spend(key_suffix):
    """Show spend only inside an active chat workspace and only after usage."""
    cost = float(st.session_state.get("session_cost", 0.0) or 0.0)
    if cost <= 0:
        return
    spend_col, reset_col = st.columns([5.3, 1], gap="small",
                                      vertical_alignment="center")
    spend_col.caption(f"\U0001F4B5 Session spend · ~${cost:.4f} token cost")
    reset_col.button("Reset", key=f"reset_session_cost_{key_suffix}",
                     on_click=_reset_session_cost, use_container_width=True,
                     help="Set this session's spend counter back to $0 (your provider bills are unaffected).")

def render_response_scroll_anchor(mode, turn_id):
    """Scroll once to the beginning of a newly committed continuation response."""
    state_key = f"_scroll_to_latest_{mode}"
    if st.session_state.get(state_key) != turn_id:
        return
    anchor_id = f"conclave-{mode}-turn-{turn_id}"
    st.html(f"""
<div id="{anchor_id}" style="height:0"></div>
<script>
setTimeout(() => {{
  const target = document.getElementById("{anchor_id}");
  if (target) target.scrollIntoView({{ behavior: "smooth", block: "start" }});
}}, 120);
</script>
""", unsafe_allow_javascript=True)
    st.session_state.pop(state_key, None)

def _mb(n):
    return f"{n / 1048576:.1f} MB"


def attachment_error_lines(turn_id, threads=None, evaluator_thread=None):
    """Provider errors on this turn that are about an attachment's size or type."""
    hits = []
    groups = list((threads or {}).items())
    if evaluator_thread:
        groups.append(("Synthesizer", evaluator_thread))
    for name, thread in groups:
        for msg in thread:
            if (msg.get("role") == "assistant" and msg.get("err")
                    and msg.get("turn_id") == turn_id):
                text = msg.get("content") or ""
                low = text.lower()
                if any(k in low for k in ("image", "exceeds", "too large", "file size",
                                          "media type", "mime")):
                    hits.append((msg.get("provider") or name, text[:240]))
    return hits


def render_user_files(m, threads=None, evaluator_thread=None):
    images = []
    for f in m.get("files", []):
        if f.get("kind") == "image" and f.get("b64"):
            st.image(base64.b64decode(f["b64"]), width=200, caption=f.get("name", ""))
        else:
            note = " (not stored in history)" if f.get("stripped") else ""
            st.caption(f"\U0001F4CE {f.get('name','file')}{note}")
        if f.get("kind") == "image":
            images.append(f)
    if not images:
        return
    errors = attachment_error_lines(m.get("turn_id"), threads, evaluator_thread)
    with st.expander("\U0001F4CE Attachment notes" + (" · ⚠️" if errors else ""), expanded=False):
        for f in images:
            prep = f.get("prepared")
            if prep:
                ow, oh = prep.get("orig_dims", [0, 0]); sw, sh = prep.get("sent_dims", [0, 0])
                st.caption(f"**{f.get('name','image')}** was {_mb(prep['orig_bytes'])} "
                           f"({ow}×{oh}); sent as {_mb(prep['sent_bytes'])} ({sw}×{sh}) so "
                           "every provider accepts it"
                           + (" — animated, so only its first frame was sent" if prep.get("animated") else "")
                           + ".")
            else:
                st.caption(f"**{f.get('name','image')}** was sent at its original size.")
        for provider, text in errors:
            st.caption(f"⚠️ **{provider}** rejected it: {text}")
        st.caption("Published image limits: " + "; ".join(
            f"**{p}** {lim}" for p, lim in IMAGE_LIMITS.items())
            + ". Conclave sends at most 3 MB and 2,576 px, under all of them. "
              "Models without image input (Mistral Small, NVIDIA's text models) "
              "receive a note instead of the picture.")

def user_text_of(m):
    """What to SHOW for a user message (weigh-in messages carry a short
    'display' text; the full packaged content goes to the API only)."""
    return m.get("display") or m.get("content", "")

def build_synthesis_download():
    threads = st.session_state.get("threads", {})
    synth   = st.session_state.get("synth")
    lines = [f"PROMPT: {st.session_state.get('prompt','')}", ""]
    if synth:
        lines.append(f"===== ⭐ SYNTHESIS ({synth['label']}) =====")
        for m in synth["thread"][1:]:
            who = "YOU" if m["role"] == "user" else "SYNTHESIZER"
            lines += [f"[{who}]", user_text_of(m) if m["role"] == "user" else m["content"], ""]
        if synth.get("legend"):
            lines.append("Legend: " + "  ".join(f"{k}={v}" for k, v in synth["legend"].items()))
        lines.append("")
    for n, th in threads.items():
        lines.append(f"===== {n} =====")
        for m in th[1:] if len(th) > 1 else th:
            who = "YOU" if m["role"] == "user" else n.upper()
            if m["role"] == "assistant":
                lines += [f"[{who}]  ({meta_caption(m)})", m["content"], ""]
            elif user_text_of(m) != st.session_state.get("prompt", ""):
                lines += [f"[{who}]", user_text_of(m), ""]
        lines.append("")
    return "\n".join(lines)

def build_research_download():
    res_threads = st.session_state.get("res_threads", {})
    research    = st.session_state.get("research")
    lines = [f"RESEARCH QUESTION: {st.session_state.get('res_prompt','')}", ""]
    for n, th in res_threads.items():
        m = next((x for x in th if x["role"] == "assistant"), None)
        if m:
            lines += [f"===== {n}  ({meta_caption(m)}) =====", m["content"], ""]
    if research:
        lines.append(f"===== \U0001F52C EVALUATION ({research['label']}) =====")
        for m in research["thread"][1:]:
            who = "YOU" if m["role"] == "user" else "EVALUATOR"
            lines += [f"[{who}]", user_text_of(m) if m["role"] == "user" else m["content"], ""]
        if research.get("legend"):
            lines.append("Legend: " + "  ".join(f"{k}={v}" for k, v in research["legend"].items()))
    return "\n".join(lines)

if "_pending_view" in st.session_state:
    st.session_state["main_view"] = st.session_state.pop("_pending_view")
if "_pending_sp_view" in st.session_state:
    st.session_state["sp_view"] = st.session_state.pop("_pending_sp_view")
if "_pending_prompt" in st.session_state:
    st.session_state["prompt_text"] = st.session_state.pop("_pending_prompt")
if "_pending_res_prompt" in st.session_state:
    st.session_state["res_prompt_text"] = st.session_state.pop("_pending_res_prompt")
# Heal a deselected segmented control (clicking the active segment stores None,
# which the keep-alive would then preserve forever — control shows nothing
# selected while content falls back to the default). required=True on the
# widgets below prevents NEW deselections; this heals any None already stored.
if st.session_state.get("main_view") is None: st.session_state["main_view"] = VIEW_SYNTH
if st.session_state.get("sp_view") is None:   st.session_state["sp_view"] = "⭐ Answer"
if st.session_state.get("view_mode") is None: st.session_state["view_mode"] = "Tabs (full width)"
_progress_refresh_rendered = False
view = st.session_state.get("main_view") or VIEW_SYNTH

if view == VIEW_SYNTH:
    st.session_state.setdefault("nonce_main_up", 0)
    MAIN_UP_KEY = f"up_main_{st.session_state['nonce_main']}_{st.session_state['nonce_main_up']}"
    MIC_KEY     = f"mic_{st.session_state['nonce_mic']}"
    _active_job = (_bg_get(st.session_state.get("bg_job_id"))
                   if bg_active() else None)
    _synthesis_run_active = bool(
        _active_job and _active_job.get("plan", {}).get("mode") == "synthesis"
    )
    _synthesis_turn_exists = bool(
        st.session_state.get("threads") or st.session_state.get("synth")
    )
    _show_initial_composer = not (_synthesis_run_active or _synthesis_turn_exists)
    submitted = False
    prompt = st.session_state.get("prompt_text", "")

    if _show_initial_composer:
        with st.container(key="conclave_home"):
            st.markdown(
                f'<div class="conclave-greeting">{html.escape(conclave_greeting())}</div>',
                unsafe_allow_html=True)
            with st.container(key="conclave_composer"):
                prompt = st.text_area(
                    "Ask anything", height=68, key="prompt_text",
                    label_visibility="collapsed",
                    placeholder="Ask anything…",
                    help="Your question goes to every active source model in parallel; the ⭐ "
                         "Synthesizer then weighs their answers claim by claim and writes one "
                         "definitive answer. Follow-ups continue with the Synthesizer alone unless "
                         "you flip on 🏛️ Full council this reply.")
                _n_att = len(stage_uploads(MAIN_UP_KEY, "_staged_main"))
                u1, u2, u3, _u4, _u5, u4 = st.columns([0.8, 0.8, 2.0, 0.75, 0.75, 1.0],
                                                      gap="small", vertical_alignment="center")
                with u1:
                    submitted = st.button("\U0001F680 Send", type="primary", key="send_synth",
                                          use_container_width=True, disabled=bg_active(),
                                          help="A run is already in progress." if bg_active() else None)
                with u2:
                    with st.popover(f"\U0001F4CE Attach ({_n_att})" if _n_att else "\U0001F4CE Attach",
                                    help="Attach images, PDFs, or text files to this question."):
                        st.file_uploader("Files for this prompt", type=ATTACH_TYPES,
                                         accept_multiple_files=True, key=MAIN_UP_KEY)
                        st.caption("To remove an attached file, use the ✕ under the chat box.")
                        st.caption("Images, PDFs and text files — or paste / drop them into the chat box. Images reach ChatGPT, Claude, Gemini, "
                                   "Grok, Meta, Qwen, Kimi, MiniMax M3, Mistral Large 4 / Medium 3.5 / Large 3, "
                                   "DeepSeek V4.1 Flash, GLM-5.3 Flash, Xiaomi MiMo, and Nemotron Nano Omni; PDFs "
                                   "reach ChatGPT, Claude, Gemini, Meta, Mistral, and Kimi. For "
                                   f"Word docs, save as PDF first. Max {MAX_FILE_MB} MB per file.")
                with u3:
                    render_council_button("cmp_council")
                with _u4:
                    render_web_search_button("cmp_web_btn")
                with _u5:
                    render_jev_suggest_button("jev_suggest_main", "prompt_text")
                flush_web_floor_toast()
                render_web_floor_warning()
                with u4:
                    audio_in = st.audio_input("Dictate", label_visibility="collapsed", key=MIC_KEY,
                                              help="Record a question; it is transcribed and added "
                                                   "to the box when you stop.")
                    if audio_in is not None:
                        with st.spinner("Transcribing…"):
                            text, err = transcribe(audio_in.getvalue())
                        st.session_state["nonce_mic"] += 1        # fresh (empty) mic next run
                        if err:
                            st.session_state["_dictate_err"] = err
                        else:
                            cur = st.session_state.get("prompt_text", "")
                            sep = "" if (not cur or cur.endswith((" ", "\n"))) else " "
                            st.session_state["_pending_prompt"] = cur + sep + (text or "")
                        st.rerun()
                render_attachment_strip(st.session_state.get("_staged_main"),
                                        "_staged_main", "nonce_main_up", "attclr_main")
                render_jev_suggestion()

        _derr = st.session_state.pop("_dictate_err", None)
        if _derr:
            st.error(f"Dictation failed: {_derr}")

    if submitted and not bg_active():
        keys_now = load_keys()
        autosynth = st.session_state["k_autosynth"]
        syn_provider = st.session_state["k_syn_provider"]
        adj_provider = st.session_state["k_adj_provider"]
        adjudicator_on = adjudicator_stack_active()
        sources = [n for n in MODEL_ORDER if ENABLED[n]]
        if autosynth:
            excluded = {syn_provider} | ({adj_provider} if adjudicator_on else set())
            sources = [n for n in sources if n not in excluded]
        if not prompt.strip():
            st.error("Type a question first.")
        elif not sources:
            st.error(f"No independent sources to run - {syn_provider} is the Synthesizer and "
                     f"{adj_provider} is the Adjudicator; both always sit out. Activate another "
                     "source model." if autosynth and adjudicator_on else
                     f"No independent sources to run - {syn_provider} is the Synthesizer and "
                     "always sits out. Activate another source model.")
        elif not any(keys_now.values()):
            st.error("No keys found — open Settings, then API Keys.")
        elif not keyed_sources(sources, keys_now)[0]:
            st.error("None of the active source models has a saved key. Add keys in "
                     "Settings ▸ API keys, or switch on a model you have a key for.")
        else:
            sources = keyed_sources(sources, keys_now)[0]
            files_main = list(st.session_state.pop("_staged_main", None) or [])
            st.session_state["nonce_main"] += 1      # fresh (empty) uploader next run
            if autosynth:
                inherit_followup_from_initial()
            user_msg = {"role": "user", "content": prompt, "files": files_main}
            source_msg = {
                "role": "user",
                "content": build_source_turn_message(
                    prompt, continuation=False, research=False),
                "files": files_main,
            }
            for n in list(ICON.keys()) + EVAL_MEMO_AUDIO_NAMES:
                st.session_state.pop(f"audio_{n}", None); st.session_state.pop(f"audio_err_{n}", None)

            source_jobs, src_meta = build_source_jobs(sources, keys_now, source_msg)
            plan = {
                "mode": "synthesis",
                "continuation": False,
                "initial_council": snapshot_initial_council(),
                "turn_id": 0,
                "prompt": prompt,
                "context": "",
                "user_msg": user_msg,
                "session_id": datetime.now().isoformat(timespec="microseconds"),
                "sources": source_jobs,
                "src_meta": src_meta,
                "phase2": None,
            }
            if autosynth:
                synth_resolved = evaluator_job(
                    "k_syn", [], st.session_state["k_syn_web"], st.session_state["k_syn_maxsearch"], keys_now=keys_now)
                plan["phase2"] = build_eval_phase(
                    "k_syn", "Synthesizer", resolved=synth_resolved,
                    kind="synth", files=files_main)
                if adjudicator_on:
                    plan["synth_memo"] = build_memo_job("k_syn", prompt, "", False, keys_now=keys_now)
                    plan["adj_memo"] = build_memo_job("k_adj", prompt, "", False, keys_now=keys_now)
                    plan["phase3"] = build_eval_phase("k_adj", "Adjudicator", keys_now=keys_now)
                    plan["phase4"] = build_eval_phase(
                        "k_syn", "Revision", resolved=synth_resolved)
            bg_start(plan)
            st.rerun()

    bg_flash("synthesis")
    threads = st.session_state.get("threads", {})
    synth   = st.session_state.get("synth")

    if _synthesis_run_active and not synth:
        render_pending_question()
        bg_refresh(show_progress=True)
        _progress_refresh_rendered = True
        render_stop_run_button("stop_run_main_synthesis")

    SP_VIEWS = ["⭐ Answer", "\U0001F9E9 The Sources"]
    sp = st.session_state.get("sp_view") or SP_VIEWS[0]

    if _synthesis_turn_exists:
        _seg_col, _pin_col, _del_col = st.columns([6, 0.7, 0.7])
        with _seg_col:
            c_segmented("Synthesis view", SP_VIEWS, "sp_view", ns="top",
                       label_visibility="collapsed", required=True)
        _pin_sid = st.session_state.get("session_id")
        if _pin_sid:
            _pin_entry = next(
                (h for h in _hist_all
                 if h.get("ts") == _pin_sid
                 and h.get("mode", "synthesis") == "synthesis"),
                None,
            )
            _pinned_now = (bool(_pin_entry.get("pinned"))
                           if _pin_entry is not None
                           else bool(st.session_state.get("session_pinned", False)))
            with _pin_col:
                st.button("📌", key="pin_chat_main", use_container_width=True,
                          type="primary" if _pinned_now else "secondary",
                          help="Unpin this chat" if _pinned_now else "Pin this chat",
                          on_click=toggle_pin_session, args=(_pin_sid, "synthesis"))
            with _del_col:
                with st.popover("🗑", help="Delete this chat"):
                    st.caption("Delete this chat? Can't be undone.")
                    st.button("Delete", key="del_chat_main", type="primary",
                              use_container_width=True, on_click=delete_session,
                              args=(_pin_sid, "synthesis"))

    if sp == "⭐ Answer":
        if not synth:
            if threads:
                st.info("No synthesis for this run — auto-synthesize was off or all sources "
                        "failed. The source answers are under \U0001F9E9 The Sources above.")
        else:
            st.markdown(f"#### ⭐ Synthesis · {synth['label']}")
            _synth_turn_number = 0
            for m in synth["thread"]:
                if m["role"] == "assistant":
                    _synth_turn_number += 1
                    render_response_scroll_anchor("synthesis", m.get("turn_id"))
                    with st.chat_message("assistant"):
                        st.caption(meta_caption(m))
                        render_synthesized_content(m["content"])
                        render_reasoning(m, synth.get("provider", ""))
                        render_adjudicator_review(m)
                        render_fresh_sources(m, f"s{_synth_turn_number}")
                        render_run_report(
                            threads, m, synth, "Synthesizer", _synth_turn_number,
                            eval_threads=st.session_state.get("eval_threads", {}))
                        render_turn_cost_breakdown(
                            threads, m, "Synthesizer",
                            _synth_turn_number,
                            eval_threads=st.session_state.get("eval_threads", {}))
                else:
                    with st.chat_message("user"):
                        st.markdown(user_text_of(m))
                        render_user_files(m, threads, synth.get("thread"))
            render_conversation_costs(
                threads, synth, "Synthesizer",
                eval_threads=st.session_state.get("eval_threads", {}))
            if synth.get("legend"):
                leg = "  ".join(f"**{k}** = {ICON.get(v,'')} {v}" for k, v in synth["legend"].items())
                st.caption("The Synthesizer saw these answers only as letters, so it couldn't favor a provider. Key — " + leg)
            _latest_synth_answer = next(
                (m["content"] for m in reversed(synth["thread"])
                 if m["role"] == "assistant"), "")
            render_read_aloud_controls("Synthesizer", _latest_synth_answer, "Synthesizer")

            st.divider()
            _bsid = st.session_state.get("session_id")
            if _bsid:
                _bp_spacer, _bp_pin, _bp_del = st.columns([6, 0.7, 0.7])
                _bpinned = any(h.get("ts") == _bsid and h.get("mode", "synthesis") == "synthesis"
                               and h.get("pinned") for h in _hist_all)
                with _bp_pin:
                    st.button("📌", key="pin_chat_bottom", use_container_width=True,
                              type="primary" if _bpinned else "secondary",
                              help="Unpin this chat" if _bpinned else "Pin this chat",
                              on_click=toggle_pin_session, args=(_bsid, "synthesis"))
                with _bp_del:
                    with st.popover("🗑", help="Delete this chat"):
                        st.caption("Delete this chat? Can't be undone.")
                        st.button("Delete", key="del_chat_bottom", type="primary",
                                  use_container_width=True, on_click=delete_session,
                                  args=(_bsid, "synthesis"))
            st.markdown("**\U0001F4AC Continue the conversation**")
            if _synthesis_run_active:
                render_pending_question()
                bg_refresh(show_progress=True)
                _progress_refresh_rendered = True
                render_stop_run_button("stop_run_main_synthesis")
                fu, fu_files = None, None
            else:
                _show_rejected_followup("fu_synth")
                fu, fu_files = follow_up_form("fu_synth", "Your next question",
                                              settings_kind="synth")
            if fu:
                error = start_council_followup("synthesis", fu, fu_files, "fu_synth")
                if error:
                    _keep_rejected_followup("fu_synth", fu, fu_files, error)
                else:
                    for _n in ["Synthesizer"] + EVAL_MEMO_AUDIO_NAMES:
                        st.session_state.pop(f"audio_{_n}", None)
                    st.rerun()

    else:  # ---------- inner view: The Sources ----------
        eval_threads = st.session_state.get("eval_threads", {})
        if not threads and not eval_threads:
            st.info("Ask a question above to see the source models' answers side by side here.")
        else:
            SRC_SUBVIEWS = ["Source models", "Evaluators"]
            src_sub = st.segmented_control("Sources sub-view", SRC_SUBVIEWS, key="src_subview",
                                           label_visibility="collapsed", required=True)
            src_sub = src_sub or SRC_SUBVIEWS[0]

            if src_sub == "Source models":
                if not threads:
                    st.info("No source answers yet for this run.")
                else:
                    names = list(threads.keys())
                    t1, t2 = st.columns([3, 1])
                    with t1:
                        if len(names) > 5:
                            st.session_state["view_mode"] = "Tabs (full width)"
                            sub = "Tabs (full width)"
                            st.caption(f"{len(names)} source answers — shown as full-width "
                                       "tabs (side-by-side columns are offered at 5 or fewer).")
                        else:
                            sub = st.segmented_control("Layout",
                                                       ["Columns (side-by-side)", "Tabs (full width)"],
                                                       key="view_mode", label_visibility="collapsed",
                                                       required=True)
                            sub = sub or "Tabs (full width)"
                    with t2:
                        st.download_button("⬇️ Download all", build_synthesis_download(),
                                           file_name="conclave_synthesis.txt", use_container_width=True)
                    st.caption(reasoning_coverage_caption(names))

                    def render_model(n, with_header):
                        th = threads[n]
                        if with_header: st.subheader(f"{ICON.get(n,'')} {n}")
                        for i, m in enumerate(th):
                            if (i == 0 and m.get("role") == "user" and
                                    m.get("turn_id", 0) == 0):
                                # The original prompt is identical across sources; later
                                # turns stay visible even when a provider joined late.
                                continue
                            if m["role"] == "assistant":
                                with st.chat_message("assistant", avatar=ICON.get(n)):
                                    st.caption(meta_caption(m))
                                    st.markdown(m["content"])
                                    render_reasoning(m, n)
                            else:
                                with st.chat_message("user"):
                                    st.markdown(user_text_of(m))
                                    render_user_files(m)
                        _latest_source_answer = next(
                            (m["content"] for m in reversed(th) if m["role"] == "assistant"), "")
                        render_read_aloud_controls(n, _latest_source_answer, n)
                        with st.expander(f"\U0001F4AC Chat just with {n}", expanded=False):
                            render_private_source_branch(
                                n, _latest_source_answer, synth, "synthesis")

                    if sub.startswith("Columns"):
                        for col, n in zip(st.columns(len(names)), names):
                            with col: render_model(n, True)
                    else:
                        for tab, n in zip(st.tabs([f"{ICON.get(n,'')} {n}" for n in names]), names):
                            with tab: render_model(n, False)

            else:  # ---------- inner sub-view: Evaluators ----------
                if not eval_threads:
                    st.info("When adjudication is on, the Synthesizer's and Adjudicator's own independent first-take "
                            "answers will appear here once a council turn finishes — each "
                            "writes its own answer before seeing any source or the other, and "
                            "it's revealed here only after the final synthesized answer is done.")
                else:
                    eval_names = [n for n in ("Synthesizer", "Adjudicator") if n in eval_threads]
                    st.caption("Each one's own independent first-take answer, written before "
                              "seeing any source or the other — revealed here only after "
                              "synthesis completes, so it never influenced the blind evaluation.")
                    esub = st.segmented_control("Evaluators layout",
                                                ["Columns (side-by-side)", "Tabs (full width)"],
                                                key="eval_view_mode", label_visibility="collapsed",
                                                required=True)
                    esub = esub or "Columns (side-by-side)"

                    def render_evaluator_memo(n, with_header):
                        th = eval_threads.get(n, [])
                        if with_header: st.subheader(f"{ICON.get(n,'')} {n}")
                        for m in th:
                            with st.chat_message("assistant", avatar=ICON.get(n)):
                                st.caption(meta_caption(m))
                                st.markdown(m["content"])
                                render_reasoning(m, st.session_state.get(
                                    "k_syn_provider" if n == "Synthesizer" else "k_adj_provider", ""))
                        _latest = th[-1]["content"] if th else ""
                        render_read_aloud_controls(n, _latest, f"eval_{n}",
                                                   cache_key=f"{n}_memo")

                    if esub.startswith("Columns"):
                        for col, n in zip(st.columns(len(eval_names)), eval_names):
                            with col: render_evaluator_memo(n, True)
                    else:
                        for tab, n in zip(st.tabs([f"{ICON.get(n,'')} {n}" for n in eval_names]),
                                          eval_names):
                            with tab: render_evaluator_memo(n, False)

    if _synthesis_turn_exists:
        st.divider()
        st.segmented_control("Synthesis view", SP_VIEWS, key="sp_view",
                             label_visibility="collapsed", required=True)
    render_session_spend("synthesis")

elif view == VIEW_RES:
    st.markdown("#### \U0001F52C Research Synthesizer")
    RES_UP_KEY = f"up_res_{st.session_state['nonce_res']}"
    RES_MIC_KEY = f"mic_res_{st.session_state['nonce_res_mic']}"
    _research_turn_exists = bool(
        st.session_state.get("res_threads") or st.session_state.get("research"))
    _research_run_active = bool(
        bg_active() and (_research_job := _bg_get(st.session_state.get("bg_job_id")))
        and _research_job.get("plan", {}).get("mode") == "research"
    )
    _show_research_composer = not (_research_turn_exists or _research_run_active)
    run_res = False
    res_prompt = st.session_state.get("res_prompt_text", "")
    if _show_research_composer:
        res_prompt = st.text_area(
            "Your research question:", height=110, key="res_prompt_text",
            placeholder="Each source answers, then the evaluator compares their answers…  (click Run)")
        _rn = len(stage_uploads(RES_UP_KEY, "_staged_res"))
        rr1, rr2, rr3, rr4 = st.columns([1.45, 1.12, 3.1, 1.35], gap="small",
                                         vertical_alignment="center")
        with rr1:
            run_res = st.button("\U0001F52C Run", type="primary",
                                key="run_res_btn", use_container_width=True,
                                disabled=bg_active(),
                                help="A run is already in progress." if bg_active() else None)
        with rr2:
            with st.popover(f"\U0001F4CE Attach ({_rn})" if _rn else "\U0001F4CE Attach"):
                st.file_uploader("Files for this research question", type=ATTACH_TYPES,
                                 accept_multiple_files=True, key=RES_UP_KEY)
        with rr3:
            if st.button(evaluator_action_label("k_res", "Evaluator"),
                         use_container_width=True, key="res_dlg_btn",
                         help="Open Research evaluator settings"):
                research_settings_dialog()
        with rr4:
            audio_res = st.audio_input("Dictate", label_visibility="collapsed", key=RES_MIC_KEY)
            if audio_res is not None:
                with st.spinner("Transcribing…"):
                    _res_text, _res_err = transcribe(audio_res.getvalue())
                st.session_state["nonce_res_mic"] += 1
                if _res_err:
                    st.session_state["_dictate_res_err"] = _res_err
                else:
                    _res_cur = st.session_state.get("res_prompt_text", "")
                    _res_sep = "" if (not _res_cur or _res_cur.endswith((" ", "\n"))) else " "
                    st.session_state["_pending_res_prompt"] = _res_cur + _res_sep + (_res_text or "")
                st.rerun()

        _res_dictate_err = st.session_state.pop("_dictate_res_err", None)
        if _res_dictate_err:
            st.error(f"Dictation failed: {_res_dictate_err}")

    # -------- run handler (background, same engine as the Synthesis tab) --------
    if run_res and not bg_active():
        keys_now = load_keys()
        res_provider = st.session_state["k_res_provider"]
        srcs = [n for n in MODEL_ORDER if ENABLED[n]]
        srcs = [n for n in srcs if n != res_provider]
        if not res_prompt.strip():
            st.error("Type a research question first.")
        elif len(srcs) < 2:
            st.error("Research needs at least two independent sources to compare. Activate more "
                     "models; the selected evaluator always remains outside the source council.")
        elif not any(keys_now.values()):
            st.error("No keys found — open Settings, then API Keys.")
        elif len(keyed_sources(srcs, keys_now)[0]) < 2:
            st.error("Research needs at least two active source models with saved keys. "
                     "Add keys in Settings ▸ API keys.")
        else:
            srcs = keyed_sources(srcs, keys_now)[0]
            files_res = list(st.session_state.pop("_staged_res", None) or [])
            st.session_state["nonce_res"] += 1       # fresh (empty) uploader next run
            user_msg = {"role": "user", "content": res_prompt, "files": files_res}
            source_msg = {
                "role": "user",
                "content": build_source_turn_message(
                    res_prompt, continuation=False, research=True),
                "files": files_res,
            }
            source_jobs, src_meta = build_source_jobs(srcs, keys_now, source_msg)
            bg_start({
                "mode": "research",
                "continuation": False,
                "turn_id": 0,
                "prompt": res_prompt,
                "context": "",
                "user_msg": user_msg,
                "session_id": datetime.now().isoformat(timespec="microseconds"),
                "sources": source_jobs,
                "src_meta": src_meta,
                "phase2": build_eval_phase(
                    "k_res", "Researcher", kind="research", files=files_res,
                    min_good=2, keys_now=keys_now),       # nothing to compare with fewer than two
            })
            st.rerun()

    bg_flash("research")
    res_threads = st.session_state.get("res_threads", {})
    research    = st.session_state.get("research")
    _research_run_active = bool(
        bg_active() and (_bgr := _bg_get(st.session_state.get("bg_job_id")))
        and _bgr["plan"]["mode"] == "research"
    )
    if _research_run_active and not research:
        render_pending_question()
        bg_refresh(show_progress=True)
        _progress_refresh_rendered = True
        render_stop_run_button("stop_run_main_research")

    if not res_threads and not research:
        st.info("Ask a research question above. The sources answer it, then an evaluator compares them — where they agree, where they conflict, what they miss. It is separate from "
                "⭐ Synthesis and only evaluates answers created here.")
    def research_details():
        if not res_threads:
            return
        latest_turn = None
        if research:
            latest_turn = max(
                (m.get("turn_id") for m in research.get("thread", [])
                 if m.get("role") == "assistant" and isinstance(m.get("turn_id"), int)),
                default=None,
            )
        latest_messages = {}
        for n, th in res_threads.items():
            candidates = [x for x in th if x["role"] == "assistant"]
            if latest_turn is not None:
                candidates = [x for x in candidates if x.get("turn_id") == latest_turn]
            if candidates:
                latest_messages[n] = candidates[-1]

        with st.expander("\U0001F9E9 The source answers", expanded=False):
            for n, m in latest_messages.items():
                st.markdown(f"**{ICON.get(n,'')} {n}** — {meta_caption(m)}")
                st.markdown(m["content"])
                render_read_aloud_controls(n, m["content"], f"research_{n}")
                st.divider()
        if latest_messages:
            with st.expander("\U0001F4AC Private source conversations", expanded=False):
                tabs = st.tabs([f"{ICON.get(n, '')} {n}" for n in latest_messages])
                for tab, (n, m) in zip(tabs, latest_messages.items()):
                    with tab:
                        render_private_source_branch(
                            n, "" if m.get("err") else m["content"],
                            research, "research")

    if research:
        st.markdown(f"##### \U0001F52C Evaluation · {research['label']}")
        _research_turn_number = 0
        # Same fix as Synthesis above -- render the whole thread so the
        # initial research question stays visible above its own answer too.
        for m in research["thread"]:
            if m["role"] == "assistant":
                _research_turn_number += 1
                render_response_scroll_anchor("research", m.get("turn_id"))
                with st.chat_message("assistant"):
                    st.caption(meta_caption(m))
                    render_synthesized_content(m["content"])
                    render_fresh_sources(m, f"r{_research_turn_number}")
                    render_turn_cost_breakdown(
                        res_threads, m, "Researcher",
                        _research_turn_number)
            else:
                with st.chat_message("user"):
                    st.markdown(user_text_of(m))
                    render_user_files(m)
        render_conversation_costs(
            res_threads, research, "Researcher",
            "\U0001F4CA Research stats & true cost")
        research_details()
        if research.get("legend"):
            leg = "  ".join(f"**{k}** = {ICON.get(v,'')} {v}" for k, v in research["legend"].items())
            st.caption("Evaluated blind (anonymized). Key — " + leg)
        _latest_research_answer = next(
            (m["content"] for m in reversed(research["thread"])
             if m["role"] == "assistant"), "")
        render_read_aloud_controls("Researcher", _latest_research_answer, "Researcher")
        st.download_button("⬇️ Download research report", build_research_download(),
                           file_name="conclave_research.txt")
        st.markdown("**\U0001F4AC Drill deeper with the council** — every follow-up goes to "
                    "the active sources first, then the independent evaluator.")
        if _research_run_active:
            render_pending_question()
            bg_refresh(show_progress=True)
            _progress_refresh_rendered = True
            render_stop_run_button("stop_run_main_research")
            fu, fu_files = None, None
        else:
            _show_rejected_followup("fu_res")
            fu, fu_files = follow_up_form("fu_res", "Your research follow-up",
                                          settings_kind="research")
        if fu:
            error = start_council_followup("research", fu, fu_files, "fu_res")
            if error:
                _keep_rejected_followup("fu_res", fu, fu_files, error)
            else:
                st.rerun()
    elif res_threads:
        research_details()
    render_session_spend("research")

# ============================================================
#  VIEW: ▦ Projects (planned workspace)
# ============================================================
elif view == VIEW_COUNCIL:
    render_council_page()

elif view == VIEW_PROJECTS:
    render_back_button("back_from_projects")
    st.markdown("#### ▦ Projects")
    st.info("Project workspaces are planned and not yet active.")
    st.markdown(
        "Projects will keep related Conclave chats, source material, decisions, and "
        "deliverables together under one persistent objective. Nothing here is "
        "usable yet — this page is a placeholder.")

# ============================================================
#  VIEW: ◉ Tasks & Lenses (planned workspace)
# ============================================================
elif view == VIEW_LENSES:
    render_back_button("back_from_lenses")
    st.markdown("#### ◉ Tasks & Lenses")
    st.info("Reusable task and expert-lens workflows are planned and not yet active.")
    st.markdown(
        "This workspace will shape how the council approaches a request independently "
        "from how much model effort it uses. Early candidates include decision review, "
        "critical analysis, creative development, technical analysis, and executive briefs.")

# ============================================================
#  VIEW: 📄 Document (placeholder)
# ============================================================
elif view == VIEW_DOC:
    render_back_button("back_from_document")
    st.markdown("#### \U0001F4C4 Document")
    st.info(
        "Coming later — not usable yet. Same council, but the output is a polished, "
        "professional document instead of a chat answer — and like Research, it will be its "
        "own chat:\n\n"
        "1. You describe the document you need — a thesis report, a résumé, a business "
        "proposal for the board, anything professional.\n"
        "2. The source models work the request.\n"
        "3. The ⭐ Synthesizer evaluates everything and **writes the document itself** — "
        "structured, complete, every accurate detail preserved.\n"
        "4. You export it as Word/PDF, ready to use.")

# ============================================================
#  VIEW: 🕘 Historical Chats
# ============================================================
elif view == VIEW_HISTORY:
    render_back_button("back_from_history")
    st.markdown("#### \U0001F558 Historical Chats")

    _hp1, _hp2, _hp3 = st.columns([4.5, 2.2, 2.1], gap="small")
    with _hp1:
        _page_query = st.text_input("Search chats", key="k_hist_page_search",
                                    placeholder="Search prompts and conversation text")
    with _hp2:
        st.selectbox("Chat type", ["All chats", "Synthesis", "Research"],
                     key="k_hist_page_mode")
    with _hp3:
        st.selectbox("Sort by", ["Newest first", "Oldest first", "A–Z"],
                     key="k_hist_page_sort")

    _history_archive = load_history_archive()
    st.toggle(f"Include archived chats ({len(_history_archive)})",
              key="k_hist_include_archive",
              help="The 50 newest chats stay in the fast index. Older chats remain intact "
                   "in a local archive and can still be searched, opened, or deleted here.")
    _history_source = list(_hist_all)
    if st.session_state["k_hist_include_archive"]:
        recent_keys = {_history_key(entry) for entry in _history_source}
        _history_source.extend(entry for entry in _history_archive
                               if _history_key(entry) not in recent_keys)

    _history_page = filtered_history(
        _history_source, _page_query.strip(), st.session_state["k_hist_page_sort"],
        st.session_state["k_hist_page_mode"],
    )
    _synth_count = sum(entry.get("mode", "synthesis") != "research"
                       for entry in _history_source)
    _research_count = len(_history_source) - _synth_count
    st.caption(f"{len(_history_page)} shown · {_synth_count} synthesis · "
               f"{_research_count} research · {len(_history_source)} loaded")

    if not _history_page:
        st.info("No historical chats match these filters." if _history_source else
                "Your first completed Synthesis or Research run will appear here.")
    _history_page = ([e for e in _history_page if e.get("pinned")] +
                     [e for e in _history_page if not e.get("pinned")])
    for _i, _entry in enumerate(_history_page):
        _mode = _entry.get("mode", "synthesis")
        _mode_label = "Research" if _mode == "research" else "Synthesis"
        _is_pinned = bool(_entry.get("pinned"))
        _raw_prompt = " ".join(str(_entry.get("prompt", "") or "").split())
        _display = chat_display_title(_entry)
        _topic_icon = chat_topic_icon(f"{_display} {_raw_prompt}", _mode)
        _title = _display[:110] + ("…" if len(_display) > 110 else "")
        with st.container(border=True):
            _hi1, _hi2 = st.columns([7, 2], gap="small")
            with _hi1:
                _pin_badge = "\U0001F4CC " if _is_pinned else ""
                st.markdown(f"**{_pin_badge}{_topic_icon} {_title or '(Untitled chat)'}**")
                st.caption(f"{_mode_label} · {history_display_date(_entry.get('ts'))} · "
                           f"{history_entry_meta(_entry)}")
                if _raw_prompt and _raw_prompt != _display:
                    st.caption("Q: " + _raw_prompt[:330] + ("…" if len(_raw_prompt) > 330 else ""))
            with _hi2:
                st.button("Open chat", key=f"histpage_open_{_i}", type="primary",
                          use_container_width=True, on_click=load_session_into_state,
                          args=(_entry,))
                st.button("Unpin" if _is_pinned else "\U0001F4CC Pin",
                          key=f"histpage_pin_{_i}", use_container_width=True,
                          on_click=toggle_pin_session,
                          args=(_entry["ts"], _entry.get("mode", "synthesis")))
                with st.popover("Delete", use_container_width=True):
                    st.caption("Delete this saved chat? This cannot be undone.")
                    st.button("Delete permanently", key=f"histpage_delete_{_i}",
                              type="primary", use_container_width=True,
                              on_click=delete_session,
                              args=(_entry["ts"], _entry.get("mode", "synthesis")))

# ============================================================
#  VIEW: Settings
# ============================================================
elif view == VIEW_SETTINGS:
    render_back_button("back_from_settings")
    st.markdown("#### ⚙ Settings & API Keys")
    st.caption("Local Conclave configuration and storage. Provider billing remains in each "
               "provider's secure console.")

    st.markdown("##### General")
    st.selectbox(
        "Council context detail",
        ["Automatic (run preset)", "Lean", "Expanded"],
        key="k_context_mode",
        help="How much of the earlier conversation is re-sent to the models with each follow-up — more means better memory but higher cost. Your full "
             "chat is always saved on this computer either way.",
    )
    st.caption("Automatic follows the run preset (more at Thorough and Deep). Lean re-sends the least "
               "(cheapest); Expanded keeps more recent detail. The key facts established so far are "
               "kept in every mode.")
    st.selectbox("Default source-answer layout",
                 ["Columns (side-by-side)", "Tabs (full width)"], key="view_mode",
                 help="Tabs (the default) give each answer the full width. Columns are only "
                      "offered when a run has 5 or fewer source answers — beyond that the "
                      "Sources view always uses tabs.")
    st.selectbox("Page width", list(PAGE_WIDTHS.keys()), key="k_page_width",
                 help="Controls how wide the main content area (chat, answers, composer) can "
                      "grow — Focused and Extra narrow pull the conversation into a tight centered "
                      "column, Narrow (the default) keeps reading lines short, Wide "
                      "uses nearly the full window on desktop and ultrawide "
                      "displays. Takes effect immediately; applies everywhere in the app.")
    _tx1, _tx2 = st.columns(2, gap="small")
    with _tx1:
        st.selectbox("Text size", list(TEXT_SIZES.keys()), key="k_text_size",
                     help="Scales the center area's text — the conversation, answers, "
                          "greeting, and composer — without touching the left panel.")
    with _tx2:
        st.selectbox("Sidebar list size", list(SIDEBAR_TEXT_SCALES.keys()), key="k_side_text",
                     help="Scales only the left panel's historical-chat titles and their "
                          "bold category labels — the rest of the panel stays put.")
    _ap1, _ap2 = st.columns(2, gap="small")
    with _ap1:
        st.selectbox("Appearance", ["System", "Light", "Dark"], key="k_theme_mode",
                     help="System switches to day at local sunrise and to night at sunset "
                          "(it asks the browser for your location; without it, day is 6:30 am–7 pm). "
                          "Light or Dark keeps one look all the time.")
    with _ap2:
        st.selectbox("Accent color", list(ACCENT_PRESETS.keys()), key="k_accent",
                     help="Swaps the action/accent shade used across buttons, edges, and "
                          "highlights. Conclave Teal is the original palette.")

    st.divider()
    st.markdown("##### API keys")
    render_api_keys_panel(expanded=False)

    st.divider()
    render_model_check()

    st.divider()
    st.markdown("##### History storage")
    _settings_archive = load_history_archive()
    _recent_bytes = os.path.getsize(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else 0
    _archive_bytes = (os.path.getsize(HISTORY_ARCHIVE_FILE)
                      if os.path.exists(HISTORY_ARCHIVE_FILE) else 0)
    _hs1, _hs2, _hs3 = st.columns(3, gap="small")
    _hs1.metric("Recent chats", len(_hist_all))
    _hs2.metric("Archived chats", len(_settings_archive))
    _hs3.metric("Local storage", f"{(_recent_bytes + _archive_bytes) / 1_048_576:.2f} MB")
    st.markdown(
        f"Conclave keeps the newest **{HISTORY_RECENT_LIMIT}** complete sessions in its fast "
        "index. Older sessions move intact into a separate local archive instead of being "
        "deleted. Turn on **Include archived chats** in Historical Chats to search or resume "
        "them. Attachment contents are never saved, and both history files stay on this computer.")
    _hb1, _hb2 = st.columns(2, gap="small")
    _hb1.download_button(
        "Download recent-history backup",
        data=json.dumps(_hist_all, indent=2, ensure_ascii=False),
        file_name="conclave_recent_history.json", mime="application/json",
        use_container_width=True,
    )
    _hb2.download_button(
        "Download archive backup",
        data=json.dumps(_settings_archive, indent=2, ensure_ascii=False),
        file_name="conclave_history_archive.json", mime="application/json",
        use_container_width=True, disabled=not _settings_archive,
    )

    st.divider()
    st.markdown("##### About")
    st.markdown("Product behavior, the Synthesizer method, current models, pricing estimates, "
                "and technical details live in About Conclave.")
    st.button("Open About Conclave", key="settings_open_about", type="primary",
              on_click=_open_about_dialog)

# ============================================================
#  SCROLLABLE DIALOG: ℹ️ About
# ============================================================
def render_about_content():
    st.markdown(
        "**One prompt. Multiple frontier AIs. One answer you can trust.**\n\n"
        "Conclave asks your question to a council of independent AI models at the same time — "
        "ChatGPT (OpenAI), Claude (Anthropic), Gemini (Google), Grok (xAI), Meta Muse (Wonder "
        "and Spark), DeepSeek, Kimi (Moonshot AI), Qwen (Alibaba Cloud), Xiaomi MiMo, Z.ai GLM, "
        "MiniMax, Mistral AI and NVIDIA Nemotron. A ⭐ **Synthesizer** then reads every answer "
        "without knowing who wrote it, checks each claim against the evidence (not by counting "
        "votes), keeps every detail that could change your understanding or decision, adds its "
        "own judgment, and writes one complete answer. The individual answers are the raw "
        "material; the synthesis is the product.\n\n"
        "Conclave runs on your own computer with your own API keys; your question goes only to the AI companies (and optional helpers) you switch on, and your chats stay on this machine.")
    st.markdown(
        "**Why Conclave is different**\n\n"
        "Most AI tools give you one model's single attempt. Conclave starts from the opposite "
        "idea: no single model should be the last word on a question that matters. Thirteen "
        "companies' models — trained on different data, with different blind spots — answer "
        "the same question without seeing each other. The Synthesizer reconciles them claim "
        "by claim, the way a careful editor reconciles conflicting sources. On the deeper "
        "presets a second, different-company **Adjudicator** then audits that answer against "
        "the same evidence before you see it, and the Synthesizer must address every problem "
        "it finds. You get one direct answer — with the sources, the audit, the reasoning the "
        "providers return, and the exact cost all one click away.")
    st.markdown(
        "**Conclave's workspaces are separate** — a chat in one never carries into another:\n"
        "- **⭐ Synthesis** — the main event. The council answers, the Synthesizer writes the "
        "answer, and then you keep talking with the Synthesizer alone (it remembers the whole "
        "conversation and costs a fraction of a full run). Switch on **🏛️ Full council this "
        "reply** whenever a follow-up deserves the whole council again.\n"
        "- **\U0001F52C Research** — the council answers a research question and an evaluator "
        "dissects their answers: where they agree, where they conflict and who is right, what "
        "only one found, and how confident to be.\n"
        "- **\U0001F4C4 Document**, **▦ Projects**, **◉ Tasks & Lenses** — coming next.")
    with st.expander("✨ What's new in this version", expanded=False):
        st.markdown(
            f"**Version {APP_VERSION}**\n"
            "- **Check models** (Settings) — asks each AI company's own model list which "
            "models Conclave uses have been retired and which new ones exist. Free, and it "
            "never changes anything on its own.\n"
            "- **Models without a key are skipped** instead of failing, and the Council page "
            "names them.\n"
            "- **Presets pick their own councils** — three models at Fast, five at Light "
            "(the default), eight at Balanced, eleven at Thorough and all thirteen at Deep. "
            "Switch any model on or off on the Council page.\n"
            "- **A first-use safety net** — if the Synthesizer or Adjudicator seat belongs to "
            "a company you have no key for, it moves to one you do.\n"
            "- **Kimi K3.1 Pro** replaces K3 as Kimi's flagship (cheaper and newer); "
            "dictation moves to OpenAI's newest transcription model.\n\n"
            "**Version 3.87**\n"
            "- **Gemini 4 Argon** — Google's newest and most capable model joins the "
            "council. It is Google's model on the Thorough and Deep presets; Gemini 3.1 Pro "
            "stays available.\n"
            "- **Web search fix** — Qwen's own search now uses a mode its 3.8 models "
            "support.\n"
            "- **Truer Claude costs** — Claude's discount for re-reading cached text is now "
            "priced per model, so revision costs are no longer overstated.\n\n"
            "**Version 3.86**\n"
            "- **Meta Muse Wonder** — Meta's most capable model (preview) joins the council. "
            "It is Meta's model on the Thorough and Deep presets; Muse Spark 1.3 handles the "
            "lighter ones.\n"
            "- **A cleaner, lighter look on every page** — thin hairline borders, slimmer "
            "accent edges, softer corners and calmer headings across chats, sources, "
            "follow-ups, the Council page, Settings, history and this window.\n"
            "- **A full audit of the app.** Among the fixes: an answer that finished while you "
            "were reading another chat now always lands in the chat you asked it in; a "
            "refused follow-up keeps your typed text and files; a damaged photo no longer "
            "breaks the message box; pinned chats stay pinned when reopened from the archive; "
            "Claude finishes a long web search instead of returning half-done; and costs are "
            "more complete (Gemini's thinking, calls that were billed but rejected, stopped "
            "runs).\n\n"
            "**Version 3.85**\n"
            "- **Claude Haiku 5.5** replaces Haiku 4.5 — a tenth of the price, with full "
            "effort and thinking settings.\n"
            "- **Mistral Large 4** (preview) becomes Mistral's model on Thorough and Deep.\n"
            "- **ChatGPT speeds**: Standard, Fast (2×), Flex (half price, slower) and "
            "Ultrafast (6×, Astra and Sol only).\n"
            "- **Qwen's own web search** is back to try (off by default).\n"
            "- Prices follow each model's long-prompt rates.\n\n"
            "**Version 3.84 and earlier this month**\n"
            "- A running **💵 Cost so far** line while a run is working.\n"
            "- **🔎 Tavily + Brave pages** — one shared web search per turn, handed to the "
            "models that can't search on their own (or that search slowly or expensively), "
            "with every page listed under the answer.\n"
            "- **Xiaomi MiMo** joined as the 13th council member; report cards on the Council "
            "page show each model's real speed, cost and failure rate.")
    with st.expander("\U0001F4D6 Read more — why synthesis beats any single model", expanded=False):
        st.markdown(
            "**Why several models beat one.** Every model has strengths, blind spots and the "
            "occasional made-up fact — and they differ. When several answer independently, "
            "where they agree is a strong signal, where they disagree is exactly where care is "
            "needed, and a detail only one of them found is often the valuable one.\n\n"
            "**How the Synthesizer works.** It sees the answers shuffled and labeled A, B, C… "
            "so it can't favor its own company's work (we saw that bias in testing). It checks "
            "each claim, drops what isn't supported, keeps anything that could change your "
            "understanding or decision even if only one answer had it, settles conflicts by "
            "evidence rather than majority, treats its own knowledge as one more input, and "
            "takes a position when you ask for one. On Thorough and Deep it works through an "
            "explicit claim-by-claim checklist. A short **How I weighed the sources** note sits "
            "collapsed under each answer.\n\n"
            "**How the Adjudicator works.** A model from a different company checks the "
            "Synthesizer's answer against the same evidence — unsupported claims, dropped or "
            "misread evidence, factual errors, contradictions — and reports findings only. If "
            "something matters, the Synthesizer must accept and fix it, reject it with "
            "evidence, qualify it, or flag it as unresolved, and the revised answer is the one "
            "you see (its reasoning is in a collapsed section under the answer). Adjudication "
            "is on by default at Thorough and Deep and can be switched on or off for any run.\n\n"
            "**What you can inspect.** Under every answer: a **Run report** (a timeline of "
            "every call, which sources shaped the answer, the audit result, how much source "
            "material survived), the **🔎 web pages** the models were given, each model's "
            "**🧠 reasoning** where its company returns it (Claude, DeepSeek, Qwen, Kimi, GLM, "
            "MiMo, MiniMax, NVIDIA, Mistral, Meta on web; OpenAI, Google and xAI share a "
            "short summary at most — nothing is ever invented), and a **turn cost** "
            "breakdown.\n\n"
            "**Speed.** The answer doesn't wait for the slowest model: once about three in four "
            "have answered (or the preset's time limit passes), the Synthesizer starts, and late "
            "answers still appear under Sources, labeled. Claude and ChatGPT stream the answer "
            "as it's written, and a live timeline and **💵 Cost so far** line show progress.\n\n"
            "**Web search.** Most models can search on their own (🌐). With a Tavily and/or "
            "Brave key, Conclave also runs **one shared search per turn on both engines** and "
            "hands the pages (🔎) to the models that can't search — DeepSeek, Mistral, NVIDIA — "
            "and to those whose own search is slow or costly — Grok, Kimi, MiniMax, Qwen. A "
            "model that can't search and isn't given the pages sits out while web is on. The "
            "**Web search** button sets all of this in one click.\n\n"
            "**Optional helpers.** A **Cerebras** key adds a fast live draft to read while the "
            "council works (labeled, never shown to the Synthesizer). A **TypeSafe** key adds "
            "**Jev**, which quickly judges how deep a question is, whether it needs current "
            "information (the 💡 button suggests a preset), and whether claims are backed by "
            "the sources — in Shadow mode it only records its view; in Active mode it can skip "
            "an audit that isn't needed. Both send your question to one more company.\n\n"
            "**Costs.** Presets set how hard every model works; each answer has its own cost "
            "breakdown, the conversation has a running total, and the Council page's **report "
            "cards** show each model's measured time, cost and failure rate (🐢 marks the slow "
            "ones). When the Adjudicator asks for a revision, the Synthesizer reuses its first "
            "conversation from the provider's **prompt cache**, which is faster and cheaper.")
    with st.expander("\U0001F527 Technical details & versions", expanded=False):
        st.markdown(f"**App version:** v{APP_VERSION}")
        st.markdown(
            "| Role | Model IDs |\n|---|---|\n"
            f"| ChatGPT family | " + " · ".join(f"`{v}`" for v in CHATGPT_MODELS.values()) + " |\n"
            f"| Claude family | " + " · ".join(f"`{v}`" for v in CLAUDE_MODELS.values()) + " |\n"
            f"| Gemini | " + " · ".join(f"`{v}`" for v in GEMINI_MODELS.values()) + " |\n"
            f"| Grok | `{MODEL_GROK}` |\n"
            f"| Meta | " + " · ".join(f"`{v}`" for v in META_MODELS.values()) + " |\n"
            f"| DeepSeek | " + " · ".join(f"`{v}`" for v in DEEPSEEK_MODELS.values()) + " |\n"
            f"| Kimi | " + " · ".join(f"`{v}`" for v in KIMI_MODELS.values()) + " |\n"
            f"| Qwen | " + " · ".join(f"`{v}`" for v in QWEN_MODELS.values()) + " |\n"
            f"| MiMo | " + " · ".join(f"`{v}`" for v in MIMO_MODELS.values()) + " |\n"
            f"| GLM | " + " · ".join(f"`{v}`" for v in GLM_MODELS.values()) + " |\n"
            f"| MiniMax | " + " · ".join(f"`{v}`" for v in MINIMAX_MODELS.values()) + " |\n"
            f"| Mistral | " + " · ".join(f"`{v}`" for v in MISTRAL_MODELS.values()) + " |\n"
            f"| NVIDIA | " + " · ".join(f"`{v}`" for v in NVIDIA_MODELS.values()) + " |\n"
            # DERIVED from DEFAULTS/CLAUDE_MODELS — these used to be hardcoded and
            # went stale the moment the default model changed.
            f"| Synthesizer defaults | initial: `{CLAUDE_MODELS[DEFAULTS['k_syn_cla_model']]}` "
            f"({DEFAULTS['k_syn_cla_eff']}) · follow-ups: "
            f"`{CLAUDE_MODELS[DEFAULTS['k_fu_cla_model']]}` ({DEFAULTS['k_fu_cla_eff']}) |\n"
            f"| Adjudication default | {'on at every preset' if ADJUDICATION_ON_FOR_TESTING else 'on at Thorough/Deep'} |\n"
            f"| Research evaluator default | `{CLAUDE_MODELS[DEFAULTS['k_res_cla_model']]}` "
            f"({DEFAULTS['k_res_cla_eff']}) |\n"
            f"| Helpers | live draft `{CEREBRAS_PREVIEW_MODEL}` (Cerebras) · decisions `{JEV_MODEL}` "
            f"(TypeSafe) · streaming: {', '.join(STREAM_PROVIDERS)} |\n"
            f"| Source start rule | {QUORUM_MODES[0].split(' (')[0].lower()} "
            f"({int(QUORUM_FRACTION * 100)}% + grace) or "
            + " / ".join(f"{k} {v}s" for k, v in SOURCE_DEADLINES.items() if k != "Custom")
            + " |")
        st.markdown("**Approximate pricing (USD per 1M tokens, in / out):**")
        st.markdown("\n".join(["| Model | In | Out |", "|---|---|---|"] +
                              [f"| `{mid}` | ${i:.2f} | ${o:.2f} |" for mid, (i, o) in PRICES.items()]))
        st.markdown("**Fast / Priority processing pricing (in / out):**")
        st.markdown("\n".join(["| Model | In | Out |", "|---|---|---|"] +
                              [f"| `{mid}` | ${i:.2f} | ${o:.2f} |"
                               for mid, (i, o) in PRIORITY_PRICES.items()]))
        st.markdown("**OpenAI Flex / Ultrafast pricing (in / out):**")
        st.markdown("\n".join(["| Model | Flex | Ultrafast |", "|---|---|---|"] +
                              [f"| `{mid}` | \\${FLEX_PRICES[mid][0]:.2f} / \\${FLEX_PRICES[mid][1]:.2f} | "
                               + (f"\\${ULTRAFAST_PRICES[mid][0]:.2f} / \\${ULTRAFAST_PRICES[mid][1]:.2f}"
                                  if mid in ULTRAFAST_PRICES else "— (runs at Fast)") + " |"
                               for mid in FLEX_PRICES]))
        st.caption("Standard is normal pay-as-you-go processing. OpenAI Fast is faster at 2× "
                   "the price; Flex is half price but slower (if it's busy, Conclave re-sends "
                   "at Standard and says so); Ultrafast is 6× (Astra and Sol only). MiniMax's "
                   "Priority tier is 1.5×. Some models charge more for very long prompts "
                   "(Haiku 5.5 above 100K input tokens, Gemini 3.1 Pro and Grok 4.7 above 200K, "
                   "GPT-6 above 272K); the estimates follow those rates.")
        def _pv(pkg):
            try: return _pkgmeta.version(pkg)
            except Exception: return "?"
        st.markdown(
            f"**Runtime:** Python {sys.version.split()[0]} · Streamlit {st.__version__} · "
            f"openai {_pv('openai')} · anthropic {_pv('anthropic')} · "
            f"google-genai {_pv('google-genai')}")
        st.caption("Web search: ChatGPT, Grok, Meta and MiniMax search through their Responses "
                   "APIs; Claude through its web_search tool (capped per question); Gemini "
                   "through Google Search;Kimi through Moonshot's search API; GLM and MiMo "
                   "through their hosted search tools; Qwen's 3.8 models through enable_search "
                   "(off by default while it is being tested). DeepSeek, Mistral and NVIDIA "
                   "have no search of their own here and read the shared Tavily + Brave pages "
                   "instead. Reasoning summaries are requested from every provider that "
                   "returns one.")
        st.caption("Storage and privacy: API keys are encrypted with your Windows login "
                   "(DPAPI) and never leave this computer except to their own provider. The 50 "
                   "newest chats stay in ai_history.json and older ones move intact to "
                   "ai_history_archive.json; attachment bytes are never saved. Both files use "
                   "atomic writes and locks, so two open copies of Conclave can't overwrite "
                   "each other. Dictation and read-aloud use OpenAI speech models.")
        st.caption("Open source: Conclave is released under the Apache License 2.0 "
                   "(github.com/btrischetta/conclave), created by Brian Trischetta. "
                   "Your keys, chats and local settings never leave this computer and are "
                   "never part of the project's files.")

    _howto = st.session_state.get("_about_focus") == "howto"
    if _howto:
        st.html('<div id="conclave-about-howto" style="height:0"></div>'
                '<script>(function(){var n=0;var iv=setInterval(function(){n++;'
                'var t=document.getElementById("conclave-about-howto");'
                'if(t&&n>=2){t.scrollIntoView({block:"start"});}'
                'if(n>=12){clearInterval(iv);}},250);})();</script>',
                unsafe_allow_javascript=True)
    _adj_default = ("on at every preset" if ADJUDICATION_ON_FOR_TESTING
                    else "on at Thorough and Deep, off at Fast, Light and Balanced")
    _testing_note = (" (while testing is on, every model starts switched on at every preset)"
                     if ALL_SOURCES_ON_FOR_TESTING else "")
    with st.expander("\U0001F4CB How to use it (step-by-step)", expanded=_howto):
        st.markdown(
        "1. **Choose how hard the council works.** Open the **🏛️ Council** page (the 🏛️ "
        "button in the message box, or the ⚙ Conclave menu at the bottom-left) and pick a "
        "preset: **Fast, Light, Balanced, Thorough or Deep**. One choice tunes every model "
        "at once, like a thinking dial. **Light** is the everyday default. Change any single "
        "setting and the preset shows **Custom**; click a preset to go back.\n"
        "2. **Choose who's in the council.** Each model has a card on the Council page with "
        "three switches on its title row: **🔎** (read the shared Tavily + Brave web pages), "
        "**🌐** (use its own web search) and **ON/OFF** (take part in this run). Open a card "
        f"for its model and effort settings{_testing_note}.\n"
        "3. **Choose the Synthesizer and Adjudicator.** At the top of the Council page, the "
        "**⭐ Synthesizer** card picks which company writes the answer (and every follow-up); "
        "the **⚖️ Adjudicator** card picks a *different* company to audit it. Whoever holds "
        "these seats sits out as a source (the Adjudicator only while adjudication is on), so no model judges its own answer. Adjudication is "
        f"{_adj_default}, and its card's ON/OFF switch overrides that for any run.\n"
        "4. **Ask.** Type (or tap the 🎤 to dictate), 📎 attach images, PDFs or text files, "
        "and press **Send** (Enter sends; Shift+Enter adds a line). The **🌐 Web** button "
        "turns web search on for the whole council in one click. While it runs you'll see "
        "each model working, a live timeline and **💵 Cost so far** — and you can keep using "
        "the app: open other pages or older chats, and the answer still lands in the chat "
        "you asked it in. **⏹ Stop** cancels what hasn't started (a request a company already "
        "accepted may still be billed).\n"
        "5. **Read the answer.** It opens with a title and the full answer. Below it: **How "
        "I weighed the sources**, the **⚖️ Adjudicator's review** (when it ran), the **🔎 web "
        "pages** the models read, the **Run report** and the **turn cost**. Switch to "
        "**🧩 Sources** at the top to read every model's own answer, or chat privately with "
        "any one of them — and use **Bring this insight into the next council turn** when "
        "you want something from that side chat included.\n"
        "6. **Keep the conversation going.** The box under the answer sends your follow-up to "
        "the Synthesizer alone — it remembers everything so far and costs a fraction of a "
        "full run. Switch on **🏛️ Full council this reply** when a follow-up deserves every "
        "model again. The caption under the box always says who will answer next.\n"
        "7. **Find old chats.** The side panel lists your chats by age, with **📌 Pinned** and "
        "**🔥 Most frequented** at the top; **🔍 Search chats** opens the full history with "
        "search, filters, pin and delete. **➕ New chat** starts fresh. The 📌 and 🗑 buttons "
        "beside Answer/Sources pin or delete the chat you're reading.\n"
        "8. **Keys, costs and settings.** **⚙ Conclave → Settings & API Keys** holds your keys "
        "(grouped into council models, web search engines and helpers, each with links to "
        "create a key and to billing), text size, page width, theme and history backups. "
        "Costs shown are estimates from each company's published prices; if a company "
        "reports you're out of credit, a red alert with its billing link appears on that "
        "model's card.")
@st.dialog("About Conclave", width="large", icon="ℹ️")
def about_dialog():
    render_about_content()

if st.session_state.pop("_show_about_dialog", False):
    about_dialog()
else:
    st.session_state.pop("_about_focus", None)


# Register the fallback only after the page has actually rendered. This covers
# navigation away, the Sources view, and a job finishing during a full rerun.
# Never leave both a progress timer and a sidebar timer mounted.
if st.session_state.get("bg_job_id") and not _progress_refresh_rendered:
    with st.sidebar:
        bg_refresh()
