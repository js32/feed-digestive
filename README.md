# rss-digest

Holt täglich die neuen Artikel aus deinem **Miniflux**, lässt sie von einem LLM anhand
deines Interessenprofils bewerten, schreibt zu den besten einen kurzen Auszug und
veröffentlicht das Ergebnis als **Atom-Feed**, den du wieder in Miniflux abonnierst.

```
Miniflux-API ──► Filter/Dedupe ──► Bewertung (günstiges Modell, alle Artikel)
                                  └► Top N ──► Auszüge (gutes Modell) ──► feed.xml ──► nginx ──► rss.js32.de
```

## Einrichtung

1. **Dateien auf den VPS kopieren**, z. B. nach `/opt/rss-digest`.

2. **Konfiguration anlegen**
   ```bash
   cd /opt/rss-digest
   cp .env.example .env
   cp -r config.example config   # dann config/interests.md mit deinen Interessen füllen
   openssl rand -hex 16          # -> als FEED_TOKEN in .env eintragen
   mkdir -p data && sudo chown 1000:1000 data
   ```
   In `.env` mindestens eintragen: `MINIFLUX_URL`, `MINIFLUX_API_TOKEN`
   (Miniflux → Einstellungen → API-Schlüssel), LLM-Anbieter, Modelle und API-Key, `FEED_TOKEN`.

   Läuft Miniflux auf demselben Host, kannst du `MINIFLUX_URL=https://deine-miniflux-domain`
   nehmen, oder (wenn der Port auf dem Host lauscht) `http://host.docker.internal:PORT`.

3. **Starten und testen**
   ```bash
   docker compose up -d --build
   docker compose exec digest python -m app check     # Miniflux + LLMs erreichbar?
   docker compose exec digest python -m app dry-run   # Probelauf, speichert nichts
   docker compose exec digest python -m app run       # echten Digest jetzt erzeugen
   ```

4. **nginx**: `deploy/nginx-rss.js32.de.conf` nach `/etc/nginx/sites-available/` kopieren,
   verlinken, Zertifikat holen (`certbot --nginx -d rss.js32.de`), `nginx -t && systemctl reload nginx`.
   DNS: A/AAAA-Eintrag für `rss.js32.de` auf den VPS.

5. **Abonnieren**: `https://rss.js32.de/<FEED_TOKEN>/feed.xml` in Miniflux hinzufügen.
   Der eigene Digest-Feed wird automatisch nicht erneut ausgewertet.

## Quellen

| `SOURCE` | Dienste | Anmeldung |
|---|---|---|
| `miniflux` | Miniflux (native API) | `MINIFLUX_URL`, `MINIFLUX_API_TOKEN` |
| `greader` | alle mit Google-Reader-API: FreshRSS, Inoreader, The Old Reader, BazQux, Miniflux, Tiny Tiny RSS / Nextcloud News (mit Plugin) | `GREADER_URL`, `GREADER_USER`, `GREADER_PASSWORD` |

Beispiele für `GREADER_URL`:
- FreshRSS: `https://freshrss.example.de/api/greader.php`. API-Zugang in den Einstellungen
  aktivieren und im Profil ein eigenes API-Passwort setzen.
- Miniflux: `https://miniflux.example.de`. Google-Reader-Integration in den Einstellungen aktivieren.
- Inoreader: `https://www.inoreader.com`, zusätzlich `GREADER_APP_ID`/`GREADER_APP_KEY` aus
  einem Inoreader-Entwicklerkonto.

Mit `GREADER_LABELS=Tech,Wissenschaft` werden nur diese Ordner gelesen. Sterne, Klicks und
Lernen funktionieren mit beiden Quellen gleich. Wer die Quelle wechselt, sollte `data/state.json`
löschen, weil sich die Artikel-IDs ändern.

## LLM-Anbieter

Zwei Stufen, getrennt konfigurierbar (`SCORING_*` und `SUMMARY_*`):

