const $ = (id) => document.getElementById(id);
const state = {
  image: null,
  bitmap: null,
  points: [],
  box: null,
  history: [],
  results: [],
  selected: null,
  tool: "positive",
  zoom: null,
  cx: 0,
  cy: 0,
  cards: [],
  version: 0,
  jobs: new Map(),
};
let catalog = [],
  space = false;
const pendingNotes = new Set();

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : JSON.stringify(body.detail || response.statusText);
    throw new Error(detail);
  }
  return response.json();
}
const json = (data, method = "POST") => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});
function message(text = "") {
  $("message").textContent = text;
  $("message").hidden = !text;
}
function imageURL() {
  return `/api/images/${state.image.id}`;
}
function resultURL(result) {
  return `${imageURL()}/results/${result.id}`;
}
function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("Could not load image"));
    image.src = url;
  });
}
function promptSnapshot() {
  return {
    points: structuredClone(state.points),
    box: state.box?.slice() || null,
  };
}
function remember() {
  state.history.push(promptSnapshot());
}
function promptsChanged() {
  $("prompt-count").textContent =
    `${state.points.filter((p) => p.label).length} include · ${state.points.filter((p) => !p.label).length} exclude${state.box ? " · 1 box" : ""}`;
  state.cards.forEach((card) => {
    if (card.stale) {
      const request = card.result.request;
      const stale =
        JSON.stringify({ points: request.points, box: request.box }) !==
        JSON.stringify(promptSnapshot());
      card.stale.textContent = stale
        ? "Earlier prompt set"
        : "Current prompt set";
      card.stale.classList.toggle("stale", stale);
    }
  });
  drawAll();
}
async function refreshHistory(selected = "") {
  const images = await api("/api/images");
  $("history").replaceChildren(new Option("Choose an image", ""));
  for (const image of images)
    $("history").add(
      new Option(`${image.name} · ${image.width} × ${image.height}`, image.id),
    );
  $("history").value = selected;
  return images;
}
async function openImage(info) {
  const version = ++state.version;
  const bitmap = await loadImage(`/api/images/${info.id}/source.png`);
  const results = await api(`/api/images/${info.id}/results`);
  if (version !== state.version) return;
  state.cards.forEach((card) => card.observer.disconnect());
  Object.assign(state, {
    image: info,
    bitmap,
    points: [],
    box: null,
    history: [],
    results,
    selected: null,
    zoom: null,
    cx: info.width / 2,
    cy: info.height / 2,
    cards: [],
  });
  $("cards").replaceChildren();
  $("empty").hidden = true;
  $("run").disabled = false;
  $("show-original").checked = true;
  $("image-info").textContent =
    `${info.name} · ${info.width} × ${info.height} px`;
  $("export").classList.remove("disabled");
  $("export").href = `${imageURL()}/export`;
  addCard(null);
  for (const result of results) addCard(result);
  $("history").value = info.id;
  localStorage.setItem("selection-lab-image", info.id);
  selectResult(null);
  promptsChanged();
  drawAll();
  message();
}
async function upload(file) {
  if (!file) return;
  message("Opening image…");
  const body = new FormData();
  body.append("file", file);
  try {
    const info = await api("/api/images", { method: "POST", body });
    await refreshHistory(info.id);
    await openImage(info);
  } catch (error) {
    message(error.message);
  }
}

function selectResult(card) {
  state.selected = card;
  for (const current of state.cards) {
    current.element.classList.toggle("selected", current === card);
    if (current.radio) current.radio.checked = current === card;
  }
  $("selected").textContent = card
    ? `${card.result.label} · candidate ${card.candidate + 1}`
    : "Select a result card to use its mask.";
  $("crop").disabled = $("matte").disabled = !card;
}

