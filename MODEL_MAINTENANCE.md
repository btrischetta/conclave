# Keeping Conclave's models current

AI companies release, rename and retire models every few weeks, and change
prices, reasoning options and web-search support along with them. None of that
reaches Conclave automatically: the model list, prices and settings live in
`conclave.py` and are updated by hand. This guide explains how.

## 1. Find out what changed

Open **Settings → Check models** and press **Check models now**. For every
provider you have a key for, Conclave asks that company's own model list (free)
and reports:

- **⚠️ not in the company's list** — a model Conclave offers may have been
  retired or renamed. Calls to it will start failing.
- **🆕 new** — models of the same family that Conclave doesn't offer yet.
- **couldn't check** — that company doesn't offer a model list, or the key
  can't read it. Check its documentation by hand.

The check never changes anything. Model lists don't include prices, reasoning
levels or web-search support, so every update must be confirmed against the
company's official documentation.

## 2. Confirm on the official pages

Always use the company's own documentation, never third-party listings. For
each change, confirm the exact model ID, input/output price per million tokens
(and any long-prompt or faster-processing rates), the reasoning/thinking
options it accepts, whether web search works on the API path Conclave uses, and
which inputs it reads (images, PDFs).

| Provider | Documentation |
|---|---|
| OpenAI (ChatGPT) | developers.openai.com — models, pricing, deprecations |
| Anthropic (Claude) | platform.claude.com/docs — models overview, pricing, deprecations |
| Google (Gemini) | ai.google.dev/gemini-api/docs — models, pricing, changelog |
| xAI (Grok) | docs.x.ai — models, release notes |
| Meta (Muse) | dev.meta.ai/docs — models, pricing |
| DeepSeek | api-docs.deepseek.com — pricing, updates |
| Moonshot (Kimi) | platform.kimi.ai/docs — pricing, changelog |
| Alibaba (Qwen) | alibabacloud.com/help/en/model-studio — models, pricing |
| Xiaomi (MiMo) | mimo.mi.com — docs, pricing |
| Z.ai (GLM) | docs.z.ai — pricing, release notes |
| MiniMax | platform.minimax.io/docs — models, pricing |
| Mistral | docs.mistral.ai — models, pricing, changelog |
| NVIDIA (Nemotron) | build.nvidia.com and docs.api.nvidia.com |

## 3. Edit `conclave.py`

All of these are plain tables near the top half of the file:

| What | Where |
|---|---|
| Models offered per provider | `CHATGPT_MODELS`, `CLAUDE_MODELS`, `GEMINI_MODELS`, `MODEL_GROK`, `META_MODELS`, `DEEPSEEK_MODELS`, `KIMI_MODELS`, `QWEN_MODELS`, `MIMO_MODELS`, `GLM_MODELS`, `MINIMAX_MODELS`, `MISTRAL_MODELS`, `NVIDIA_MODELS` |
| Prices (USD per 1M tokens, input/output) | `PRICES`, plus `LONG_PROMPT_PRICES`, `PRIORITY_PRICES`, `FLEX_PRICES`, `ULTRAFAST_PRICES`, `CACHED_INPUT_PRICES` |
| Reasoning/effort options | each provider's `*_EFFORT` / `*_THINK` table and the capability sets next to it (for example `CLAUDE_ALWAYS_THINK`, `GEMINI_MINIMAL_MODELS`, `QWEN_WEB_MODELS`) |
| What each preset uses | `PRESETS` (source models), `SYNTH_PRESETS` (Synthesizer and Adjudicator seats), `PRESET_SOURCES` (who joins at each preset), `DEFAULTS` |
| Retiring a model | move its label to `_RETIRED_MODEL_LABELS` (old label → replacement label) so saved settings keep working; **keep its `PRICES` row** so old chats still show costs |
| Model families for Check models | `MODEL_FAMILY_PREFIX` |

**Adding a model:** add it to its provider table and `PRICES`, then decide
whether any preset should use it. **Replacing a model:** add the new one, point
the presets at it, then retire the old label as above.

## 4. Run the tests

```bash
.venv\Scripts\python -m unittest discover -s tests -v
```

The settings-consistency tests catch the common mistakes: a preset pointing at
a model that no longer exists, a model without a price, or a retired label that
maps to nothing. Some tests pin specific preset choices on purpose — update
them in the same change when you deliberately change a preset.

## 5. Make one live check

The tests make no paid calls, so they can't prove a provider accepts the new
model ID. After updating, send one inexpensive question (Fast preset, web search
off) with that model switched on, and confirm its answer caption shows the new
model ID.
