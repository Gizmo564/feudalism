#!/usr/bin/env bash
# One command to look after the bot. Run `feudalism help` to see everything.
set -euo pipefail

APP_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
SERVICE="feudalism-bot"
BACKUPS="${FEUDALISM_BACKUPS:-$HOME/feudalism-backups}"
SYSTEMCTL="${SYSTEMCTL:-sudo systemctl}"

db_path() {
  local db
  db="$(grep -E '^DB_PATH=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
  db="${db:-game.db}"
  case "$db" in /*) echo "$db" ;; *) echo "$APP_DIR/$db" ;; esac
}

# "main at 1a2b3c4" so it is always clear which version is on the box
where() { echo "$(git -C "$APP_DIR" branch --show-current) at $(git -C "$APP_DIR" rev-parse --short HEAD)"; }

# A backup name like game-2026-11-11-180623.db (path tricks are refused)
backup_file() {
  local name="${1:-}"
  case "$name" in ""|*/*|*..*) echo "Give a backup name from 'feudalism backups'." >&2; exit 1 ;; esac
  [[ "$name" == *.db ]] || name="$name.db"
  [ -f "$BACKUPS/$name" ] || { echo "No backup called $name. See 'feudalism backups'." >&2; exit 1; }
  echo "$BACKUPS/$name"
}

usage() {
  cat <<'EOF'
feudalism <command>

  status               is the bot running? (also shows which branch it is on)
  start | stop | restart
  logs [-f]            recent log lines (-f keeps following)
  update               get the latest code for the branch you are on (the one 'try' picked) and restart
  auto on | off        turn automatic updates on or off
  try <branch>         switch to a test branch from GitHub and restart (back again: feudalism try main)
  env                  edit the settings file (.env), then offer to restart

  backup [folder]      save game.db (default folder ~/feudalism-backups)
  backup -n <name>     same, but called <name>.db (never deleted automatically)
  backups              list backups (also: listbkp)
  backup rm <name>     delete one backup
  restore <name>       put a backup back (stops the bot, keeps a safety copy first)
EOF
}

cmd="${1:-help}"; [ $# -gt 0 ] && shift
case "$cmd" in
  help|-h|--help) usage ;;
  status)  echo "Code: $(where)"; $SYSTEMCTL status "$SERVICE" --no-pager -n 5 ;;
  start|stop|restart) $SYSTEMCTL "$cmd" "$SERVICE"; echo "Bot: $cmd done." ;;
  logs)
    if [ "${1:-}" = "-f" ]; then journalctl -u "$SERVICE" -f; else journalctl -u "$SERVICE" -n 50 --no-pager; fi ;;
  update)  echo "Updating the branch you are on: $(where)"; exec "$APP_DIR/install.sh" ;;
  auto)
    case "${1:-}" in
      on)  exec "$APP_DIR/install.sh" --auto ;;
      off) exec "$APP_DIR/install.sh" --no-auto ;;
      *)   echo "Use: feudalism auto on|off"; exit 1 ;;
    esac ;;
  try)
    branch="${1:-}"
    [ -n "$branch" ] || { echo "Use: feudalism try <branch>   (back to normal: feudalism try main)"; exit 1; }
    cd "$APP_DIR"
    git fetch -q origin
    git checkout -q "$branch" 2>/dev/null || git checkout -q -b "$branch" "origin/$branch" \
      || { echo "No branch called $branch on GitHub."; exit 1; }
    echo "Now on branch $branch."
    exec "$APP_DIR/install.sh" ;;
  env)
    "${EDITOR:-nano}" "$APP_DIR/.env"
    read -r -p "Restart the bot to use the new settings? [y/N] " yn
    [[ "$yn" =~ ^[Yy] ]] && $SYSTEMCTL restart "$SERVICE" && echo "Restarted." || true ;;
  backup)
    if [ "${1:-}" = "rm" ]; then
      f="$(backup_file "${2:-}")"; rm -- "$f"; echo "Deleted $(basename "$f")"
    else
      "$APP_DIR/backup.sh" "$@"
    fi ;;
  backups|listbkp)
    [ -d "$BACKUPS" ] && ls -1t "$BACKUPS"/*.db >/dev/null 2>&1 || { echo "No backups yet. Run: feudalism backup"; exit 0; }
    for f in $(ls -1t "$BACKUPS"/*.db); do printf '%s  %s\n' "$(du -h "$f" | cut -f1)" "$(basename "$f")"; done ;;
  restore)
    f="$(backup_file "${1:-}")"; db="$(db_path)"
    read -r -p "Replace the current game with $(basename "$f")? [y/N] " yn
    [[ "$yn" =~ ^[Yy] ]] || { echo "Cancelled."; exit 0; }
    keep="$(mktemp)"; trap 'rm -f "$keep"' EXIT
    cp -- "$f" "$keep"                                   # hold the chosen backup safe before we make a new one
    "$APP_DIR/backup.sh" >/dev/null && echo "Saved the current game as a backup first."
    $SYSTEMCTL stop "$SERVICE"
    cp -- "$keep" "$db"
    rm -f -- "$db-wal" "$db-shm"
    $SYSTEMCTL start "$SERVICE"
    echo "Restored $(basename "$f")." ;;
  *) echo "Unknown command '$cmd'."; echo; usage; exit 1 ;;
esac