function addCard(result) {
  const card = {
    result,
    candidate: 0,
    render: null,
    mask: null,
    imageVersion: state.version,
    loadVersion: 0,
  };
  const element = (card.element = node("article", "card"));
  const head = node("div", "card-head");
  if (result) {
    const radio = (card.radio = document.createElement("input"));
    radio.type = "radio";
    radio.name = "result";
    radio.setAttribute("aria-label", `Select ${result.label} result`);
    radio.onchange = () => selectResult(card);
    head.append(radio);
  }
  head.append(node("strong", "", result ? result.label : "Original image"));
  head.append(
    node(
      "span",
      "tag",
      result ? result.metadata.output_kind : "Add prompts here",
    ),
  );
  if (result) {
    const hide = node("button", "quiet", "Hide");
    hide.onclick = () => {
      element.hidden = true;
      drawAll();
    };
    head.append(hide);
  }
  const wrap = node("div", "canvas-wrap");
  card.canvas = document.createElement("canvas");
  card.canvas.setAttribute(
    "aria-label",
    result ? `${result.label} preview` : "Original image prompt canvas",
  );
  wrap.append(card.canvas);
  element.append(head, wrap);
  const foot = node("div", "card-foot");
  if (!result) {
    foot.append(
      node(
        "p",
        "muted",
        "All models receive this image. The same points and box go to each SAM model.",
      ),
    );
  } else {
    const controls = node("div", "row");
    card.candidateSelect = document.createElement("select");
    card.candidateSelect.setAttribute(
      "aria-label",
      `${result.label} candidate`,
    );
    result.scores.forEach((score, i) =>
      card.candidateSelect.add(
        new Option(
          `Candidate ${i + 1}${score !== null ? ` · predicted IoU ${score.toFixed(3)}` : ""}`,
          i,
        ),
      ),
    );
    card.candidateSelect.onchange = () => {
      card.candidate = Number(card.candidateSelect.value);
      card.showTrimap = false;
      if (card.trimapButton) card.trimapButton.textContent = "Show trimap";
      selectResult(card);
      loadMask(card).catch((error) => message(error.message));
      updateDownloads(card);
    };
    controls.append(card.candidateSelect);
    const restore = node("button", "quiet", "Restore prompts");
    restore.onclick = () => {
      remember();
      state.points = structuredClone(result.request.points);
      state.box = result.request.box?.slice() || null;
      promptsChanged();
    };
    controls.append(restore);
    foot.append(controls);
    const stats = node("div", "stats");
    for (const text of [
      `Run ${result.total_seconds.toFixed(2)} s`,
      `Load ${result.metadata.load_seconds.toFixed(2)} s`,
      `Image ${result.metadata.encode_seconds.toFixed(2)} s${result.metadata.cached_embeddings ? " · cached" : ""}`,
      result.metadata.device,
    ])
      stats.append(node("span", "", text));
    foot.append(stats);
    if (result.request.model.startsWith("sam")) {
      card.stale = node("span", "micro");
      foot.append(card.stale);
    }
    if (result.request.parent_id) {
      const parent = state.results.find(
        (r) => r.id === result.request.parent_id,
      );
      foot.append(
        node(
          "p",
          "micro",
          `From ${parent?.label || result.request.parent_id.slice(0, 8)} · candidate ${result.request.candidate + 1}`,
        ),
      );
    }
    if (result.has_trimap) {
      const button = (card.trimapButton = node(
        "button",
        "quiet",
        "Show trimap",
      ));
      button.onclick = () => {
        card.showTrimap = !card.showTrimap;
        button.textContent = card.showTrimap ? "Show result" : "Show trimap";
        loadMask(card).catch((error) => message(error.message));
      };
      foot.append(button);
    }
    const links = node("div", "downloads");
    card.maskLink = node("a", "", "Mask PNG");
    card.cutoutLink = node("a", "", "Cutout PNG");
    card.maskLink.download = `${result.request.model}-mask.png`;
    card.cutoutLink.download = `${result.request.model}-cutout.png`;
    links.append(card.maskLink, card.cutoutLink);
    foot.append(links);
    updateDownloads(card);
    const notes = document.createElement("textarea");
    notes.placeholder = "What worked? What needs correction?";
    notes.value = result.note;
    notes.setAttribute("aria-label", `Notes for ${result.label}`);
    notes.onchange = () => {
      const value = notes.value;
      const save = api(
        `/api/images/${result.image_id}/results/${result.id}`,
        json({ note: value }, "PATCH"),
      )
        .then(() => {
          result.note = value;
        })
        .catch((error) => {
          message(`Notes were not saved: ${error.message}`);
          throw error;
        });
      pendingNotes.add(save);
      save.finally(() => pendingNotes.delete(save)).catch(() => {});
    };
    foot.append(notes);
    const details = node("details", "provenance");
    details.append(node("summary", "", "Run details"));
    details.append(
      node(
        "div",
        "",
        `${result.metadata.repo} @ ${result.metadata.revision} · ${result.metadata.dtype}`,
      ),
    );
    details.append(
      node(
        "div",
        "",
        `Processing ${result.metadata.predict_seconds.toFixed(2)} s, including image encoding. ${result.metadata.model_input ? `Model input ${result.metadata.model_input.join(" × ")} px.` : ""}`,
      ),
    );
    details.append(
      node(
        "div",
        "",
        `Prompts: ${result.request.points.length} points${result.request.box ? ", box " + result.request.box.map(Math.round).join(", ") : ""}. ${result.has_trimap ? `Unknown band ${result.request.matte_radius} px.` : ""}`,
      ),
    );
    foot.append(details);
  }
  element.append(foot);
  $("cards").append(element);
  state.cards.push(card);
  card.observer = new ResizeObserver(drawAll);
  card.observer.observe(wrap);
  wireCanvas(card);
  if (result) loadMask(card).catch((error) => message(error.message));
  drawAll();
  return card;
}
function updateDownloads(card) {
  card.maskLink.href = `${resultURL(card.result)}/mask-${card.candidate}.png`;
  card.cutoutLink.href = `${resultURL(card.result)}/cutout/${card.candidate}.png`;
}
async function loadMask(card) {
  const version = ++card.loadVersion;
  const mask = await loadImage(
    `${resultURL(card.result)}/${card.showTrimap ? "trimap.png" : `mask-${card.candidate}.png`}`,
  );
  if (version !== card.loadVersion || card.imageVersion !== state.version)
    return;
  card.mask = mask;
  compose(card);
  drawAll();
}
function compose(card) {
  if (!card.mask) return;
  const { width, height } = state.image;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  context.drawImage(card.mask, 0, 0);
  if ($("view").value === "mask" || card.showTrimap) {
    card.render = canvas;
    return;
  }
  const pixels = context.getImageData(0, 0, width, height);
  const overlay = $("view").value === "overlay";
  for (let i = 0; i < pixels.data.length; i += 4) {
    const alpha = pixels.data[i];
    pixels.data[i] = 88;
    pixels.data[i + 1] = 229;
    pixels.data[i + 2] = 181;
    pixels.data[i + 3] = overlay ? Math.round(alpha * 0.48) : alpha;
  }
  context.putImageData(pixels, 0, 0);
  context.globalCompositeOperation = overlay ? "destination-over" : "source-in";
  context.drawImage(state.bitmap, 0, 0);
  context.globalCompositeOperation = "source-over";
  card.render = canvas;
}
function fitScale() {
  if (!state.image || !state.cards.length) return 1;
  const visible = state.cards.filter((c) => !c.element.hidden);
  if (!visible.length) return 1;
  return Math.max(
    0.02,
    Math.min(
      ...visible.map((c) =>
        Math.min(
          (c.canvas.clientWidth - 24) / state.image.width,
          (c.canvas.clientHeight - 24) / state.image.height,
        ),
      ),
    ),
  );
}
function scale() {
  return state.zoom ?? fitScale();
}
function drawAll() {
  if (!state.image) return;
  const zoom = scale();
  $("zoom-label").textContent = `${Math.round(zoom * 100)}%`;
  for (const card of state.cards) {
    if (card.element.hidden) continue;
    const canvas = card.canvas,
      width = canvas.clientWidth,
      height = canvas.clientHeight,
      dpr = window.devicePixelRatio || 1;
    if (
      canvas.width !== Math.round(width * dpr) ||
      canvas.height !== Math.round(height * dpr)
    ) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#0d1113";
    ctx.fillRect(0, 0, width, height);
    const x = width / 2 - state.cx * zoom,
      y = height / 2 - state.cy * zoom;
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, state.image.width * zoom, state.image.height * zoom);
    ctx.clip();
    const bg = $("background").value;
    if (bg === "checker") {
      ctx.fillStyle = "#b4babc";
      ctx.fillRect(0, 0, width, height);
      ctx.fillStyle = "#e1e5e6";
      for (let row = 0; row < height / 14; row++)
        for (let col = 0; col < width / 14; col++)
          if ((row + col) % 2 === 0) ctx.fillRect(col * 14, row * 14, 14, 14);
    } else {
      ctx.fillStyle = { white: "#fff", black: "#000", pink: "#e283b5" }[bg];
      ctx.fillRect(0, 0, width, height);
    }
    ctx.imageSmoothingEnabled = zoom < 2;
    ctx.drawImage(
      card.result ? card.render || state.bitmap : state.bitmap,
      x,
      y,
      state.image.width * zoom,
      state.image.height * zoom,
    );
    if (!card.result) {
      if (state.box) {
        const [x0, y0, x1, y1] = state.box;
        ctx.strokeStyle = "#ffe39a";
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 4]);
        ctx.strokeRect(
          x + x0 * zoom,
          y + y0 * zoom,
          (x1 - x0) * zoom,
          (y1 - y0) * zoom,
        );
        ctx.setLineDash([]);
      }
      for (const point of state.points) {
        const px = x + point.x * zoom,
          py = y + point.y * zoom;
        ctx.beginPath();
        ctx.arc(px, py, 6, 0, Math.PI * 2);
        ctx.fillStyle = point.label ? "#8ce1c5" : "#ff8787";
        ctx.fill();
        ctx.strokeStyle = "#10241d";
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(px - 3, py);
        ctx.lineTo(px + 3, py);
        if (point.label) {
          ctx.moveTo(px, py - 3);
          ctx.lineTo(px, py + 3);
        }
        ctx.stroke();
      }
    }
    ctx.restore();
  }
}
function zoomTo(value, anchor = null) {
  const before = scale(),
    after = Math.max(0.02, Math.min(16, value));
  if (anchor) {
    state.cx += anchor.x / before - anchor.x / after;
    state.cy += anchor.y / before - anchor.y / after;
  }
  state.zoom = after;
  drawAll();
}
function wireCanvas(card) {
  const canvas = card.canvas;
  let drag = null;
  const position = (event) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left - rect.width / 2) / scale() + state.cx,
      y: (event.clientY - rect.top - rect.height / 2) / scale() + state.cy,
    };
  };
  const inside = (p) =>
    p.x >= 0 && p.y >= 0 && p.x < state.image.width && p.y < state.image.height;
  canvas.oncontextmenu = (event) => event.preventDefault();
  canvas.onwheel = (event) => {
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    zoomTo(scale() * Math.exp(-event.deltaY * 0.002), {
      x: event.clientX - rect.left - rect.width / 2,
      y: event.clientY - rect.top - rect.height / 2,
    });
  };
  canvas.onpointerdown = (event) => {
    if (!state.image) return;
    const p = position(event),
      pan = space || event.button === 1 || !!card.result;
    if (pan) {
      event.preventDefault();
      drag = {
        type: "pan",
        x: event.clientX,
        y: event.clientY,
        cx: state.cx,
        cy: state.cy,
      };
      if (card.result) selectResult(card);
    } else if (inside(p)) {
      if (state.tool === "box" && event.button === 0) {
        remember();
        drag = { type: "box", start: p };
        state.box = [p.x, p.y, p.x, p.y];
      } else {
        remember();
        state.points.push({
          x: p.x,
          y: p.y,
          label: event.button === 2 || state.tool === "negative" ? 0 : 1,
        });
        promptsChanged();
      }
    }
    if (drag) canvas.setPointerCapture(event.pointerId);
  };
  canvas.onpointermove = (event) => {
    if (!drag) return;
    if (drag.type === "pan") {
      state.cx = drag.cx - (event.clientX - drag.x) / scale();
      state.cy = drag.cy - (event.clientY - drag.y) / scale();
    } else {
      const p = position(event);
      p.x = Math.max(0, Math.min(state.image.width, p.x));
      p.y = Math.max(0, Math.min(state.image.height, p.y));
      state.box = [
        Math.min(p.x, drag.start.x),
        Math.min(p.y, drag.start.y),
        Math.max(p.x, drag.start.x),
        Math.max(p.y, drag.start.y),
      ];
    }
    drawAll();
  };
  canvas.onpointerup = () => {
    if (drag?.type === "box") {
      if (state.box[2] - state.box[0] < 1 || state.box[3] - state.box[1] < 1)
        state.box = state.history.pop().box;
      promptsChanged();
    }
    drag = null;
  };
  canvas.onpointercancel = () => {
    if (drag?.type === "box") state.box = state.history.pop().box;
    drag = null;
    promptsChanged();
  };
}

