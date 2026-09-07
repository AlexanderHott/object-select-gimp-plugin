"""Transfer a layer snapshot and mask through GIMP's public interfaces."""

import gi

gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
gi.require_version("Babl", "0.1")
from gi.repository import Babl, Gegl, Gimp
from PIL import Image


def snapshot(layer):
    width, height = layer.get_width(), layer.get_height()
    if width * height > 24_000_000:
        raise ValueError("Select a layer no larger than 24 megapixels.")
    pixels = layer.get_buffer().get(
        Gegl.Rectangle.new(0, 0, width, height), 1.0, "R'G'B'A u8", Gegl.AbyssPolicy.NONE
    )
    return Image.frombytes("RGBA", (width, height), bytes(pixels))


def write_mask(drawable, mask):
    # Preserve coverage at every image precision; luminance conversion changes soft masks.
    model = Babl.get_name(Babl.format_get_model(drawable.get_format()))
    if model not in ("Y", "Y'", "Y~"):
        raise ValueError("Expected a grayscale mask channel.")
    # These masks are newly created inside an undo group. Direct writes avoid
    # clipping through the image's existing selection during shadow merging.
    buffer = drawable.get_buffer()
    buffer.set(Gegl.Rectangle.new(0, 0, *mask.size), model + " u8", mask.tobytes())
    buffer.flush()
    drawable.update(0, 0, *mask.size)


def apply(image, layer, mask, output="selection", mode="new"):
    if not layer.is_valid() or layer.get_image() != image:
        raise ValueError("The source layer is no longer available.")
    if mask.size != (layer.get_width(), layer.get_height()):
        raise ValueError("The layer dimensions changed. Reopen Object Select.")
    if output == "mask" and layer.get_mask() is not None:
        raise ValueError("This layer already has a mask. Output a selection instead.")
    operations = {
        "new": Gimp.ChannelOps.REPLACE,
        "add": Gimp.ChannelOps.ADD,
        "subtract": Gimp.ChannelOps.SUBTRACT,
        "intersect": Gimp.ChannelOps.INTERSECT,
    }
    operation = operations[mode]
    old_layers = image.get_selected_layers()
    old_channels = image.get_selected_channels()
    temporary = None
    added_mask = None
    image.undo_group_start()
    Gimp.context_push()
    try:
        if output == "mask":
            added_mask = layer.create_mask(Gimp.AddMaskType.WHITE)
            if added_mask is None or not layer.add_mask(added_mask):
                raise RuntimeError("GIMP could not create the layer mask.")
            write_mask(added_mask, mask)
        else:
            full = Image.new("L", (image.get_width(), image.get_height()))
            full.paste(mask, layer.get_offsets()[-2:])
            temporary = Gimp.Channel.new(
                image, "Object Select transfer", *full.size, 100, Gegl.Color.new("black")
            )
            if not image.insert_channel(temporary, None, 0):
                raise RuntimeError("GIMP could not create a selection channel.")
            write_mask(temporary, full)
            Gimp.context_set_feather(False)
            if not image.select_item(operation, temporary):
                raise RuntimeError("GIMP could not apply the selection.")
    except Exception:
        if added_mask is not None and layer.get_mask() == added_mask:
            layer.remove_mask(Gimp.MaskApplyMode.DISCARD)
        raise
    finally:
        if temporary is not None and temporary.get_image() == image:
            image.remove_channel(temporary)
        if old_channels:
            image.set_selected_channels(old_channels)
        else:
            image.set_selected_layers(old_layers)
        Gimp.context_pop()
        image.undo_group_end()
    Gimp.displays_flush()
