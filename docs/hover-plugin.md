# Hover-and-pick GIMP plugin

This is a regular GIMP plugin with its own GTK preview. It uses real segmentation models and applies a selection or layer mask to the active GIMP image. The browser prototypes remain on `prototype/object-selection-flows` as the design reference.

## Install and open

```bash
uv sync
python3 -m object_select.install
```

Restart GIMP, select one pixel layer, and choose **Select > Object Select…**. The installer detects this machine's GIMP Flatpak and its configuration directory. Use `--native` for a native installation or `--plugin-dir` to override the destination.

The installer copies the interface and records this checkout's worker and `.venv/bin/python` paths in `launcher.json`. Keep the checkout and environment in place, and rerun the installer after updating the interface or moving the repository. To uninstall, remove `object-select` from GIMP's plug-ins directory.

The tested GIMP Flatpak supplies GTK 3, PyGObject, GdkPixbuf, and Pillow. Model dependencies run in the repository's virtual environment. No comparison web server is needed.

## Workflow

The side panel follows the order of use:

1. **Set up.** Opening the plugin loads the selected model and encodes the image, with progress shown. Picking becomes available when both are ready. SAM 2.1 large is the default; base+ and tiny are available. Changing models loads and encodes for the new choice. SAM 3 requires approved model access.
2. **Pick objects.** Hover and pause for 180 ms to request candidates at that exact image pixel. The blue overlay appears when inference finishes and stays unchanged while the pointer stays still. Click to accept. Tab or scroll down cycles alternatives; Shift+Tab or scroll up goes back. Candidate preference chooses the best or smaller result. New, Add, Subtract, and Intersect combine draft picks. Shift-click adds and Alt-click subtracts. Undo and Clear sit below the picking controls.
3. **Inspect & clean up.** Choose an overlay, transparency, black, white, or grayscale preview. Zoom to 100%, 200%, or 400% and pan using scrollbars. Clean new candidates applies the current contour settings before showing each result. Expand Contour cleanup settings to adjust hole filling, contour smoothing, and island removal. Clean selected mask applies those settings to the accepted draft. Expand Refine boundary · ViTMatte to choose the boundary band, Crisp selection or Soft matte output, and run refinement.
4. **Apply to GIMP.** Choose a GIMP selection or new layer mask. For selection output, choose how to combine it with GIMP's existing selection. Press Apply at the bottom of the dialog.

Draw a box when pointing is ambiguous. Include selects an additional object or part. Exclude, or right-click, adds a negative point to the most recently picked object. Repeated exclusions accumulate while preserving other added objects. After Undo or refinement, pick again before using point exclusion, or use Subtract to remove another candidate.

A green overlay with a white boundary marks the accepted draft. Clicking before a preview arrives requests a selection at the click position. The plugin does not precompute regions, show cached area proposals, or schedule a second prediction to replace a displayed preview. The former Region / gap mode and preparation controls have been removed. Narrow spaces now rely on point or box predictions and manual add/subtract corrections.

Cancel closes without applying document changes. Escape closes the dialog during model loading; otherwise it cancels a pending request or gesture, and another Escape can close the dialog. Results from obsolete requests cannot replace the current preview. Leaving the preview preserves the displayed candidate and discards pending work. Ctrl+Z undoes accepted draft changes.

## Edge cleanup

Cleanup fills enclosed holes up to 64 square pixels, smooths small contour irregularities with a 1-pixel neighborhood, and removes detached islands up to 64 square pixels by default. Contour smoothing produces a crisp boundary without feathering. Expand the cleanup settings to change these values, or disable Clean new candidates for raw model output.

The largest component and other components above the island threshold remain. Automatic cleanup protects holes marked with exclusion points and surviving components marked with inclusion points, then clips to layer alpha. Thin features can disappear during smoothing; use zero smoothing to retain them. Undo restores the mask before manual cleanup.

