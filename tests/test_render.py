import io

from PIL import Image

from population import render, world as W, tick
from conftest import RULES_PATH


def open_png(data):
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def test_public_overview_renders(rules):
    w = W.new_world(rules, ["a", "b", "c"], 6, 1)
    img = open_png(render.render(w, rules))
    assert img.format == "PNG" and img.width > 300 and img.height > 300


def test_zoom_is_smaller_than_overview(rules):
    w = W.new_world(rules, ["a", "b", "c"], 6, 1)
    full = open_png(render.render(w, rules, legend=False))
    zoom = open_png(render.render(w, rules, nid=0, center=w["nations"][0]["capital"], radius=2, tile=40, legend=False))
    assert zoom.width < full.width and zoom.height < full.height


def test_fog_changes_the_picture(rules):
    w = W.new_world(rules, ["a", "b", "c"], 6, 1)
    clear = render.render(w, rules, nid=0)
    w["settings"]["fog"] = True
    assert render.render(w, rules, nid=0) != clear


def test_renders_after_ticks_and_for_every_nation(rules):
    w = W.new_world(rules, ["a", "b", "c", "d"], 6, 2)
    for _ in range(3):
        tick.run_tick(w, rules)
    for n in w["nations"]:
        render.render(w, rules, nid=n["id"])


def test_nation_colours_are_distinct():
    colours = {render.nation_color(i) for i in range(25)}
    assert len(colours) == 25


def test_coordinate_labels_change_picture(rules):
    w = W.new_world(rules, ["a", "b"], 5, 1)
    assert render.render(w, rules, coords=True) != render.render(w, rules)
