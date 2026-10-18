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

All settings live in `.env`. `.env.example` documents every option; the important groups:

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

Too many or too few articles? Adjust `MIN_SCORE` and `TOP_N`. `FEED_MODE=items` turns every
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

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md). You may use, modify and share feed-digestive for any
noncommercial purpose: personal use, research, education, charitable and public organisations.
Commercial use, including use of parts of the code, is not permitted. For commercial licensing,
please get in touch.

This makes feed-digestive *source available*, not open source in the OSI sense.

Dependencies such as httpx, PyTorch and [Laya](https://laya.convaiinnovations.com/) (Apache 2.0)
are covered by their own licenses and are not part of this repository.
