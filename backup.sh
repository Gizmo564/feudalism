#!/usr/bin/env bash
# Back up every game (games/<server id>.db). Safe while the bot is running. Keeps the newest 30 backups.
#   feudalism-backup              copy to ~/feudalism-backups/games-YYYY-MM-DD-HHMMSS/
#   feudalism-backup /some/dir    copy somewhere else (a USB stick, a synced folder)
#   feudalism-backup -n before-war   name it before-war (named backups are never deleted automatically)
set -euo pipefail

APP_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
KEEP=30
NAME=""
if [ "${1:-}" = "-n" ]; then
  NAME="${2:-}"; shift 2 || { echo "Use: backup -n <name> [folder]" >&2; exit 1; }
  [[ "$NAME" =~ ^[A-Za-z0-9._-]+$ && "$NAME" != *..* ]] || { echo "Use letters, numbers, . _ - in the name." >&2; exit 1; }
fi
DEST="${1:-$HOME/feudalism-backups}"

GAMES="$(grep -E '^GAMES_DIR=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
GAMES="${GAMES:-games}"
case "$GAMES" in /*) ;; *) GAMES="$APP_DIR/$GAMES" ;; esac
ls "$GAMES"/*.db >/dev/null 2>&1 || { echo "No games in $GAMES yet, nothing to back up."; exit 1; }

if [ -n "$NAME" ]; then
  OUT="$DEST/$NAME"
  [ ! -e "$OUT" ] || { echo "$NAME already exists. Pick another name or run: feudalism backup rm $NAME" >&2; exit 1; }
else
  OUT="$DEST/games-$(date +%Y-%m-%d-%H%M%S)"
  [ ! -e "$OUT" ] || OUT="$OUT-2"             # two backups in the same second
fi
mkdir -p "$OUT"
"$APP_DIR/.venv/bin/python" - "$GAMES" "$OUT" <<'PY'
import glob, os, sqlite3, sys
games, out = sys.argv[1:3]
for path in glob.glob(os.path.join(games, "*.db")):
    src, dst = sqlite3.connect(path), sqlite3.connect(os.path.join(out, os.path.basename(path)))
    src.backup(dst)          # a consistent copy even while the bot is writing
    dst.close(); src.close()
PY
echo "Saved $(ls "$OUT" | wc -l) game(s) to $OUT"

{ ls -1dt "$DEST"/games-*/ 2>/dev/null || true; } | tail -n +$((KEEP + 1)) | xargs -r rm -r --
