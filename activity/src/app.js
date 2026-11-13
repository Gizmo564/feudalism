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

const THEMES = {   // the map's colours: the normal ones, paper (old parchment map), and industrial (Industrialism games only)
  normal: {terrain: {field: "#e2e8c8", forest: "#bed2aa", hills: "#e2d4b0", mountain: "#c8c6c2", water: "#dcebf7"}, neutral: "#ede9e0", fog: "#c4c2ba", back: "#2b2d31"},
  paper: {terrain: {field: "#ebdfb9", forest: "#cfc795", hills: "#dcc78f", mountain: "#cbbfa5", water: "#b7cdd1"}, neutral: "#f1e6c6", fog: "#cdbf9c", back: "#e3d3a8"},
  industrial: {terrain: {field: "#b3a67c", forest: "#85906a", hills: "#b89e74", mountain: "#8f867a", water: "#7a9a9c"}, neutral: "#c9bb9a", fog: "#4d4338", back: "#17120e"},
};
let TERRAIN = THEMES.normal.terrain, NEUTRAL = THEMES.normal.neutral, FOG = THEMES.normal.fog, BACK = THEMES.normal.back;
function setTheme(name) {
  if (!THEMES[name]) name = "normal";
  const t = THEMES[name];
  TERRAIN = t.terrain; NEUTRAL = t.neutral; FOG = t.fog; BACK = t.back;
  for (const x of ["paper", "industrial"]) document.body.classList.toggle(x, x === name);
  try { localStorage.setItem("theme", name); } catch (e) {}
}
const theme = () => ["paper", "industrial"].find((x) => document.body.classList.contains(x)) || "normal";
try { setTheme(localStorage.getItem("theme") || (localStorage.getItem("paper") === "1" ? "paper" : "normal")); } catch (e) {}
const RES_COLOUR = {food: "#8fc46b", wood: "#c28a4a", stone: "#a9a7a1", ore: "#7fa3c7", oil: "#6b5a50", livestock: "#e0a1a1", tools: "#d9a441", steel: "#9aa5b1", provisions: "#e6c27a", bricks: "#c0614a", coal: "#3b3b3f", coke: "#6e6a66", planks: "#d2a66b", cement: "#b8b4aa", machine_parts: "#7d8a99", engines: "#5c6b7a"};
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
// buildings: the same icons as the Discord map (house, crossed swords, market stall, wall, and a crown for capitals)
const BUILDING_D = {
  home: "M-4 0L0 -4L4 0M-2.8 -1V3.5H2.8V-1",
  barracks: "M-4.5 -4.5L3 3M4.5 -4.5L-3 3M-1 3L3 -1M-3 -1L1 3",
  market: "M-4.2 -1L-3.2 -4H3.2L4.2 -1ZM-3 -1V4H3V-1",
  wall: "M-4 4V-3H-2.6V-1.6H-1.2V-3H1.2V-1.6H2.6V-3H4V4Z",
  toolworks: "M-4.5 4.5L1.5 -1.5M-1 -5H5V1H-1Z",
  rail: "M-5 -2H5M-5 2H5M-3.5 -4V4M0 -4V4M3.5 -4V4",
  forge: "M-5 -2H5L3 1H-3ZM-2 1V4H2V1",
  cookhouse: "M-4 -1H4V3H-4ZM-5 -1H5M-1.5 -4Q0 -2.5 1.5 -4",
  port: "M0 -5V4M-3 -2H3M-4.5 1Q0 6 4.5 1",
  kiln: "M-5 -3H5V0H-5ZM-5 0H5V3H-5ZM0 -3V0M-2.5 0V3M2.5 0V3",
  sawmill: "M-5 3H5M-5 3L-3 -3L-1 3L1 -3L3 3L5 -3",
  cokeworks: "M-5 4H5V-1H-5ZM-3 -1V-5M3 -1V-5",
  cementworks: "M-5 4H5L3 -2H-3ZM-1.5 -2V-5H1.5V-2",
  factory: "M-5 4V-1L-1.5 -3V-1L2 -3V-1L5 -1V4ZM3 -1V-5",
  engineworks: "M-3 -3H3V3H-3ZM0 -5V-3M0 3V5M-5 0H-3M3 0H5",
  capital: "M-5 4.5V-2.5L-2.5 0.5L0 -4.5L2.5 0.5L5 -2.5V4.5ZM-5 2H5",
};
const DIAMOND = new Path2D("M0 -12L10 0L0 12L-10 0Z");
const SHIELD = new Path2D("M0 -10L8 -7V1Q8 8 0 11Q-8 8 -8 1V-7Z");
const PATH = Object.fromEntries(Object.entries({...ICON, ...BUILDING_D}).map(([k, d]) => [k, new Path2D(d)]));
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
  const badge = data.rank ? [el("span", {className: "rank", textContent: data.rank.title, style: "background:" + (data.rank.color || "#888")})] : [];   // each rank has its own colour
  $("who").replaceChildren(swatch(mine ? mine.color : "#888"), name, ...badge, el("span", {className: "night", textContent: "Night " + data.tick}));
  const raw = ["food", "wood", "stone", "ore", "oil", "coal", "livestock"];        // refined goods only show up once you have some
  ui.contests = (data.contests || []).length;
  ui.pending = data.pending || 0;
  drawTabs();
  $("pills").replaceChildren(...data.resources.filter(([r, n]) => raw.includes(r) || n > 0).map(([r, n]) =>
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
    ...["capital", ...types].filter((b) => ["capital", "home", "barracks", "market", "wall"].includes(b) || buildings[b]).map((b) => chip("", b, buildings[b] || 0)));
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
    ...data.orders.map((o, i) => el("div", {className: "row"}, el("span", {textContent: o}),
      el("button", {className: "x", textContent: "Cancel", title: "Cancel this order", onclick: () => act({action: "cancel", number: i + 1, text: o})}))),
    data.orders.length > 1 ? button("Cancel all orders", () => act({action: "cancel"}), "plain") : "")];
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
    tags.append(el("span", {className: "tag", title: (card.notes || {})[t.building] || "", textContent: t.building ? cap(t.building) + (t.level > 1 ? " (level " + t.level + ")" : "") : "No building"}));
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
          const btn = button(cap(b) + " - " + cost, () => act({action: "build", tile: t.k, building: b}), short ? "off" : "plain");
          btn.title = (card.notes || {})[b] || "";                       // hover: what it does
          return btn;
        }),
        el("div", {className: "note", textContent: "Greyed out: you cannot pay for it yet."})));
    }
    const up = card.upgrade;
    if (up) {
      const short = Object.keys(up.missing).length > 0;
      const gain = Object.entries(up.gives).map(([k, v]) => k === "slots" ? "gets " + v + " job slots" : "holds " + v + (k === "houses_workers" ? " " + data.worker + "s" : " " + data.soldier + "s")).join(", ");
      things.push(el("div", {className: "box"}, el("h3", {textContent: "Upgrade to level " + up.level}),
        ...Object.entries(up.cost).map(([r, n]) => row(r, n, r in up.missing)),
        el("div", {className: "note", textContent: "Then it " + gain + "."}),
        button(up.queued ? "An upgrade is already queued" : short ? "You cannot pay for this yet" : "Upgrade",
               () => act({action: "upgrade", tile: t.k}), up.queued || short ? "off" : "primary")));
    }
    const here = data.armies.filter((a) => a.owner === data.me && a.tile === t.k);
    const trains = here.filter((a) => a.unit === "train");
    for (const tr of trains) {                                       // a train on this tile: what is aboard, and who may board
      const aboard = tr.aboard || [];
      const others = here.filter((a) => a.unit !== "train");
      things.push(el("div", {className: "box"}, el("h3", {textContent: "Train " + tr.id + " (" + tr.n + " unit" + (tr.n > 1 ? "s" : "") + ")"}),
        el("div", {className: "note", textContent: aboard.length ? "Aboard: " + aboard.map((b) => b.n + " " + (b.unit || data.soldier) + "s").join(", ") : "Nobody aboard. It stops at a border if it carries soldiers or tanks."}),
        ...others.map((a) => button("Board army " + a.id + " (" + a.n + " " + (a.unit || data.soldier) + "s)", () => act({action: "board", train: tr.id, army: a.id}), "plain")),
        ...(aboard.length ? [button("Everyone steps off", () => act({action: "disembark", train: tr.id}), "plain")] : [])));
    }
    const armies = data.armies.filter((a) => a.owner === data.me && a.tile !== t.k);
    if (card.can_move && armies.length) {
      const part = el("input", {className: "field", placeholder: "How many (blank: all)", type: "number", min: 1});
      things.push(el("div", {className: "box"}, el("h3", {textContent: "Send an army here"}),
        ...(data.mode === "advanced" ? [part] : []),
        ...armies.map((a) => button((a.unit ? cap(a.unit) + " " : "Army ") + a.id + " (" + a.n + " " + (a.unit ? a.unit + "s" : data.soldier + "s") + ") from " + a.tile,
                                    () => act({action: "move", army: a.id, tile: t.k, ...(part.value ? {n: Number(part.value)} : {})}), "plain")),
        el("div", {className: "note", textContent: "It marches one tile a night."})));
    }
    out.push(...things);
    if (!things.length) out.push(el("p", {className: "hint", textContent: "Nothing to do on this tile right now."}));
  }
  out.push(...ordersBox(data, act));
  $("card").replaceChildren(...out);
}


