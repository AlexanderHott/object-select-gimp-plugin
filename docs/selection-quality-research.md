# Selection quality research

Checked 2026-09-06 against model authors' repositories, code and papers. No models were run. The recommendations below are proposals for experiments, not measured rankings for this project.

The user clarified that SAM 2.1 and another segmentation model disappointed around edges, while BiRefNet lite produced better edges in background removal experiments. That makes reproducing the BiRefNet lite result the first quality baseline. The exact checkpoint, images and preprocessing still need to be recovered.

The user subsequently confirmed approved Hugging Face access to SAM 3 and SAM 3.1. Access approval is therefore no longer a blocker for their evaluation, although local authentication and hardware support have not been checked. Include SAM 3 in the initial comparison. For hover and point correction, evaluate its image visual-prompt interface, documented as SAM3 Tracker PVS. Its text/exemplar concept interface is a separate interaction to assess if useful. SAM 3.1's advertised changes concern multi-object video tracking, so start with SAM 3 for still-image tests. Sources: [SAM 3 model card](https://huggingface.co/facebook/sam3), [SAM 3.1 model card](https://huggingface.co/facebook/sam3.1).

The previous attempt may have suffered from the surrounding interaction or image processing as well as the model. SAM 2's intended image interface caches image features, accepts foreground and background points, boxes and previous mask logits, and returns three candidates for an ambiguous single click. The authors explicitly say that multiple candidates can improve single-click results, while a single output can work better with several prompts. A wrapper that uses only one positive click and discards the alternatives leaves useful model capabilities unused. [SAM 2 image predictor source](https://github.com/facebookresearch/sam2/blob/main/sam2/sam2_image_predictor.py)

## Models worth comparing

