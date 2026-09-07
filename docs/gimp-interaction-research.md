# Object selection interaction in GIMP

Checked 2026-09-06 against GIMP 3.2.4 documentation and the matching source tag. Local inspection found GIMP 3.2.4 installed as `org.gimp.GIMP` through Flatpak. This repository currently contains only a Python starter program. No previous segmentation implementation was available to inspect.

## What a plugin can do

A regular plugin can read image data, show its own interactive image preview, and apply a result as a GIMP selection. GIMP documents custom GTK interfaces, and `GimpUi.PreviewArea` is a GTK drawing area with pixel drawing and mask compositing. Pointer interaction would belong to the plugin's preview widget. It would not be the main image canvas. Sources: [plugin architecture](https://developer.gimp.org/resource/about-plugins/), [PreviewArea](https://developer.gimp.org/api/3.0/libgimpui/class.PreviewArea.html).

The public selection API can convert an item into a selection and combine it with the existing selection. A proposed implementation would transfer the model's mask into a channel, then apply the requested replace, add, subtract, or intersect operation. Coordinate conversion must account for layer offsets and model resizing. Source: [Image.select_item](https://developer.gimp.org/api/3.0/libgimp/method.Image.select_item.html).

## Main-canvas hover needs core integration

The supported plugin API does not expose registration of a native toolbox tool, canvas pointer motion, or arbitrary transient canvas overlays. This conclusion comes from comparing the public API with the matching core source, rather than an explicit promise about all possible workarounds.

`Gimp.PlugIn` registers procedures. Public `Gimp.Display` provides display lifecycle and native window handles, with no canvas widget or pointer-event interface. Plugins run in separate processes. Sources: [PlugIn API](https://developer.gimp.org/api/3.0/libgimp/class.PlugIn.html), [Display API](https://developer.gimp.org/api/3.0/libgimp/class.Display.html), [plugin loading](https://developer.gimp.org/resource/about-plugins/#loading).

Native tools have the interaction hooks we need, including `oper_update`, `cursor_update`, button events, and motion. Core startup registers a compiled list of tool implementations. Canvas events route through the core tool manager. Sources pinned to 3.2.4: [GimpTool](https://github.com/GNOME/gimp/blob/GIMP_3_2_4/app/tools/gimptool.h), [tool registration](https://github.com/GNOME/gimp/blob/GIMP_3_2_4/app/tools/gimp-tools.c), [canvas event handling](https://github.com/GNOME/gimp/blob/GIMP_3_2_4/app/display/gimpdisplayshell-tool-events.c).

The practical route to the requested native interaction is a GIMP core tool, initially in a custom build, with a possible upstream contribution later. A general plugin tool API would also require changes to GIMP and would be a larger project. A core tool could delegate inference to a separate local worker, so the model need not be embedded into GIMP's UI process. This architecture is a proposal, not an implemented or upstream-approved design.

GEGL filters do support main-canvas effect previews. That capability alone does not provide a freely programmable hover-selection tool. A native window handle is also not a reference to GIMP's GTK canvas. I would not base the product on OS pointer interception and window overlays because it would require implementing synchronization outside the supported tool interfaces. Sources: [filter previews](https://developer.gimp.org/resource/writing-a-filter/), [native window handles](https://developer.gimp.org/api/3.0/libgimp/method.Display.get_window_handle.html).

## Product choices

| Approach | Interaction | Distribution consequence |
| --- | --- | --- |
| Regular plugin | Hover and correction in its own preview window; apply to GIMP | Works with stock GIMP using public APIs |
| GIMP core tool plus local inference worker | Hover and correction directly on the main canvas | Requires a custom GIMP build until any upstream acceptance and release |
| New general tool extension API | Could eventually support native interaction through plugins | Requires GIMP API design and core development before this plugin can use it |

Given the user's emphasis on UX, treat a separate preview window as an explicit product choice. It should not silently become the final interaction merely because it is easier to package.

## Proposed hover behavior

Load the model once and compute image features when the source changes. Evaluate cached candidate masks under the cursor, or decode a point prompt after a short hover dwell. Benchmark both approaches, since generating all candidates can delay first use and miss intended objects.

Draw a translucent tentative mask. Keep that preview separate from the committed selection and its undo history. Keep the current candidate stable during small cursor movements, and offer a way to switch between an object and its parts. Clicking accepts the visible candidate. Provide add/remove correction points and a box prompt when hover is ambiguous.

Run inference asynchronously. Discard results for an old cursor position or source revision. Invalidate image features after relevant pixel, layer, or source-mode changes. Preserve correct coordinates across zoom, pan, layer offsets, and image resizing. Offer current-layer and visible-composite modes with clearly defined behavior.

Refine expensive boundaries after a candidate is chosen. An engineering target to test is a cached hover preview within roughly 100 ms; this is a proposed responsiveness budget, not a measured capability. Measure model loading, image encoding, first preview, repeated hover, correction, and final refinement separately on the user's actual hardware.

## Next evidence to collect

The user previously tried SAM 2.1 and an unnamed non-SAM model. Boundary quality disappointed them compared with their BiRefNet lite background-removal experiments. They want to explore both UI approaches before deciding. Compare whole-object choice and boundary quality separately, with BiRefNet lite as a baseline on representative images. Count corrections and inspect cutouts against contrasting backgrounds. See [selection quality research](selection-quality-research.md) for model candidates and the evaluation proposal.

If main-canvas hover is essential, prove the native tool interaction with fixed masks before integrating a model. Independently test segmentation quality in a small viewer. These experiments answer different risks and can share a model worker later.

No runtime integration or latency measurements were performed during this research.