// ---- the tabs beside the map: Tile (above), then Nation, Trade, Pacts and Report. They use the same orders as the commands ----
const TABS = [["tile", "Tile"], ["nation", "Nation"], ["updates", "Updates"], ["trade", "Trade"], ["pacts", "Pacts"], ["report", "Report"]];
const ui = {tab: "tile", headers: null, msg: null, refresh: async () => {}};
const plain = (text) => text.replace(/\*\*/g, "");
const box = (title, ...kids) => el("div", {className: "box"}, ...(title ? [el("h3", {textContent: title})] : []), ...kids);
const note = (text) => el("div", {className: "note", textContent: text});
const line = (a, b) => el("div", {className: "row"}, el("span", {textContent: a}), el("span", {textContent: b}));
const input = (placeholder) => el("input", {className: "field", placeholder});
const choose = (options) => {                         // options: [[value, label], ...]
  const s = el("select", {className: "field"});
  for (const [v, l] of options) s.append(el("option", {value: v, textContent: l}));
  return s;
};

// place an order from any tab, then reload the map (stock, queue) and this tab
async function send(body) {
  let msg;
  try {
    const got = await fetch("/.proxy/api/order", {method: "POST", headers: {...ui.headers, "Content-Type": "application/json"}, body: JSON.stringify(body)});
    msg = await got.json();
    if (!msg.message) msg = {ok: false, message: "That did not work."};
  } catch (e) { msg = {ok: false, message: "Could not reach the game."}; }
  ui.msg = msg;
  await ui.refresh();
  await loadNation();
}

