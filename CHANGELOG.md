# Changelog

## Unreleased

- Status page: log lines show the date next to the time.
- Fix: excerpts failed with `'list' object has no attribute 'get'` when the model wrapped its answer in a list
  or the text contained square brackets and something after the JSON. Both are read correctly now.

## v1.2 – 2026-11-11

Hardening, tests and a clearer view of why an article was picked: status page behind a password, signals that
fade, topic feeds, push messages – and the 👍/👎 links are gone again.

**Learning**
- **👍/👎 links removed.** Readers that load the full article dropped them anyway. The negative side is covered
  by what was there from the start: an article shown in a digest and neither clicked nor starred counts as a
  negative example. Old votes in `feedback.jsonl` no longer count; old links answer with a short note (410).
  `VOTE_LINKS` is gone.
- **Signals fade** (`SIGNAL_HALF_LIFE_DAYS`, default 30): after one half-life a click, a star or a "shown, but
  left alone" counts half, after two a quarter – in the learned model and in the embedding pre-filter's
  favourites. `0` turns it off.
- `FEEDBACK_DAYS` now defaults to 7 instead of 3 – for people who leave digest entries unread as a to-do list.
- **Profile suggestion** (`python -m app profile-suggest`, also under Berichte on the status page): the summary
  model compares `interests.md` with what you clicked, starred and left alone and proposes what to add, sharpen
  or drop, with titles as evidence. Never applied automatically; not sent to a cloud provider with `PRIVACY=strict`.

**Output**
- **"Warum steht das hier?"** per article on the status page: selection path (regular, topic variety, filler,
  random pick), score with the LLM's reason, topic, pre-filter value and how the article got through (by value,
  seat for its topic, control sample), similarity to a starred article.
- **Topic feeds:** one feed per topic of your profile at `/<token>/topic/<topic>.xml`, listed on the status page.
  Older digests are filled in from the history. Topics are now also assigned without a pre-filter (from the
  bullets in `interests.md`), so `MAX_PER_TOPIC` works with `PREFILTER=none` too.
- **Push messages** via ntfy (`NTFY_URL`, `NTFY_TOKEN`) and/or Bark for iPhone (`BARK_URL`) when a digest is
  ready or a run fails; only the number of articles unless `NOTIFY_DETAILS=true`. `check` sends a test message. Bark messages carry the new project logo
  (`BARK_ICON`; loaded from the public repo, never from your secret feed path).
- nginx example: topic feeds passed through, `/v/` removed.
- Roadmap: Laya stays the core pre-filter, embeddings the light alternative; ONNX is an open item again.

**Security and privacy**
- Status page behind a password: the nginx example adds Basic Auth on top of the secret path – the page shows
  what you read, click and like, and the feed URL travels further than you'd think. README shows how to create
  the password file.
- Fix in the nginx example: the location for clicks and 👍/👎 needs quotes (the regex contains `{…}`) – without
  them nginx refused to load the config.
- Only `http(s)` links from feeds are used; `javascript:`/`data:` links and control characters are dropped
  before they reach the feed or a redirect.
- Clicks no longer count when a browser or link preview loads the link ahead of time
  (`Sec-Purpose: prefetch`), and link-preview fetchers like WhatsApp or `facebookexternalhit` count as bots.
- `.dockerignore`: the build context no longer includes `data/` (models, several GB), `.env` or `config/`.
- Laya dependencies pinned in `constraints-laya.txt` (PyTorch 2.15.0, transformers 5.22.1 …).

**Operation**
- `deploy/update.sh`: `git pull`, build while the old container keeps working, wait until no digest is running,
  then restart – a rebuild no longer kills a run.
- **Reports on the status page:** `learn`, `laya-report`, `privacy-report`, `opens-report` with one tap, with a
  copy button – no more copying long terminal output on a phone.
- Status page: the hint below the score chart no longer suggests a lower `MIN_SCORE` when the limit was the
  number of places (per digest, per feed, per topic).
- **`PRESET=small-server | laya | api-only`** fills in the matching defaults; anything set explicitly still wins.
  `.env.example` now starts with `PRESET=small-server`.

**Development**
- Tests (`python -m pytest tests`, 100+ tests, no models or network) and a GitHub Action running them on every push.

**LLM and selection**
- `REASONING_EFFORT=none`: if a model rejects `none`, `low` is tried before the parameter is dropped – Mistral
  Large 4 only knows low/medium/high and would otherwise think at full depth (its default). The provider's
  reason is logged; `check` leaves room for thinking models and warns about empty answers.
- Fix: the reasoning fallback crashed (`IndexError`) when several excerpts were written in parallel.
- README: "Small servers (little RAM)" – four levels from "everything via the API" to Laya `auto`.
- `check` reports Laya errors and frees the Laya model before the next tests.
- Rate limit: several simultaneous HTTP 429 answers from parallel requests now raise the learned delay
  once instead of doubling it per answer (20 → 40 → 65 s within a millisecond).
- Topic grouping: clearer instructions for headings (a common rubric name, never a row of loose keywords).
  Stays with the summary model – a small model (Mistral Small) wrote clumsy headings in a test.

## v1.1 – 2026-10-30

Selection quality and learning: rare topics get through, the pre-filter tunes itself, 👍/👎 per article.

- Topic grouping no longer fails on truncated answers: more room for the answer, a JSON retry with three times
  the token budget, and the start of a broken answer plus truncation (`finish_reason=length`) in the log.
- Seats per topic fixed: small topics are served first, and each topic's seats go to articles whose *main*
  topic it is – before, tech articles that also touched e.g. philosophy used up those seats.
- README: "Choosing a pre-filter" (none / embeddings / laya), shared self-tuning explained once, reader tips
  (don't let the reader replace digest entries with the full article), settings reference complete.
- **`PREFILTER=embeddings`** (new, not yet battle-tested): a light local pre-filter with the existing sentence embeddings instead of Laya –
  compares articles with the topics in `interests.md`, your examples, stars and 👍/👎. Seconds instead of hours,
  ~200 MB instead of ~2.5 GB RAM; seats per topic, control sample and both learning paths work unchanged.
- **Topic variety (`MAX_PER_TOPIC=3`):** the scoring LLM also assigns each article a topic; at most 3 per topic
  go in first, free seats go to other topics scoring up to one point below `MIN_SCORE`.
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