function runSnapshot(parent = null) {
  return {
    image_id: state.image.id,
    ...promptSnapshot(),
    parent_id: parent?.result.id || null,
    candidate: parent?.candidate || 0,
    crop_padding: Number($("padding").value) / 100,
    matte_radius: Number($("radius").value),
    matte_max_side: Number($("matte-size").value),
  };
}
async function queue(model, parent = null, snapshot = null) {
  if (!state.image) return;
  const request = { ...(snapshot || runSnapshot(parent)), model };
  const response = await api("/api/runs", json(request));
  const element = node("div", "job"),
    dot = node("span", "pulse"),
    text = node("div");
  text.append(node("strong", "", catalog.find((m) => m.id === model).label));
  const stage = node("small", "", "Queued");
  text.append(stage);
  const cancel = node("button", "", "Cancel");
  element.append(dot, text, cancel);
  $("jobs").append(element);
  const job = { request, element, stage, cancel };
  state.jobs.set(response.id, job);
  cancel.onclick = async () => {
    try {
      await api(`/api/jobs/${response.id}/cancel`, json({}));
      cancel.disabled = true;
    } catch (error) {
      message(error.message);
    }
  };
  poll(response.id);
}
async function poll(id) {
  const job = state.jobs.get(id);
  if (!job) return;
  try {
    const data = await api(`/api/jobs/${id}`);
    job.stage.textContent = data.stage;
    if (data.state === "done") {
      job.element.remove();
      state.jobs.delete(id);
      if (
        state.image?.id === data.result.image_id &&
        !state.results.some((r) => r.id === data.result.id)
      ) {
        state.results.push(data.result);
        const card = addCard(data.result);
        selectResult(card);
        promptsChanged();
      }
      return;
    }
    if (data.state === "error" || data.state === "cancelled") {
      job.element.classList.add("error");
      job.element.querySelector(".pulse").remove();
      job.stage.textContent = data.error || "Cancelled";
      job.cancel.textContent = "Dismiss";
      job.cancel.disabled = false;
      job.cancel.onclick = () => {
        job.element.remove();
        state.jobs.delete(id);
      };
      return;
    }
  } catch (error) {
    job.stage.textContent = `Connection interrupted: ${error.message}. Retrying…`;
  }
  setTimeout(() => poll(id), 1000);
}