| Anbieter | `*_PROVIDER` | Key-Variable | Hinweis |
|---|---|---|---|
| Anthropic Claude | `anthropic` | `ANTHROPIC_API_KEY` | Profil wird per Prompt-Caching wiederverwendet |
| OpenAI | `openai` | `OPENAI_API_KEY` | |
| Mistral | `mistral` | `MISTRAL_API_KEY` | EU-Anbieter |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | viele Modelle über einen Key |
| Google Gemini | `gemini` | `GEMINI_API_KEY` | OpenAI-kompatibler Endpunkt |
| Ollama | `ollama` | – | `OLLAMA_BASE_URL`, lokal, kostenlos, langsamer |
| Sonstige | `custom` | `CUSTOM_API_KEY` | `CUSTOM_BASE_URL`, jeder OpenAI-kompatible Endpunkt |

Mischen geht, z. B. Bewertung lokal mit Ollama und Auszüge mit Claude.

### Lokal bewerten, per API zusammenfassen

```bash
docker compose --profile local up -d
docker compose exec ollama ollama pull qwen3.5:4b
```
`.env`:
```
SCORING_PROVIDER=ollama
SCORING_MODEL=qwen3.5:4b
OLLAMA_BASE_URL=http://ollama:11434/v1
SCORING_BATCH_SIZE=15
EXAMPLES_IN_PROMPT=40
SUMMARY_PROVIDER=mistral          # oder anthropic, openai …
SUMMARY_MODEL=mistral-medium-latest
```
Dann gehen nur noch die ~10 ausgewählten Artikel an die API, nicht mehr alle 300
und nicht mehr dein vollständiges Profil mit allen Beispielen. Auf einer CPU dauert ein
Lauf je nach Artikelmenge eine halbe bis mehrere Stunden; da er nachts läuft, ist das egal.
Bei sehr langsamen Läufen: `MAX_ENTRIES` senken oder `CATEGORY_IDS` setzen.

**Volumen pro Tag** (grob): bei 300 neuen Artikeln etwa 8 Bewertungs-Aufrufe mit je ca.
10–15 k Eingabe-Token (Profil + 40 Anrisse) und 10 Auszug-Aufrufe mit je ca. 5–8 k Token.
Mit `EXAMPLES_IN_PROMPT`, `SCORING_BATCH_SIZE` und `CATEGORY_IDS` lässt sich das senken.

## Laya-Vorfilter (lokal, ohne LLM)

[Laya](https://laya.convaiinnovations.com/) ist ein kleines Entscheidungsmodell (ca. 320–420
Mio. Parameter, Apache 2.0), das Ja/Nein- und Skalenfragen mit Wahrscheinlichkeiten
beantwortet. Es läuft auf der CPU des VPS, kein Artikel verlässt dafür den Server.

```
alle neuen Artikel ──► Laya (lokal, Fragenkatalog) ──► Top 40 ──► LLM-Bewertung ──► Top 10 ──► Auszüge
```

Einrichten:
```bash
WITH_LAYA=1 docker compose up -d --build     # Image mit PyTorch-CPU + laya
# .env: PREFILTER=laya
docker compose exec digest python -m app check   # lädt beim ersten Mal das Modell (~1 GB nach data/hf)
docker compose exec digest python -m app dry-run
```

Die Fragen stehen in `config/laya_questions.json`:
- `topics`: je Interesse eine Ja/Nein-Frage mit Gewicht (0–1)
- `depth`: Skala von „Kurzmeldung“ bis „herausragend“
- `penalties`: Ausschlussgründe (PR, Clickbait …)

Wert = 10 × beste gewichtete Themen-Wahrscheinlichkeit × (0,4 + 0,6 × Tiefe) × (1 − 0,8 × Malus).
Konkrete, eng gefasste Fragen funktionieren besser als allgemeine. Englische Fragen sind am
zuverlässigsten, die Artikel selbst dürfen deutsch sein (`LAYA_MODEL=multilingual`).

