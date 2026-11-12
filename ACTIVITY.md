# Discord Activity (experimental)

A map that opens as a panel inside Discord. Step 1 only shows "Hello <your name> of <nation>!" to prove the setup works.
Everything here is off until you set `ACTIVITY_PORT` in `.env`.

## 1. Developer Portal (discord.com/developers/applications, your bot's application)
1. **Activities > Settings**: turn on **Enable Activities**.
2. **Activities > URL Mappings**: prefix `/`, target `feudalism.0801564.xyz` (no `https://`).
3. **OAuth2**: add one redirect URI (any placeholder works, like `https://127.0.0.1`). Click **Reset Secret** and copy the **Client Secret**.

## 2. The box
Add to `~/feudalism/.env`:
```
ACTIVITY_PORT=8080
DISCORD_CLIENT_SECRET=<the client secret>
```
Then `feudalism restart`. `feudalism logs` should say `Activity server listening on port 8080`.

## 3. Cloudflare Tunnel (makes `feudalism.0801564.xyz` reach the box)
Use a name one level under the domain. Cloudflare's free certificate does not cover `map.feudalism.0801564.xyz`.
```
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb
cloudflared tunnel login                      # opens a link: pick 0801564.xyz
cloudflared tunnel create feudalism
cloudflared tunnel route dns feudalism feudalism.0801564.xyz
```
Create `/etc/cloudflared/config.yml` (use the tunnel ID that `create` printed):
```
tunnel: feudalism
credentials-file: /home/feudalism/.cloudflared/<TUNNEL-ID>.json
ingress:
  - hostname: feudalism.0801564.xyz
    service: http://127.0.0.1:8080
  - service: http_status:404
```
Then `sudo cloudflared service install` and check `https://feudalism.0801564.xyz` in a browser (you should see "Connecting to Discord...").

## 4. Try it
In Discord open the **App Launcher** (the rocket icon in the chat box), pick the app, and launch it. It should say "Hello <name> of <nation>!".

## Changing the page
The page source is `activity/src/app.js`. After editing, run `activity/build.sh` (needs Node) and commit `activity/static/app.js`. The server itself never needs Node.
