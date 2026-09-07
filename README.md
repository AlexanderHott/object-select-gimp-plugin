# Selection Lab

A local comparison tool for object selection and edge refinement, ahead of GIMP integration.

## GIMP hover-and-pick plugin

The chosen prototype is now a regular GIMP plugin with an interactive hover preview and real SAM inference. Hover and pause to predict candidates at the pointer, then click to select. The preview stays stable while the pointer stays still; there are no precomputed regions. The side panel follows setup, picking, inspection and cleanup, then output to a GIMP selection or new layer mask. Contour cleanup smooths jagged shapes and removes small detached islands without feathering.

```bash
uv sync
python3 -m object_select.install
```

Restart GIMP and choose **Select > Object Select…** with one pixel layer selected. The worker loads SAM and encodes the image immediately; picking becomes available when ready. This prepares image features without generating selection regions. See [plugin usage and limits](docs/hover-plugin.md).

Under **Refine boundary · ViTMatte**, choose **Crisp selection** to refine the contour with an adjustable opacity cutoff, or **Soft matte** to retain partial coverage. Crisp refinement does not add smoothing; contour cleanup remains a separate operation.

## Findings

Manual comparisons produced the best results with these pipelines:

- **SAM 2.1 large → ViTMatte**
- **SAM 3 → ViTMatte**

These are the preferred starting points for further development. SAM identifies the object; ViTMatte refines its edges. BiRefNet lite remains available as a background-removal baseline.

## Run

With [uv](https://docs.astral.sh/uv/) installed:

```bash
uv run python main.py
```

Open **http://127.0.0.1:8765**. The project uses Python 3.12 and CPU PyTorch wheels on Linux. Images and inference stay local; weights download from Hugging Face on first use.

SAM 3 requires approved model access and a local login:

```bash
uv run hf auth login
```

## Compare and refine

1. Open an image. Add include/exclude points or draw a box around the object. Right-click adds an exclusion.
2. Run SAM 2.1 large and SAM 3 with the same prompts. Inspect their candidate masks and select the result to refine.
3. Click **Refine with ViTMatte**. Adjust the unknown band width in original-image pixels and choose 1024, 2048, or original resolution. **Show trimap** reveals the foreground/background/unknown guidance.
4. Hide the original and older results to compare outputs side by side. Scroll to zoom; Space + drag or middle-drag pans all views together. Inspect edges against white and black backgrounds.

Each run preserves its prompts. **Restore prompts** lets you continue from an earlier result. Predicted IoU is a model estimate, not a measured quality score.

BiRefNet full-image ignores prompts. Its crop refinement uses the selected mask to define a crop, so nearby objects inside that crop may also be included.

## Saved results and limits

Completed runs persist in `.comparison-data/`. **Export experiment** saves the source, masks, prompts, model revisions, timings, and notes as a ZIP. Individual masks and transparent cutouts export as PNG; float32 mask arrays are included in the ZIP.

- Inputs are limited to 24 megapixels and 40 MB. The tool applies EXIF orientation, converts to sRGB, and composites existing transparency onto white.
- Models run sequentially. Repeated SAM prompts reuse image features until the image or model changes. Loading time includes downloads; cancellation waits for the current model operation to finish.
- ViTMatte uses an automatic trimap, with no painting UI yet. A boundary band may miss transparent interiors. Cutouts retain original RGB values, including any background color contamination at edges.
- Large models and original-resolution matting require more time and memory. Linux defaults to CPU; CUDA or MPS requires a compatible PyTorch installation.

## Development checks

```bash
uv run pytest -q
uv run ruff check comparison main.py tests
node --check comparison/static/app.js
```

Background research: [selection quality](docs/selection-quality-research.md) and [GIMP interaction](docs/gimp-interaction-research.md).
