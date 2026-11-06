import importlib.util
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "hover_preview", ROOT / "gimp-plugin" / "object-select" / "preview.py"
)
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def square_mask(size=40, box=(10, 10, 29, 29), value=255):
    mask = Image.new("L", (size, size))
    ImageDraw.Draw(mask).rectangle(box, fill=value)
    return mask


def test_contour_ignores_masks_that_fill_the_visible_crop():
    assert preview.contour(Image.new("L", (20, 20), 255)).getbbox() is None


def test_contour_traces_soft_masks_at_half_coverage():
    assert preview.contour(square_mask(value=128)).getbbox() == (10, 10, 30, 30)
    assert preview.contour(square_mask(value=100)).getbbox() is None


def test_mask_view_stays_black_where_nothing_is_visible_in_the_crop():
    source = Image.new("RGBA", (4, 4), "red")
    assert preview.render_base(source, Image.new("L", (4, 4)), "mask").getpixel((1, 1)) == (0, 0, 0)


def test_black_and_white_views_use_their_named_backdrops():
    source = Image.new("RGBA", (40, 40), "red")
    selected = square_mask()
    assert preview.render_base(source, selected, "black").getpixel((0, 0)) == (0, 0, 0)
    assert preview.render_base(source, selected, "white").getpixel((0, 0)) == (255, 255, 255)
    assert preview.render_base(source, selected, "black").getpixel((20, 20)) == (255, 0, 0)


def test_overlay_outline_stays_visible_on_a_white_image():
    source = Image.new("RGBA", (40, 40), "white")
    result = preview.render_base(source, square_mask(), "overlay")
    assert result.getpixel((10, 20)) == (255, 255, 255), "Outline is white"
    assert sum(result.getpixel((9, 20))) < 3 * 180, "A dark halo separates it from white"


def test_checkerboard_stays_anchored_while_scrolling():
    whole = preview.checkerboard((48, 48))
    scrolled = preview.checkerboard((24, 24), (17, 5))
    assert scrolled.tobytes() == whole.crop((17, 5, 41, 29)).tobytes()


def test_candidate_color_shows_what_a_click_would_do():
    base = Image.new("RGB", (40, 40), "black")
    candidate = square_mask()
    added = preview.render_candidate(base, candidate, "add")
    subtracted = preview.render_candidate(base, candidate, "subtract")
    assert added.getpixel((10, 20)) == (100, 170, 255)
    assert subtracted.getpixel((10, 20)) == (237, 108, 118)
    assert added.getpixel((0, 0)) == (0, 0, 0)


def test_faint_candidate_is_weaker_and_has_no_outline():
    base = Image.new("RGB", (40, 40), "black")
    faint = preview.render_candidate(base, square_mask(), "new", faint=True)
    strong = preview.render_candidate(base, square_mask(), "new")
    assert faint.getpixel((20, 20))[2] < strong.getpixel((20, 20))[2]
    assert faint.getpixel((10, 20)) == faint.getpixel((20, 20))
