#!/usr/bin/env bash
# Back up game.db. Safe while the bot is running. Keeps the newest 30 backups.
#   feudalism-backup              copy to ~/feudalism-backups/game-YYYY-MM-DD-HHMMSS.db
#   feudalism-backup /some/dir    copy somewhere else (a USB stick, a synced folder)
set -euo pipefail

APP_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
DEST="${1:-$HOME/feudalism-backups}"
KEEP=30

DB="$(grep -E '^DB_PATH=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
DB="${DB:-game.db}"
case "$DB" in /*) ;; *) DB="$APP_DIR/$DB" ;; esac
[ -f "$DB" ] || { echo "No database at $DB"; exit 1; }

mkdir -p "$DEST"
OUT="$DEST/game-$(date +%Y-%m-%d-%H%M%S).db"
[ -e "$OUT" ] && OUT="${OUT%.db}-2.db"      # two backups in the same second
"$APP_DIR/.venv/bin/python" - "$DB" "$OUT" <<'PY'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)          # a consistent copy even while the bot is writing
dst.close(); src.close()
PY
echo "Saved $OUT"

ls -1t "$DEST"/game-*.db | tail -n +$((KEEP + 1)) | xargs -r rm --