$("upload").onchange = (event) => {
  upload(event.target.files[0]);
  event.target.value = "";
};
$("open-empty").onclick = () => $("upload").click();
$("history").onchange = async (event) => {
  try {
    const images = await api("/api/images");
    const info = images.find((i) => i.id === event.target.value);
    if (info) await openImage(info);
  } catch (error) {
    message(error.message);
  }
};
$("tools").onclick = (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.tool = button.dataset.tool;
  $("tools")
    .querySelectorAll("button")
    .forEach((b) => b.classList.toggle("active", b === button));
};
$("undo").onclick = () => {
  const previous = state.history.pop();
  if (previous) Object.assign(state, previous);
  promptsChanged();
};
$("clear").onclick = () => {
  remember();
  state.points = [];
  state.box = null;
  promptsChanged();
};
$("run").onclick = async () => {
  message();
  $("run").disabled = true;
  try {
    const models = [...$("models").querySelectorAll("input:checked")].map(
      (input) => input.value,
    );
    if (!models.length) throw new Error("Choose at least one model.");
    // Validate the whole comparison before submitting any jobs.
    if (
      models.some((m) => m.startsWith("sam")) &&
      !state.box &&
      !state.points.some((p) => p.label)
    )
      throw new Error("Mark the object with an include point or a box first.");
    const snapshot = runSnapshot();
    for (const model of models) await queue(model, null, snapshot);
  } catch (error) {
    message(error.message);
  } finally {
    $("run").disabled = !state.image;
  }
};
for (const [id, model] of [
  ["crop", "birefnet-crop"],
  ["matte", "vitmatte"],
])
  $(id).onclick = () =>
    queue(model, state.selected).catch((error) => message(error.message));
