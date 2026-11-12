// The Activity page. Built into static/app.js with activity/build.sh (esbuild bundles Discord's SDK into it).
import {DiscordSDK} from "@discord/embedded-app-sdk";

const $ = (id) => document.getElementById(id);
const say = (text) => { $("status").textContent = text; };

const TERRAIN = {field: "#e2e8c8", forest: "#bed2aa", hills: "#e2d4b0", mountain: "#c8c6c2", water: "#dcebf7"};
const NEUTRAL = "#ede9e0", FOG = "#c4c2ba";
const SQRT3 = Math.sqrt(3);
// the same little line icons the Discord map uses (drawn about 14 units wide)
const ICON = {
  field: "M-5 -3.5H5M-5 0H5M-5 3.5H5",
  forest: "M-4.5 1L0 -6L4.5 1ZM0 1V5",
  hills: "M-7 4Q-3.5 -5 0 4Q3.5 -5 7 4",
  mountain: "M-7 5L-2 -5L2 1L4 -2L7 5",
  water: "M-7 -2Q-3.5 -6 0 -2T7 -2M-7 3Q-3.5 -1 0 3T7 3",
};
const PATH = Object.fromEntries(Object.entries(ICON).map(([k, d]) => [k, new Path2D(d)]));
// for each hex side: the neighbour across it and the two corners it joins (corner i is at angle 60*i - 30 degrees)
const EDGES = [[1, 0, 0, 1], [0, 1, 1, 2], [-1, 1, 2, 3], [-1, 0, 3, 4], [0, -1, 4, 5], [1, -1, 5, 0]];

const dark = (hex) => "#" + [1, 3, 5].map((i) => Math.round(parseInt(hex.substr(i, 2), 16) * 0.6).toString(16).padStart(2, "0")).join("");

// show a card of text; **word** is bold
function showCard(text) {
  const card = $("card");
  card.textContent = "";
  text.split("**").forEach((part, i) => {
    const el = document.createElement(i % 2 ? "b" : "span");
    el.textContent = part;
    card.appendChild(el);
  });
  card.style.display = "block";
}

