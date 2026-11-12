#!/usr/bin/env bash
# Back up game.db. Safe while the bot is running. Keeps the newest 30 backups.
#   feudalism-backup              copy to ~/feudalism-backups/game-YYYY-MM-DD-HHMMSS.db
#   feudalism-backup /some/dir    copy somewhere else (a USB stick, a synced folder)
#   feudalism-backup -n before-war   name it before-war.db (named backups are never deleted automatically)
set -euo pipefail

APP_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
KEEP=30
NAME=""
if [ "${1:-}" = "-n" ]; then
  NAME="${2:-}"; shift 2 || { echo "Use: backup -n <name> [folder]" >&2; exit 1; }
  NAME="${NAME%.db}"
  [[ "$NAME" =~ ^[A-Za-z0-9._-]+$ && "$NAME" != *..* ]] || { echo "Use letters, numbers, . _ - in the name." >&2; exit 1; }
fi
DEST="${1:-$HOME/feudalism-backups}"

DB="$(grep -E '^DB_PATH=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
DB="${DB:-game.db}"
case "$DB" in /*) ;; *) DB="$APP_DIR/$DB" ;; esac
[ -f "$DB" ] || { echo "No database at $DB"; exit 1; }

mkdir -p "$DEST"
if [ -n "$NAME" ]; then
  OUT="$DEST/$NAME.db"
  [ ! -e "$OUT" ] || { echo "$NAME.db already exists. Pick another name or run: feudalism backup rm $NAME" >&2; exit 1; }
else
  OUT="$DEST/game-$(date +%Y-%m-%d-%H%M%S).db"
  [ ! -e "$OUT" ] || OUT="${OUT%.db}-2.db"      # two backups in the same second
fi
"$APP_DIR/.venv/bin/python" - "$DB" "$OUT" <<'PY'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)          # a consistent copy even while the bot is writing
dst.close(); src.close()
PY
echo "Saved $OUT"

{ ls -1t "$DEST"/game-*.db 2>/dev/null || true; } | tail -n +$((KEEP + 1)) | xargs -r rm --