Varianten:
- **Laya + LLM** (empfohlen): `PREFILTER=laya`, `SCORING_PROVIDER=…` – das LLM sieht nur noch ~40 Artikel.
- **Nur Laya**: `SCORING_PROVIDER=none`, `MIN_SCORE=4` – die Bewertung kostet nichts, nur
  die Auszüge gehen noch an ein LLM (oder an Ollama lokal → komplett ohne Cloud).

## Lernen aus Klicks und Sternen

Der Digest merkt sich, was du liest, und passt die Laya-Bewertung daran an.
Alles bleibt auf deinem Server.

**Was gesammelt wird** (in `data/`, 180 Tage):
- `history.jsonl`: jeder bewertete Artikel mit den Laya-Antworten und ob er im Digest stand
- `feedback.jsonl`: Klicks und Sterne

**Rückmeldung:**
- **Klicks**: Die Links im Digest zeigen auf `rss.js32.de/<token>/r/<id>`. Die App zählt den
  Klick und leitet sofort weiter. Abrufe von Miniflux-Crawler, Vorschau-Bots und Skripten
  zählen nicht. Das Ziel sieht weder den geheimen Pfad (`Referrer-Policy: no-referrer`) noch
  dass du über den Digest kamst.
- **Sterne**: Sterne in Miniflux, sowohl auf Original-Artikeln als auch auf Digest-Einträgen,
  holt die App bei jedem Lauf per API ab.

**Lernen:** Ein kleines Regressionsmodell lernt aus den Laya-Antworten, was du anklickst.
Es startet ab 5 positiven und 10 negativen Rückmeldungen. Sein Anteil am Wert wächst mit der
Zahl der Klicks und Sterne auf höchstens 80 %, den Rest liefert weiterhin die Formel mit
deinen Gewichten. Ein gezeigter Artikel zählt erst nach `FEEDBACK_DAYS` ohne Klick als negativ.

```bash
docker compose exec digest python -m app learn   # was hat es gelernt?
```

**Filterblase:** Pro Digest ist ein Platz (`EXPLORE_SLOTS`) ein 🎲 Zufallsfund knapp
unterhalb der Auswahl. So bekommen auch Themen, die das Modell gerade unterschätzt, eine
Chance auf deine Klicks.

Ohne Laya werden Klicks und Sterne trotzdem gesammelt. Gelernt wird erst, wenn Laya aktiv ist.

## Interessenprofil anpassen

Das persönliche Profil liegt in `config/` und ist per `.gitignore` vom Repo
ausgeschlossen. Im Repo liegen nur die Vorlagen in `config.example/`.

- `config/interests.md` – Themen, Prioritäten, was du nicht willst. Frei editierbar,
  wird bei jedem Lauf neu gelesen (kein Neustart nötig).
- `config/examples.txt` – deine zuletzt gespeicherten Artikel als Beispiele. Neu erzeugen
  aus einem frischen Export:
  ```bash
  python3 tools/import_readlater.py ReadLater.json config/examples.txt 200
  ```
- Mit Stern markierte Artikel in Miniflux fließen automatisch mit ein (`USE_MINIFLUX_STARRED`).

Kommen zu viele/zu wenige Artikel: `MIN_SCORE` und `TOP_N` anpassen.
`FEED_MODE=items` macht aus jedem Artikel einen eigenen Feed-Eintrag statt eines Tages-Digests.

## Betrieb

- Logs: `docker compose logs -f digest`
- Zustand liegt in `data/` (`state.json`, `digests.json`, `feed.xml`). Löschen von
  `state.json` = nächster Lauf startet wieder mit den letzten `LOOKBACK_HOURS`.
- Datenschutz: Titel, Anrisse und Volltexte der Artikel sowie dein Profil gehen an den
  gewählten LLM-Anbieter. Wer das nicht will: Ollama.
