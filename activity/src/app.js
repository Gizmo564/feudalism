// The Activity page. Built into static/app.js with activity/build.sh (esbuild bundles Discord's SDK into it).
import {DiscordSDK} from "@discord/embedded-app-sdk";

const say = (text) => { document.getElementById("status").textContent = text; };

async function main() {
  const {clientId} = await (await fetch("/.proxy/api/config")).json();
  const sdk = new DiscordSDK(clientId);
  await sdk.ready();
  const {code} = await sdk.commands.authorize({
    client_id: clientId, response_type: "code", state: "", prompt: "none", scope: ["identify"],
  });
  const login = await fetch("/.proxy/api/token", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({code}),
  });
  const {access_token} = await login.json();
  await sdk.commands.authenticate({access_token});
  const me = await (await fetch("/.proxy/api/me", {headers: {Authorization: "Bearer " + access_token}})).json();
  say(me.nation ? "Hello " + me.name + " of " + me.nation + "!" : "Hello " + me.name + ". You are not in a running game.");
}

main().catch((e) => say("Something went wrong: " + (e && e.message ? e.message : e)));
