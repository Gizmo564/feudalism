// The Activity page. Built into static/app.js with activity/build.sh (esbuild bundles Discord's SDK into it).
import {DiscordSDK} from "@discord/embedded-app-sdk";

const $ = (id) => document.getElementById(id);
const say = (text) => { $("status").textContent = text; };
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
// make an element; text always goes in as text (nation names come from players)
function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) { if (k === "style") e.style.cssText = v; else e[k] = v; }
  for (const k of kids) e.append(k);
  return e;
}

const TERRAIN = {field: "#e2e8c8", forest: "#bed2aa", hills: "#e2d4b0", mountain: "#c8c6c2", water: "#dcebf7"};
const NEUTRAL = "#ede9e0", FOG = "#c4c2ba";
const RES_COLOUR = {food: "#8fc46b", wood: "#c28a4a", stone: "#a9a7a1", ore: "#7fa3c7", oil: "#6b5a50", livestock: "#e0a1a1"};
const SQRT3 = Math.sqrt(3);
// the same little line icons the Discord map uses (drawn about 14 units wide)
const ICON = {
  field: "M-5 -3.5H5M-5 0H5M-5 3.5H5",
  forest: "M-4.5 1L0 -6L4.5 1ZM0 1V5",
  hills: "M-7 4Q-3.5 -5 0 4Q3.5 -5 7 4",
  mountain: "M-7 5L-2 -5L2 1L4 -2L7 5",
  water: "M-7 -2Q-3.5 -6 0 -2T7 -2M-7 3Q-3.5 -1 0 3T7 3",
};
const CART_D = "M-4.5 -3.5H3.5V1.5H-4.5ZM3.5 -1H5.5L6.5 1.5H3.5M-3 4.5V3M3 4.5V3";
const CART = new Path2D(CART_D);
const SHIELD = new Path2D("M0 -10L8 -7V1Q8 8 0 11Q-8 8 -8 1V-7Z");
const PATH = Object.fromEntries(Object.entries(ICON).map(([k, d]) => [k, new Path2D(d)]));
// for each hex side: the neighbour across it and the two corners it joins (corner i is at angle 60*i - 30 degrees)
const EDGES = [[1, 0, 0, 1], [0, 1, 1, 2], [-1, 1, 2, 3], [-1, 0, 3, 4], [0, -1, 4, 5], [1, -1, 5, 0]];

const dark = (hex) => "#" + [1, 3, 5].map((i) => Math.round(parseInt(hex.substr(i, 2), 16) * 0.6).toString(16).padStart(2, "0")).join("");
const svgIcon = (d) => {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("width", 16); s.setAttribute("height", 16); s.setAttribute("viewBox", "-8 -8 16 16");
  s.setAttribute("fill", "none"); s.setAttribute("stroke", "#f2f3f5"); s.setAttribute("stroke-width", 1.4);
  s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", d); s.append(p);
  return s;
};
const swatch = (colour) => el("i", {className: "sw", style: "background:" + colour + ";border-color:" + dark(colour)});

// ---- the bars above the map: your name, your stock, what you hold ----
function showBars(data, name) {
  const mine = data.nations.find((n) => n.id === data.me);
  $("who").replaceChildren(swatch(mine ? mine.color : "#888"), name);
  $("pills").replaceChildren(...data.resources.map(([r, n]) =>
    el("div", {className: "pill"}, el("i", {style: "background:" + (RES_COLOUR[r] || "#888")}), el("span", {textContent: cap(r)}), el("b", {textContent: n}))));
  const chip = (icon, label, n) => {
    const c = el("div", {className: "hchip" + (n ? "" : " zero")}, ...(icon ? [svgIcon(ICON[icon])] : []),
                 el("span", {textContent: cap(label)}), el("b", {textContent: n}));
    return c;
  };
  const label = (t) => el("span", {className: "hlabel", textContent: t});
  const {land, buildings, building_types: types} = data.holdings;
  const total = Object.values(land).reduce((a, b) => a + b, 0);
  const owned = Object.values(buildings).reduce((a, b) => a + b, 0);
  $("hold").replaceChildren(
    label("Land " + total), ...["field", "forest", "hills", "mountain"].map((t) => chip(t, t, land[t] || 0)),
    el("span", {className: "hsep"}), label("Buildings " + owned),
    ...["capital", ...types].map((b) => chip("", b, buildings[b] || 0)));
}

// ---- the side panel (a bottom sheet on a phone) ----
const button = (text, onclick, kind = "primary") => {
  const b = el("button", {className: "btn " + kind, textContent: text, disabled: kind === "off"});
  b.onclick = onclick;
  return b;
};

