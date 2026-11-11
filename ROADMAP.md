# Roadmap

Ideas and planned work for feed-digestive. Checked items are done; everything checked is part of
v1.0 unless noted. See [CHANGELOG.md](CHANGELOG.md) for releases.

## Privacy

What leaves your server today: titles and snippets of the articles Laya lets through, the full
text of the ~10 articles that get an excerpt, and **your interest profile with every LLM call**.
The goal is to send as little about *you* as possible.

- [x] Laya as a local pre-filter, so only a small share of articles reaches the LLM
- [x] `EXAMPLES_IN_PROMPT=0` keeps your reading history out of the prompt
- [x] `USE_MINIFLUX_STARRED=false` keeps starred titles out of the prompt (stars still count for local learning)
- [x] **Privacy preset** `PRIVACY=strict`: no examples, no starred titles, lean excerpt prompts, clicks stored per day
- [x] **Leaner profile for excerpts:** `SUMMARY_PROFILE=lean` sends only `interests.md` to the summary model
- [x] **Private section in `interests.md`:** a marked block (e.g. `<!-- local-only -->`) that Laya and local
      models may use but that is stripped before anything goes to a cloud provider
- [x] **Audit log:** optionally write every outgoing prompt to `data/llm_audit.jsonl`, plus
      `python -m app privacy-report` to show what left the server in the last N days
- [ ] **Less local tracking:** clicks per day is done (`PRIVACY=strict`). Shorter retention is open: clicks and
      the history log must be pruned together, and not below `UNREAD_MAX_AGE_DAYS`, or learning and de-duplication break
- [ ] **Fully local mode** documented end to end (Laya + Ollama), with realistic hardware requirements
- [ ] Notes per provider on data retention and training opt-out (Mistral, Anthropic, OpenAI, …)
- [x] `REASONING_EFFORT=none` turns off paid "thinking" tokens on hybrid models (Mistral Small 4, Medium 3.5)

## Digest layout

- [x] **"Warum lesen" first:** title → why it's worth reading → excerpt → source/reading time/score → link
- [ ] Table of contents with the one-line "why" next to each title
- [x] Group articles by topic (AI, health, web tech, …) instead of one flat list
- [ ] Configurable excerpt style: prose (default) or 3 bullet points
- [ ] Configurable excerpt length (`EXCERPT_SENTENCES`)
- [x] Mark the 🎲 random pick and topped-up articles (below `MIN_SCORE`) visibly as such

## Settings

- [x] `ARTICLES=8-10` instead of `TOP_N` + `MIN_ITEMS`; short `.env.example` with all options explained on top
- [x] Rate limits learned from HTTP 429 / `Retry-After` (no `LLM_MIN_INTERVAL` needed), `LAYA_THREADS` from the container limit

## Selection quality

- [x] Same story from several sources only once (embedding similarity, `DEDUP_SIMILARITY`)
- [x] "Similar to your starred article …" next to each suggestion
- [x] Scoring prompt with a calibrated scale and article length; "why read" without template phrases
- [x] `python -m app laya-report`: which Laya questions help, overlap, and where Laya and the LLM disagree
- [x] **"Warum steht das hier?"** per article on the status page: selection path, score and reason, topic,
      pre-filter value and how the article got through, similarity to a star
- [x] **Topic feeds:** one feed per topic of your profile (`/<token>/topic/<topic>.xml`)
- [x] **Profile suggestion** (`profile-suggest`): a readable proposal for `interests.md` from clicks and
      stars – never applied automatically
- [ ] Profile suggestion also proposes new `laya_questions.json` topics
- [x] Embeddings as learning features, so learning from clicks works without Laya (`PREFILTER=embeddings`)

## Performance

Laya stays the core pre-filter – a local model that answers readable questions about each article is
what sets this project apart. Embeddings stay as the light alternative for small servers. That makes
Laya's speed and memory use the open issue:

- [ ] **Laya via ONNX Runtime** (`LAYA_BACKEND=onnx`, same fp32 weights, identical answers, ~1.45× faster on CPU
      per Laya's authors) – implemented on branch `onnx`; the export failed on a full disk and has not been
      tested on a real checkpoint yet. Next step: export with enough free disk space, compare answers, merge
- [ ] int4 weight-only variant as an option, checked against your own data with `laya-export --check`
      (int8 dropped: Laya's authors measured a real accuracy loss)
- [x] Laya learns its weights from the LLM scores, plus control sample and seats per topic
- [x] `laya-questions`: starter catalogue generated from `interests.md`
- [ ] Re-score articles with feedback after a catalogue change (fetch them again from the reader), so learning from clicks doesn't restart
- [ ] Load Laya in bfloat16 as a memory-saving option (only pays off on CPUs with BF16 support)

## Learning

- [x] Weighted signals (★ 1.5, click 1.0, read 0.3); "shown, but left alone" as the negative example
- [x] **Signals fade** (`SIGNAL_HALF_LIFE_DAYS`, default 30): old clicks and stars count less and less
- [x] 👍/👎 links removed again (v1.1 had them): readers that load the full article dropped them, and
      "shown, but left alone" covers the negative side without a tap
- [x] Optional "read in the reader" signal (`READ_SIGNAL`, Miniflux)
- [ ] Decide on `OPEN_PIXEL` after a week of measurements: use it as a signal or drop it
- [ ] "Read" signal for Google Reader API sources

- [ ] Fine-tune Laya on your own clicks and stars once enough feedback has accumulated
- [ ] Optional: mark articles as read in the reader once they've been shown in a digest

## Operation and maintenance

- [x] Automated tests (pytest, no models or network needed) and GitHub Actions on every push
- [x] `deploy/update.sh`: update without killing a running digest
- [x] Status page behind a password (nginx Basic Auth) on top of the secret path
- [x] Reports (`learn`, `laya-report`, `privacy-report`, `opens-report`) on the status page
- [x] `PRESET=small-server | laya | api-only` instead of a dozen single settings
- [x] Pinned Laya dependencies (`constraints-laya.txt`), `.dockerignore` keeps `data/` out of the build
- [x] Push message via ntfy when a digest is ready or a run fails (`NTFY_URL`)
- [ ] After a few weeks of use: check in the "Gelernt" report whether learning from clicks and stars has a
      measurable effect; if not, replace it with the profile suggestion as the only path from signals to selection

## Sources

- [ ] Native Nextcloud News adapter (currently via Google Reader API plugin)
