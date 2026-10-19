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
  from several sources, group the digest by topic and tell you which of your starred articles a
  suggestion resembles.
- **Learns from you:** clicks in the digest and stars in your reader train a small model on top
  of Laya's answers. A 🎲 random pick per digest keeps the filter bubble in check.
- **Publishes a feed:** one daily digest entry, or one entry per article. Excerpts in the
  language of your choice, whatever language the original is in.
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
```

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
(e.g. `certbot --nginx -d your.domain`) and reload nginx. Only the feed and the click redirects
are passed through, everything else returns 404.

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
slim image without it). The questions live in `config/laya_questions.json`:

- `topics`: one yes/no question per interest, each with a weight (0–1)
- `depth`: a scale from "short news item" to "exceptional essay or major finding"
- `penalties`: reasons to drop an article (press release, clickbait, …)

Score = 10 × best weighted topic probability × (0.4 + 0.6 × depth) × (1 − 0.8 × penalty).
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
- **Topic sections:** similar articles are grouped under a heading. The headings are written by the
  summary model in one short extra call (titles only). Articles without a group go under
  "Weitere Themen".
- **"Ähnlich zu deinem Stern":** each article shows the starred article it most resembles, if any
  is close enough (`SIMILAR_MIN`). Your stars are compared locally and never sent anywhere for this.

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

- `config/interests.md`: topics, priorities, what you don't want. Re-read on every run.
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

Everything scoring at least `MIN_SCORE` goes in, up to `TOP_N`. If that's fewer than `MIN_ITEMS`,
the digest is topped up with the next best articles, but never below `FLOOR_SCORE`. Topped-up
articles are marked with ↓ and "aufgefüllt"; `MIN_ITEMS=0` never tops up. `FEED_MODE=items` turns every
article into its own feed entry instead of one daily digest.

## Learning from clicks and stars

feed-digestive learns what you actually read, and keeps that knowledge on your server.

- **Clicks:** links in the digest point to `your.domain/<token>/r/<id>`, which records the click
  and redirects to the article. Crawlers, link previews and scripts don't count. The target
  site sees neither your secret path (`Referrer-Policy: no-referrer`) nor that you came from the digest.
- **Stars:** stars in your reader, on original articles or on digest entries, are collected on
  every run.
- **Learning:** a small logistic regression on Laya's answers predicts what you'll click. It
  kicks in after 5 positive and 10 negative examples, and its share of the score grows with
  feedback up to 80%. A shown article only counts as "not interesting" after `FEEDBACK_DAYS`
  without a click.
- **Filter bubble:** one slot per digest (`EXPLORE_SLOTS`) is a 🎲 random pick from just below
  the cut-off, so topics the model underrates still get a chance.

```bash
docker compose exec digest python -m app learn    # show what it has learned
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
| `LAYA_MODEL` | `auto` | `auto`: English articles to the English checkpoint, others to the multilingual one. `multilingual` or `english`: one model for all (less RAM). |
| `LAYA_MAX_LEN` | `1024` | Maximum tokens per article Laya reads. Lower (e.g. 512) is faster and uses less RAM. |
| `LAYA_BATCH_SIZE` | `8` | Articles per forward pass. Lower (e.g. 2) uses less RAM. |
| `LAYA_THREADS` | `0` | CPU threads for Laya. `0` lets PyTorch decide; set to your core count if only one core is busy. |
| `LAYA_CHUNK` | `32` | Progress is logged and results are saved every N articles, so an aborted run loses nothing. |
| `LAYA_DEVICE` | – | `cpu`, `cuda` or `xpu`. Empty lets Laya choose. |

The questions Laya asks are in `config/laya_questions.json`.

### Embeddings (local)

