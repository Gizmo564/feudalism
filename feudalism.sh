#!/usr/bin/env bash
# One command to look after the bot. Run `feudalism help` to see everything.
set -euo pipefail

APP_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
SERVICE="feudalism-bot"
BACKUPS="${FEUDALISM_BACKUPS:-$HOME/feudalism-backups}"
SYSTEMCTL="${SYSTEMCTL:-sudo systemctl}"

games_dir() {
  local d
  d="$(grep -E '^GAMES_DIR=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
  d="${d:-games}"
  case "$d" in /*) echo "$d" ;; *) echo "$APP_DIR/$d" ;; esac
}

# "main at 1a2b3c4" so it is always clear which version is on the box
where() { echo "$(git -C "$APP_DIR" branch --show-current) at $(git -C "$APP_DIR" rev-parse --short HEAD)"; }

# A backup is a folder of games, like games-2026-11-11-180623 (path tricks are refused)
backup_dir() {
  local name="${1:-}"
  case "$name" in ""|*/*|*..*) echo "Give a backup name from 'feudalism backups'." >&2; exit 1 ;; esac
  [ -d "$BACKUPS/$name" ] || { echo "No backup called $name. See 'feudalism backups'." >&2; exit 1; }
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

  games                how many games and servers, and whether the box needs to be bigger
  backup [folder]      save every game (default folder ~/feudalism-backups)
  backup -n <name>     same, but called <name> (never deleted automatically)
  backups              list backups (also: listbkp)
  backup rm <name>     delete one backup
  restore <name> [server id]
                       put a backup back: every game in it, or just one server's (stops the bot,
                       keeps a safety copy first)
EOF
}

cmd="${1:-help}"; [ $# -gt 0 ] && shift
case "$cmd" in
  help|-h|--help) usage ;;
  games)   "$APP_DIR/.venv/bin/python" -m population.stats ;;
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
      d="$(backup_dir "${2:-}")"; rm -r -- "$d"; echo "Deleted $(basename "$d")"
    else
      "$APP_DIR/backup.sh" "$@"
    fi ;;
  backups|listbkp)
    ls -1d "$BACKUPS"/*/ >/dev/null 2>&1 || { echo "No backups yet. Run: feudalism backup"; exit 0; }
    for d in $(ls -1dt "$BACKUPS"/*/); do printf '%s games, %s  %s\n' "$(ls "$d" | wc -l)" "$(du -sh "$d" | cut -f1)" "$(basename "$d")"; done ;;
  restore)
    d="$(backup_dir "${1:-}")"; only="${2:-}"; games="$(games_dir)"
    if [ -n "$only" ]; then
      [ -f "$d/$only.db" ] || { echo "That backup has no game for server '$only'."; exit 1; }
      what="the game of server $only"
    else
      what="every game in it (games started after the backup are left alone)"
    fi
    read -r -p "Replace $what with $(basename "$d")? [y/N] " yn
    [[ "$yn" =~ ^[Yy] ]] || { echo "Cancelled."; exit 0; }
    keep="$(mktemp -d)"; trap 'rm -rf "$keep"' EXIT
    cp -- "$d"/*.db "$keep"/                              # hold the chosen backup safe before we make a new one
    "$APP_DIR/backup.sh" >/dev/null && echo "Saved the current games as a backup first." || true
    $SYSTEMCTL stop "$SERVICE"
    if [ -n "$only" ]; then files="$keep/$only.db"; else files="$keep"/*.db; fi
    for f in $files; do
      cp -- "$f" "$games/$(basename "$f")"
      rm -f -- "$games/$(basename "$f")-wal" "$games/$(basename "$f")-shm"
    done
    $SYSTEMCTL start "$SERVICE"
    echo "Restored $(basename "$d")." ;;
  *) echo "Unknown command '$cmd'."; echo; usage; exit 1 ;;
esac
