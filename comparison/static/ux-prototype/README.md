# Object selection UX prototypes

Throwaway study on branch `prototype/object-selection-flows`. No winning flow has been chosen. The existing model comparison work remains separate and unchanged.

The question is whether users prefer picking directly in the editor, drawing a region and refining in a separate workspace, or working in a plugin preview before applying the result.

Run from the repository root with system Python. No packages or model weights are needed.

```bash
python3 comparison/ux_prototype.py
```

Open **http://127.0.0.1:8766**. You can also open `index.html` directly in a browser. The existing comparison server can serve this directory at `/static/ux-prototype/index.html`.

| Flow | URL | Interaction to evaluate |
| --- | --- | --- |
| A, hover and pick | http://127.0.0.1:8766/?variant=A | Hover previews a whole object or part; click accepts; New, Add, Remove and Intersect combine picks. |
| B, draw and refine | http://127.0.0.1:8766/?variant=B | Rectangle or lasso snaps to objects; Finder offers hover; a dedicated refinement workspace previews the output. |
| C, plugin preview | http://127.0.0.1:8766/?variant=C | A separate preview leads through picking, correction and review; Apply changes the simulated host image. Cancel preserves the host. |

The bottom bar and left/right arrows switch flows. Switching or resetting clears that flow's in-memory state. Select controls retain their native arrow-key behavior. Ctrl/Cmd+Z undoes changes; Escape cancels a pending response or gesture, cancels refinement, or closes the plugin preview. Right-click excludes a region. The expandable "Pick by name" control provides an alternative to targeting objects with a pointer.

## Try these comparisons

Use the same task in each flow before switching. The task line counts selection actions and marks completion.

1. With **Clean selection**, select the camera and cup together. Inspect them on transparency and apply the result. In A or B, use Add for the cup. In C, add another include point.
2. Choose **Includes camera strap**. Select the camera, then exclude the strap. Apply a layer mask. Does the unwanted region make the correction control obvious?
3. Choose **Misses camera lens**. Select the camera, then include the lens. Apply a layer mask. In B, output controls are inside Select and refine.
4. Repeat with a **1.2 second** response delay. Move between targets before a preview finishes, click and move away, or press Escape while waiting. Does the distinction between a preview and an accepted selection remain clear?
5. In C, apply once, reopen, make a different selection and cancel. The previous image output should remain intact.

"Inspect prototype state" exposes the draft regions, hover candidate, pending operation, prompts, output, simulated host selection/mask, gesture and undo depth. No state survives a reload.

## What is simulated

The scene is an original SVG illustration with fixed paths for the camera body, lens, strap, plant foliage, pot and cup. Point picking uses those paths. Rectangle and lasso gestures choose regions with more than 45% sampled coverage. Whole-object picking groups regions and injects the selected scripted mistake. Corrections add or remove entire fixed regions; they do not paint arbitrary pixels.

Mask and cutout previews use those same paths. Edge softening applies SVG Gaussian blur to the mask. This does not represent learned matting, hair refinement, color decontamination or measured segmentation quality. The delay is a timer, not a hardware or model benchmark. There is no image upload, network inference, model loading, export, persistence or live GIMP integration.

Applying a selection updates simulated image state; applying a mask also shows the cutout and a layer-mask thumbnail. The illustrated desktop and its menu strip are context, not a full editor. The bottom variant bar belongs only to this standalone prototype and is not included in the comparison app's page or a production build.

## Source workflows and GIMP fit

Affinity Photo documents a hover preview followed by a click, whole-object/component selection, combination modes and refinement. A borrows those interactions with an explicit Whole object/Object part control. It does not reproduce Affinity's modifier keys or every setting. Source: [Affinity Photo Object Selection Tool](https://affinity.help/photo2/en-US.lproj/pages/Tools/tools_objectSelection.html).

Photoshop documents Object Finder hover, rectangle/lasso targeting and selection combination modes. B uses those entry points and a simplified refinement workspace. Sources: [Photoshop Object Selection](https://helpx.adobe.com/photoshop/desktop/make-selections/get-started-selections/select-objects-with-object-selection-tool.html), [Select and Mask](https://helpx.adobe.com/photoshop/using/select-mask.html).

A and B are main-canvas concepts. Existing repository research concludes that a native toolbox interaction needs GIMP core integration. C explores the separate-preview approach that fits the plugin interface. GIMP provides a preview widget and an API for combining an item with the image selection. Sources: [GimpUi.PreviewArea](https://developer.gimp.org/api/3.0/libgimpui/class.PreviewArea.html), [Gimp.Image.select_item](https://developer.gimp.org/api/3.0/libgimp/method.Image.select_item.html), [repository GIMP research](../../../../docs/gimp-interaction-research.md). Sources checked September 6, 2026.

## Initial assessment

Prototyping without inference is useful for comparing target discovery, correction, waiting, refinement and the Apply boundary. It cannot establish whether a model finds the intended object or produces a good edge on a real photograph.

My initial preference is A for direct object picking. C is the practical candidate to evaluate for a regular GIMP plugin. This is a design hypothesis, not a user-validated decision. Keep B available to test whether an explicit region and refinement workspace feels more predictable. Once a flow is chosen, record the user's reasons and implement that behavior separately from this throwaway code.

## Verification

Browser smoke checks exercised hover without acceptance, click acceptance, include/exclude correction, undo, rectangle and lasso gestures, refinement cancellation, selection/mask output, plugin cancellation after an earlier apply, and stale-response cancellation when switching variants. JavaScript syntax was checked with `node --check`. No model or GIMP integration checks apply to this prototype.
