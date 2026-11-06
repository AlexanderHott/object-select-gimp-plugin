"""Selection state shared by the GTK UI and tests. No GIMP objects live here."""

from PIL import Image, ImageChops


class Selection:
    def __init__(self, size):
        self.mask = Image.new("L", size)
        self.history = []
        self.future = []
        self.revision = 0

    def accept(self, candidate, mode="new"):
        if candidate.size != self.mask.size:
            raise ValueError("Selection dimensions differ from the layer.")
        self.history.append(self.mask.copy())
        # Bound undo memory to 128 MiB, retaining at least one previous mask.
        limit = max(1, min(20, 128_000_000 // (self.mask.width * self.mask.height)))
        self.history = self.history[-limit:]
        self.future = []
        self.revision += 1
        if mode == "new":
            self.mask = candidate.copy()
        elif mode == "add":
            self.mask = ImageChops.lighter(self.mask, candidate)
        elif mode == "subtract":
            self.mask = ImageChops.subtract(self.mask, candidate)
        elif mode == "intersect":
            self.mask = ImageChops.darker(self.mask, candidate)
        else:
            raise ValueError("Unknown selection operation.")

    def undo(self):
        if self.history:
            self.future.append(self.mask)
            self.mask = self.history.pop()
            self.revision += 1

    def redo(self):
        if self.future:
            self.history.append(self.mask)
            self.mask = self.future.pop()
            self.revision += 1

    def clear(self):
        self.accept(Image.new("L", self.mask.size))


def coverage(mask):
    """Return the selected share of the layer, counting soft pixels by their weight."""
    total = sum(value * count for value, count in enumerate(mask.histogram()))
    return total / (255 * mask.width * mask.height)


def effective_mode(mode, shift=False, ctrl=False, alt=False):
    """Apply GIMP's selection modifiers, with Alt as a second way to subtract."""
    if shift and ctrl:
        return "intersect"
    if shift:
        return "add"
    if ctrl or alt:
        return "subtract"
    return mode


def pick_candidate(masks, point=None, smaller=False):
    """Prefer masks containing the prompt; SAM scores already order candidates."""
    if not masks:
        raise ValueError("The model returned no masks.")
    candidates = [mask for mask in masks if mask.getbbox()]
    if point:
        contained = [mask for mask in candidates if mask.getpixel(point) > 127]
        if not contained:
            return Image.new("L", masks[0].size)
        candidates = contained
    if not candidates:
        return masks[0]
    if smaller:
        return min(candidates, key=lambda mask: sum(mask.histogram()[128:]))
    return candidates[0]


def image_coordinates(x, y, viewport, image_size):
    scale = min(viewport[0] / image_size[0], viewport[1] / image_size[1])
    left = (viewport[0] - image_size[0] * scale) / 2
    top = (viewport[1] - image_size[1] * scale) / 2
    px, py = int((x - left) // scale), int((y - top) // scale)
    if 0 <= px < image_size[0] and 0 <= py < image_size[1]:
        return px, py
    return None
