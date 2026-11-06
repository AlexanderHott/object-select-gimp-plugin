"""Run inside GIMP's Python batch interpreter; creates only disposable images."""

import sys
from pathlib import Path

import gi

gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
gi.require_version("Babl", "0.1")
from gi.repository import Babl, Gegl, Gimp

sys.path.append(Gimp.directory() + "/plug-ins/object-select/vendor")
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gimp-plugin" / "object-select"))
from gimp_io import apply, snapshot


def read_mask(drawable, size):
    model = Babl.get_name(Babl.format_get_model(drawable.get_format()))
    return bytes(
        drawable.get_buffer().get(
            Gegl.Rectangle.new(0, 0, *size), 1.0, model + " u8", Gegl.AbyssPolicy.NONE
        )
    )


def check():
    procedure = Gimp.get_pdb().lookup_procedure("plug-in-object-select-hover")
    assert procedure is not None, "Installed plugin did not register"
    for precision in (
        Gimp.Precision.U8_NON_LINEAR,
        Gimp.Precision.U16_LINEAR,
        Gimp.Precision.FLOAT_LINEAR,
    ):
        image = Gimp.Image.new_with_precision(30, 20, Gimp.ImageBaseType.RGB, precision)
        try:
            layer = Gimp.Layer.new(
                image, "Test source", 10, 10, Gimp.ImageType.RGBA_IMAGE, 100, Gimp.LayerMode.NORMAL
            )
            assert image.insert_layer(layer, None, 0)
            layer.set_offsets(-2, 5)
            rgba = bytes([200, 120, 30, 255]) * 100
            buffer = layer.get_buffer()
            buffer.set(Gegl.Rectangle.new(0, 0, 10, 10), "R'G'B'A u8", rgba)
            buffer.flush()
            assert snapshot(layer).size == (10, 10)
            mask = Image.new("L", (10, 10), 128)
            apply(image, layer, mask)
            result = read_mask(image.get_selection(), (30, 20))
            assert result[5 * 30] == 128, (precision, result[5 * 30])
            assert result[5 * 30 + 8] == 0
            assert result[4 * 30] == 0
            assert not image.get_channels(), "Temporary channel leaked"
            apply(image, layer, Image.new("L", (10, 10), 64), mode="subtract")
            result = read_mask(image.get_selection(), (30, 20))
            assert abs(result[5 * 30] - 64) <= 1, (precision, result[5 * 30])
            before = result
            apply(image, layer, mask, output="mask")
            assert read_mask(layer.get_mask(), (10, 10)) == bytes([128]) * 100
            assert read_mask(image.get_selection(), (30, 20)) == before
            try:
                apply(image, layer, Image.new("L", (10, 10), 0), output="mask")
            except ValueError:
                pass
            else:
                raise AssertionError("Existing layer mask was replaced")
            assert read_mask(layer.get_mask(), (10, 10)) == bytes([128]) * 100
            assert image.get_selected_layers() == [layer]
            print(f"PASS: offset, soft selection, subtract, layer mask, preservation: {precision}")
        finally:
            image.delete()


check()
