// One continuous paper surface, drawn from the real receipt. No DOM band copies.
(function () {
  "use strict";
  const clamp = (x) => Math.max(0, Math.min(1, x));
  const smooth = (x) => { x = clamp(x); return x * x * (3 - 2 * x); };
  const mix = (a, b, p) => a + (b - a) * p;

  // Every row shares the same monotone vertical map. Adjacent rows therefore
  // meet exactly, while the lower edge remains a narrow neck until late in flight.
  function point(progress, row, target, mouth) {
    const p = clamp(progress);
    const v = clamp(row);
    const head = smooth(p);
    const tail = smooth((p - 0.48) / 0.52);
    const top = mix(mouth.y, target.top, head);
    const bottom = mix(mouth.y, target.top + target.height, tail);
    const spread = smooth(p * 1.65 - v * 0.65);
    const width = mix(mouth.width, target.width, spread);
    const center = mix(mouth.x, target.left + target.width / 2, head) +
      Math.sin(v * Math.PI) * Math.sin(p * Math.PI) * target.width * 0.035;
    return { x: center - width / 2, y: mix(top, bottom, v), width };
  }
  function advance(value, destination, elapsed) {
    return clamp(value + (destination ? 1 : -1) * Math.min(elapsed, 40) / 1050);
  }
  if (typeof document === "undefined") {
    if (typeof module !== "undefined") module.exports = { point, advance };
    return;
  }
  const plate = document.getElementById("hero-envelope");
  const toggle = document.getElementById("hero-envelope-toggle");
  const receipt = document.getElementById("hero-sample-receipt");
  if (!plate || !toggle || !receipt) return;
  const action = toggle.querySelector(".orpho-envelope__toggle-action");
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  let progress = 0;
  let destination = 0;
  let frame = 0;
  let previousTime = 0;
  let texture = null;
  let staleTexture = false;
  let surface = null;
  let context = null;
  let layout = null;
  let target = null;
  let currentHeight = 0;
  let resizeFrame = 0;
  const pixelRatio = () => Math.min(devicePixelRatio || 1, 2);

  function dimensions() {
    if (layout) return layout;
    const width = Math.max(1, plate.clientWidth);
    const envelopeHeight = Math.min(width * 0.94, 420) / 1.62;
    layout = { width, envelopeHeight, closed: envelopeHeight + 100, opened: receipt.offsetHeight + 156 };
    return layout;
  }
  function fit() {
    const size = dimensions();
    currentHeight = mix(size.closed, size.opened, smooth(progress));
    plate.style.height = currentHeight + "px";
  }
  function clearSurface() {
    if (surface) surface.remove();
    surface = null;
    context = null;
    plate.classList.remove("is-warp");
  }
  function settle() {
    cancelAnimationFrame(frame);
    frame = 0;
    progress = destination;
    fit();
    if (staleTexture) { texture = null; layout = null; staleTexture = false; fit(); }
    clearSurface();
    plate.classList.toggle("is-open", Boolean(destination));
    plate.classList.remove("is-closing");
  }

  // Rasterize the visible content in place from measured glyph positions. This
  // avoids foreignObject/SVG security restrictions and keeps the exact live values
  // and fonts rather than maintaining a second, decorative receipt template.
  function snapshot() {
    const bounds = receipt.getBoundingClientRect();
    const ratio = pixelRatio();
    const image = document.createElement("canvas");
    image.width = Math.ceil(bounds.width * ratio);
    image.height = Math.ceil(bounds.height * ratio);
    const ctx = image.getContext("2d");
    if (!ctx) throw new Error("Canvas unavailable");
    ctx.scale(ratio, ratio);
    const paper = ctx.createLinearGradient(0, 0, 0, bounds.height);
    paper.addColorStop(0, "#fcf8ef");
    paper.addColorStop(1, "#f1e8d6");
    ctx.fillStyle = paper;
    ctx.fillRect(0, 0, bounds.width, bounds.height);
    ctx.strokeStyle = "rgba(133,107,66,.22)";
    ctx.strokeRect(0.5, 0.5, bounds.width - 1, bounds.height - 1);
    receipt.querySelectorAll("hr, dd").forEach((node) => {
      const r = node.getBoundingClientRect();
      ctx.beginPath();
      ctx.strokeStyle = node.tagName === "HR" ? "#bc9a60" : "rgba(133,107,66,.16)";
      ctx.setLineDash(node.tagName === "HR" ? [] : [1, 2]);
      const y = node.tagName === "HR" ? r.top - bounds.top : r.bottom - bounds.top;
      ctx.moveTo(r.left - bounds.left, y);
      ctx.lineTo(r.right - bounds.left, y);
      ctx.stroke();
    });
    const crest = receipt.querySelector("img");
    if (crest && crest.complete && crest.naturalWidth) {
      const r = crest.getBoundingClientRect();
      ctx.drawImage(crest, r.left - bounds.left, r.top - bounds.top, r.width, r.height);
    }
    const walker = document.createTreeWalker(receipt, NodeFilter.SHOW_TEXT);
    let text;
    while ((text = walker.nextNode())) {
      if (!text.textContent.trim()) continue;
      const style = getComputedStyle(text.parentElement);
      ctx.font = style.fontStyle + " " + style.fontWeight + " " + style.fontSize + " " + style.fontFamily;
      ctx.fillStyle = style.color;
      ctx.textBaseline = "alphabetic";
      const fontSize = parseFloat(style.fontSize);
      const range = document.createRange();
      for (let i = 0; i < text.length; i++) {
        const character = text.textContent[i];
        if (/\s/.test(character)) continue;
        range.setStart(text, i);
        range.setEnd(text, i + 1);
        const r = range.getBoundingClientRect();
        const glyph = style.textTransform === "uppercase" ? character.toUpperCase() : character;
        ctx.fillText(glyph, r.left - bounds.left, r.top - bounds.top + fontSize * 0.86);
      }
    }
    return image;
  }

  function prepare() {
    if (!texture) texture = snapshot();
    if (surface) return;
    surface = document.createElement("canvas");
    surface.className = "orpho-genie-surface";
    surface.setAttribute("aria-hidden", "true");
    const ratio = pixelRatio();
    const height = Math.max(dimensions().opened, dimensions().closed);
    surface.width = Math.ceil(plate.clientWidth * ratio);
    surface.height = Math.ceil(height * ratio);
    surface.style.width = plate.clientWidth + "px";
    surface.style.height = height + "px";
    context = surface.getContext("2d");
    if (!context) throw new Error("Canvas unavailable");
    context.scale(ratio, ratio);
    const p = plate.getBoundingClientRect();
    const r = receipt.getBoundingClientRect();
    target = { left: r.left - p.left, top: r.top - p.top, width: r.width, height: r.height };
    plate.appendChild(surface);
    plate.classList.add("is-warp");
  }

  function draw() {
    const size = dimensions();
    const mouth = {
      x: size.width / 2,
      y: currentHeight - 58 - size.envelopeHeight * 0.88,
      width: size.envelopeHeight * 1.62 * 0.12
    };
    const ctx = context;
    ctx.clearRect(0, 0, surface.width, surface.height);
    if (progress < 0.003) return;
    const rows = Math.ceil(target.height / 2);
    const edges = Array.from({ length: rows + 1 }, (_, i) => point(progress, i / rows, target, mouth));
    ctx.save();
    // A single continuous silhouette clips the whole image; no disconnected
    // rectangles, cracks, or full-height text stacked at the mouth.
    ctx.beginPath();
    edges.forEach((a, i) => i ? ctx.lineTo(a.x, a.y) : ctx.moveTo(a.x, a.y));
    for (let i = rows; i >= 0; i--) ctx.lineTo(edges[i].x + edges[i].width, edges[i].y);
    ctx.closePath();
    ctx.clip();
    ctx.fillStyle = "#f8f2e5";
    ctx.fillRect(0, 0, surface.width, surface.height);
    for (let i = 0; i < rows; i++) {
      const a = edges[i];
      const b = edges[i + 1];
      const h = b.y - a.y;
      if (h <= 0) continue;
      const sy = i / rows * texture.height;
      ctx.drawImage(texture, 0, sy, texture.width, texture.height / rows,
        (a.x + b.x) / 2, a.y, (a.width + b.width) / 2, h + 0.6);
    }
    ctx.restore();
  }

  function tick(now) {
    const elapsed = previousTime ? Math.min(now - previousTime, 40) : 0;
    previousTime = now;
    progress = advance(progress, destination, elapsed);
    fit();
    try { draw(); } catch (_) { settle(); return; }
    if (progress === destination) { settle(); return; }
    frame = requestAnimationFrame(tick);
  }
  function setOpen(next) {
    destination = next ? 1 : 0;
    toggle.setAttribute("aria-expanded", next ? "true" : "false");
    action.textContent = next ? "Return the receipt" : "Open the receipt";
    plate.classList.toggle("is-open", next || progress > 0);
    plate.classList.toggle("is-closing", !next && progress > 0);
    fit();
    if (motion.matches || document.hidden || plate.clientWidth < 1) { settle(); return; }
    try {
      prepare();
      if (!frame) { previousTime = 0; frame = requestAnimationFrame(tick); }
    } catch (_) { settle(); }
  }
  toggle.addEventListener("click", () => setOpen(!destination));
  plate.addEventListener("click", (event) => {
    if (!event.target.closest("button, a")) setOpen(!destination);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && destination) {
      setOpen(false);
      toggle.focus({ preventScroll: true });
    }
  });
  function refreshLayout() {
    resizeFrame = 0;
    layout = null;
    texture = null;
    settle();
  }
  function resize() {
    // Resize events can arrive many times in one frame while dragging a window.
    // Stop the old surface immediately, then measure once at the next paint.
    cancelAnimationFrame(frame);
    frame = 0;
    clearSurface();
    if (!resizeFrame) resizeFrame = requestAnimationFrame(refreshLayout);
  }
  window.addEventListener("resize", resize);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      cancelAnimationFrame(resizeFrame);
      resizeFrame = 0;
      settle();
      texture = null;
    } else {
      resize();
    }
  });
  motion.addEventListener("change", () => { if (motion.matches) settle(); });
  if (document.fonts) document.fonts.ready.then(() => {
    if (frame) { staleTexture = true; } else { layout = null; texture = null; fit(); }
  });
  plate.classList.add("is-interactive", "is-continuous");
  fit();
  requestAnimationFrame(() => plate.classList.add("is-ready"));
})();