function drawTabs() {
  $("tabs").replaceChildren(...TABS.map(([id, label]) => {
    const alert = (id === "nation" && ui.contests) || (id === "updates" && ui.pending);   // a dot when a tile is contested or something waits for your answer
    const b = el("button", {className: "tab" + (ui.tab === id ? " on" : "") + (alert ? " alert" : ""), textContent: label + (alert ? " \u25cf" : "")});
    b.onclick = () => { ui.tab = id; ui.msg = null; drawTabs(); showTab(); };
    return b;
  }));
}

function showTab() {
  $("card").style.display = ui.tab === "tile" ? "" : "none";
  $("npanel").style.display = ui.tab === "tile" ? "none" : "";
  if (ui.tab !== "tile") loadNation();
}

async function loadNation() {
  if (ui.tab === "tile") return;
  let got;
  try { got = await fetch("/.proxy/api/nation", {headers: ui.headers}); } catch (e) { return; }
  if (!got.ok) return;
  const n = await got.json();
  const out = [];
  if (ui.msg) out.push(el("div", {className: "msg " + (ui.msg.ok ? "ok" : "bad"), textContent: ui.msg.message}));
  out.push(...{nation: nationTab, updates: updatesTab, trade: tradeTab, pacts: pactsTab, report: reportTab}[ui.tab](n));
  $("npanel").replaceChildren(...out);
}

