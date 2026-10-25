# Changelog

## 3.88.0 — first public release (October 2026)

Conclave's first public release. Earlier versions were developed privately;
their history is not part of this repository.

**What's included**

- A council of 13 AI providers (Claude, ChatGPT, Gemini, Grok, Meta Muse,
  DeepSeek, Kimi, Qwen, Xiaomi MiMo, Z.ai GLM, MiniMax, Mistral, NVIDIA
  Nemotron), each with its native model, reasoning, speed and web-search
  settings.
- Blind, claim-by-claim synthesis into one answer; an optional cross-company
  Adjudicator audit with one controlled revision.
- Synthesizer-only follow-ups with conversation memory, and an opt-in full
  council for any reply.
- Five presets (Fast → Deep) plus Custom, with councils of 3, 5, 8, 11 and 13
  models; any model can be switched on or off, and models without a saved key
  are skipped.
- Each model's own web search, plus an optional shared search through Tavily
  and Brave.
- Images, PDFs and text attachments; dictation and read-aloud.
- Live progress with a running cost, per-answer cost breakdowns, run reports,
  returned reasoning where providers share it, and model report cards.
- A Research workspace for comparing the council's answers.
- **Check models**: asks each company's model list what has been retired or
  added.
- Local-only server, DPAPI-encrypted keys, no telemetry.
