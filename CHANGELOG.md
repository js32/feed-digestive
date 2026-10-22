# Changelog

## Unreleased

- **Laya tunes itself:** it learns from the LLM's scores which of its answers matter (`LEARN_FROM_LLM`),
  gets a random control sample from below the cut-off scored too (`PREFILTER_EXPLORE`), and every
  topic gets seats before the ranking (`PREFILTER_PER_TOPIC`) so rare interests survive.
- **`python -m app laya-questions`** writes a starter question catalogue from `interests.md`.
- Changing the wording of a Laya question now triggers re-scoring (question fingerprint per article);
  learning and `laya-report` only use answers to the current catalogue.
- Better default penalties: "news" covers war, attacks, disasters, crime and event dates; "promo" no
  longer hits articles about new products or models.

- **👍 mehr davon / 👎 weniger davon** under each article (`VOTE_LINKS`, on by default). 👍 counts
  more than a star (2.0 vs 1.5), 👎 is an equally strong negative example; the latest vote wins.
- **Weighted signals:** learning weights each example by its strongest signal – 👍 2.0, ★ 1.5, click 1.0,
  read 0.3. The status page shows all of them per article.
- **`READ_SIGNAL`** (off by default): read status of digest entries from Miniflux (`FEED_MODE=items`),
  bulk "mark all as read" ignored. Off because many people mark entries as read to tidy up.
- **`OPEN_PIXEL=log`** (experimental): measures whether the reader loads an invisible image only when
  an entry is opened; `opens-report` evaluates it. Does not count for learning.
- nginx example: new locations for `/v/` and `/p/`.
- Status page on phones: no more sideways scrolling; "similar to …" on its own shortened line.
- `LAYA_MODEL=auto`: articles are grouped by language before Laya runs, so each model is loaded once per
  run instead of switching (and reloading 1–2 GB) on every block of mixed articles.

## v1.0 – 2026-10-22

First stable release. Runs daily in production on a small VPS (2 vCPU, Miniflux, Mistral).

**Sources**
- Miniflux (native API) and every reader with the Google Reader API (FreshRSS, Inoreader,
  The Old Reader, BazQux, Tiny Tiny RSS / Nextcloud News via plugin)
- All unread articles as candidates (`SELECT_FROM=unread`) or only new ones; scores are cached,
  nothing is shown twice

**Selection**
- Local pre-filter with [Laya](https://laya.convaiinnovations.com/): per-article language routing
  (`LAYA_MODEL=auto`), editable question catalogue, news/PR/clickbait penalties, checkpoints and
  progress for long runs
- LLM scoring and excerpts with Anthropic, OpenAI, Mistral, OpenRouter, Gemini, Ollama or any
  OpenAI-compatible endpoint; separate providers for scoring and writing
- `ARTICLES=8-10`: minimum and maximum per digest, topped-up articles marked
- Local embeddings (optional): the same story from several sources only once, "similar to your
  starred article …"
- Topic sections, readable digest titles, "Warum lesen" first, 🎲 random pick against the filter bubble

**Learning**
- Clicks (via redirect on your own server, bots filtered) and stars from your reader train a
  small local model on top of Laya's answers; stars are picked up every 15 minutes
- `laya-report` shows which Laya questions help and where Laya and the LLM disagree

**Privacy**
- `PRIVACY=strict`, `SUMMARY_PROFILE=lean`, private `local-only` section in `interests.md`
- Local audit log of every LLM call (`LLM_AUDIT`) with `privacy-report` and a status-page card
- `REASONING_EFFORT=none` against paid thinking tokens

**Operation**
- One hardened Docker container (read-only, no capabilities, resource limits), state in plain files
- Status page with run progress, last digest, score distribution, service checks and live log
- Rate limits learned from HTTP 429 / `Retry-After`; `LAYA_THREADS` from the container limit
- Short `.env.example` with every option explained

**Not in v1.0:** Laya via ONNX Runtime lives on branch `onnx` until it has been tested on a real
checkpoint – see [ROADMAP.md](ROADMAP.md).