ViTMatte uses the image and selected mask to refine the boundary within the chosen band. **Crisp selection** is the default output. It thresholds the refined opacity at 50% after resizing to layer dimensions, producing a hard boundary on opaque source layers. Adjust Opacity cutoff from 1–99% before running refinement: higher values keep less of the boundary, lower values keep more. Thresholding does not run contour smoothing, hole filling, or island removal, even when Clean new candidates is enabled. Use Clean selected mask separately if desired.

**Soft matte** retains partial opacity for hair and transparency; its cutoff control is disabled. Both outputs preserve source layer transparency, so partly transparent source pixels can still have partial coverage in Crisp selection mode. ViTMatte runs at up to 1024 pixels on the long side. The trusted foreground and background outside the refinement band stay fixed, so refinement cannot recover missing parts beyond that band. Undo restores the mask before refinement. Changing the output choice or cutoff affects the next refinement run.

## Models and limits

Models may download weights on first use. SAM 3 requires Hugging Face approval and a local `uv run hf auth login`. Image pixels travel only between the local GIMP process and the local worker.

The plugin snapshots one active pixel layer, up to 24 megapixels. It does not sample the visible composite or listen for subsequent pixel edits. Layer offsets are accounted for when applying a selection. Transparent pixels are composited on white for inference; output masks are clipped to layer alpha. The original color pixels and precision are preserved. Existing layer masks are protected from replacement.

Opening the dialog starts the worker, sends the snapshot, loads SAM, and computes its image features. This startup preparation does not generate masks or regions. The first and subsequent point or box requests reuse those features and predict masks on demand. Only the latest waiting request is retained. In-flight inference finishes before another request runs, while obsolete responses are discarded. Changing picking or cleanup settings during loading does not interrupt it; changing models starts preparation for the new model. Closing the dialog terminates the worker. ViTMatte replaces SAM in memory, so a subsequent SAM request reloads and re-encodes.

SAM alternatives do not guarantee a semantic object/part hierarchy or a selectable mask for every gap. There is no edge-brush painting. Preview color parity with GIMP is not verified. This plugin does not create a native toolbox tool or intercept events on GIMP's main canvas.

## Verification

```bash
uv run pytest -q
uv run ruff check object_select gimp-plugin tests scripts
flatpak run --command=python3 org.gimp.GIMP scripts/check_hover_controls.py
flatpak run --command=python3 org.gimp.GIMP scripts/check_hover_requests.py
```

All 32 repository tests pass. They cover mask combination and undo, pointer mapping, candidate selection, crisp cleanup, island removal, alpha handling, and worker request behavior. Refinement tests check exact threshold behavior, preservation of soft coverage and source alpha, cleanup independence, and rejection of invalid options before inference. Tests also confirm that startup encoding does not predict masks and removed cache operations cannot trigger inference.

The hover request script controls callbacks and timers in GIMP's Python environment. It checks startup and model-change preparation, safe control changes during loading, one prediction after dwell, no automatic replacement, one-pixel targeting, stale-result rejection, candidate cycling, and click acceptance. The control script checks Tab without keyboard focus and wheel/touchpad cycling.

`scripts/check_gimp_hover_dialog.py` runs in GIMP's batch interpreter against the installed plugin with real SAM and ViTMatte. It checks startup encoding without region predictions, reuse of those features by the first hover, sidebar group order, a stable preview, crisp and soft refinement, Undo for both, and applying a binary refinement result to a disposable GIMP image. It saves a dialog capture to `/tmp/object-select-panel.png`.

`scripts/check_gimp_hover.py` checks negative offsets, temporary-channel cleanup, exact fractional coverage, subtraction, and layer-mask preservation at 8-bit nonlinear, 16-bit linear, and float linear precision. Set its `__file__` to the script path when executing through GIMP's batch interpreter.

Integration uses GIMP's [Python plugin interface](https://developer.gimp.org/resource/writing-a-plug-in/tutorial-python/) and [Image.select_item](https://developer.gimp.org/api/3.0/libgimp/method.Image.select_item.html). The [interaction research](gimp-interaction-research.md) explains the difference between a plugin preview and a native canvas tool.