| Candidate | What the primary sources establish | Proposed role |
| --- | --- | --- |
| BiRefNet lite | Official model card lists 44.4M parameters, MIT licensing and a 1024-square image-only inference example. [Model card](https://huggingface.co/ZhengPeng7/BiRefNet_lite) | Reproduce the user's preferred edges first. Compare full-image background removal with cropped-object use. |
| SAM 2.1 | Four sizes, 38.9M to 224.4M parameters. Code and checkpoints use Apache 2.0. Published speed measurements use an A100, so they do not establish desktop hover latency. [Official repository](https://github.com/facebookresearch/sam2) | Baseline. Compare small or base-plus with large to measure the actual quality and memory tradeoff. |
| HQ-SAM / HQ-SAM 2 | HQ-SAM targets detailed boundaries and complex structures. HQ-SAM 2 extends this approach to SAM 2.1 and remains marked beta. Its documented large model has 224.7M parameters, and its code and checkpoints use Apache 2.0. [HQ-SAM](https://github.com/SysCV/sam-hq), [HQ-SAM 2](https://github.com/SysCV/sam-hq/blob/main/sam-hq2/README.md) | Compare against SAM 2.1 large on fine structures and correction behavior. Do not assume its name guarantees better results on every image. |
| SAM 3 | Supports visual prompts and selection of instances matching text or exemplar concepts. The full model has 848M parameters. Official installation lists Python 3.12+, PyTorch 2.7+, and a CUDA GPU with CUDA 12.6+. Checkpoint downloads require access approval and authentication. [Official repository](https://github.com/facebookresearch/sam3) | Initial comparison now that the user has access, subject to hardware support. Its concept segmentation capability does not by itself establish superior single-object hover or edge quality. |
| FocalClick | Designed for interactive correction, including updating an existing mask while preserving correct regions. It uses localized prediction and refinement. [Paper](https://arxiv.org/abs/2204.02574), [official implementation](https://github.com/alibaba/ClickSEG) | Add if SAM corrections damage previously correct regions or require too many clicks. |

SAM 3 uses a custom SAM License, not SAM 2's Apache license. Redistribution must retain that agreement; the terms include additional use restrictions. Treat this as a distinct packaging decision. SAM 3.1, released March 27, 2026, primarily changes multi-object video tracking through shared memory and inference optimizations. That release does not establish improved still-image cutout quality. [SAM License](https://github.com/facebookresearch/sam3/blob/main/LICENSE), [SAM 3.1 release notes](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md)

FocalClick's README says MIT while its repository license display says Apache 2.0. Resolve the actual file-level and weight terms before distributing it. ViTMatte's repository identifies MIT for its code. This research did not establish a complete license inventory for their dependencies and checkpoints. [ClickSEG](https://github.com/alibaba/ClickSEG), [ViTMatte](https://github.com/hustvl/ViTMatte)

## Using BiRefNet for a chosen object

The authors provide a box-guided demo. Its code crops the original image to the supplied box, resizes the crop to 1024-square, runs ordinary BiRefNet, and places the result back into an image-sized coordinate frame. The box controls the input crop; it is not a learned box prompt. The notebook uses standard BiRefNet, so reproducing that wrapper with lite is an experiment. [Official box-guided notebook](https://colab.research.google.com/drive/1B6aKZ3ekcvKMkSBn0N5mCASLUYMp0whK)

The lite example passes only an image tensor to the model. It does not document arbitrary mask input or point corrections. A proposed SAM-to-BiRefNet pipeline should use SAM to identify the target and derive a crop, then test whether BiRefNet retains that target when the crop includes overlapping objects. Treat identity preservation as a failure case to measure. Intersecting the result with a coarse SAM mask can also discard precisely the edge detail that BiRefNet recovered. [Official lite inference example](https://huggingface.co/ZhengPeng7/BiRefNet_lite)

BiRefNet also has distinct matting and higher-resolution checkpoints. Benchmark them separately from the user's lite model. Published standard-model timing on a 4090 cannot be reused as a lite-model latency guarantee. [Official model zoo](https://github.com/ZhengPeng7/BiRefNet)

## Object boundaries and cutout edges are separate quality problems

Segmentation identifies which object pixels to include. Matting estimates fractional opacity for mixed pixels such as hair, fur and transparent boundaries. SAMA's authors describe these as complementary tasks and propose a unified model with separate segmentation and matting heads. Their 2026 paper is useful evidence that the distinction remains relevant; this research did not verify a public deployable SAMA implementation or checkpoint. [SAMA paper](https://arxiv.org/abs/2601.12147)

ViTMatte accepts an image and a trimap defining foreground, background and unknown pixels. Matte Anything demonstrates a pipeline using SAM, automatic trimap generation, transparency handling and ViTMatte. This is concrete precedent for supporting code improving a SAM-based cutout. [ViTMatte](https://github.com/hustvl/ViTMatte), [Matte Anything](https://github.com/hustvl/Matte-Anything)

Engineering implication: feeding segmentation probabilities directly into an alpha channel does not turn them into physical opacity estimates. Similarly, feathering a mask smooths the current boundary but does not recover missing strands. A useful optional refinement stage should operate on original image pixels, offer an editable unknown region, and show the cutout over light and dark backgrounds. Transparent interiors may need an unknown region beyond a narrow outline. These are proposed requirements, not guarantees that matting will recover arbitrary glass or remove background color contamination.

## Proposed selection pipeline

1. Capture the chosen layer or visible composite and its coordinate mapping. Keep the full-resolution pixels available. Cache image features by image content revision, not mouse position.
2. For hover, try a positive point at the cursor against cached image features. Retain candidate masks and let the user cycle object-versus-part alternatives. Keep temporary hover prompts separate from committed correction clicks.
3. Evaluate precomputed automatic masks as an alternative hover strategy. SAM 2's generator samples a point grid, filters masks and optionally processes image crops. Cached masks permit cheap pointer hit testing, but the initial generation cost and missed candidates must be measured. [Automatic mask generator source](https://github.com/facebookresearch/sam2/blob/main/sam2/automatic_mask_generator.py)
4. On commitment, allow foreground/background corrections and a bounding box for ambiguous objects. Pass earlier mask logits back through the model's documented refinement interface. Preserve undoable prompt history.
5. Compare an object crop with context against full-image inference for small objects and difficult edges. Remap prompts and masks explicitly. Cropping changes context, so accept it only where it improves the result. SAM 2 resizes model inputs and upsamples predicted masks; original-sized output alone is not evidence of original-resolution detail. [SAM 2 transforms](https://github.com/facebookresearch/sam2/blob/main/sam2/utils/transforms.py)
6. Offer a separate edge-refinement pass when opacity matters. Start by evaluating ViTMatte with a trimap derived from the corrected mask and editable by the user.

Run inference asynchronously in a persistent worker. Coalesce pointer requests, discard stale replies and keep the UI responsive while initial features are computed. Test preview changes for flicker between overlapping candidates. These scheduling and selection policies are proposals; achievable latency depends on hardware and implementation.

## Evidence needed before choosing a model

Start with 20 to 30 representative images, including the user's previous failures. Cover overlapping objects, object parts, thin structures and holes, small objects in large images, low contrast, hair/fur, and soft or transparent boundaries. Use the same images and comparable prompts across models.

Measure first-hover acceptance, corrections and elapsed time to an acceptable selection, boundary errors at full resolution, and whether corrections break good regions. For opaque masks, use overlap and a boundary metric; for alpha mattes, inspect composites and use an alpha metric where ground truth exists. Record image preparation, first prediction, subsequent prediction and refinement latency separately, plus peak memory. Include actual human clicks: RClicks provides primary research and code for evaluating how click placement changes segmentation performance. [RClicks](https://github.com/emb-ai/rclicks)

The initial experiment should reproduce BiRefNet lite's better edges and compare SAM 3 against the previous SAM 2.1 baseline. Test SAM-selected crops through BiRefNet lite explicitly for both edge quality and retention of the intended object, and compare mask-guided matting where opacity matters. HQ-SAM 2 remains another boundary-focused candidate. Choose on time to a usable selection and failure cases, not a generic benchmark score. The local installation is Linux with GIMP 3.2.4 Flatpak. GPU/VRAM, required image sizes and exact previous settings are still unknown.
