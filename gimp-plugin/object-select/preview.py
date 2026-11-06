"""Compose the preview image with PIL only. No GTK or GIMP objects live here."""

from PIL import Image, ImageDraw, ImageFilter

CHECK_SIZE = 12
SELECTION_COLOR = "#62d397"
CANDIDATE_COLORS = {
    "new": "#64aaff",
    "add": "#64aaff",
    "subtract": "#ed6c76",
    "intersect": "#f2b84b",
}


def checkerboard(size, origin=(0, 0)):
    """Draw a checkerboard anchored to the image so it stays put while scrolling."""
    board = Image.new("RGB", size, "white")
    dark = Image.new("RGB", (CHECK_SIZE, CHECK_SIZE), "#cccccc")
    first_row = origin[1] // CHECK_SIZE
    first_column = origin[0] // CHECK_SIZE
    for row in range(first_row, (origin[1] + size[1]) // CHECK_SIZE + 1):
        for column in range(first_column, (origin[0] + size[0]) // CHECK_SIZE + 1):
            if (row + column) % 2:
                board.paste(dark, (column * CHECK_SIZE - origin[0], row * CHECK_SIZE - origin[1]))
    return board


def contour(mask):
    """Return the one-pixel boundary at 50% coverage, including soft masks."""
    edge = mask.point(lambda p: 255 if p > 127 else 0).filter(ImageFilter.FIND_EDGES)
    # PIL leaves the outermost pixels unfiltered, which would frame the visible crop.
    ImageDraw.Draw(edge).rectangle((0, 0, edge.width - 1, edge.height - 1), outline=0)
    return edge


def render_base(source, selected, view, origin=(0, 0)):
    """Render a resized RGBA source and selection mask for one preview mode."""
    if view == "mask":
        return selected.convert("RGB")
    if view == "black":
        backdrop = Image.new("RGB", source.size, "black")
    elif view == "white":
        backdrop = Image.new("RGB", source.size, "white")
    else:
        backdrop = checkerboard(source.size, origin)
    coverage = source.getchannel("A") if view == "overlay" else selected
    result = Image.composite(source.convert("RGB"), backdrop, coverage)
    if view != "overlay" or selected.getbbox() is None:
        return result
    result = Image.composite(
        Image.new("RGB", source.size, SELECTION_COLOR),
        result,
        selected.point(lambda p: round(p * 0.25)),
    )
    # A dark halo keeps the white edge visible on light images.
    edge = contour(selected)
    halo = edge.filter(ImageFilter.MaxFilter(3)).point(lambda p: round(p * 0.6))
    result = Image.composite(Image.new("RGB", source.size, "black"), result, halo)
    return Image.composite(Image.new("RGB", source.size, "white"), result, edge)


def render_candidate(base, candidate, mode, faint=False):
    """Tint a candidate in the color of the operation a click would perform."""
    color = Image.new("RGB", base.size, CANDIDATE_COLORS[mode])
    strength = 0.18 if faint else 0.4
    result = Image.composite(color, base, candidate.point(lambda p: round(p * strength)))
    return result if faint else Image.composite(color, result, contour(candidate))
