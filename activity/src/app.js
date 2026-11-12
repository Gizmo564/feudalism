// The Activity page. Built into static/app.js with activity/build.sh (esbuild bundles Discord's SDK into it).
import {DiscordSDK} from "@discord/embedded-app-sdk";

const $ = (id) => document.getElementById(id);
const say = (text) => { $("status").textContent = text; };

const TERRAIN = {field: "#e2e8c8", forest: "#bed2aa", hills: "#e2d4b0", mountain: "#c8c6c2", water: "#dcebf7"};
const NEUTRAL = "#ede9e0", FOG = "#c4c2ba";
const SQRT3 = Math.sqrt(3);

// ---- the map view: pan by dragging, zoom with the wheel, pinch or the + / - buttons ----
function showMap(data) {
  const canvas = $("map"), ctx = canvas.getContext("2d");
  const color = Object.fromEntries(data.nations.map((n) => [n.id, n.color]));
  const tiles = data.tiles.map((t) => {
    const [q, r] = t.k.split(",").map(Number);
    return {...t, q, r, x: SQRT3 * (q + r / 2), y: 1.5 * r};      // position in units of one hex size
  });
  const at = Object.fromEntries(tiles.map((t) => [t.k, t]));
  let size = 34, cx = 0, cy = 0, coords = false;                  // (cx, cy): the map point at the screen centre

  const centreOn = (k) => { const t = at[k]; if (t) { cx = t.x; cy = t.y; } };
  centreOn(data.capital);

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    canvas.width = innerWidth * dpr; canvas.height = innerHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }

  function hex(x, y, s) {
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const a = Math.PI / 180 * (60 * i - 30);
      ctx.lineTo(x + s * Math.cos(a), y + s * Math.sin(a));
    }
    ctx.closePath();
  }

  function draw() {
    ctx.fillStyle = "#2b2d31"; ctx.fillRect(0, 0, innerWidth, innerHeight);
    const sx = (t) => innerWidth / 2 + (t.x - cx) * size, sy = (t) => innerHeight / 2 + (t.y - cy) * size;
    ctx.lineJoin = "round";
    for (const t of tiles) {
      const x = sx(t), y = sy(t);
      if (x < -size * 2 || y < -size * 2 || x > innerWidth + size * 2 || y > innerHeight + size * 2) continue;
      let fill = !t.visible ? FOG : t.terrain === "water" ? TERRAIN.water : t.owner !== null ? color[t.owner] || "#888" : NEUTRAL;
      hex(x, y, size); ctx.fillStyle = fill; ctx.fill();
      if (t.visible && t.owner === null && t.terrain !== "water") { ctx.fillStyle = TERRAIN[t.terrain] || NEUTRAL; ctx.fill(); }
      ctx.strokeStyle = t.owner === data.me ? "#ffffff" : "rgba(0,0,0,.35)"; ctx.lineWidth = t.owner === data.me ? 2 : 1; ctx.stroke();
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      if (t.visible && t.building) {
        ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(x, y, size * 0.36, 0, 7); ctx.fill();
        ctx.fillStyle = "#2c2c2a"; ctx.font = "bold " + Math.round(size * 0.42) + "px system-ui";
        ctx.fillText(t.building[0].toUpperCase(), x, y + 1);
      } else if (t.visible && t.terrain !== "water") {
        ctx.fillStyle = "rgba(44,44,42,.55)"; ctx.font = Math.round(size * 0.3) + "px system-ui";
        ctx.fillText(t.terrain[0].toUpperCase() + t.terrain.slice(1, 3), x, y - size * 0.12);
      }
      if (t.visible && t.oil) { ctx.fillStyle = "#342c28"; ctx.beginPath(); ctx.arc(x + size * 0.5, y - size * 0.45, size * 0.14, 0, 7); ctx.fill(); }
      if (coords && size >= 26) {
        ctx.fillStyle = "rgba(44,44,42,.8)"; ctx.font = Math.round(size * 0.28) + "px system-ui";
        ctx.fillText(t.q + "," + t.r, x, y + size * 0.62);
      }
    }
    for (const a of data.armies) {
      const t = at[a.tile]; if (!t) continue;
      const x = sx(t) - size * 0.45, y = sy(t) + size * 0.4;
      ctx.beginPath(); ctx.arc(x, y, size * 0.26, 0, 7); ctx.fillStyle = color[a.owner] || "#888"; ctx.fill();
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.fillStyle = "#fff"; ctx.font = "bold " + Math.round(size * 0.3) + "px system-ui"; ctx.fillText(a.n, x, y + 1);
    }
  }

  function zoom(factor) { size = Math.min(90, Math.max(10, size * factor)); draw(); }

  const pointers = new Map();
  let pinch = 0;
  canvas.addEventListener("pointerdown", (e) => { canvas.setPointerCapture(e.pointerId); pointers.set(e.pointerId, [e.clientX, e.clientY]); });
  canvas.addEventListener("pointerup", (e) => { pointers.delete(e.pointerId); pinch = 0; });
  canvas.addEventListener("pointercancel", (e) => { pointers.delete(e.pointerId); pinch = 0; });
  canvas.addEventListener("pointermove", (e) => {
    const was = pointers.get(e.pointerId); if (!was) return;
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
    if (pointers.size === 1) { cx -= (e.clientX - was[0]) / size; cy -= (e.clientY - was[1]) / size; draw(); }
    else if (pointers.size === 2) {
      const [a, b] = [...pointers.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (pinch) zoom(d / pinch);
      pinch = d;
    }
  });
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15); }, {passive: false});

  $("zin").onclick = () => zoom(1.25);
  $("zout").onclick = () => zoom(0.8);
  $("home").onclick = () => { centreOn(data.capital); draw(); };
  $("coords").onclick = () => { coords = !coords; $("coords").classList.toggle("on", coords); draw(); };
  addEventListener("resize", resize);

  $("status").style.display = "none";
  canvas.style.display = "block"; $("bar").style.display = "flex";
  resize();
}

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
  const headers = {Authorization: "Bearer " + access_token};
  const me = await (await fetch("/.proxy/api/me", {headers})).json();
  if (!me.nation) return say("Hello " + me.name + ". You are not in a running game.");
  $("who").textContent = me.nation;
  showMap(await (await fetch("/.proxy/api/map", {headers})).json());
}

main().catch((e) => say("Something went wrong: " + (e && e.message ? e.message : e)));
