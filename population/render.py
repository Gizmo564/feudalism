"""Draws the map as a PNG in the "political" style: flat nation colours, dark borders, line icons.
Buildings get a white badge. The overview puts the badge in place of the terrain icon; a zoomed
view (center + radius) shows both."""
import colorsys
import io
import math
import re

from PIL import Image, ImageDraw, ImageFont

from . import views, world as W
from .hexes import parse, within, neighbors

BG = (250, 249, 245)
NEUTRAL, WATER, FOG = (237, 234, 224), (220, 235, 247), (196, 194, 186)
TINT = {"field": (226, 232, 200), "forest": (190, 210, 170), "hills": (226, 212, 176), "mountain": (200, 198, 194),
        "water": WATER}  # neutral-tile colours when terrain is shown as colour
INK, MUTED = (44, 44, 42), (95, 94, 90)
SS = 2  # draw at double size, then shrink, for smooth edges

TERRAIN_ICON = {
    "field": "M-5 -3.5H5M-5 0H5M-5 3.5H5",
    "forest": "M-4.5 1L0 -6L4.5 1ZM0 1V5",
    "hills": "M-7 4Q-3.5 -5 0 4Q3.5 -5 7 4",
    "mountain": "M-7 5L-2 -5L2 1L4 -2L7 5",
    "water": "M-7 -2Q-3.5 -6 0 -2T7 -2M-7 3Q-3.5 -1 0 3T7 3",
}
BUILDING_ICON = {
    "home": "M-4 0L0 -4L4 0M-2.8 -1V3.5H2.8V-1",
    "barracks": "M-3.5 -4H3.5V0.5Q3.5 3.5 0 5Q-3.5 3.5 -3.5 0.5Z",
    "market": "M-4.2 -1L-3.2 -4H3.2L4.2 -1ZM-3 -1V4H3V-1",
    "wall": "M-4 4V-3H-2.6V-1.6H-1.2V-3H1.2V-1.6H2.6V-3H4V4Z",
}
OIL_DROP = "M0 -6Q5 0 4 3Q3 6 0 6Q-3 6 -4 3Q-5 0 0 -6Z"
OIL_SHINE = "M-2 2Q-2 3.6 -0.6 4.2"
OIL_COLOUR = (52, 44, 40)
# (neighbour offset, first corner, second corner) for each hex edge; corner i sits at angle 60*i-30 degrees
EDGES = [((1, 0), 0, 1), ((0, 1), 1, 2), ((-1, 1), 2, 3), ((-1, 0), 3, 4), ((0, -1), 4, 5), ((1, -1), 5, 0)]

FIRST_COLOURS = [(127, 119, 221), (216, 90, 48), (212, 83, 126), (55, 138, 221),
                 (186, 117, 23), (29, 158, 117), (99, 153, 34)]


def nation_colours(nid):
    """(fill, dark outline) colours. The first seven are hand-picked; the rest are spread by hue."""
    if nid < len(FIRST_COLOURS):
        fill = FIRST_COLOURS[nid]
    else:
        h = ((nid - len(FIRST_COLOURS)) * 0.61803398875 + 0.07) % 1.0
        r, g, b = colorsys.hsv_to_rgb(h, 0.6, 0.82)
        fill = (int(r * 255), int(g * 255), int(b * 255))
    return fill, tuple(int(c * 0.6) for c in fill)


def nation_color(nid):
    return nation_colours(nid)[0]


