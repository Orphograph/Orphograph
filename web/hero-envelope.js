// hero-envelope.js — accessible envelope/receipt reveal on the homepage.
// The static HTML stays fully readable when JavaScript is unavailable.
//
// Motion has two tiers:
//   1. GENIE WARP (below, Web Animations API): a clone of the letter is cut
//      into horizontal bands that funnel out of the pocket mouth — the top
//      band first, every band squeezed to the throat width and released on a
//      stagger — the way a window leaves the macOS Dock. Closing plays the
//      same warp backwards, tail first.
//   2. CSS keyframes (orpho-genie in orpho-home.css): the fallback for an
//      engine without Element.animate or inset clip-paths. Reduced motion
//      skips both and lands on the is-open pose without a transition.

(function () {
  "use strict";

  const plate = document.getElementById("hero-envelope");
  const toggle = document.getElementById("hero-envelope-toggle");
  const receipt = document.getElementById("hero-sample-receipt");
  const action = toggle && toggle.querySelector(".orpho-envelope__toggle-action");

  if (!plate || !toggle || !receipt || !action) return;

  let open = false;
  let settleTimer = 0;
  const CLOSE_SETTLE_MS = 1050;
  const reducedMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Warp availability. Anything less falls back to the CSS keyframes.
  const warpAvailable = !reducedMotion &&
    typeof Element.prototype.animate === "function" &&
    Boolean(window.CSS && CSS.supports && CSS.supports("clip-path", "inset(0 0 0 0)"));

  // Warp choreography. Bands run top (0) to bottom (N-1). Band i starts
  // f_i * STAGGER_MS after the first and travels for TRAVEL_MS, so the head
  // of the letter is out while the tail is still in the throat — that lag
  // is the genie taper. Closing reverses the stagger: tail in first.

  const TRAVEL_MS = 560;
  const STAGGER_MS = 300;
  const LEAD_MS = 120;           // the flap starts lifting before the letter moves
  const WARP_MS = LEAD_MS + TRAVEL_MS + STAGGER_MS;
  const THROAT = 0.28;           // band width at the mouth, fraction of the letter
  const SWAY = 0.05;             // lateral bow at mid-flight, fraction of the width
  let warpStage = null;
  let warpRun = 0;
  let warpAnimations = [];

  function cancelWarp() {
    ++warpRun;
    warpAnimations.forEach((animation) => animation.cancel());
    warpAnimations = [];
    if (warpStage) warpStage.remove();
    warpStage = null;
    plate.classList.remove("is-warp");
  }

  // Phones: the plate grows to the letter's real height instead of a fixed
  // 780px the letter used to overflow. Desktop keeps its fixed composition.
  function fitPlate() {
    if (window.innerWidth > 680) {
      plate.style.removeProperty("--orpho-open-h");
      return;
    }
    plate.style.setProperty("--orpho-open-h",
      Math.ceil(receipt.offsetHeight * 0.98 + 40) + "px");
  }

  // The letter's OPEN pose in plate coordinates plus the pocket mouth, read
  // with every transition suppressed so the end state is measured, not the
  // in-flight one. State classes are restored before anything paints.
  function measureOpenPose() {
    const hadOpen = plate.classList.contains("is-open");
    const hadClosing = plate.classList.contains("is-closing");
    plate.classList.add("is-measuring");
    plate.classList.add("is-open");
    plate.classList.remove("is-closing");
    void plate.offsetWidth;
    const p = plate.getBoundingClientRect();
    const r = receipt.getBoundingClientRect();
    const pocket = plate.querySelector(".orpho-envelope__pocket");
    const m = pocket ? pocket.getBoundingClientRect() : p;
    const cs = getComputedStyle(receipt);
    const box = {
      left: receipt.offsetLeft,
      top: receipt.offsetTop,
      width: receipt.offsetWidth,
      height: receipt.offsetHeight,
      transform: cs.transform,
      origin: cs.transformOrigin,
      rect: { left: r.left - p.left, top: r.top - p.top, width: r.width, height: r.height },
      mouthTop: m.top - p.top,
      mouthLeft: m.left - p.left,
      mouthWidth: m.width
    };
    plate.classList.toggle("is-open", hadOpen);
    plate.classList.toggle("is-closing", hadClosing);
    void plate.offsetWidth;
    plate.classList.remove("is-measuring");
    return box;
  }

  function buildStage(box) {
    const stage = document.createElement("div");
    stage.className = "orpho-genie";
    stage.setAttribute("aria-hidden", "true");
    stage.style.left = box.left + "px";
    stage.style.top = box.top + "px";
    stage.style.width = box.width + "px";
    stage.style.height = box.height + "px";
    stage.style.transform = box.transform === "none" ? "" : box.transform;
    stage.style.transformOrigin = box.origin;
    const bands = [];
    const WARP_BANDS = window.innerWidth <= 680 ? 48 : 64;
    for (let i = 0; i < WARP_BANDS; i++) {
      const band = document.createElement("div");
      band.className = "orpho-genie__band";
      band.style.transformOrigin = "50% " + ((i + 0.5) / WARP_BANDS * 100) + "%";
      // A one-pixel overlap prevents antialiased seams between adjacent slices.
      const overlap = 100 / box.height;
      const top = Math.max(0, (i / WARP_BANDS) * 100 - overlap);
      const bottom = Math.max(0, 100 - ((i + 1) / WARP_BANDS) * 100 - overlap);
      band.style.clipPath = "inset(" + top + "% 0 " + bottom + "% 0)";
      const paper = receipt.cloneNode(true);
      paper.removeAttribute("id");
      paper.classList.remove("orpho-hero__receipt", "orpho-receipt--floating");
      paper.classList.add("orpho-genie__paper");
      paper.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
      band.appendChild(paper);
      stage.appendChild(band);
      bands.push(band);
    }
    plate.appendChild(stage);
    return { stage, bands };
  }

  // One band's flight path. Every band starts on the mouth line, squeezed to
  // the throat and pulled to the mouth's centre, and travels to its resting
  // place with a small lateral bow half-way — the neck of the genie.
  function bandFrames(f, box, mouthY, mouthDX) {
    const startY = mouthY - f * box.height;
    const sway = Math.sin(Math.PI * f) * box.width * SWAY;
    return [
      { transform: "translate3d(" + mouthDX + "px, " + startY + "px, 0) scaleX(" + THROAT + ") scaleY(.02)", offset: 0 },
      { transform: "translate3d(" + (mouthDX * 0.55 + sway) + "px, " + (startY * 0.42) + "px, 0) scaleX(" +
                   (THROAT + (1 - THROAT) * 0.5) + ") scaleY(.6)", offset: 0.5 },
      { transform: "translate3d(0, 0, 0) scaleX(1) scaleY(1)", offset: 1 }
    ];
  }

  function runWarp(opening) {
    cancelWarp();
    const run = warpRun;
    fitPlate();
    const box = measureOpenPose();
    // The real letter parks, invisible, at its end pose for the whole flight;
    // the choreography classes still drive the flap, the tie and the toggle.
    plate.classList.add("is-warp");
    plate.classList.remove("is-genie");
    plate.classList.toggle("is-open", opening);
    plate.classList.toggle("is-closing", !opening);
    toggle.setAttribute("aria-expanded", opening ? "true" : "false");
    action.textContent = opening ? "Return the receipt" : "Open the receipt";

    const built = buildStage(box);
    const stage = built.stage;
    warpStage = stage;
    const sy = box.rect.height / box.height || 1;
    const sx = box.rect.width / box.width || 1;
    const mouthY = (box.mouthTop - box.rect.top) / sy;
    const mouthDX = (box.mouthLeft + box.mouthWidth / 2 - box.rect.left) / sx - box.width / 2;
    const fMouth = Math.min(1, Math.max(0, mouthY / box.height));
    const lead = opening ? LEAD_MS : 0;
    // Behind the pocket while the head funnels out of the mouth; in front once
    // the tail — the part that rests over the pocket — starts to leave it.
    stage.style.zIndex = opening ? "2" : "6";
    const animations = [];
    built.bands.forEach((band, i) => {
      const f = (i + 0.5) / built.bands.length;
      const animation = band.animate(bandFrames(f, box, mouthY, mouthDX), {
        duration: TRAVEL_MS,
        delay: lead + (opening ? f : 1 - f) * STAGGER_MS,
        easing: "cubic-bezier(.22, .9, .32, 1)",
        direction: opening ? "normal" : "reverse",
        fill: "both"
      });
      animations.push(animation);
      warpAnimations.push(animation);
    });
    const zAt = opening ? lead + fMouth * STAGGER_MS
                        : (1 - fMouth) * STAGGER_MS + TRAVEL_MS * 0.6;
    // Use the document animation timeline for layering as well as bands.
    // Background throttling must not advance only the pocket hand-off.
    const layer = stage.animate([
      { zIndex: opening ? "2" : "6" },
      { zIndex: opening ? "6" : "2" }
    ], { duration: 1, delay: zAt, fill: "forwards" });
    warpAnimations = animations.concat(layer);
    const done = () => {
      if (run !== warpRun) return;
      warpAnimations.forEach((animation) => animation.cancel());
      warpAnimations = [];
      stage.remove();
      warpStage = null;
      plate.classList.remove("is-warp");
      if (!open) plate.classList.remove("is-closing");
    };
    Promise.all(animations.map((a) => a.finished)).then(done, done);
  }

  function setOpen(next) {
    window.clearTimeout(settleTimer);
    // Genie emergence only runs when opening from the settled tucked state;
    // re-opening while the close transition is mid-flight would restart the
    // animation at its 0% frame and teleport the receipt back into the
    // pocket, so that path stays on the (smoothly reversing) transition.
    const settled = !plate.classList.contains("is-closing") && !warpStage;
    open = Boolean(next);
    if (warpAvailable && settled) {
      try {
        runWarp(open);
        return;
      } catch (_) {
        // A partial WAAPI failure must leave the real receipt usable.
        cancelWarp();
      }
    }
    // Both directions get the genie motion when starting from a settled
    // state; a direction change mid-flight stays on the transition path,
    // which reverses smoothly instead of restarting at a keyframe.
    fitPlate();
    plate.classList.toggle("is-genie", settled);
    plate.classList.toggle("is-open", open);
    plate.classList.toggle("is-closing", !open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    action.textContent = open ? "Return the receipt" : "Open the receipt";

    if (open) {
      plate.classList.remove("is-closing");
    } else if (reducedMotion || !plate.classList.contains("is-ready")) {
      // With transitions disabled there is no transitionend event to perform
      // the final tuck-behind-the-pocket layer change.
      plate.classList.remove("is-closing");
    } else {
      // transitionend is the fast path below. This deadline is the durable
      // fallback for engines that omit it when transform layers are retimed.
      settleTimer = window.setTimeout(() => {
        if (!open) plate.classList.remove("is-closing");
      }, CLOSE_SETTLE_MS);
    }
  }

  // One click = one motion. While a pop-out or return is in flight, extra
  // clicks are swallowed instead of toggling the state back mid-animation —
  // the founder-reported "takes four clicks to close" was mid-close clicks
  // re-opening the envelope.
  let busyUntil = 0;
  // Covers the longest motion: the warp (lead + travel + stagger) or the CSS
  // emergence (120ms delay plus an 820ms animation). A guard shorter than
  // that leaves a window where a click can land mid-flight and reverse the
  // state.
  const MOTION_MS = Math.max(960, WARP_MS + 100);

  function motionInFlight() {
    return Boolean(warpStage) || performance.now() < busyUntil;
  }

  function toggleOpen() {
    if (motionInFlight()) return;
    busyUntil = performance.now() + MOTION_MS;
    setOpen(!open);
  }

  // Escape and click-away close through the same guard as a click; otherwise
  // they can start a return while the emergence is still playing.
  function requestClose() {
    if (motionInFlight()) return;
    busyUntil = performance.now() + MOTION_MS;
    setOpen(false);
  }

  toggle.addEventListener("click", toggleOpen);

  // The full physical object is clickable, while the visible button remains
  // the single keyboard and accessibility control.
  plate.addEventListener("click", (event) => {
    if (event.target.closest("button")) return;
    toggleOpen();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && open) {
      requestClose();
      toggle.focus({ preventScroll: true });
    }
  });

  document.addEventListener("click", (event) => {
    if (open && !plate.contains(event.target)) requestClose();
  });

  receipt.addEventListener("transitionend", (event) => {
    if (event.propertyName === "transform" && !open && !warpStage) {
      window.clearTimeout(settleTimer);
      plate.classList.remove("is-closing");
    }
  });

  window.addEventListener("resize", () => {
    cancelWarp();
    plate.classList.remove("is-closing", "is-genie");
    fitPlate();
    busyUntil = 0;
  });
  if (typeof ResizeObserver === "function") {
    new ResizeObserver(fitPlate).observe(receipt);
  }

  // Apply the tucked state without animating the page's first paint. Motion
  // is enabled one frame later for deliberate user-triggered transitions.
  plate.classList.add("is-interactive");
  requestAnimationFrame(() => {
    requestAnimationFrame(() => plate.classList.add("is-ready"));
  });
})();
