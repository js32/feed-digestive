# feed-digestive

*Eats your RSS feeds, digests them overnight, serves you only the nutritious bits. Self-hosted, light-weight(s), learns your taste, no tracking but your own.*

Every night feed-digestive reads all new articles from your feed reader, picks the handful that
actually match your interests, writes a short excerpt for each and publishes the result as an
**Atom feed**, which you subscribe to in that same reader. 300 articles in, 10 worth reading out.

```
feed reader ──► dedupe ──► Laya (local, optional) ──► LLM scoring ──► top 10 ──► excerpts ──► feed.xml
   ▲                                                                                              │
   └──────────── stars & clicks ◄── learns your taste ◄── you read the digest ◄──────────────────┘
```

## What it does

- **Reads your feed reader:** Miniflux (native API) or anything that speaks the Google Reader
  API (FreshRSS, Inoreader, The Old Reader, BazQux, Miniflux, Tiny Tiny RSS / Nextcloud News via plugin).
- **Filters locally:** [Laya](https://laya.convaiinnovations.com/), a small decision model
  (~320M parameters) running on your CPU, pre-scores every article against a question catalogue,
  so nothing leaves your server for that step.
- **Scores and summarises with an LLM of your choice:** Anthropic, OpenAI, Mistral, OpenRouter,
  Gemini, Ollama (local) or any OpenAI-compatible endpoint. Scoring and excerpts can use
  different providers, e.g. a cheap model for scoring and a good one for writing.
- **Knows what you like:** an editable interest profile, examples from your reading history and
  the articles you starred.
- **Sorts locally:** optional sentence embeddings (fastembed, no PyTorch) spot the same story
  from several sources and tell you which of your starred articles a suggestion resembles.
- **Groups by topic:** the digest comes in sections like "Rechenzentren" or "Gesundheit".
- **Learns from you:** clicks in the digest and stars in your reader train a small model on top
  of Laya's answers. A 🎲 random pick per digest keeps the filter bubble in check.
- **Publishes a feed:** one daily digest entry, or one entry per article. Excerpts in the
  language of your choice, whatever language the original is in.
- **Shows what it's doing:** a status page with the current run, the latest digest, how scores
  are spread around your threshold, service checks and the live log.
- **Tells you what left your server:** every LLM call is logged locally; `privacy-report` and the
  status page show which articles and which parts of your profile went to which provider.
- **Stays yours:** one Docker container, state in plain JSON files, no database, no telemetry.

## Requirements

- Docker with Docker Compose
- A feed reader with API access (see [Sources](#sources))
- An API key for at least one LLM provider, or Ollama for fully local operation
- A reverse proxy with HTTPS (example config for nginx included) so your reader can fetch the feed
- With Laya: about 2 GB of free RAM during the nightly run and ~1.5 GB of disk for image and model

## Installation

```bash
git clone https://github.com/js32/feed-digestive.git
cd feed-digestive

cp .env.example .env                 # configure, see below
cp -r config.example config          # your interest profile
mkdir -p data && sudo chown 1000:1000 data
openssl rand -hex 16                 # use as FEED_TOKEN in .env
```

Minimum settings in `.env`:

```ini
SOURCE=miniflux                      # or greader
MINIFLUX_URL=https://miniflux.example.org
MINIFLUX_API_TOKEN=...               # Miniflux → Settings → API Keys

SCORING_PROVIDER=mistral             # anthropic | openai | mistral | openrouter | gemini | ollama | custom
SCORING_MODEL=mistral-small-latest
SUMMARY_PROVIDER=mistral
SUMMARY_MODEL=mistral-medium-latest
MISTRAL_API_KEY=...

PUBLIC_URL=https://digest.example.org
FEED_TOKEN=<random hex>
DIGEST_TIME=06:30
TZ=Europe/Berlin
OUTPUT_LANGUAGE=English
ARTICLES=8-10                        # at least 8, at most 10 articles per digest
# PRIVACY=strict                     # send as little about you as possible, see Privacy
```

Every other setting has a sensible default; `.env.example` lists them all with a short
explanation at the top and keeps the block to fill in short.

Then build, check and do a test run:

```bash
docker compose up -d --build
docker compose exec digest python -m app check     # reader, Laya and LLMs reachable?
docker compose exec digest python -m app dry-run   # full run, prints the result, saves nothing
docker compose exec digest python -m app run       # create a real digest now
```

The first `check` downloads the Laya model (~1 GB) into `data/hf`.

**Reverse proxy:** the container only listens on `127.0.0.1:8087`. Copy
`deploy/nginx.conf.example`, replace `digest.example.org` with your domain, get a certificate
(e.g. `certbot --nginx -d your.domain`) and reload nginx. Only the feed, the click redirects, the 👍/👎 links
are passed through, plus the status page (see below); everything else returns 404.

**Subscribe** to `https://your.domain/<FEED_TOKEN>/feed.xml` in your reader. The digest feed
itself is automatically excluded from the next digest.

## Configuration

All settings live in `.env`. The most important ones are explained below; every option is listed in the [settings reference](#settings-reference).

### Sources

| `SOURCE` | Works with | Credentials |
|---|---|---|
| `miniflux` | Miniflux | `MINIFLUX_URL`, `MINIFLUX_API_TOKEN` |
| `greader` | FreshRSS, Inoreader, The Old Reader, BazQux, Miniflux, TT-RSS / Nextcloud News (plugin) | `GREADER_URL`, `GREADER_USER`, `GREADER_PASSWORD` |

`GREADER_URL` examples:
- FreshRSS: `https://freshrss.example.org/api/greader.php`. Enable API access and set a separate
  API password in your profile.
- Miniflux: `https://miniflux.example.org`. Enable the Google Reader integration first.
- Inoreader: `https://www.inoreader.com`, plus `GREADER_APP_ID` / `GREADER_APP_KEY` from a
  developer account.

`GREADER_LABELS=Tech,Science` limits the digest to those folders. When switching sources, delete
`data/state.json`, since article IDs differ between sources.

### LLM providers

| Provider | `*_PROVIDER` | Key | Notes |
|---|---|---|---|
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | profile is prompt-cached across batches |
| OpenAI | `openai` | `OPENAI_API_KEY` | |
| Mistral | `mistral` | `MISTRAL_API_KEY` | EU provider |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | many models, one key |
| Google Gemini | `gemini` | `GEMINI_API_KEY` | via OpenAI-compatible endpoint |
| Ollama | `ollama` | – | local; `OLLAMA_BASE_URL` |
| Anything else | `custom` | `CUSTOM_API_KEY` | `CUSTOM_BASE_URL`, any OpenAI-compatible API |

Rough daily volume for 300 new articles without Laya: ~8 scoring calls of 10–15k input tokens
each, plus ~10 excerpt calls of 5–8k tokens. With Laya in front, only ~40 articles reach the
scoring model.

### Laya pre-filter

```
all new articles ──► Laya (local, question catalogue) ──► top 40 ──► LLM scoring ──► top 10 ──► excerpts
```

Enabled with `PREFILTER=laya` (the default image includes Laya; build with `WITH_LAYA=0` for a
slim image without it). The questions live in `config/laya_questions.json`. You don't have to
write them yourself:

```bash
docker compose exec digest python -m app laya-questions   # from config/interests.md
```

writes a starter catalogue from your interest profile (via the summary LLM; the `local-only` part
only goes to a local model). An existing catalogue is never overwritten – the suggestion lands in
`laya_questions.suggested.json`. The catalogue has three parts:

- `topics`: one yes/no question per interest, each with a weight (0–1)
- `depth`: a scale from "short news item" to "exceptional essay or major finding"
- `penalties`: reasons to drop an article (press release, clickbait, …)

Laya is a coarse sieve; the fine judgement comes from the LLM. Three things keep it from losing
good articles, without any hand-tuning:

- **It learns from the LLM** (`LEARN_FROM_LLM`): every day the LLM scores the articles Laya let
  through, and Laya's answers for those are known. A small regression learns which answers predict
  a good LLM score – vague questions get little weight, reliable ones a lot. From 30 scored articles
  on it replaces the starting formula step by step (90 % from 200 on). `python -m app learn` shows
  the learned weights.
- **A control sample** (`PREFILTER_EXPLORE=5`): a few random articles from *below* the cut-off go to
  the LLM too. That's what lets the model learn what it would otherwise throw away – and if the LLM
  likes one, it can still make the digest.
- **Seats per topic** (`PREFILTER_PER_TOPIC=2`): before sorting by score, every topic gets its best
  articles through, so rare interests aren't crowded out by frequent ones.

Starting formula: 10 × best weighted topic probability × (0.4 + 0.6 × depth) × (1 − 0.8 × penalty).
Changing the wording of a question makes Laya re-score all articles on the next run.
Narrow, concrete questions work better than broad ones. Questions are best written in English;
the articles themselves can be in any language (`LAYA_MODEL=auto` picks the English or multilingual checkpoint per article).

Two ways to run it:
- **Laya + LLM** (recommended): `PREFILTER=laya` plus a scoring provider.
- **Laya only**: `SCORING_PROVIDER=none`, `MIN_SCORE=4`. Scoring costs nothing; only the
  excerpts go to an LLM, or to Ollama for a fully offline setup.

### Embeddings: duplicates, topics, "similar to your stars"

Enabled with `EMBEDDINGS=true` (the default image includes it; build with `WITH_EMBEDDINGS=0` to
leave it out). A small multilingual model (~220 MB, downloaded to `data/fastembed/` on first use)
turns every candidate into a vector, locally on the CPU in a few seconds. That gives you:

- **One story, one entry:** if two articles tell the same story, e.g. an English and a German
  report on the same event, only the better scored one is kept (`DEDUP_SIMILARITY`).
- **"Ähnlich zu deinem Stern":** each article shows the starred article it most resembles, if any
  is close enough (`SIMILAR_MIN`). Your stars are compared locally and never sent anywhere for this.
  Every comparison is logged with its value (`Stern-Vergleich 0.61: …`), so you can tune the threshold.

Vectors are cached in `data/embeddings-*.npz`, so articles seen on earlier runs are not recomputed.
`python -m app check` shows how similar an English and a German version of the same headline are.

### Fully local with Ollama

```bash
docker compose --profile local up -d
docker compose exec ollama ollama pull qwen3.5:4b
```

```ini
SCORING_PROVIDER=ollama
SCORING_MODEL=qwen3.5:4b
OLLAMA_BASE_URL=http://ollama:11434/v1
SCORING_BATCH_SIZE=15
EXAMPLES_IN_PROMPT=40
```

On a CPU-only server a run can take an hour or more. It runs at night, so that's usually fine.

### Your interest profile

Your personal profile lives in `config/`, which is git-ignored. The repo only ships templates in
`config.example/`.

- `config/interests.md`: topics, priorities, what you don't want. Re-read on every run. Anything
  between `<!-- local-only -->` and `<!-- /local-only -->` is only given to local models (Ollama)
  and stripped before a prompt goes to a cloud provider.
- `config/examples.txt`: articles you saved in the past, as examples for the LLM. Generate from a
  read-later export (JSON list with `link` and `tags`):
  ```bash
  python3 tools/import_readlater.py ReadLater.json config/examples.txt 200
  ```
- `config/laya_questions.json`: the Laya question catalogue.
- Starred articles in your reader are added to the profile automatically (`USE_MINIFLUX_STARRED`).

### Which articles are considered

By default (`SELECT_FROM=unread`) every unread article in your reader is a candidate, newest
first, up to `MAX_ENTRIES` and no older than `UNREAD_MAX_AGE_DAYS`. Articles are scored only
once: Laya features and LLM scores are cached in `data/history.jsonl` and reused, and anything
that already appeared in a digest is never shown again. `SELECT_FROM=new` only looks at articles
that arrived since the last run.

### How many articles

Usually one setting is enough: `ARTICLES=8-10` means at least 8 and at most 10 articles per
digest, the random pick included. Under the hood, everything scoring at least `MIN_SCORE` (6/10)
goes in, up to the maximum (`TOP_N`). If that's fewer than the minimum (`MIN_ITEMS`), the digest
is topped up with the next best articles, but never below `FLOOR_SCORE`. To keep one topic from taking over, at most `MAX_PER_TOPIC` (3) articles per topic
go in first; the free seats then go to other topics that score at least `MIN_SCORE` − 1. Topped-up
articles are marked with ↓ and "aufgefüllt"; `MIN_ITEMS=0` never tops up. `FEED_MODE=items` turns every
article into its own feed entry instead of one daily digest.

With `GROUP_TOPICS=true` (default) the summary model sorts the selected articles into a few topic
sections in one short extra call (titles and "why read" only). Articles that fit no group go under
"Weitere Themen"; in `FEED_MODE=items` the topic is set as the entry's category.

## Learning from what you read

feed-digestive learns what you actually read, and keeps that knowledge on your server. Three
signals count, each article by its strongest one:

| Signal | How it's detected | Weight |
|---|---|---|
| 👍 **"mehr davon"** | tap on the link under each article (`VOTE_LINKS`) | 2.0 |
| ★ **Starred** | star in your reader, on the digest entry or the original article | 1.5 |
| ✓ **Clicked "→ Zum Artikel"** | redirect through your own server | 1.0 |
| 👁 **Read in your reader** | off by default (`READ_SIGNAL`), see below | 0.3 |
| 👎 **"weniger davon"** | tap on the link under each article – a clear *negative* example | −2.0 |

- **Clicks:** links in the digest point to `your.domain/<token>/r/<id>`, which records the click
  and redirects to the article. Crawlers, link previews and scripts don't count. The target
  site sees neither your secret path (`Referrer-Policy: no-referrer`) nor that you came from the digest.
- **👍/👎:** two small links next to "→ Zum Artikel". A tap opens a short "Notiert" page on your
  server (with a link to change your mind); the latest vote counts. 👎 is the only way to tell the
  app what you *don't* want – otherwise it can only guess from missing clicks.
- **Read (`READ_SIGNAL=true`, off by default):** with `FEED_MODE=items` the reader's read status of
  each digest entry is fetched from Miniflux. Only useful if you never mark entries as read to tidy
  up – a reader can't tell the app whether you actually read something. "Mark all as read" (4 or
  more within 15 seconds) is ignored. Not yet available with the Google Reader API.
- **Measuring "opened" (`OPEN_PIXEL=log`, experimental):** adds an invisible 1×1 image to each
  article and only *logs* when it's loaded – it does not count for learning. After a week,
  `python -m app opens-report` shows whether your reader loads it only when you open an entry, or
  for everything in advance (then it's useless as a signal).
- **Stars:** stars in your reader, on original articles or on digest entries, are collected on
  every run and every `STAR_POLL_MINUTES`.
- **Learning:** a small logistic regression on Laya's answers predicts what you'll be interested
  in, with each example weighted by its signal. It kicks in after 5 weighted positive and 10
  negative examples, and its share of the score grows with feedback up to 80%. A shown article only
  counts as "not interesting" after `FEEDBACK_DAYS` without any signal.
- **Filter bubble:** one slot per digest (`EXPLORE_SLOTS`) is a 🎲 random pick from just below
  the cut-off, so topics the model underrates still get a chance.

```bash
docker compose exec digest python -m app learn    # show what it has learned
docker compose exec digest python -m app laya-report   # which Laya questions actually help
docker compose exec digest python -m app opens-report   # result of the OPEN_PIXEL measurement
```

Data is kept in `data/history.jsonl` and `data/feedback.jsonl` for 180 days. Set
`TRACK_CLICKS=false` to learn from stars only.

## Settings reference

All settings go into `.env`. Docker Compose also reads that file for the container
settings at the end of this list. Changes to `.env` take effect after `docker compose up -d`.
Files in `config/` are re-read on every run and need no restart.

### Source

| Setting | Default | Description |
|---|---|---|
| `SOURCE` | `miniflux` | Where articles come from: `miniflux` (native API) or `greader` (Google Reader API). |
| `MINIFLUX_URL` | – | Base URL of your Miniflux instance, without `/v1`. |
| `MINIFLUX_API_TOKEN` | – | API key from Miniflux → Settings → API Keys. |
| `GREADER_URL` | – | Base URL of the Google Reader API, e.g. `https://freshrss.example.org/api/greader.php`. |
| `GREADER_USER` | – | User name for the Google Reader API. |
| `GREADER_PASSWORD` | – | API password (FreshRSS: the separate API password from your profile). |
| `GREADER_APP_ID`, `GREADER_APP_KEY` | – | Only for Inoreader: app credentials from a developer account. |
| `GREADER_LABELS` | – | Comma-separated folders/labels to read. Empty means all. |
| `CATEGORY_IDS` | – | Miniflux only: comma-separated category IDs to read. Empty means all. |
| `EXCLUDE_FEED_IDS` | – | Feeds to ignore, comma-separated. Miniflux: numeric IDs; Google Reader: `feed/https://…`. |

### Which articles are considered

| Setting | Default | Description |
|---|---|---|
| `SELECT_FROM` | `unread` | `unread`: every unread article is a candidate. `new`: only articles that arrived since the last run. |
| `MAX_ENTRIES` | `400` | Upper limit of articles looked at per run (newest first). |
| `UNREAD_MAX_AGE_DAYS` | `30` | With `unread`: ignore articles older than this. `0` = no limit. |
| `LOOKBACK_HOURS` | `24` | With `new`: how far back the very first run looks. |
| `ONLY_UNREAD` | `false` | With `new`: only take the unread ones among the new articles. |

Articles are scored once and the scores reused; articles that already appeared in a digest
are never shown again.

### Laya pre-filter (local)

| Setting | Default | Description |
|---|---|---|
| `PREFILTER` | `none` | `laya` scores all candidates locally first; only the best go to the LLM. `none` sends all candidates to the LLM. |
| `PREFILTER_KEEP` | `40` | How many of Laya's best articles are passed on to the LLM. |
| `PREFILTER_MIN` | `2.0` | Laya score (0–10) below which an article is dropped. At least 3 × `MIN_ITEMS` are always kept (up to `PREFILTER_KEEP`). |
| `PREFILTER_PER_TOPIC` | `2` | Seats per topic: each topic's best articles go to the LLM first, so rare interests aren't crowded out. `0` = off. |
| `PREFILTER_EXPLORE` | `5` | Random articles from below the cut-off that the LLM scores as well – training data for `LEARN_FROM_LLM` and a second chance for articles Laya misjudged. |
| `LEARN_FROM_LLM` | `true` | Learn Laya's weights from the LLM scores (from 30 scored articles on), replacing the hand-set weights and formula step by step. |
| `LAYA_MODEL` | `auto` | `auto`: English articles to the English checkpoint, others to the multilingual one. `multilingual` or `english`: one model for all (less RAM). |
| `LAYA_MAX_LEN` | `1024` | Maximum tokens per article Laya reads. Lower (e.g. 512) is faster and uses less RAM. |
| `LAYA_BATCH_SIZE` | `8` | Articles per forward pass. Lower (e.g. 2) uses less RAM. |
| `LAYA_THREADS` | `0` | CPU threads for Laya. `0` = the cores this container may use (from the Docker `cpus:` limit). |
| `LAYA_CHUNK` | `32` | Progress is logged and results are saved every N articles, so an aborted run loses nothing. |
| `LAYA_DEVICE` | – | `cpu`, `cuda` or `xpu`. Empty lets Laya choose. |

The questions Laya asks are in `config/laya_questions.json`.

### Embeddings (local)

| Setting | Default | Description |
|---|---|---|
| `EMBEDDINGS` | `false` | `true` enables duplicate detection and "similar to your stars". Needs an image built with `WITH_EMBEDDINGS=1`. |
| `EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Any [fastembed model](https://qdrant.github.io/fastembed/examples/Supported_Models/). The multilingual default matches stories across languages. |
| `DEDUP_SIMILARITY` | `0.75` | Similarity (0–1) from which two articles count as the same story. Lower catches more, but may merge different stories on the same subject. |
| `SIMILAR_MIN` | `0.55` | Show "Ähnlich zu deinem Stern" from this similarity on. `0` turns it off. |

### LLM: scoring and excerpts

| Setting | Default | Description |
|---|---|---|
| `SCORING_PROVIDER` | `anthropic` | Provider for scoring: `anthropic`, `openai`, `mistral`, `openrouter`, `gemini`, `ollama`, `custom`, or `none` (use the Laya score, requires `PREFILTER=laya`). |
| `SCORING_MODEL` | – | Model name for scoring, e.g. `mistral-small-latest`. |
| `SUMMARY_PROVIDER` | `anthropic` | Provider for the excerpts (same choices, except `none`). |
| `SUMMARY_MODEL` | – | Model name for the excerpts, e.g. `mistral-medium-latest`. |
| `SCORING_BATCH_SIZE` | `40` | Articles per scoring call. Use 10–15 for small local models. |
| `EXAMPLES_IN_PROMPT` | `120` | How many lines of `config/examples.txt` are sent along. `0` keeps your reading history private. |
| `SUMMARY_PROFILE` | `full` | What the excerpt model gets about you. `full`: interests, examples and starred titles. `lean`: only `interests.md` – fewer tokens and less personal data; scoring is unaffected. |
| `USE_MINIFLUX_STARRED` | `true` | Send the titles of your 30 latest starred articles along as an interest signal (works for both sources). Stars are still used for local learning when `false`. |
| `OUTPUT_LANGUAGE` | `Deutsch` | Language of titles, excerpts and "why read", regardless of the article's language. |
| `LLM_MIN_INTERVAL` | `0` | Fixed minimum seconds between two LLM calls. Usually not needed: after the first "too many requests" (HTTP 429) the app learns the required gap itself (from `Retry-After`, otherwise doubling from 5 s) and keeps it for the rest of the run. |
| `SUMMARY_PARALLEL` | `4` | Excerpts written in parallel. `1` for rate-limited free tiers. |
| `REASONING_EFFORT` | – | Sent as `reasoning_effort` to OpenAI-compatible APIs. `none` turns thinking off on hybrid models (Mistral Small 4, Medium 3.5), saving output tokens. Models that reject the parameter (e.g. Mistral Large 3) are retried once without it, so it is safe to set for both stages. Thinking blocks are always dropped from the answer. |
| `LLM_TIMEOUT` | `120` / `900` | Seconds per call. Default is 900 for `ollama` and `custom`, 120 for cloud APIs. |

API keys and endpoints (only the ones for your provider are needed):

| Setting | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key. |
| `OPENAI_API_KEY` | OpenAI API key. |
| `MISTRAL_API_KEY` | Mistral API key. |
| `OPENROUTER_API_KEY` | OpenRouter API key. |
| `GEMINI_API_KEY` | Google Gemini API key. |
| `OLLAMA_BASE_URL` | Ollama endpoint, default `http://host.docker.internal:11434/v1`. With the bundled container: `http://ollama:11434/v1`. |
| `OLLAMA_API_KEY` | Usually empty. |
| `CUSTOM_BASE_URL`, `CUSTOM_API_KEY` | Any OpenAI-compatible endpoint (LM Studio, vLLM, LiteLLM, …). |
| `<PROVIDER>_BASE_URL` | Overrides the endpoint of any provider, e.g. `MISTRAL_BASE_URL`, `ANTHROPIC_BASE_URL`. |

### Selection

| Setting | Default | Description |
|---|---|---|
| `ARTICLES` | – | Shortcut for `TOP_N` and `MIN_ITEMS`: `8-10` means at least 8, at most 10 articles per digest, random pick included; `10` means up to 10 every day. Wins over `TOP_N`/`MIN_ITEMS` when set. |
| `TOP_N` | `10` | Maximum number of articles per digest. |
| `MIN_SCORE` | `6` | Articles scoring at least this (0–10) always go in, up to `TOP_N`. |
| `MIN_ITEMS` | `5` | Minimum per digest: if fewer reach `MIN_SCORE`, the next best are added and marked as topped up. `0` never tops up. |
| `MAX_PER_TOPIC` | `3` | At most this many articles per topic at first; free seats go to other topics scoring up to one point below `MIN_SCORE`, and only then to more articles of the strong topics. The topic comes from the LLM while scoring (from the Laya topic list), else from Laya. `0` = off. |
| `FLOOR_SCORE` | `3` | Never top up with articles scoring below this; the digest gets shorter instead. |
| `GROUP_TOPICS` | `true` | Sort the digest into topic sections (one extra short call to the summary model). |
| `EXPLORE_SLOTS` | `1` | Slots for a 🎲 random pick from just below the cut-off, against the filter bubble. `0` turns it off. |
| `EXPLORE_MIN_REGULAR` | `4` | Only add the random pick once at least this many regular articles were selected. |

At most 3 articles per source feed are taken, unless that's needed to reach `MIN_ITEMS`.

### Learning

| Setting | Default | Description |
|---|---|---|
| `TRACK_CLICKS` | `true` | Route digest links through `/<token>/r/<id>` and count clicks. `false` uses direct links and learns from stars only. |
| `LEARN` | `true` | With Laya: learn weights from clicks and stars. |
| `FEEDBACK_DAYS` | `3` | A shown article counts as "not interesting" only after this many days without a click. |
| `PRIVACY` | `standard` | `strict` sends as little about you as possible: only `interests.md` (without its `local-only` part) goes to cloud providers – no examples (`EXAMPLES_IN_PROMPT=0`), no starred titles (`USE_MINIFLUX_STARRED=false`), lean excerpt prompts (`SUMMARY_PROFILE=lean`) – and clicks are stored with their date only. Stars and clicks still train the local model. Any of these set individually in `.env` wins. Scoring gets a bit less precise without examples. |
| `LLM_AUDIT` | `on` | Log every LLM call to `data/llm_audit.jsonl`: provider, model, purpose, size, article titles and which parts of your profile went along. `full` also stores the complete prompts, `off` disables it. Read it with `privacy-report` or on the status page. |
| `LLM_AUDIT_DAYS` | `30` | Delete audit entries older than this. |
| `STAR_POLL_MINUTES` | `15` | Pick up new stars from the feed reader this often, in addition to each run, so they show up on the status page soon. `0` = only during runs. |
| `VOTE_LINKS` | `true` | 👍 mehr davon / 👎 weniger davon under each article. Both are learning signals; 👎 is a strong negative example. |
| `READ_SIGNAL` | `false` | Count "read in the reader" (Miniflux, `FEED_MODE=items`) as a weak signal (0.3). Leave off if you mark entries as read to tidy up. |
| `OPEN_PIXEL` | `off` | `log` adds an invisible image per article and logs when it's loaded – a measurement only, see `opens-report`. Needs the `/p/` location in nginx. |

### Output feed

| Setting | Default | Description |
|---|---|---|
| `FEED_MODE` | `daily` | `daily`: one feed entry per digest with all articles. `items`: one feed entry per article – needed for `READ_SIGNAL`. Takes effect on the next start. |
| `FEED_TITLE` | `Mein Tages-Digest` | Title of the feed in your reader. |
| `KEEP_DAYS` | `30` | How long digests stay in the feed. |
| `PUBLIC_URL` | `http://localhost:8080` | Public base URL as your reader sees it, e.g. `https://digest.example.org`. Used for feed and click links. |
| `FEED_TOKEN` | – | Secret path segment: the feed lives at `PUBLIC_URL/FEED_TOKEN/feed.xml`. Generate with `openssl rand -hex 16`. |

### Schedule

| Setting | Default | Description |
|---|---|---|
| `DIGEST_TIME` | `06:30` | Time of the daily run (HH:MM). |
| `TZ` | `Europe/Berlin` | Time zone for the schedule and the dates in the digest. |
| `RUN_ON_START` | `false` | Also run once right after the container starts. |

### Container and paths

These are read by Docker Compose (from the same `.env`) or are internal paths you normally
don't touch.

| Setting | Default | Description |
|---|---|---|
| `WITH_LAYA` | `1` | Build the image with Laya (PyTorch CPU, ~1 GB larger). `0` for a slim image. Needs `docker compose build`. |
| `DIGEST_MEM_LIMIT` | `3g` | Memory limit for the digest container. |
| `DIGEST_CPUS` | `2` | CPU limit for the digest container. |
| `OLLAMA_MEM_LIMIT` | `6g` | Memory limit for the optional Ollama container. |
| `HTTP_PORT` | `8080` | Port inside the container. The host port (`127.0.0.1:8087`) is set in `docker-compose.yml`. |
| `DATA_DIR` | `/data` | State, digests, feed, learning logs and the Laya model (mounted from `./data`). |
| `CONFIG_DIR` | `/config` | Interest profile, examples and Laya questions (mounted read-only from `./config`). |

## Operation

| Command | What it does |
|---|---|
| `docker compose logs -f digest` | follow the logs |
| `docker compose exec digest python -m app check` | test reader, Laya and LLM connections |
| `docker compose exec digest python -m app dry-run` | full run without saving anything |
| `docker compose exec digest python -m app run` | create a digest now |
| `docker compose exec digest python -m app learn` | show learned weights |
| `docker compose exec digest python -m app privacy-report [days]` | what was sent to LLM providers in the last days (default 30): calls, characters, articles, and which parts of your profile |
| `docker compose exec digest python -m app opens-report [days]` | evaluate the `OPEN_PIXEL` measurement: does your reader load the image only when you open an entry? |
| `docker compose exec digest python -m app laya-report` | evaluate each Laya question against LLM scores, clicks and stars (hit rate, correlation, overlaps, disagreements) |

**Status page:** `https://your.domain/<FEED_TOKEN>/status`, like the feed only reachable via the
secret path. It shows:

- whether a run is in progress, in which stage and why it waits (e.g. a provider's rate limit); the
  browser tab title and icon show the state too
- the numbers of the current or last run: fetched, new, scored, picked
- the latest digest grouped by topic, with scores and which articles you clicked or starred, plus
  the best articles that just missed it
- how scores of the last 7 days are spread around `MIN_SCORE` and `FLOOR_SCORE`, as a hint for
  tuning the threshold
- the last runs with duration and result
- what left the server in the last 7 days (see `LLM_AUDIT`)
- the live log with filters and search

Click the status badge (or open `…/status#dienste`) to check each service: feed reader login, both
LLM providers (via their model list, which costs no tokens; skipped while a run is active), Laya,
embeddings, feed file age, disk space and errors in the last 24 hours.

Scheduled runs and runs started with `docker compose exec … run` both show up; logs are kept in
`data/status/` and survive restarts. Both kinds of run share a lock, so a manual run never
overlaps with the scheduled one.

If you set up nginx before this page existed, add the `/status` location from
`deploy/nginx.conf.example`. To keep the page private instead, leave nginx alone and open it
through an SSH tunnel: `ssh -N -L 8087:127.0.0.1:8087 user@server`, then
`http://localhost:8087/<FEED_TOKEN>/status`.

State lives in `data/` as plain files: `state.json` (where the last run stopped), `digests.json`,
`feed.xml`, the learning logs (`history.jsonl`, `feedback.jsonl`), the LLM audit log
(`llm_audit.jsonl`), the status page's log and run history (`status/`) and the Laya model. Deleting `state.json` makes the next run start
over with the last `LOOKBACK_HOURS`.

The container runs as an unprivileged user with a read-only filesystem, no capabilities and
memory/CPU limits (`DIGEST_MEM_LIMIT`, `DIGEST_CPUS`).

## Privacy

- **Stays on your server:** your reading history, clicks, stars, the learned model and,
  with Laya, the pre-filtering of all articles.
- **Goes to your LLM provider:** titles, snippets and text of the articles being scored or
  summarised, plus your interest profile. With Laya in front, that's ~40 instead of all
  articles. Use Ollama if nothing should leave your server.
- **`PRIVACY=strict`** cuts that down to the minimum: only `interests.md` goes along – no reading
  history examples, no starred titles, lean excerpt prompts – and clicks are stored by date only.
- **Private notes:** anything between `<!-- local-only -->` and `<!-- /local-only -->` in
  `interests.md` is never sent to a cloud provider.
- **You can check it:** every LLM call is logged locally (`LLM_AUDIT`); `privacy-report` and the
  status page show what went where. `REASONING_EFFORT=none` also avoids paying for "thinking"
  tokens on hybrid models.
- The feed URL contains a secret token. Anyone with the URL can read your digest, so treat it
  like a password.

## Roadmap

Planned work, especially on privacy and the digest layout, is tracked in [ROADMAP.md](ROADMAP.md).

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md). You may use, modify and share feed-digestive for any
noncommercial purpose: personal use, research, education, charitable and public organisations.
Commercial use, including use of parts of the code, is not permitted. For commercial licensing,
please get in touch.

This makes feed-digestive *source available*, not open source in the OSI sense.

Dependencies such as httpx, PyTorch and [Laya](https://laya.convaiinnovations.com/) (Apache 2.0)
are covered by their own licenses and are not part of this repository.
