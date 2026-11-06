# Object Select for GIMP

Select objects from a pixel layer in GIMP 3 using a hover-and-click preview, local SAM models, and optional ViTMatte edge refinement. Apply the result as a selection or a new layer mask.

## Requirements

- Linux with GIMP 3. The plugin has been tested with the GIMP Flatpak.
- Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/).
- Internet access for the first model download from Hugging Face. Inference runs locally. Linux dependencies use CPU PyTorch wheels.

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

Restart GIMP, open an image, select one pixel layer, and choose **Select > Object Select…**. Wait for the model to load, then hover over an object and click to accept a candidate. Choose **GIMP selection** or **New layer mask** under **Apply as**, then apply the result.

The **Shortcuts** button in the dialog lists every key and mouse action. Selection modifiers match GIMP: Shift adds, Ctrl subtracts, and Shift+Ctrl intersects. The candidate's color shows what a click will do.

SAM 2.1 large is the default model. SAM 3 also requires approved access to its Hugging Face model and a local login:

```bash
uv run hf auth login
```

The installer copies the GIMP interface, adds Pillow if needed, and writes the repository's worker and `.venv/bin/python` paths into `launcher.json`. Keep this repository and its `.venv` in place.

## Update or uninstall

After updating GIMP or the repository, or moving the repository, rerun `uv sync` and the install command with the same options, then restart GIMP. GIMP updates can change its Python version, which requires a new copy of Pillow.

To uninstall, remove the `object-select` folder from the plug-ins directory printed by the installer.
