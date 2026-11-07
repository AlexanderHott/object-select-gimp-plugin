"""Selection state shared by the GTK UI and tests. No GIMP objects live here."""

import zlib
from dataclasses import dataclass

from PIL import Image, ImageChops


@dataclass(frozen=True)
class Pick:
    """One recorded pick, with what's needed to refine it later.

    The mask holds the compressed refined result, or None while the pick's edges are coarse.
    Picks compress well, so keeping them costs far less than undo's full-size masks.
    """

    request: dict
    mode: str
    mask: bytes | None = None


def pack(mask):
    return zlib.compress(mask.tobytes(), 1)


def unpack(data, size):
    return Image.frombytes("L", size, zlib.decompress(data))


def combine(mask, candidate, mode):
    if mode == "new":
        return candidate.copy()
    if mode == "add":
        return ImageChops.lighter(mask, candidate)
    if mode == "subtract":
        return ImageChops.subtract(mask, candidate)
    if mode == "intersect":
        return ImageChops.darker(mask, candidate)
    raise ValueError("Unknown selection operation.")


def superseded(picks, index):
    """A correction re-decides the pick before it, so only a chain's last result counts."""
    following = index + 1
    return following < len(picks) and picks[following].mode == "correction"


def refine_plan(picks):
    """List (pick index, refine) requests for a replay, in the order the worker must run them.

    Corrections feed on the previous pick's logits, so a chain is decoded from its start. Each
    chain with a coarse result is refined. The last chain always runs, because a later
    correction click needs its logits back in the worker. Its stored result stays in use.
    """
    plan = []
    start = 0
    for index in range(len(picks)):
        if superseded(picks, index):
            continue
        coarse = picks[index].mask is None
        if coarse or index == len(picks) - 1:
            plan += [(item, False) for item in range(start, index)]
            plan.append((index, coarse))
        start = index + 1
    return plan


def replay(origin, picks, results):
    """Rebuild the selection from its picks and return it with the last pick's base.

    The results map pick indices to masks. Superseded picks don't need one.
    """
    mask = origin
    base = Image.new("L", origin.size)
    for index, pick in enumerate(picks):
        result = results.get(index)
        if result is None and pick.mask is not None:
            result = unpack(pick.mask, origin.size)
        if pick.mode != "correction":
            base = mask if pick.mode == "add" else Image.new("L", origin.size)
        if superseded(picks, index):
            continue
        if pick.mode == "correction":
            mask = ImageChops.lighter(base, result)
        else:
            mask = combine(mask, result, pick.mode)
    return mask, base


class Selection:
    def __init__(self, size):
        self.mask = Image.new("L", size)
        self.history = []
        self.future = []
        self.revision = 0
        # Picks replay from this mask, which holds anything no longer tied to a pick.
        self.origin = self.mask
        self.picks = ()

    def _remember(self):
        self.history.append((self.mask, self.origin, self.picks))
        # Bound undo memory to 128 MiB, retaining at least one previous mask.
        limit = max(1, min(20, 128_000_000 // (self.mask.width * self.mask.height)))
        self.history = self.history[-limit:]
        self.future = []
        self.revision += 1

    def accept(self, candidate, mode="new", pick=None):
        """Apply a mask. Without a pick, the result can no longer be split back into picks."""
        if candidate.size != self.mask.size:
            raise ValueError("Selection dimensions differ from the layer.")
        result = combine(self.mask, candidate, mode)
        self._remember()
        self.mask = result
        if pick is None:
            self.origin, self.picks = result, ()
        elif pick.mode == "new":
            self.origin, self.picks = Image.new("L", result.size), (pick,)
        else:
            self.picks = (*self.picks, pick)

    def unrefined(self):
        """Count picks whose coarse edges still show."""
        return sum(
            pick.mask is None and not superseded(self.picks, index)
            for index, pick in enumerate(self.picks)
        )

    def replace(self, mask, picks):
        """Swap in a replayed result as one undo step."""
        self._remember()
        self.mask = mask
        self.picks = tuple(picks)

    def undo(self):
        if self.history:
            self.future.append((self.mask, self.origin, self.picks))
            self.mask, self.origin, self.picks = self.history.pop()
            self.revision += 1

    def redo(self):
        if self.future:
            self.history.append((self.mask, self.origin, self.picks))
            self.mask, self.origin, self.picks = self.future.pop()
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