// ---- the map view: pan by dragging, zoom with the wheel, pinch or the + / - buttons, click a tile ----
function showMap(data, headers) {
  const canvas = $("map"), ctx = canvas.getContext("2d");
  const color = Object.fromEntries(data.nations.map((n) => [n.id, n.color]));
  const tiles = data.tiles.map((t) => {
    const [q, r] = t.k.split(",").map(Number);
    return {...t, q, r, x: SQRT3 * (q + r / 2), y: 1.5 * r};      // position in units of one hex size
  });
  const at = Object.fromEntries(tiles.map((t) => [t.k, t]));
  let size = 34, cx = 0, cy = 0, coords = false, selected = null;   // (cx, cy): the map point at the screen centre

  const centreOn = (k) => { const t = at[k]; if (t) { cx = t.x; cy = t.y; } };
  centreOn(data.capital);

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    canvas.width = innerWidth * dpr; canvas.height = innerHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }

  const corner = (x, y, s, i) => [x + s * Math.cos(Math.PI / 180 * (60 * i - 30)), y + s * Math.sin(Math.PI / 180 * (60 * i - 30))];

  function hex(x, y, s) {
    ctx.beginPath();
    for (let i = 0; i < 6; i++) ctx.lineTo(...corner(x, y, s, i));
    ctx.closePath();
  }

  function icon(name, x, y, colour) {
    const k = size / 18;
    ctx.save(); ctx.translate(x, y); ctx.scale(k, k);
    ctx.strokeStyle = colour; ctx.lineWidth = 1.3; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.stroke(PATH[name]); ctx.restore();
  }

  function draw() {
    ctx.fillStyle = "#2b2d31"; ctx.fillRect(0, 0, innerWidth, innerHeight);
    const sx = (t) => innerWidth / 2 + (t.x - cx) * size, sy = (t) => innerHeight / 2 + (t.y - cy) * size;
    const seen = tiles.filter((t) => {
      const x = sx(t), y = sy(t);
      return !(x < -size * 2 || y < -size * 2 || x > innerWidth + size * 2 || y > innerHeight + size * 2);
    });
    ctx.lineJoin = "round"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const t of seen) {                                          // 1. tile colours, terrain icons, buildings
      const x = sx(t), y = sy(t);
      const mine = t.visible && t.owner !== null && t.terrain !== "water";
      hex(x, y, size);
      ctx.fillStyle = !t.visible ? FOG : t.terrain === "water" ? TERRAIN.water : mine ? color[t.owner] || "#888" : TERRAIN[t.terrain] || NEUTRAL;
      ctx.fill();
      ctx.strokeStyle = "rgba(0,0,0,.25)"; ctx.lineWidth = 1; ctx.stroke();
      if (!t.visible) continue;
      if (t.building) {
        ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(x, y, size * 0.36, 0, 7); ctx.fill();
        ctx.fillStyle = "#2c2c2a"; ctx.font = "bold " + Math.round(size * 0.42) + "px system-ui";
        ctx.fillText(t.building[0].toUpperCase(), x, y + 1);
      } else {
        icon(t.terrain, x, y, mine ? "rgba(255,255,255,.75)" : t.terrain === "water" ? "rgba(70,110,150,.55)" : "rgba(44,44,42,.5)");
      }
      if (t.oil) { ctx.fillStyle = "#342c28"; ctx.beginPath(); ctx.arc(x + size * 0.5, y - size * 0.45, size * 0.14, 0, 7); ctx.fill(); }
      if (coords && size >= 26) {
        ctx.fillStyle = "rgba(44,44,42,.8)"; ctx.font = Math.round(size * 0.28) + "px system-ui";
        ctx.fillText(t.q + "," + t.r, x, y + size * 0.62);
      }
    }
    ctx.lineCap = "round";
    for (const t of seen) {                                          // 2. thick border where a nation's land ends
      if (!t.visible || t.owner === null || t.terrain === "water") continue;
      const x = sx(t), y = sy(t);
      ctx.strokeStyle = dark(color[t.owner] || "#888888"); ctx.lineWidth = Math.max(2, size * 0.1);
      for (const [dq, dr, a, b] of EDGES) {
        const n = at[(t.q + dq) + "," + (t.r + dr)];
        if (n && n.visible && n.owner === t.owner) continue;
        ctx.beginPath(); ctx.moveTo(...corner(x, y, size, a)); ctx.lineTo(...corner(x, y, size, b)); ctx.stroke();
      }
    }
    for (const a of data.armies) {                                   // 3. armies
      const t = at[a.tile]; if (!t) continue;
      const x = sx(t) - size * 0.45, y = sy(t) + size * 0.4;
      ctx.beginPath(); ctx.arc(x, y, size * 0.26, 0, 7); ctx.fillStyle = color[a.owner] || "#888"; ctx.fill();
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.fillStyle = "#fff"; ctx.font = "bold " + Math.round(size * 0.3) + "px system-ui"; ctx.fillText(a.n, x, y + 1);
    }
    if (selected && at[selected]) {                                  // 4. the selected tile
      hex(sx(at[selected]), sy(at[selected]), size * 0.95); ctx.strokeStyle = "#5865f2"; ctx.lineWidth = 3; ctx.stroke();
    }
  }

  function zoom(factor) { size = Math.min(90, Math.max(10, size * factor)); draw(); }

  async function pick(px, py) {
    let best = null, bestD = size * 0.95;
    for (const t of tiles) {
      const d = Math.hypot(innerWidth / 2 + (t.x - cx) * size - px, innerHeight / 2 + (t.y - cy) * size - py);
      if (d < bestD) { best = t; bestD = d; }
    }
    if (!best) { selected = null; $("card").style.display = "none"; return draw(); }
    selected = best.k; draw();
    const got = await fetch("/.proxy/api/tile?k=" + encodeURIComponent(best.k), {headers});
    if (selected === best.k) showCard(got.ok ? (await got.json()).text : "Could not load that tile.");
  }

  const pointers = new Map();
  let pinch = 0, down = null;
  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId); pointers.set(e.pointerId, [e.clientX, e.clientY]);
    down = pointers.size === 1 ? [e.clientX, e.clientY] : null;
  });
  canvas.addEventListener("pointerup", (e) => {
    if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 6) pick(e.clientX, e.clientY);   // a click, not a drag
    down = null; pointers.delete(e.pointerId); pinch = 0;
  });
  canvas.addEventListener("pointercancel", (e) => { down = null; pointers.delete(e.pointerId); pinch = 0; });
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
  showMap(await (await fetch("/.proxy/api/map", {headers})).json(), headers);
}

main().catch((e) => say("Something went wrong: " + (e && e.message ? e.message : e)));
