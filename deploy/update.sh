#!/bin/sh
# Update ohne abgebrochenen Digest: holt den neuen Stand, baut das Image (der laufende Container
# arbeitet dabei weiter), wartet, bis kein Lauf mehr aktiv ist, und startet erst dann neu.
#
#   cd /opt/feed-digestive && sh deploy/update.sh
#
# Läufe – geplante wie per "docker compose exec … run" gestartete – halten eine Sperre auf
# data/status/run.lock. Dieselbe Sperre fragt das Skript ab (flock aus util-linux).
set -eu
cd "$(dirname "$0")/.."

git pull --ff-only
docker compose build

lock=data/status/run.lock
if [ -e "$lock" ] && command -v flock >/dev/null 2>&1; then
    while ! flock -n "$lock" true; do
        echo "$(date +%H:%M) Ein Digest läuft gerade – warte, bis er fertig ist (Strg+C bricht nur das Warten ab) …"
        sleep 30
    done
elif [ -e "$lock" ]; then
    echo "flock fehlt – kann nicht prüfen, ob ein Lauf aktiv ist. Bitte selbst auf der Statusseite nachsehen." >&2
    exit 1
fi

docker compose up -d
docker compose ps digest