$("padding").oninput = () =>
  ($("padding-value").textContent = `${$("padding").value}%`);
$("radius").oninput = () =>
  ($("radius-value").textContent = `${$("radius").value} px`);
$("view").onchange = () => {
  state.cards.filter((c) => c.result).forEach(compose);
  drawAll();
};
$("background").onchange = drawAll;
$("show-original").onchange = () => {
  const original = state.cards.find((card) => !card.result);
  if (original) original.element.hidden = !$("show-original").checked;
  drawAll();
};
$("show-all").onclick = () => {
  state.cards
    .filter((card) => card.result)
    .forEach((card) => (card.element.hidden = false));
  drawAll();
};
$("export").onclick = async (event) => {
  event.preventDefault();
  if (!state.image) return;
  try {
    await Promise.all([...pendingNotes]);
    window.location.assign(`${imageURL()}/export`);
  } catch {
    message("Save your notes before exporting.");
  }
};
$("fit").onclick = () => {
  if (state.image) {
    state.zoom = null;
    state.cx = state.image.width / 2;
    state.cy = state.image.height / 2;
    drawAll();
  }
};
$("actual").onclick = () => zoomTo(1);
$("zoom-in").onclick = () => zoomTo(scale() * 1.4);
$("zoom-out").onclick = () => zoomTo(scale() / 1.4);
window.addEventListener("keydown", (event) => {
  if (
    event.code === "Space" &&
    !/INPUT|TEXTAREA|SELECT|BUTTON/.test(event.target.tagName)
  ) {
    space = true;
    event.preventDefault();
  }
});
window.addEventListener("keyup", (event) => {
  if (event.code === "Space") space = false;
});
window.addEventListener("blur", () => (space = false));
document.addEventListener("dragover", (event) => event.preventDefault());
document.addEventListener("drop", (event) => {
  event.preventDefault();
  if (event.dataTransfer.files.length) upload(event.dataTransfer.files[0]);
});

async function init() {
  try {
    const status = await api("/api/status");
    catalog = status.models;
    $("device").textContent =
      `${status.device.toUpperCase()} · local inference`;
    $("auth").hidden = status.authenticated;
    for (const model of catalog.filter((m) => m.kind !== "refine")) {
      const label = node("label", "model-option");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = model.id;
      checkbox.checked = model.id === "sam2-tiny" || model.id === "birefnet";
      label.append(checkbox, node("span", "", model.label));
      if (model.gated) label.append(node("small", "", "HF login"));
      $("models").append(label);
    }
    const saved = localStorage.getItem("selection-lab-image");
    const images = await refreshHistory(saved);
    const image = images.find((i) => i.id === saved);
    if (image) await openImage(image);
  } catch (error) {
    message(`Could not connect: ${error.message}`);
  }
}
init();