// what the panel can do: act(body) places an order and shows the result, cancel() clears them
function ordersBox(data, act) {
  if (!data.orders.length) return [];
  return [el("div", {className: "box"}, el("h3", {textContent: "Queued for tonight"}),
    ...data.orders.map((o) => el("div", {className: "row"}, el("span", {textContent: o}))),
    button("Cancel all orders", () => act({action: "cancel"}), "plain"))];
}

function showTile(t, data, card, act, msg) {
  const names = Object.fromEntries(data.nations.map((n) => [n.id, n]));
  const stock = Object.fromEntries(data.resources);
  const out = [];
  if (msg) out.push(el("div", {className: "msg " + (msg.ok ? "ok" : "bad"), textContent: msg.message}));
  out.push(el("div", {className: "eyebrow", textContent: "Tile " + t.k}), el("h2", {textContent: cap(t.terrain)}));
  const tags = el("div", {className: "tags"});
  if (!t.visible) {
    out.push(tags, el("p", {className: "hint", textContent: "You cannot see this tile. Land and armies near yours are visible."}));
  } else {
    if (t.owner === null) tags.append(el("span", {className: "tag", textContent: t.terrain === "water" ? "Water" : "Free land"}));
    else {
      const n = names[t.owner];
      tags.append(el("span", {className: "tag"}, swatch(n ? n.color : "#888"), (n ? n.name : "Unknown") + (t.owner === data.me ? " (you)" : "")));
    }
    tags.append(el("span", {className: "tag", textContent: t.building ? cap(t.building) : "No building"}));
    if (t.oil) tags.append(el("span", {className: "tag", textContent: "Oil deposit"}));
    out.push(tags);
    for (const line of card.text.split("\n").filter((l) => l.startsWith("Caravan:")))
      out.push(el("p", {className: "hint", style: "margin:12px 0 0", textContent: line}));
    const row = (res, need, short) => el("div", {className: "row"}, el("span", {textContent: cap(res)}),
      el("span", {className: short ? "short" : "", textContent: need + " of " + (stock[res] || 0) + " you have"}));
    const things = [];
    if (card.can_claim) {
      const short = Object.keys(card.claim_missing).length > 0;
      things.push(el("div", {className: "box"}, el("h3", {textContent: "Claim this tile"}),
        ...Object.entries(card.claim_cost).map(([r, n]) => row(r, n, r in card.claim_missing)),
        button(card.claim_queued ? "A claim is already queued" : short ? "You cannot pay for this yet" : "Claim this tile",
               () => act({action: "claim", tile: t.k}), card.claim_queued || short ? "off" : "primary")));
    }
    if (card.builds.length) {
      things.push(el("div", {className: "box"}, el("h3", {textContent: "Build here"}),
        ...card.builds.map((b) => {
          const short = Object.keys(card.build_missing[b] || {}).length > 0;
          const cost = Object.entries(card.build_cost[b]).map(([r, n]) => n + " " + r).join(", ");
          return button(cap(b) + " - " + cost, () => act({action: "build", tile: t.k, building: b}), short ? "off" : "plain");
        }),
        el("div", {className: "note", textContent: "Greyed out: you cannot pay for it yet."})));
    }
    const armies = data.armies.filter((a) => a.owner === data.me && a.tile !== t.k);
    if (card.can_move && armies.length) {
      things.push(el("div", {className: "box"}, el("h3", {textContent: "Send an army here"}),
        ...armies.map((a) => button("Army " + a.id + " (" + a.n + " " + data.soldier + "s) from " + a.tile,
                                    () => act({action: "move", army: a.id, tile: t.k}), "plain")),
        el("div", {className: "note", textContent: "It marches one tile a night."})));
    }
    out.push(...things);
    if (!things.length) out.push(el("p", {className: "hint", textContent: "Nothing to do on this tile right now."}));
  }
  out.push(...ordersBox(data, act));
  $("card").replaceChildren(...out);
}

