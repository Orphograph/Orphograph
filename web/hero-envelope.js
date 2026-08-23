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
  const reducedMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function setOpen(next) {
    open = Boolean(next);
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
    }
  }

  function toggleOpen() {
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
