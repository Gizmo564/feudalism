#!/usr/bin/env bash
# Checks GitHub for new commits. If there are any: pull, install requirements, run the tests, and restart the bot.
# If the tests fail, the previous version is put back and keeps running. A commit that failed is not retried
# until a newer commit arrives. Run by the feudalism-update systemd timer (see install.sh --auto).
set -uo pipefail

APP_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
SERVICE="${SERVICE:-feudalism-bot}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
OWNER="$(stat -c %U "$APP_DIR")"
as_owner() { if [ -n "${RUN_AS-x}" ] && [ "$(id -u)" -eq 0 ]; then runuser -u "$OWNER" -- "$@"; else "$@"; fi; }
log() { echo "[auto-update] $*"; }
tell() { as_owner .venv/bin/python -m population.notify "$@" >/dev/null 2>&1 || true; }   # phone message; never stops an update

cd "$APP_DIR"
as_owner git fetch -q origin || { log "could not reach GitHub, will try again later"; exit 0; }
old="$(as_owner git rev-parse HEAD)"
new="$(as_owner git rev-parse "@{u}")"
[ "$old" = "$new" ] && exit 0
[ "$(cat .auto-update-failed 2>/dev/null)" = "$new" ] && exit 0      # already tried this one and it failed

if ! busy="$(as_owner .venv/bin/python -m population.quiet)"; then       # restarting would interrupt someone
  log "update waiting, $busy"; exit 0
fi

log "updating $old -> $new"
if ! as_owner git merge --ff-only -q "$new"; then
  log "cannot fast-forward (local changes?), leaving things alone"
  tell "Update could not run" "Could not fast-forward to ${new:0:7}. Local changes on the box?" high; exit 1
fi
as_owner .venv/bin/pip install -q -r requirements.txt
if as_owner .venv/bin/python -m pytest -q tests >/tmp/feudalism-tests.log 2>&1; then
  $SYSTEMCTL restart "$SERVICE"
  rm -f .auto-update-failed
  log "updated to $(as_owner git rev-parse --short HEAD) and restarted"
  tell "Updated" "${old:0:7} -> ${new:0:7} (tests passed, bot restarted)"
else
  log "tests failed on $new, putting $old back. See /tmp/feudalism-tests.log"
  as_owner git reset -q --hard "$old"
  as_owner .venv/bin/pip install -q -r requirements.txt
  echo "$new" > .auto-update-failed
  tell "Update failed its tests" "${new:0:7} was rolled back to ${old:0:7}. See /tmp/feudalism-tests.log" high
  exit 1
fi