// ---- the map view: pan by dragging, zoom with the wheel, pinch or the buttons, click a tile ----
function showMap(data, headers) {
  const canvas = $("map"), ctx = canvas.getContext("2d");
  const color = Object.fromEntries(data.nations.map((n) => [n.id, n.color]));
  const tiles = data.tiles.map((t) => {
    const [q, r] = t.k.split(",").map(Number);
    return {...t, q, r, x: SQRT3 * (q + r / 2), y: 1.5 * r};      // position in units of one hex size
  });
  const at = Object.fromEntries(tiles.map((t) => [t.k, t]));
  let size = 34, cx = 0, cy = 0, coords = false, terrain = true, selected = null, W = 0, H = 0;   // (cx, cy): the map point at the centre

  const centreOn = (k) => { const t = at[k]; if (t) { cx = t.x; cy = t.y; } };
  centreOn(data.capital);

  function resize() {
    const box = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    W = box.width; H = box.height;
    canvas.width = W * dpr; canvas.height = H * dpr;
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

  // army: the nation's dark colour with a white outline, the soldier count inside (a "?" for other nations' armies)
  function shield(x, y, k, owner, label) {
    ctx.save(); ctx.translate(x, y); ctx.scale(k, k);
    ctx.fillStyle = dark(color[owner] || "#888888"); ctx.fill(SHIELD);
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke(SHIELD);
    ctx.fillStyle = "#fff"; ctx.font = "bold 10px Inter, system-ui, sans-serif"; ctx.fillText(label, 0, 1);
    ctx.restore();
  }

  // caravan: a cart in the same colours (dark nation colour, white outline)
  function cart(x, y, k, owner) {
    ctx.save(); ctx.translate(x, y); ctx.scale(k, k);
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.fillStyle = "#fff"; ctx.strokeStyle = "#fff"; ctx.lineWidth = 4.2; ctx.fill(CART); ctx.stroke(CART);
    const c = dark(color[owner] || "#888888");
    ctx.fillStyle = c; ctx.strokeStyle = c; ctx.lineWidth = 2; ctx.fill(CART); ctx.stroke(CART);
    ctx.restore();
  }

  function draw() {
    ctx.fillStyle = "#2b2d31"; ctx.fillRect(0, 0, W, H);
    const sx = (t) => W / 2 + (t.x - cx) * size, sy = (t) => H / 2 + (t.y - cy) * size;
    const seen = tiles.filter((t) => {
      const x = sx(t), y = sy(t);
      return !(x < -size * 2 || y < -size * 2 || x > W + size * 2 || y > H + size * 2);
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
        ctx.fillStyle = "#2c2c2a"; ctx.font = "bold " + Math.round(size * 0.42) + "px Inter, system-ui, sans-serif";
        ctx.fillText(t.building[0].toUpperCase(), x, y + 1);
      } else if (terrain) {
        icon(t.terrain, x, y, mine ? "rgba(255,255,255,.75)" : t.terrain === "water" ? "rgba(70,110,150,.55)" : "rgba(44,44,42,.5)");
      }
      if (t.oil) { ctx.fillStyle = "#342c28"; ctx.beginPath(); ctx.arc(x + size * 0.5, y - size * 0.45, size * 0.14, 0, 7); ctx.fill(); }
      if (coords && size >= 26) {
        ctx.fillStyle = "rgba(44,44,42,.8)"; ctx.font = Math.round(size * 0.28) + "px Inter, system-ui, sans-serif";
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
    for (const t of seen) {                                          // 3. nation names under their capitals
      if (!t.visible || t.building !== "capital" || t.owner === null) continue;
      const n = data.nations.find((m) => m.id === t.owner); if (!n) continue;
      const x = sx(t), y = sy(t) + size * 0.98, w = n.name.length * size * 0.26 + size * 0.5, h = size * 0.42;
      ctx.beginPath(); ctx.roundRect(x - w / 2, y - h / 2, w, h, h / 2);
      ctx.fillStyle = dark(n.color); ctx.fill(); ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.fillStyle = "#fff"; ctx.font = "bold " + Math.round(size * 0.26) + "px Inter, system-ui, sans-serif"; ctx.fillText(n.name, x, y + 0.5);
    }
    for (const a of data.armies) {                                   // 4a. a dotted line to where one of your armies is marching
      const from = at[a.tile], to = a.dest && at[a.dest];
      if (!from || !to || a.dest === a.tile) continue;
      const x1 = sx(from), y1 = sy(from), x2 = sx(to), y2 = sy(to), ang = Math.atan2(y2 - y1, x2 - x1);
      const ex = x2 - Math.cos(ang) * size * 0.45, ey = y2 - Math.sin(ang) * size * 0.45;
      ctx.strokeStyle = "#fff"; ctx.lineWidth = size * 0.12; ctx.setLineDash([1, size * 0.2]);
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(ex, ey); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = "#fff"; ctx.beginPath();
      ctx.moveTo(ex + Math.cos(ang) * size * 0.06, ey + Math.sin(ang) * size * 0.06);
      ctx.lineTo(ex - Math.cos(ang - 0.5) * size * 0.22, ey - Math.sin(ang - 0.5) * size * 0.22);
      ctx.lineTo(ex - Math.cos(ang + 0.5) * size * 0.22, ey - Math.sin(ang + 0.5) * size * 0.22);
      ctx.fill();
    }
    for (const a of data.armies) {                                   // 4. armies: shields (the middle, or a corner if there is a building)
      const t = at[a.tile]; if (!t || !seen.includes(t)) continue;
      let x = sx(t), y = sy(t), k = size / 21;
      if (t.building) { x -= size * 0.5; y += size * 0.45; k *= 0.8; }
      shield(x, y, k, a.owner, a.owner === data.me ? String(a.n) : "?");
    }
    for (const c of data.caravans || []) {                           // 5. caravans: carts, moved to the top-left corner if something else is there
      const t = at[c.tile]; if (!t || !seen.includes(t)) continue;
      let x = sx(t), y = sy(t), k = size / 21 * 1.5;
      if (t.building || data.armies.some((a) => a.tile === c.tile)) { x -= size * 0.5; y -= size * 0.4; k *= 0.7; } else x -= 0.5 * k;
      cart(x, y, k, c.from);
    }
    if (selected && at[selected]) {                                  // 6. the selected tile
      hex(sx(at[selected]), sy(at[selected]), size * 0.95); ctx.strokeStyle = "#5865f2"; ctx.lineWidth = 3; ctx.stroke();
    }
  }

  function zoom(factor) { size = Math.min(90, Math.max(10, size * factor)); draw(); }

  const hint = () => $("card").replaceChildren(el("p", {className: "hint", textContent: "Click a tile to see what is there."}));

  // show the card for a tile; msg is the result of an order just placed
  async function open(t, msg) {
    const got = await fetch("/.proxy/api/tile?k=" + encodeURIComponent(t.k), {headers});
    if (selected !== t.k) return;
    if (got.ok) showTile(t, data, await got.json(), (body) => act(t, body), msg);
    else $("card").replaceChildren(el("p", {className: "hint", textContent: "Could not load that tile."}));
  }

  // place an order, then reload what changed (stock, armies, queue) and show the answer
  async function act(t, body) {
    let msg;
    try {
      const got = await fetch("/.proxy/api/order", {method: "POST", headers: {...headers, "Content-Type": "application/json"}, body: JSON.stringify(body)});
      msg = await got.json();
      if (!msg.message) msg = {ok: false, message: "That did not work."};
    } catch (e) { msg = {ok: false, message: "Could not reach the game."}; }
    const fresh = await fetch("/.proxy/api/map", {headers});
    if (fresh.ok) {
      const d = await fresh.json();
      for (const k of ["resources", "holdings", "armies", "caravans", "orders"]) data[k] = d[k];
      showBars(data, data.name);
      draw();
    }
    return open(t, msg);
  }

  async function pick(px, py) {
    let best = null, bestD = size * 0.95;
    for (const t of tiles) {
      const d = Math.hypot(W / 2 + (t.x - cx) * size - px, H / 2 + (t.y - cy) * size - py);
      if (d < bestD) { best = t; bestD = d; }
    }
    if (!best) { selected = null; hint(); return draw(); }
    selected = best.k; draw();
    return open(best);
  }

  const pointers = new Map();
  let pinch = 0, down = null;
  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId); pointers.set(e.pointerId, [e.clientX, e.clientY]);
    down = pointers.size === 1 ? [e.clientX, e.clientY] : null;
  });
  canvas.addEventListener("pointerup", (e) => {
    const box = canvas.getBoundingClientRect();
    if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 6) pick(e.clientX - box.left, e.clientY - box.top);   // a click, not a drag
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
  $("terrain").onclick = () => { terrain = !terrain; $("terrain").classList.toggle("on", terrain); draw(); };
  addEventListener("resize", resize);

  // the key: your nation, the others on the map, free land, water, and the two kinds of piece
  const key = (mark, text) => el("span", {}, mark, text);
  const marker = (d, colour, outlined) => {      // a small copy of a piece for the key
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("width", 16); s.setAttribute("height", 16); s.setAttribute("viewBox", "-9 -11 18 22");
    const path = (fill, stroke, w) => {
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      for (const [k, v] of Object.entries({d, fill, stroke, "stroke-width": w, "stroke-linejoin": "round", "stroke-linecap": "round"})) p.setAttribute(k, v);
      s.append(p);
    };
    if (outlined) path("#fff", "#fff", 4.2);       // the cart: a white outline under the coloured cart
    path(colour, outlined ? colour : "#fff", 2);   // the shield: coloured, with its white outline
    return s;
  };
  const mine = data.nations.find((n) => n.id === data.me);
  const myDark = dark(mine ? mine.color : "#888888");
  $("legend").replaceChildren(
    ...data.nations.map((n) => key(swatch(n.color), n.name + (n.id === data.me ? " (you)" : ""))),
    key(swatch(NEUTRAL), "Free land"), key(swatch(TERRAIN.water), "Water"),
    key(marker(CART_D, myDark, true), "Caravan"), key(marker("M0 -10L8 -7V1Q8 8 0 11Q-8 8 -8 1V-7Z", myDark, false), "Army"));

  $("status").style.display = "none";
  $("app").style.display = "flex";
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
  const data = await (await fetch("/.proxy/api/map", {headers})).json();
  data.name = me.nation;
  showBars(data, me.nation);
  showMap(data, headers);
}

main().catch((e) => say("Something went wrong: " + (e && e.message ? e.message : e)));
