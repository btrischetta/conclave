# Conclave

**One question. Thirteen AI companies. One answer you can check.**

Conclave is a local, private desktop app that sends your question to a council
of independent AI models at the same time — Claude, ChatGPT, Gemini, Grok, Meta
Muse, DeepSeek, Kimi, Qwen, Xiaomi MiMo, Z.ai GLM, MiniMax, Mistral and NVIDIA
Nemotron — and turns their answers into one stronger answer.

Most AI tools give you one model's single attempt. Conclave starts from the
opposite idea: **no single model should be the last word on a question that
matters.**

## How it works

1. **The council answers independently.** Each model you switch on answers the
   same question without seeing the others.
2. **The Synthesizer weighs them blind.** A model you choose reads every answer
   shuffled and labeled A, B, C… so it can't favor its own company. It checks
   each claim against the evidence (not by counting votes), keeps any detail
   that could change your understanding or decision — even if only one answer
   had it — and writes one complete answer, with a short note on how it weighed
   the sources.
3. **An Adjudicator audits it (optional).** A model from a *different* company
   checks that answer against the same evidence before you see it. If it finds
   a real problem, the Synthesizer must fix it, reject it with evidence, qualify
   it, or flag it as unresolved.
4. **You can inspect everything.** Every answer shows each model's own answer,
   the web pages they read, the reasoning companies return, a run timeline, and
   the cost of every call.

Follow-up questions go to the Synthesizer alone by default (it remembers the
whole conversation and costs a fraction of a full run); you can call the full
council back for any reply.

## Features

- **13 providers, each with its real settings** — model, reasoning effort,
  thinking, processing speed and web search, per model.
- **Presets** — Fast, Light, Balanced, Thorough and Deep tune every model at
  once, and the council grows with them: 3 models at Fast (ChatGPT, Claude,
  Gemini), 5 at Light, the default (+ Grok, DeepSeek), 8 at Balanced (+ Meta,
  Qwen, Mistral), 11 at Thorough (+ Kimi, MiMo, GLM) and all 13 at Deep.
  These are starting points: switch any model on or off, or change any
  setting, on the Council page (that makes it Custom).
- **Web search** — each model's own search (🌐), plus an optional shared search
  (🔎) through Tavily and/or Brave for models that can't search on their own.
- **Attachments** — images, PDFs and text files, sent in each provider's native
  format where supported.
- **Cost transparency** — a live "cost so far" while a run works, a cost
  breakdown under every answer, and per-model report cards built from your own
  runs.
- **Check models** — one button asks each company's own model list which models
  Conclave uses have been retired and which new ones exist.
- **Research workspace (experimental)** — a second workspace where an evaluator
  compares the council's answers: agreement, conflicts, unique findings,
  confidence. It works but is less polished than the main Synthesis workspace.
- **Dictation and read-aloud** (OpenAI speech models), saved chat history with
  search, pinning and archiving, light/dark themes.

## Requirements

- **Windows 10 or 11.** API keys are encrypted with Windows' own data protection
  (DPAPI); on other systems Conclave refuses to save keys rather than store them
  unprotected.
- **Python 3.14.**
- **An API key from at least one provider** — two or more for a real council.
  Optional: Tavily or Brave (shared web search), Cerebras (a quick live draft
  while the council works), TypeSafe (Jev, quick decision checks).

## Install and run

```bash
git clone https://github.com/btrischetta/conclave.git
cd conclave
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Then double-click **`launch_conclave.bat`** (it opens Conclave in a Microsoft
Edge app window), or run:

```bash
.venv\Scripts\python -m streamlit run conclave.py
```

Open **⚙ Conclave → Settings & API Keys** to add your keys. Every key field has
links to that company's key page and billing page. Models without a saved key
are skipped automatically.

## What it costs

Conclave itself is free. You pay each AI company directly for what you use,
through your own accounts. A question can cost from a few cents (Fast, few
models, no web search) to a dollar or more (Thorough or Deep with web search
and adjudication). Every answer shows its estimated cost, and the estimates use
each company's published prices — they are estimates, not bills.

## Privacy and security

- Conclave runs on your computer and listens on `127.0.0.1` only — it is never
  reachable from your network.
- Your questions go only to the AI companies (and optional helpers) you switch
  on. Nothing is sent to the Conclave project; there is no telemetry.
- API keys are encrypted with your Windows login. Chats are saved in local JSON
  files next to the app; attachment contents are never saved.
- Keys and chat history are listed in `.gitignore` and are never part of the
  project's files.

See [SECURITY.md](SECURITY.md) to report a vulnerability.

## Keeping models current

AI companies release and retire models constantly, and Conclave's model list,
prices and settings are maintained by hand. Use **Settings → Check models** to
see what has changed, then follow [MODEL_MAINTENANCE.md](MODEL_MAINTENANCE.md)
to update. Corrections are welcome as pull requests.

## Tests

The test suite makes no paid calls and never touches real keys or chats:

```bash
.venv\Scripts\python -m unittest discover -s tests -v
```

## Project status

Conclave is a working personal project, maintained occasionally. Some defaults
are still being tuned, and several workspaces were planned but not built —
see [ROADMAP.md](ROADMAP.md) for what's settled, what's open, and what was
planned next. Issues and pull requests are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## License and credit

Created by **Brian Trischetta** ([@btrischetta](https://github.com/btrischetta)).
Released under the [Apache License 2.0](LICENSE). If you build on Conclave,
please keep the [NOTICE](NOTICE) file and credit the original project.
