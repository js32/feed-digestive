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

**Volumen pro Tag** (grob): bei 300 neuen Artikeln etwa 8 Bewertungs-Aufrufe mit je ca.
10–15 k Eingabe-Token (Profil + 40 Anrisse) und 10 Auszug-Aufrufe mit je ca. 5–8 k Token.
Mit `EXAMPLES_IN_PROMPT`, `SCORING_BATCH_SIZE` und `CATEGORY_IDS` lässt sich das senken.

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
