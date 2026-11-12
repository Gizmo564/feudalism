#!/usr/bin/env bash
# Rebuild activity/static/app.js from activity/src/app.js. Only needed when the page code changes;
# the built file is committed, so the server never needs Node.
set -euo pipefail
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
npm install --silent @discord/embedded-app-sdk esbuild
npx esbuild src/app.js --bundle --minify --format=iife --outfile=static/app.js
