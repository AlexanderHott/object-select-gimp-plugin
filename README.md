# Object Select for GIMP

Select objects from a pixel layer in GIMP 3 using a hover-and-click preview. SAM finds the object and BiRefNet draws its edge, all on your machine. Apply the result as a selection or a new layer mask.

## Requirements

- Linux with GIMP 3. The plugin has been tested with the GIMP Flatpak.
- Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/).
- Internet access for the first model download from Hugging Face. Inference runs locally. Linux dependencies use CPU PyTorch wheels.
- 16 GB of RAM or more. Refining a pick briefly uses about 9 GB, then drops back to about 2 GB.

GIMP's Python environment must provide PyGObject, GTK 3, and GdkPixbuf. The tested Flatpak includes these. If GIMP's Python can't load Pillow, the installer adds a matching copy to the plug-in's folder. Model dependencies use a separate virtual environment in this repository.

## Install

From this repository's root directory, run:

```bash
uv sync
uv run python -m object_select.install
```

The installer detects the GIMP Flatpak and locates its plug-ins directory. For a native GIMP installation, run:

```bash
uv run python -m object_select.install --native
```

To specify a plug-ins directory yourself, add `--plugin-dir /path/to/plug-ins`. Keep `--native` when targeting native GIMP on a machine that also has the Flatpak installed.

Restart GIMP, open an image, select one pixel layer, and choose **Select > Object Select…**. Wait for the model to load, then hover over an object and click to accept a candidate. Each pick then refines its edges, which takes several seconds on a CPU. Choose **GIMP selection** or **New layer mask** under **Apply as**, then apply the result.

The **Shortcuts** button in the dialog lists every key and mouse action. Selection modifiers match GIMP: Shift adds, Ctrl subtracts, and Shift+Ctrl intersects. The candidate's color shows what a click will do.

SAM 2.1 large is the default model. In our tests SAM 3 selected no better and encoded about four times slower on a CPU. SAM 3 also requires approved access to its Hugging Face model and a local login:

```bash
uv run hf auth login
```

The installer copies the GIMP interface, adds Pillow if needed, and writes the repository's worker and `.venv/bin/python` paths into `launcher.json`. Keep this repository and its `.venv` in place.

## How selections are built

1. **Hover:** SAM offers up to three candidates, largest first, because its own quality score favors small parts. Scroll or press Tab to step through them, or choose **Smaller part** to start small.
2. **Click:** SAM runs again on a crop around the object, so the object fills more of its input. This removes most stray islands and false holes.
3. **Edges:** BiRefNet draws a precise contour on the same crop. It segments whatever stands out, which may be another object, so its whole result is used only when it closely matches SAM's mask. Otherwise it may only move SAM's contour locally, and only where it has an edge of its own nearby. Thin parts that SAM missed can still connect through. If you drew a box, nothing leaves the box.
4. **Cleanup:** Only specks and pinholes smaller than about 0.01% of the object are removed. Nothing is smoothed or eroded, so points, thin parts, and real cutouts survive.

Shift-click a part that SAM missed. Right-click to exclude a part from the last pick. These corrections reuse the previous mask, so the rest of the selection stays put.

For hair and fur, open **Hair and fur** and choose **Refine hair and fur** to run ViTMatte on the picked edges. To skip refinement on slow machines, turn off **Refine edges of each pick**.

## Update or uninstall

After updating GIMP or the repository, or moving the repository, rerun `uv sync` and the install command with the same options, then restart GIMP. GIMP updates can change its Python version, which requires a new copy of Pillow.

To uninstall, remove the `object-select` folder from the plug-ins directory printed by the installer.
