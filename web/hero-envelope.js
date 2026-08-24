// hero-envelope.js — accessible envelope/receipt reveal on the homepage.
// The static HTML stays fully readable when JavaScript is unavailable.

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

  function setOpen(next) {
    window.clearTimeout(settleTimer);
    // Genie emergence only runs when opening from the settled tucked state;
    // re-opening while the close transition is mid-flight would restart the
    // animation at its 0% frame and teleport the receipt back into the
    // pocket, so that path stays on the (smoothly reversing) transition.
    const wasClosing = plate.classList.contains("is-closing");
    open = Boolean(next);
    plate.classList.toggle("is-genie", open && !wasClosing);
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
  const MOTION_MS = 820;

  function toggleOpen() {
    const now = performance.now();
    if (now < busyUntil) return;
    busyUntil = now + MOTION_MS;
    setOpen(!open);
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
      setOpen(false);
      toggle.focus({ preventScroll: true });
    }
  });

  document.addEventListener("click", (event) => {
    if (open && !plate.contains(event.target)) setOpen(false);
  });

  receipt.addEventListener("transitionend", (event) => {
    if (event.propertyName === "transform" && !open) {
      window.clearTimeout(settleTimer);
      plate.classList.remove("is-closing");
    }
  });

  // Apply the tucked state without animating the page's first paint. Motion
  // is enabled one frame later for deliberate user-triggered transitions.
  plate.classList.add("is-interactive");
  requestAnimationFrame(() => {
    requestAnimationFrame(() => plate.classList.add("is-ready"));
  });
})();