function nationTab(n) {
  const p = n.people;
  const meter = (label, v, room) => el("div", {className: "meter"}, line(cap(label), v + " of " + room),
    el("div", {className: "track"}, el("i", {className: room && v >= room ? "full" : "", style: "width:" + Math.min(100, room ? 100 * v / room : 0) + "%"})));
  // every list row is the same grid: name, minus, value, plus, about how much it makes, and a spare column, so nothing moves
  const ctl = (name, dim, minus, value, plus, est, extra, tip) => {
    const r = el("div", {className: "ctl" + (dim ? " dim" : ""), title: tip || ""}, el("span", {textContent: name}), minus,
                 el("b", {textContent: value}), plus, el("span", {className: "est", textContent: est}), extra || el("span"));
    return r;
  };
  const made = (amount) => amount ? "+" + amount + "/night" : "";
  const prio = (pr) => {
    const step = (d) => button(d > 0 ? "+" : "-", () => send({action: "priority", resource: pr.resource, value: pr.value + d}),
                               !pr.can || pr.value + d < 0 || pr.value + d > 5 ? "off" : "plain");
    return ctl(cap(pr.resource), !pr.can, step(-1), String(pr.value), step(1), made(pr.amount));
  };
  const room = Math.max(0, p.soldier_room - p.soldiers);
  const count = input("How many");
  count.type = "number"; count.min = 1; count.value = 1;
  const kinds = [["", "Plain"], ["steel", "Steel" + (n.steel.problem ? " (" + n.steel.problem + ")" : " - costs " + n.steel.cost)]];
  for (const u of n.units) kinds.push([u.unit, cap(u.unit) + (u.problem ? " (" + u.problem + ")" : " - costs " + u.cost)]);
  const kind = choose(kinds);
  const food = n.food.net >= 0 ? "+" + n.food.net : String(n.food.net);
  // advanced mode: an exact number of workers on a resource (or "auto": it follows the priorities)
  const exact = (res) => {
    const set = n.assign[res], working = n.working[res] || 0, room = n.room[res] || 0;
    const step = (d) => button(d > 0 ? "+" : "-", () => send({action: "assign", resource: res, count: Math.max(0, (set === undefined ? working : set) + d)}),
                               !room ? "off" : "plain");
    const auto = button("Auto", () => send({action: "assign", resource: res, count: null}), set === undefined ? "off" : "plain");
    auto.classList.add("auto");
    const pr = n.priorities.find((x) => x.resource === res);
    const tip = (room ? "Room for " + room + " workers. " : "No land for this yet. ") + (set !== undefined && working < set ? working + " of the " + set + " fit." : "");
    return ctl(cap(res), !room, step(-1), String(set === undefined ? working : set), step(1), made(pr && pr.amount), auto, tip);
  };
  const advanced = n.mode === "advanced";
  const contestRow = (c) => el("div", {className: "contest"},
    note(c.tile + " is contested with " + c.against.join(", ") + ". Give it up, or fight: the soldiers each side has within 3 tiles roll a duel when the night starts. No answer counts as fight."),
    el("div", {className: "pair"},
      button("Give up", () => send({action: "contest", tile: c.tile, choice: "yield"}), c.choice === "yield" ? "off" : "plain"),
      button("Fight", () => send({action: "contest", tile: c.tile, choice: "fight"}), c.choice === "fight" ? "off" : "primary")),
    note(c.choice ? "You chose to " + (c.choice === "yield" ? "give it up." : "fight.") : "You haven't chosen yet."));
  const modeBox = box("Mode: " + (advanced ? "advanced" : "basic"),
    note(advanced ? "You set exact worker counts. Resources left on auto follow the priorities below. Counts are kept if you go back to basic."
                  : "Priorities decide who works on what. Advanced lets you set exact worker counts and send part of an army."),
    button(advanced ? "Switch to basic" : "Switch to advanced", () => send({action: "mode", mode: advanced ? "basic" : "advanced"}),
           !advanced && !n.advanced_ok ? "off" : "plain"),
    ...(!advanced && !n.advanced_ok ? [note("An admin has switched advanced mode off on this server.")] : []));
  return [
    modeBox,
    ...(n.contests.length ? [box("Contested tiles", ...n.contests.map(contestRow))] : []),
    ...(advanced ? [box("Workers on each resource", ...n.priorities.map((pr) => exact(pr.resource)),
        note("A tile holds a fixed number of workers, so a count can't go past the room shown. 0 turns a resource off."))] : []),
    box("People", meter(n.worker + "s", p.workers, p.worker_room), meter(n.soldier + "s", p.soldiers, p.soldier_room)),
    box("Work tonight", ...(n.work.length ? n.work.map((w) => line(cap(w.resource), w.workers + " working, +" + w.amount)) : [note("Nobody is working.")]),
        line("Food", food + " a night" + (n.food.nights == null ? "" : ", lasts about " + n.food.nights + " nights"))),
    box("Priorities (about how much each makes tonight)", ...n.priorities.map(prio), note(advanced ? "These only decide the resources you left on auto." :
        "0 turns a resource off, 5 is top. Only the proportions matter. Greyed resources need land that makes them.")),
    box("Train " + n.soldier + "s", note("Each costs " + n.train_cost + " and a " + n.worker + ". Room for " + room + " more."), count, kind,
        button("Train", () => send({action: "train", n: Number(count.value), steel: kind.value === "steel", unit: ["", "steel"].includes(kind.value) ? null : kind.value}), room ? "primary" : "off")),
    box("Advice", ...n.advice.map((a) => note("- " + a))),
  ];
}