| Setting | Default | Description |
|---|---|---|
| `EMBEDDINGS` | `false` | `true` enables duplicate detection, topic sections and "similar to your stars". Needs an image built with `WITH_EMBEDDINGS=1`. |
| `EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Any [fastembed model](https://qdrant.github.io/fastembed/examples/Supported_Models/). The multilingual default matches stories across languages. |
| `DEDUP_SIMILARITY` | `0.75` | Similarity (0–1) from which two articles count as the same story. Lower catches more, but may merge different stories on the same subject. |
| `GROUP_TOPICS` | `true` | Group the digest into topic sections. |
| `TOPIC_SIMILARITY` | `0.5` | How similar articles must be to share a section. Lower gives bigger, looser groups. |
| `SIMILAR_MIN` | `0.5` | Show "Ähnlich zu deinem Stern" from this similarity on. `0` turns it off. |

### LLM: scoring and excerpts

| Setting | Default | Description |
|---|---|---|
| `SCORING_PROVIDER` | `anthropic` | Provider for scoring: `anthropic`, `openai`, `mistral`, `openrouter`, `gemini`, `ollama`, `custom`, or `none` (use the Laya score, requires `PREFILTER=laya`). |
| `SCORING_MODEL` | – | Model name for scoring, e.g. `mistral-small-latest`. |
| `SUMMARY_PROVIDER` | `anthropic` | Provider for the excerpts (same choices, except `none`). |
| `SUMMARY_MODEL` | – | Model name for the excerpts, e.g. `mistral-medium-latest`. |
| `SCORING_BATCH_SIZE` | `40` | Articles per scoring call. Use 10–15 for small local models. |
| `EXAMPLES_IN_PROMPT` | `120` | How many lines of `config/examples.txt` are sent along. `0` keeps your reading history private. |
| `USE_MINIFLUX_STARRED` | `true` | Send the titles of your 30 latest starred articles along as an interest signal (works for both sources). Stars are still used for local learning when `false`. |
| `OUTPUT_LANGUAGE` | `Deutsch` | Language of titles, excerpts and "why read", regardless of the article's language. |
| `LLM_MIN_INTERVAL` | `0` | Minimum seconds between two LLM calls. For free tiers with ~2 requests/minute use `31`. |
| `SUMMARY_PARALLEL` | `4` | Excerpts written in parallel. `1` for rate-limited free tiers. |
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
| `TOP_N` | `10` | Maximum number of articles per digest. |
| `MIN_SCORE` | `6` | Articles scoring at least this (0–10) always go in, up to `TOP_N`. |
| `MIN_ITEMS` | `5` | Minimum per digest: if fewer reach `MIN_SCORE`, the next best are added and marked as topped up. `0` never tops up. |
| `FLOOR_SCORE` | `3` | Never top up with articles scoring below this; the digest gets shorter instead. |
| `EXPLORE_SLOTS` | `1` | Slots for a 🎲 random pick from just below the cut-off, against the filter bubble. `0` turns it off. |
| `EXPLORE_MIN_REGULAR` | `4` | Only add the random pick once at least this many regular articles were selected. |

At most 3 articles per source feed are taken, unless that's needed to reach `MIN_ITEMS`.

### Learning

| Setting | Default | Description |
|---|---|---|
| `TRACK_CLICKS` | `true` | Route digest links through `/<token>/r/<id>` and count clicks. `false` uses direct links and learns from stars only. |
| `LEARN` | `true` | With Laya: learn weights from clicks and stars. |
| `FEEDBACK_DAYS` | `3` | A shown article counts as "not interesting" only after this many days without a click. |

### Output feed

| Setting | Default | Description |
|---|---|---|
| `FEED_MODE` | `daily` | `daily`: one feed entry per digest with all articles. `items`: one feed entry per article. Takes effect on the next start. |
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

State lives in `data/` as plain files: `state.json` (where the last run stopped), `digests.json`,
`feed.xml`, the learning logs and the Laya model. Deleting `state.json` makes the next run start
over with the last `LOOKBACK_HOURS`.

The container runs as an unprivileged user with a read-only filesystem, no capabilities and
memory/CPU limits (`DIGEST_MEM_LIMIT`, `DIGEST_CPUS`).

## Privacy

- **Stays on your server:** your reading history, clicks, stars, the learned model and,
  with Laya, the pre-filtering of all articles.
- **Goes to your LLM provider:** titles, snippets and text of the articles being scored or
  summarised, plus your interest profile. With Laya in front, that's ~40 instead of all
  articles. Use Ollama if nothing should leave your server.
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
