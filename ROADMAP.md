# Roadmap

Ideas and planned work for feed-digestive. Checked items are done.

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

## Selection quality

- [x] Same story from several sources only once (embedding similarity, `DEDUP_SIMILARITY`)
- [x] "Similar to your starred article …" next to each suggestion
- [x] Scoring prompt with a calibrated scale and article length; "why read" without template phrases
- [x] `python -m app laya-report`: which Laya questions help, overlap, and where Laya and the LLM disagree
- [ ] Interest profile export: topic clusters + keywords from your stars as TOML/CSV
      (`python -m app profile`), optionally suggesting new `laya_questions.json` topics
- [ ] Embeddings as learning features, so learning from clicks works without Laya

## Performance

- [ ] Laya via ONNX Runtime with int8 quantisation for much faster CPU inference and lower RAM
- [ ] Smaller default question catalogue (fewer, merged topics)
- [ ] Load Laya in bfloat16 as a memory-saving option

## Learning

- [ ] Fine-tune Laya on your own clicks and stars once enough feedback has accumulated
- [ ] Optional: mark articles as read in the reader once they've been shown in a digest

## Sources

- [ ] Native Nextcloud News adapter (currently via Google Reader API plugin)