function tradeTab(n) {
  const offerRow = (o) => box("", note(plain(o.line)),
    o.mine ? button("Withdraw", () => send({action: "trade_decline", id: o.id}), "plain")
           : el("div", {className: "pair"}, button("Accept", () => send({action: "trade_accept", id: o.id}), "primary"),
                                            button("Decline", () => send({action: "trade_decline", id: o.id}), "plain")));
  const to = choose([["", "Anyone with a Market (trade board)"], ...n.others.map((m) => [m, m])]);
  const give = input("You give, e.g. wood 10"), get = input("You want, e.g. stone 5");
  const how = choose([["public", "Public: posted on the trade board"], ["private", "Private: only you and the named nation"]]);
  const by = choose([["", "Goods go: by train if one is free, else caravan"], ["train", "Goods go by train (needs a train and rails)"], ["caravan", "Goods go by caravan (slower)"]]);
  return [
    ...(n.offers.length ? n.offers.map(offerRow) : [el("p", {className: "hint", textContent: "No open offers right now."})]),
    box("Make an offer", to, give, get, how, ...(n.trains ? [by] : []),
        button("Send offer", () => send({action: "trade_offer", to: to.value, give: give.value, get: get.value, private: how.value === "private", by: n.trains ? by.value : ""}), "primary"),
        note("Both sides need a Market. A private offer needs a nation named above.")),
  ];
}

function pactsTab(n) {
  const row = (p) => {
    const acts = p.state === "waiting for you"
      ? el("div", {className: "pair"}, button("Accept", () => send({action: "pact_accept", id: p.id}), "primary"),
                                       button("Decline", () => send({action: "pact_decline", id: p.id}), "plain"))
      : p.state === "active" ? button("Break pact", () => send({action: "pact_break", id: p.id}), "plain") : note("Waiting for them to answer.");
    return box("", line("Pact " + p.id + " with " + p.other, p.state), note(p.text), acts);
  };
  const to = choose(n.others.map((m) => [m, m])), text = input("What you promise each other");
  return [
    ...(n.pacts.length ? n.pacts.map(row) : [el("p", {className: "hint", textContent: "You have no pacts."})]),
    box("Propose a pact", to, text, button("Propose", () => send({action: "pact_propose", to: to.value, text: text.value}), "primary"),
        note("A pact is a public record only. Nothing enforces it, and breaking it shows in the next report.")),
  ];
}

