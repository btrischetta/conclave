# Roadmap and open questions

Conclave works today, but several areas were still being tuned or designed
when it was released. They're listed here so anyone building on it knows what
is settled, what is a reasonable default still waiting for evidence, and what
was planned next. Contributions in any of these areas are welcome.

## 1. Council tuning: reasonable defaults, not yet proven

These choices are working defaults based on limited testing, not on measured
evidence across many questions.

- **Which models belong in each preset.** Fast starts with a small council and
  more models join toward Deep (`PRESET_SOURCES`). The original plan was to
  collect real runs, scoring each model on speed, cost and what it uniquely
  contributed to the final answer, and use that data to decide who earns a seat
  at each preset. The Council page's report cards gather the speed and cost
  half automatically; the quality half is still a manual review.
- **Effort levels per preset.** Each provider's reasoning/effort setting climbs
  from Fast to Deep, but the specific rungs were chosen by judgment, not by
  benchmark. In particular: whether the Deep Adjudicator needs maximum effort
  (it is slow and auditing rarely needs it), and whether heavier presets should
  pair the Adjudicator with a stronger model than its own ladder gives it.
- **The Synthesizer and Adjudicator "seat floor".** The judging seats never run
  a provider's cheapest model, because their output is the one nobody else
  checks. Whether that floor is set at the right level per provider is open.
- **When a revision triggers.** Any finding the Adjudicator marks as material
  triggers one revision. Early runs triggered a revision almost every time —
  whether that reflects real catches or an over-sensitive bar is undecided.
- **Jev in Active mode.** The optional Jev helper runs in Shadow mode (it only
  records its verdicts). The plan was to compare its verdicts with the
  Adjudicator's audits over a few weeks before letting it skip unneeded audits.
- **Web search per model.** Which models should use their own search and which
  should read the shared Tavily + Brave pages was set from a handful of runs.

## 2. Things never confirmed with a live call

The test suite is free and never calls a real provider, so these paths are
built to each provider's documentation but were not yet seen working live:

- Gemini 4 Argon and Meta Muse Wonder as sources and judges.
- Qwen's own web search in the US region, now using the `max` search strategy.
- Gemini Flash-Lite handing web-search calls to Gemini 3.8 Flash.
- NVIDIA's reasoning-budget setting placement, and the Nemotron Nano Omni
  (vision) model ID.
- Mistral Large 4's exact model ID (`mistral-large-4` vs `mistral-large-4-0`;
  Conclave retries with the second automatically).
- First web searches from Kimi and MiniMax, Claude Haiku 5.5 with thinking on,
  GLM 5.3 Flash with an image, and Mistral with a PDF.
- Prompt caching on the revision pass, and the shared Tavily + Brave search.

If you run one of these and it fails, the error under the answer usually says
exactly what the provider rejected — please open an issue with it.

## 3. Designed but held back

- **Citations.** Most providers can return the web pages behind each claim, and
  several offer search options (how much context to fetch, domain filters,
  recency). Showing citations raises open design questions: where they appear,
  the cost of feeding them to the Synthesizer, and keeping the blind
  (anonymized) evaluation intact.
- **Moving model data out of the code.** Models, prices and preset ladders live
  in `conclave.py`. Moving them into a single data file would make updates
  safer and let the app load community-maintained updates.
- **A "re-synthesize" button.** Re-run only the Synthesizer on the answers
  already collected — to recover a failed synthesis or get a second opinion
  without paying for the sources again.
- **Trimming stray narration.** Occasionally a Synthesizer writes a sentence
  about its process before the answer's heading; the idea is to move such text
  into the "How I weighed the sources" note automatically.
- **Read-aloud length.** Read-aloud currently covers about the first 4,000
  characters of an answer.
- **An evidence-researcher layer.** A dedicated search step that returns claims
  with citations, dates and conflicts, and feeds them into the Synthesizer's
  claim-by-claim check rather than adding another voice to the council.

## 4. Planned workspaces

The sidebar already shows these destinations; they are placeholders today.

- **Projects** — keep related chats, source material, decisions and deliverables
  together under one goal, with project-level defaults.
- **Document** — the same council, but the deliverable is a polished document
  (a report, résumé, proposal): the sources work the request, the Synthesizer
  writes the document, and it exports to Word or PDF.
- **Tasks & Lenses** — choose *how* the council approaches a request, separate
  from how much effort it spends: transparent, editable modes such as Decision,
  Critical Review, Creative Development, Technical Analysis and Executive Brief.
- **Research** — works today, but is less polished than Synthesis.

## 5. Platform

- **macOS and Linux.** Keys are protected with Windows DPAPI, and Conclave
  refuses to store keys unprotected elsewhere. Supporting the macOS Keychain and
  Linux secret stores would open it to other systems; the rest of the app is
  already portable.
- **Streamlit.** Conclave's look relies on custom CSS aimed at Streamlit's
  internal page structure, which can shift between Streamlit versions. A
  dedicated front end (talking to the same engine through a small local bridge)
  was considered as the long-term replacement.
- **A hosted, multi-user version** would need accounts, server-side key storage
  and per-user cost metering — none of which exists in this single-user,
  local-only design.

## 6. Known dates and model candidates (as of October 25, 2026)

**Dates that will need action:**

- **December 1, 2026** — Gemini 4 Argon's introductory price ends; update its
  `PRICES` row to $4 / $20.
- **End of December 2026** — Moonshot retires its K2 models, including Kimi
  K2.6 (Kimi's balanced choice here). K3.1 Pro already carries Kimi's heavier
  presets.
- **January 1, 2027** — Gemini 3.8 Flash's introductory price ends ($1.50 /
  $7.50).
- **January 6, 2027** — OpenAI shuts down `gpt-4o-mini-tts` and `tts-1`, the
  models behind read-aloud. OpenAI's named replacement works only through its
  Realtime API, so read-aloud needs to be rebuilt on that API (or another
  speech provider) before then, or it will stop working.
- **February 26, 2027** — OpenAI shuts down `gpt-4o-transcribe` and
  `whisper-1`. Dictation already uses the newer `gpt-transcribe`, with
  `gpt-4o-transcribe` as its backup until then.
- **Anthropic SDK 2.0** — released with a new API version that rejects some
  request fields. Conclave pins SDK 1.2.0; review the changes before accepting
  an automated upgrade.

**Newer models not added yet** (each needs a code change or a confirmed ID):

- MiniMax **M3.1 Flash** — cheaper than M3, but it always thinks and uses a
  different reasoning setting.
- NVIDIA **Nemotron 3.5 Super VL** (images and video) — its hosted model ID
  isn't published yet.
- Kimi **K3.1 Pro highspeed** — the same model, faster, at twice the price.
- DeepSeek **V4.1 Pro** — announced, not yet launched. DeepSeek's documentation
  also contradicts itself on whether the old V4 Pro still runs separately.
- DeepSeek's new **search API** — a possible third shared search engine
  alongside Tavily and Brave.

## 7. The longer-term idea

Conclave's core principle is that no single AI should be the last word: answers
should be checked across independent models, with the work shown. The
continuing-conversation design — one Synthesizer that remembers the whole
conversation and keeps weighing new information — was meant as the seed of
something larger: a persistent, personal assistant that grows with a person
over time and checks itself against independent sources.