def _font(size, bold=True):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for path in ("/usr/share/fonts/truetype/dejavu/" + name, name, "/Library/Fonts/Arial Bold.ttf",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(path, max(1, int(size)))
        except OSError:
            continue
    return ImageFont.load_default(max(1, int(size)))  # Pillow's built-in scalable font


def _mix(a, b, t):
    return tuple(int(a[i] * t + b[i] * (1 - t)) for i in range(3))


def path_points(d):
    """Turn a small SVG path (absolute M L H V Q T Z only) into lists of points."""
    tokens = re.findall(r"[MLHVQTZ]|-?\d+\.?\d*", d)
    subs, cur, i = [], [], 0
    x = y = cx = cy = 0.0
    cmd = None
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd == "Z":
                if cur:
                    cur.append(cur[0])
                continue
        if cmd == "M":
            if cur:
                subs.append(cur)
            x, y = float(tokens[i]), float(tokens[i + 1])
            i += 2
            cur = [(x, y)]
            cmd = "L"
        elif cmd == "L":
            x, y = float(tokens[i]), float(tokens[i + 1])
            i += 2
            cur.append((x, y))
        elif cmd == "H":
            x = float(tokens[i])
            i += 1
            cur.append((x, y))
        elif cmd == "V":
            y = float(tokens[i])
            i += 1
            cur.append((x, y))
        elif cmd in ("Q", "T"):
            if cmd == "Q":
                cx, cy = float(tokens[i]), float(tokens[i + 1])
                ex, ey = float(tokens[i + 2]), float(tokens[i + 3])
                i += 4
            else:
                cx, cy = 2 * x - cx, 2 * y - cy
                ex, ey = float(tokens[i]), float(tokens[i + 1])
                i += 2
            for s in range(1, 11):
                t = s / 10
                cur.append(((1 - t) ** 2 * x + 2 * (1 - t) * t * cx + t * t * ex,
                            (1 - t) ** 2 * y + 2 * (1 - t) * t * cy + t * t * ey))
            x, y = ex, ey
    if cur:
        subs.append(cur)
    return subs


class Canvas:
    """A drawing surface that works in final-image pixels but draws at SS times the size."""

    def __init__(self, w, h):
        self.img = Image.new("RGB", (int(w * SS), int(h * SS)), BG)
        self.d = ImageDraw.Draw(self.img)

    def polygon(self, pts, fill, outline=None, width=1):
        p = [(x * SS, y * SS) for x, y in pts]
        self.d.polygon(p, fill=fill)
        if outline:
            self.d.line(p + [p[0]], fill=outline, width=max(1, round(width * SS)), joint="curve")

    def line(self, a, b, fill, width):
        w = max(1, round(width * SS))
        self.d.line([(a[0] * SS, a[1] * SS), (b[0] * SS, b[1] * SS)], fill=fill, width=w)
        for p in (a, b):  # round caps
            r = w / 2
            self.d.ellipse([p[0] * SS - r, p[1] * SS - r, p[0] * SS + r, p[1] * SS + r], fill=fill)

    def circle(self, x, y, r, fill, outline=None, width=1):
        self.d.ellipse([(x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS], fill=fill, outline=outline,
                       width=max(1, round(width * SS)) if outline else 0)

    def rect(self, x0, y0, x1, y1, fill, outline=None, width=1):
        self.d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=3 * SS, fill=fill, outline=outline,
                                 width=max(1, round(width * SS)) if outline else 0)

    def icon(self, path, x, y, scale, color, width=1.3):
        for pts in path_points(path):
            p = [((x + px * scale) * SS, (y + py * scale) * SS) for px, py in pts]
            w = max(1, round(width * scale * SS))
            self.d.line(p, fill=color, width=w, joint="curve")
            for q in (p[0], p[-1]):
                self.d.ellipse([q[0] - w / 2, q[1] - w / 2, q[0] + w / 2, q[1] + w / 2], fill=color)

    def oil(self, x, y, scale):
        """A small dark oil drop with a light highlight."""
        pts = path_points(OIL_DROP)[0]
        self.d.polygon([((x + px * scale) * SS, (y + py * scale) * SS) for px, py in pts], fill=OIL_COLOUR,
                       outline=(255, 255, 255), width=max(1, round(0.8 * scale * SS)))
        self.icon(OIL_SHINE, x, y, scale, (210, 205, 195), 0.9)

    def text(self, x, y, s, size, fill, bold=True, halo=None, anchor="mm"):
        self.d.text((x * SS, y * SS), s, font=_font(size * SS, bold), fill=fill, anchor=anchor,
                    stroke_width=3 * SS // 2 if halo else 0, stroke_fill=halo)

    def png(self, w, h):
        out = io.BytesIO()
        self.img.resize((int(w), int(h)), Image.LANCZOS).save(out, "PNG")
        return out.getvalue()


def render(world, rules, nid=None, center=None, radius=None, tile=30, legend=True, coords=False,
           tint=False, icons=True, claim=False, tidy=False, mark=None, buildings=True, people=False, production=None):
    """Return PNG bytes. nid=None is the public view (everything shown, no armies).
    center + radius draws a zoomed window around one tile (terrain icon and building badge both shown).
    tint: terrain as tile colour. icons: terrain line icons (overview/zoom). claim: highlight tiles the viewer
    can claim (needs nid). coords: True = every tile, "claim" = claimable tiles only. tidy: names below the land,
    army counts in a tile corner. mark: ring one tile (the selected one). buildings: show building badges
    (the capital always shows). people: tag your homes/barracks/capital with how many people they house.
    production: {tile: "F5"} small tags on your working tiles (resource letter and workers)."""
    if nid is None:
        info = {k: {"k": k, "terrain": t["terrain"], "oil": W.oil_shown(world, t), "visible": True, "owner": t["owner"],
                    "building": t["building"]} for k, t in world["tiles"].items()}
    else:
        info = {v["k"]: v for v in views.map_view(world, rules, nid)}
    keys = set(info)
    zoomed = center is not None and radius is not None
    if zoomed:
        keys &= set(within(center, radius))
    s = tile
    K = s / 21.0
    bw = 3 * (0.7 + 0.3 * K)
    pos = {}
    for k in keys:
        q, r = parse(k)
        pos[k] = (s * math.sqrt(3) * (q + r / 2), s * 1.5 * r)
    pad = s + 8
    ox, oy = pad - min(p[0] for p in pos.values()), pad - min(p[1] for p in pos.values())
    map_w = max(p[0] for p in pos.values()) - min(p[0] for p in pos.values()) + 2 * pad
    map_h = max(p[1] for p in pos.values()) - min(p[1] for p in pos.values()) + 2 * pad
    key_rows = len(BUILDING_ICON) + len(TERRAIN_ICON) + 3
    legend_w = 200 if legend else 0
    height = max(map_h, 60 + 22 * (len(world["nations"]) + key_rows) if legend else map_h)
    width = map_w + legend_w
    cv = Canvas(width, height)

    claimable = set()
    if nid is not None and (claim or coords == "claim"):
        mine = {k for k in info if info[k]["owner"] == nid}
        for k in mine:
            q, r = parse(k)
            for dq, dr in [e[0] for e in EDGES]:
                o = info.get("%d,%d" % (q + dq, r + dr))
                if o and o["k"] in keys and o["visible"] and o["owner"] is None and o["terrain"] != "water":
                    claimable.add(o["k"])

    def corner(k, i, scale=1.0):
        x, y = pos[k][0] + ox, pos[k][1] + oy
        a = math.radians(60 * i - 30)
        return x + s * scale * math.cos(a), y + s * scale * math.sin(a)

    order = sorted(keys, key=lambda k: pos[k][1])
    for k in order:
        t = info[k]
        if not t["visible"]:
            fill, line = FOG, FOG
        elif t["terrain"] == "water":
            fill, line = WATER, WATER
        elif t["owner"] is not None:
            fill = line = nation_colours(t["owner"])[0]
        else:
            fill, line = (TINT[t["terrain"]] if tint else NEUTRAL), BG
        if t["owner"] is not None and tint and t["visible"] and t["terrain"] != "water":
            fill = line = _mix(fill, TINT[t["terrain"]], 0.78)
        cv.polygon([corner(k, i) for i in range(6)], fill, line, 1.2 if t["owner"] is not None else 0.8)

    for k in order:  # borders: dark line wherever the next tile has a different owner
        t = info[k]
        if t["owner"] is None:
            continue
        q, r = parse(k)
        dark = nation_colours(t["owner"])[1]
        inset = 0.9
        for (dq, dr), c1, c2 in EDGES:
            other = info.get("%d,%d" % (q + dq, r + dr))
            if other is not None and other["owner"] == t["owner"]:
                continue
            cv.line(corner(k, c1, inset), corner(k, c2, inset), dark, bw)

    if claim:
        mine_col = nation_colours(nid)[1]
        for k in claimable:
            pts = [corner(k, i, 0.84) for i in range(6)]
            for i in range(6):
                cv.line(pts[i], pts[(i + 1) % 6], mine_col, 2.0)

    if mark in pos:
        pts = [corner(mark, i, 0.92) for i in range(6)]
        for i in range(6):
            cv.line(pts[i], pts[(i + 1) % 6], (255, 255, 255), 5)
        for i in range(6):
            cv.line(pts[i], pts[(i + 1) % 6], INK, 2.4)

    for k in order:
        t = info[k]
        if not t["visible"]:
            continue
        x, y = pos[k][0] + ox, pos[k][1] + oy
        owner = t["owner"]
        show_icon = icons and (zoomed or icons != "zoom")
        dark = nation_colours(owner)[1] if owner is not None else MUTED
        if t["oil"]:
            cv.oil(x + 8 * K, y - 8 * K, 0.85 * K)
        if t["building"] == "capital":
            cv.circle(x, y, 10 * K, (255, 255, 255), dark, 2 * (0.7 + 0.3 * K))
            cv.text(x, y + 0.5 * K, world["nations"][owner]["name"][0].upper(), 13 * K, dark)
            if people and owner == nid:
                housing_tag(cv, rules, "capital", x, y + 10 * K + s * 0.2, s)
            continue
        base = fill_of(t, owner)
        icon_col = _mix((24, 95, 165) if t["terrain"] == "water" else MUTED, base, 0.5)
        b = t["building"] if buildings else None
        if b:  # a building takes the place of the terrain icon
            by, br, bs = y, 9 * K, 9 / 6.5 * K
        elif show_icon:
            cv.icon(TERRAIN_ICON[t["terrain"]], x, y, K, icon_col)
        if b:
            cv.circle(x, by, br, (255, 255, 255), dark, 1.5 * (0.7 + 0.3 * K))
            cv.icon(BUILDING_ICON[b], x, by, bs, INK, 1.2)
            if people and owner == nid:
                housing_tag(cv, rules, b, x, by + br + s * 0.2, s)
        if production and k in production and not b:
            tag(cv, production[k], x, y + s * 0.02, s)
        if coords is True or (coords == "claim" and k in claimable):
            cv.text(x, y + s * 0.5 if tidy else y - s * 0.72, k, s * 0.2 if tidy else s * 0.26, INK, bold=False)

    kc = min(K, 1.6)
    for n in world["nations"]:  # nation names at the middle of their land
        mine = [pos[k] for k in keys if info[k]["owner"] == n["id"] and info[k]["visible"]]
        if mine:
            mx = sum(p[0] for p in mine) / len(mine) + ox
            my = sum(p[1] for p in mine) / len(mine) + oy
            if tidy:
                my = max(p[1] for p in mine) + oy + s * 0.95
            else:
                my += 22 * kc
            cv.text(mx, my, n["name"], 12 * kc, nation_colours(n["id"])[1], halo=BG)

    if nid is not None:
        for a in views.armies_view(world, rules, nid):
            if a["tile"] not in keys:
                continue
            x, y = pos[a["tile"]][0] + ox, pos[a["tile"]][1] + oy
            label = str(a["n"]) if a["owner"] == nid else "?"
            w = s * (0.42 + 0.2 * len(label))
            if tidy:  # small tag in the lower-right corner of the tile
                x, y = x + s * 0.5, y + s * 0.45
                w, hh = s * (0.34 + 0.16 * len(label)), s * 0.34
            else:
                hh = s * 0.4
            top = y - hh / 2 if tidy else y + s * 0.3
            cv.rect(x - w / 2, top, x + w / 2, top + hh, (255, 255, 255), nation_colours(a["owner"])[1], 1.5)
            cv.text(x, top + hh / 2, label, s * 0.26 if tidy else s * 0.3, INK)

    if legend:
        x0, y = map_w + 12, 22
        cv.text(x0, y, "Nations", 14, INK, anchor="lm")
        y += 24
        for n in world["nations"]:
            fill, dark = nation_colours(n["id"])
            cv.rect(x0, y - 7, x0 + 16, y + 7, fill if n["alive"] else FOG, dark, 1.5)
            rank = views.rank_title(world, rules, n["id"]) if n["alive"] else None
            label = n["name"] + (" (%s)" % rank if rank else "") + ("" if n["alive"] else " - fallen")
            cv.text(x0 + 24, y, label[:22], 12, MUTED, bold=False, anchor="lm")
            y += 22
        y += 10
        cv.text(x0, y, "Key", 14, INK, anchor="lm")
        y += 22
        for name, path in list(TERRAIN_ICON.items()) + list(BUILDING_ICON.items()):
            if name in BUILDING_ICON:
                cv.circle(x0 + 9, y, 9, (255, 255, 255), MUTED, 1.2)
                cv.icon(path, x0 + 9, y, 1.25, INK, 1.2)
            elif tint and not icons:
                cv.rect(x0, y - 8, x0 + 18, y + 8, TINT[name], MUTED, 1)
            else:
                cv.icon(path, x0 + 9, y, 1.1, MUTED)
            cv.text(x0 + 24, y, name.capitalize(), 12, MUTED, bold=False, anchor="lm")
            y += 22
        cv.oil(x0 + 9, y, 1.2)
        cv.text(x0 + 24, y, "Oil deposit", 12, MUTED, bold=False, anchor="lm")

    return cv.png(width, height)


def tag(cv, label, x, y, s):
    size = s * 0.22
    w = size * 0.62 * len(label) + 6
    cv.rect(x - w / 2, y - size * 0.7, x + w / 2, y + size * 0.7, (255, 255, 255), MUTED, 1)
    cv.text(x, y, label, size, INK)


def housing_tag(cv, rules, building, x, y, s):
    """A small label under a building: how many workers (w) and soldiers (s) it houses."""
    spec = rules["capital"] if building == "capital" else rules["buildings"][building]
    parts = [("%d%s" % (spec[key], letter)) for key, letter in (("houses_workers", "w"), ("houses_soldiers", "s"))
             if spec.get(key)]
    if not parts:
        return
    label = " ".join(parts)
    size = s * 0.22
    w = size * 0.62 * len(label) + 6
    cv.rect(x - w / 2, y - size * 0.7, x + w / 2, y + size * 0.7, (255, 255, 255), MUTED, 1)
    cv.text(x, y, label, size, INK)


def fill_of(t, owner):
    if not t["visible"]:
        return FOG
    if t["terrain"] == "water":
        return WATER
    return nation_colours(owner)[0] if owner is not None else NEUTRAL