function updatesTab(n) {   // what waits for your answer, then your last nights' reports
  const u = n.updates, todo = u.pending;
  const ask = (text, ...buttons) => box("", note(plain(text)), el("div", {className: "pair"}, ...buttons));
  const rows = [
    ...todo.pacts.map((p) => ask(p.line, button("Accept", () => send({action: "pact_accept", id: p.id}), "primary"),
                                         button("Decline", () => send({action: "pact_decline", id: p.id}), "plain"))),
    ...todo.offers.map((o) => ask(o.line, button("Accept", () => send({action: "trade_accept", id: o.id}), "primary"),
                                          button("Decline", () => send({action: "trade_decline", id: o.id}), "plain"))),
    ...todo.contests.map((c) => ask(c.tile + " is contested with " + c.against.join(", ") + ". No answer counts as fight.",
                                    button("Give up", () => send({action: "contest", tile: c.tile, choice: "yield"}), "plain"),
                                    button("Fight", () => send({action: "contest", tile: c.tile, choice: "fight"}), "primary"))),
  ];
  return [
    ...(rows.length ? [el("h3", {textContent: "Needs your answer"}), ...rows] : [el("p", {className: "hint", textContent: "Nothing is waiting for your answer."})]),
    ...(u.past.length ? [el("h3", {textContent: "Your last nights"}), ...u.past.map((r) => box("Night " + r.tick, el("div", {className: "text", textContent: plain(r.text)})))]
                      : [el("p", {className: "hint", textContent: "Your reports will collect here after each night."})]),
  ];
}

function reportTab(n) {
  return [
    box("Last night", el("div", {className: "text", textContent: plain(n.report)})),
    box("Standings", el("div", {className: "text", textContent: plain(n.standings)})),
    box("Private messages", note("Off until you turn them on. When on, the bot DMs you your night report and private trade offers."),
        button(n.dm ? "Turn off" : "Turn on", () => send({action: "dm", on: !n.dm}), n.dm ? "plain" : "primary"),
        line("Now", n.dm ? "on" : "off")),
  ];
}

// ---- the map view: pan by dragging, zoom with the wheel, pinch or the buttons, click a tile ----
function showMap(data, headers) {
  const canvas = $("map"), ctx = canvas.getContext("2d");
  let color, tiles, at;
  function adopt() {                                               // (re)build what is drawn from the latest data
    color = Object.fromEntries(data.nations.map((n) => [n.id, n.color]));
    tiles = data.tiles.map((t) => {
      const [q, r] = t.k.split(",").map(Number);
      return {...t, q, r, x: SQRT3 * (q + r / 2), y: 1.5 * r};      // position in units of one hex size
    });
    at = Object.fromEntries(tiles.map((t) => [t.k, t]));
  }
  adopt();
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

  function icon(name, x, y, colour, more = 1) {
    const k = size / 18 * more;
    ctx.save(); ctx.translate(x, y); ctx.scale(k, k);
    ctx.strokeStyle = colour; ctx.lineWidth = 1.3; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.stroke(PATH[name]); ctx.restore();
  }

  // army: the nation's dark colour with a white outline, the soldier count inside (a "?" for other nations' armies)
  function shield(x, y, k, owner, label, steel) {
    ctx.save(); ctx.translate(x, y); ctx.scale(k, k);
    const shape = steel ? DIAMOND : SHIELD;                         // steel soldiers: a diamond with a silver rim
    ctx.fillStyle = dark(color[owner] || "#888888"); ctx.fill(shape);
    ctx.strokeStyle = steel ? "#cfd6de" : "#fff"; ctx.lineWidth = steel ? 3 : 2; ctx.lineJoin = "round"; ctx.stroke(shape);
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
    ctx.fillStyle = BACK; ctx.fillRect(0, 0, W, H);
    const sx = (t) => W / 2 + (t.x - cx) * size, sy = (t) => H / 2 + (t.y - cy) * size;
    const seen = tiles.filter((t) => {
      const x = sx(t), y = sy(t);
      return !(x < -size * 2 || y < -size * 2 || x > W + size * 2 || y > H + size * 2);
    });
    ctx.lineJoin = "round"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const t of seen) {                                          // 1. tile colours, terrain icons, buildings
      const x = sx(t), y = sy(t);
      const mine = t.visible && t.owner !== null && (t.terrain !== "water" || t.building === "port");
      hex(x, y, size);
      ctx.fillStyle = !t.visible ? FOG : t.terrain === "water" && !mine ? TERRAIN.water : mine ? color[t.owner] || "#888" : TERRAIN[t.terrain] || NEUTRAL;
      ctx.fill();
      ctx.strokeStyle = "rgba(0,0,0,.25)"; ctx.lineWidth = 1; ctx.stroke();
      if (!t.visible) continue;
      if (t.building) {
        const crown = t.building === "capital", r = size * (crown ? 0.4 : 0.36);
        const ink = crown ? dark(color[t.owner] || "#888888") : "#2c2c2a";
        ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
        if (crown) { ctx.strokeStyle = ink; ctx.lineWidth = 2; ctx.stroke(); }
        icon(t.building, x, y, ink, crown ? 0.85 : 0.8);
        for (let i = 1; i < (t.level || 1); i++) {                    // one dot per upgrade
          ctx.fillStyle = ink; ctx.beginPath(); ctx.arc(x + (i - (t.level - 1) / 2 - 0.5) * size * 0.12, y + r - size * 0.07, size * 0.045, 0, 7); ctx.fill();
        }
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
      if (!t.visible || t.owner === null || (t.terrain === "water" && t.building !== "port")) continue;
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
      shield(x, y, k, a.owner, a.owner === data.me ? (a.unit ? a.unit[0].toUpperCase() : "") + a.n : "?", a.steel);
    }
    for (const c of data.caravans || []) {                           // 5. caravans: carts, moved to the top-left corner if something else is there
      const t = at[c.tile]; if (!t || !seen.includes(t)) continue;
      let x = sx(t), y = sy(t), k = size / 21 * 1.5;
      if (t.building || data.armies.some((a) => a.tile === c.tile)) { x -= size * 0.5; y -= size * 0.4; k *= 0.7; } else x -= 0.5 * k;
      cart(x, y, k, c.from);
    }
    for (const k of data.contested || []) {                          // 5b. contested tiles: a dashed red outline
      const t = at[k]; if (!t || !seen.includes(t)) continue;
      hex(sx(t), sy(t), size * 0.88); ctx.save(); ctx.setLineDash([6, 4]); ctx.strokeStyle = "#e5484d"; ctx.lineWidth = 3; ctx.stroke(); ctx.restore();
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
    await refresh();
    return open(at[t.k] || t, msg);
  }

  // fetch the latest map: the game changes at every tick, and orders change your stock, armies and queue
  async function refresh() {
    let got;
    try { got = await fetch("/.proxy/api/map", {headers}); } catch (e) { return false; }
    if (!got.ok) return false;
    const fresh = await got.json(), newTick = fresh.tick !== data.tick;
    Object.assign(data, fresh);
    adopt(); showBars(data, data.name); draw();
    if (newTick) loadNation();
    return newTick;
  }

  // keep up with the game: look again every 15 seconds while the page is open, and show the new tile card after a tick
  setInterval(async () => {
    if (document.hidden) return;
    if (await refresh() && selected && at[selected]) open(at[selected]);
  }, 15000);

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
  for (const x of ["paper", "industrial"]) {                 // each theme chip switches its theme on, or back to normal
    $(x).classList.toggle("on", theme() === x);
    $(x).onclick = () => {
      setTheme(theme() === x ? "normal" : x);
      for (const y of ["paper", "industrial"]) $(y).classList.toggle("on", theme() === y);
      legend(); draw();
    };
  }
  $("terrain").onclick = () => { terrain = !terrain; $("terrain").classList.toggle("on", terrain); draw(); };
  addEventListener("resize", resize);
  ui.headers = headers; ui.refresh = refresh;
  drawTabs();

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
  const legend = () => $("legend").replaceChildren(
    ...data.nations.map((n) => key(swatch(n.color), n.name + (n.id === data.me ? " (you)" : ""))),
    key(swatch(NEUTRAL), "Free land"), key(swatch(TERRAIN.water), "Water"),
    key(marker(CART_D, myDark, true), "Caravan"), key(marker("M0 -10L8 -7V1Q8 8 0 11Q-8 8 -8 1V-7Z", myDark, false), "Army"));
  legend();

  $("status").style.display = "none";
  $("app").style.display = "flex";
  resize();
}

function showOver(me) {   // the game is over (or this nation has fallen): a crown, who won, and the final standings
  const o = me.over, won = o && o.you_won;
  const title = won ? "Victory" : o ? "Game over" : "Your nation has fallen";
  const sub = o ? (o.winner ? o.winner + " wins: " + o.reason : "The game has ended.") : "You are out of the game. The others play on without you.";
  const table = o ? el("table", {}, el("tr", {}, ...["#", "Nation", "Land", "People"].map((t) => el("th", {textContent: t}))),
    ...o.rows.map((r, i) => el("tr", {className: (r.me ? "me " : "") + (r.alive ? "" : "out")},
      ...[i + 1, r.name + (r.me ? " (you)" : "") + (r.alive ? "" : " - fallen"), r.land, r.population].map((v) => el("td", {textContent: v}))))) : "";
  const art = me.mode === "industrialism" ? (won ? "/static/over_beam.png" : "/static/over_broken.png")   // Industrialism: a whole beam, or a broken one
                                           : (won ? "/static/crown.png" : "/static/crown_fallen.png");
  $("over").replaceChildren(el("img", {src: art, alt: ""}),
    el("h1", {textContent: title}), el("p", {className: "sub", textContent: sub}), table);
  $("status").style.display = "none";
  $("over").style.display = "block";
}

function showIntro(me) {   // first launch only: a quick look at the game and how to win
  let seen = false;
  try { seen = localStorage.getItem("intro") === "1"; } catch (e) {}
  if (seen) return;
  const done = () => { try { localStorage.setItem("intro", "1"); } catch (e) {} $("intro").style.display = "none"; };
  const art = me.mode === "industrialism" ? [el("div", {className: "art"}, el("img", {src: "/static/coal.png", alt: ""}), el("img", {src: "/static/i_beam.png", alt: ""}))] : [];
  $("intro").replaceChildren(el("div", {className: "card"}, ...art,
    el("h2", {textContent: "Welcome, " + me.nation}),
    el("p", {textContent: "Everyone plays at once on one hex map. Pick tiles to claim, build, train armies and trade. Orders you give are queued and happen together at the nightly tick."}),
    el("p", {textContent: "Your nation needs food and upkeep each night. Grow, make pacts, and watch your neighbours."}),
    el("h3", {textContent: "How to win"}),
    el("p", {textContent: me.win}),
    el("button", {textContent: "Got it", onclick: done})));
  $("intro").style.display = "flex";
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
  const headers = {Authorization: "Bearer " + access_token, "X-Guild-Id": sdk.guildId || ""};   // which server this game belongs to
  const me = await (await fetch("/.proxy/api/me", {headers})).json();
  if (!me.nation) return say("Hello " + me.name + ". You are not in a running game.");
  if (me.over || me.fallen) return showOver(me);
  const data = await (await fetch("/.proxy/api/map", {headers})).json();
  data.name = me.nation;
  showBars(data, me.nation);
  if (me.mode !== "industrialism" && theme() === "industrial") setTheme("normal");        // that theme belongs to Industrialism games
  showMap(data, headers);
  $("industrial").style.display = me.mode === "industrialism" ? "" : "none";
  showIntro(me);
}

main().catch((e) => say("Something went wrong: " + (e && e.message ? e.message : e)));
