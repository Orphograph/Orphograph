/* Orphograph — Experience route.
   Lenis smooth scroll · Three.js seal medallion · GSAP ScrollTrigger ·
   custom cursor · Lottie. Degrades gracefully if any lib is absent.
   Preview-only; vendor libs before promoting (see header in experience.html). */
(function () {
  "use strict";
  var doc = document, body = doc.body;
  body.classList.remove("no-js");

  var prefersReduced = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var hasGSAP = typeof window.gsap !== "undefined";
  var hasST = hasGSAP && typeof window.ScrollTrigger !== "undefined";
  var hasLenis = typeof window.Lenis !== "undefined";
  var hasTHREE = typeof window.THREE !== "undefined";
  var hasLottie = typeof window.lottie !== "undefined";

  if (hasST) gsap.registerPlugin(ScrollTrigger);

  /* ── Lenis smooth scroll ─────────────────────────────────────── */
  // ?nolenis=1 disables smooth scroll (native scroll) — useful for
  // headless screenshot tools whose compositor doesn't track Lenis state.
  var noLenis = /[?&]nolenis=1/.test(location.search);
  var lenis = null;
  if (hasLenis && !prefersReduced && !noLenis) {
    lenis = new Lenis({
      duration: 1.15,
      easing: function (t) { return Math.min(1, 1.001 - Math.pow(2, -10 * t)); },
      smoothWheel: true,
      wheelMultiplier: 0.9,
      touchMultiplier: 1.2,
    });
    if (hasST) {
      lenis.on("scroll", ScrollTrigger.update);
      gsap.ticker.add(function (time) { lenis.raf(time * 1000); });
      gsap.ticker.lagSmoothing(0);
    } else {
      var raf = function (t) { lenis.raf(t); requestAnimationFrame(raf); };
      requestAnimationFrame(raf);
    }
    if (typeof window !== "undefined") window.__lenis = lenis;
  }

  /* ── Scroll progress hairline ────────────────────────────────── */
  var bar = doc.getElementById("scroll-progress");
  function setProgress() {
    var h = doc.documentElement;
    var max = (h.scrollHeight - h.clientHeight) || 1;
    var p = Math.min(1, Math.max(0, (window.scrollY || h.scrollTop) / max));
    if (bar) bar.style.width = (p * 100).toFixed(2) + "%";
  }
  window.addEventListener("scroll", setProgress, { passive: true });
  setProgress();

  /* ── Custom cursor ───────────────────────────────────────────── */
  (function cursor() {
    var dot = doc.getElementById("cursor-dot");
    var ring = doc.getElementById("cursor-ring");
    if (!dot || !ring || window.matchMedia("(pointer:coarse)").matches) return;
    var mx = innerWidth / 2, my = innerHeight / 2, rx = mx, ry = my;
    window.addEventListener("mousemove", function (e) {
      mx = e.clientX; my = e.clientY;
      dot.style.transform = "translate(" + mx + "px," + my + "px) translate(-50%,-50%)";
    }, { passive: true });
    (function loop() {
      rx += (mx - rx) * 0.18; ry += (my - ry) * 0.18;
      ring.style.transform = "translate(" + rx + "px," + ry + "px) translate(-50%,-50%)";
      requestAnimationFrame(loop);
    })();
    doc.querySelectorAll("[data-cursor], a, button").forEach(function (el) {
      el.addEventListener("mouseenter", function () { ring.classList.add("is-hover"); });
      el.addEventListener("mouseleave", function () { ring.classList.remove("is-hover"); });
    });
  })();

  /* ── Three.js seal medallion ─────────────────────────────────── */
  var sealScroll = 0; // 0..1 across the hero, fed to the light
  (function seal() {
    var stage = doc.getElementById("seal-stage");
    if (!hasTHREE || !stage || prefersReduced) {
      // Fallback: drop a static seal image so the hero is never empty.
      if (stage) {
        var img = doc.createElement("img");
        img.src = "/seal-display.png?v=8"; img.alt = "";
        img.style.cssText = "position:absolute;top:50%;left:50%;width:min(46vw,520px);transform:translate(-50%,-50%);filter:drop-shadow(0 24px 60px rgba(60,40,20,.18));";
        stage.appendChild(img);
      }
      return;
    }
    // clientWidth/Height can be 0 if the stage isn't laid out yet at init;
    // fall back to the viewport so the canvas is never sized 0×0.
    function dims() {
      return [stage.clientWidth || window.innerWidth || 1,
              stage.clientHeight || window.innerHeight || 1];
    }
    var d0 = dims(), w = d0[0], h = d0[1];
    var scene = new THREE.Scene();
    var camera = new THREE.PerspectiveCamera(35, w / h, 0.1, 100);
    camera.position.set(0, 0, 6);

    var renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setSize(w, h);
    stage.appendChild(renderer.domElement);

    function resize() {
      var dd = dims(); w = dd[0]; h = dd[1];
      camera.aspect = w / h; camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    }
    // Re-assert size after layout settles and whenever the stage resizes.
    window.addEventListener("load", resize);
    requestAnimationFrame(resize);
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(resize).observe(stage);
    }

    var group = new THREE.Group();
    scene.add(group);

    // Medallion: a short cylinder = an embossed wax disc.
    var tex = new THREE.TextureLoader().load("/seal-display.png?v=8", function () {
      renderer.render(scene, camera);
    });
    tex.anisotropy = renderer.capabilities.getMaxAnisotropy ?
      renderer.capabilities.getMaxAnisotropy() : 4;

    var faceMat = new THREE.MeshStandardMaterial({
      map: tex, bumpMap: tex, bumpScale: 0.05,
      roughness: 0.62, metalness: 0.15, color: 0xfaf3df,
    });
    var edgeMat = new THREE.MeshStandardMaterial({
      color: 0xe7d9bc, roughness: 0.8, metalness: 0.1,
    });
    var R = 1.9, TH = 0.34;
    var disc = new THREE.Mesh(
      new THREE.CylinderGeometry(R, R, TH, 96, 1, false),
      [edgeMat, faceMat, faceMat]
    );
    disc.rotation.x = Math.PI / 2; // face the camera
    group.add(disc);

    // Lights: warm key (the "wax glow"), cool-ish fill, ambient floor.
    var ambient = new THREE.AmbientLight(0xfff3e0, 0.55);
    scene.add(ambient);
    var key = new THREE.PointLight(0xffcf8f, 1.5, 40);
    key.position.set(3, 3, 4);
    scene.add(key);
    var rim = new THREE.PointLight(0xb8835a, 0.9, 30);
    rim.position.set(-4, -2, 2);
    scene.add(rim);

    // Drag to rotate + gentle auto-rotate.
    var auto = 0.0035, velX = 0, velY = 0, tgtX = 0, dragging = false, px = 0, py = 0;
    function down(x, y) { dragging = true; px = x; py = y; }
    function move(x, y) {
      if (!dragging) return;
      velY = (x - px) * 0.006; velX = (y - py) * 0.006;
      group.rotation.y += velY; group.rotation.x += velX;
      group.rotation.x = Math.max(-0.6, Math.min(0.6, group.rotation.x));
      px = x; py = y;
    }
    function up() { dragging = false; }
    var el = renderer.domElement;
    el.style.touchAction = "pan-y";
    el.addEventListener("mousedown", function (e) { down(e.clientX, e.clientY); });
    window.addEventListener("mousemove", function (e) { move(e.clientX, e.clientY); });
    window.addEventListener("mouseup", up);
    el.addEventListener("touchstart", function (e) { var t = e.touches[0]; down(t.clientX, t.clientY); }, { passive: true });
    el.addEventListener("touchmove", function (e) { var t = e.touches[0]; move(t.clientX, t.clientY); }, { passive: true });
    el.addEventListener("touchend", up);

    // Pointer-parallax of the whole group (subtle).
    var mxn = 0, myn = 0;
    window.addEventListener("mousemove", function (e) {
      mxn = (e.clientX / innerWidth - 0.5);
      myn = (e.clientY / innerHeight - 0.5);
    }, { passive: true });

    (function render() {
      requestAnimationFrame(render);
      if (!dragging) {
        group.rotation.y += auto + velY * 0.0;
        velY *= 0.92; velX *= 0.92;
        group.rotation.x += (myn * 0.18 - group.rotation.x) * 0.04;
      }
      // Scroll drives the key light: it sweeps + brightens as you descend
      // the hero — the "glow reacts to scroll position" beat.
      var s = sealScroll;
      key.position.x = 3 * Math.cos(s * Math.PI);
      key.position.y = 3 - s * 5;
      key.intensity = 1.5 + s * 1.8;
      rim.intensity = 0.9 + s * 1.2;
      group.position.y = s * 0.6;          // drifts down slightly
      group.scale.setScalar(1 - s * 0.12); // recedes a touch
      camera.position.x += (mxn * 0.4 - camera.position.x) * 0.05;
      camera.lookAt(0, 0, 0);
      renderer.render(scene, camera);
    })();

    window.addEventListener("resize", resize);

    // Feed hero scroll progress to the light (via ST if present, else scroll).
    if (hasST) {
      ScrollTrigger.create({
        trigger: "#hero", start: "top top", end: "bottom top",
        onUpdate: function (self) { sealScroll = self.progress; },
      });
    } else {
      window.addEventListener("scroll", function () {
        var hero = doc.getElementById("hero");
        var rect = hero.getBoundingClientRect();
        sealScroll = Math.min(1, Math.max(0, -rect.top / (rect.height || 1)));
      }, { passive: true });
    }
  })();

  /* ── GSAP reveals, parallax, expansion ───────────────────────── */
  if (hasST && !prefersReduced) {
    // line + block reveals
    gsap.utils.toArray(".reveal, .reveal-line").forEach(function (el) {
      ScrollTrigger.create({
        trigger: el, start: "top 86%",
        onEnter: function () { el.classList.add("is-in"); },
      });
    });

    // Parallax depth layers
    gsap.utils.toArray("#depth .layer").forEach(function (layer) {
      var depth = parseFloat(layer.getAttribute("data-depth") || "0.5");
      gsap.fromTo(layer, { yPercent: depth * 40 }, {
        yPercent: -depth * 40, ease: "none",
        scrollTrigger: { trigger: "#depth", start: "top bottom", end: "bottom top", scrub: true },
      });
    });

    // Image expansion: small framed image grows to fullscreen, caption fades in.
    var frame = doc.getElementById("expand-frame");
    var cap = doc.getElementById("expand-caption");
    if (frame) {
      var tl = gsap.timeline({
        scrollTrigger: { trigger: "#expand", start: "top top", end: "bottom bottom", scrub: 0.6 },
      });
      tl.fromTo(frame,
        { width: "38vw", height: "46vh", borderRadius: "6px" },
        { width: "100vw", height: "100vh", borderRadius: "0px", ease: "power2.inOut" }, 0);
      tl.fromTo(frame.querySelector("img"), { scale: 1.15 }, { scale: 1.0, ease: "none" }, 0);
      if (cap) tl.to(cap, { opacity: 1, ease: "power1.in" }, 0.45);
    }

    // Gallery head subtle horizontal drift as the rail enters (depth cue).
    gsap.fromTo("#gallery .gallery-head", { x: -20 }, {
      x: 0, ease: "none",
      scrollTrigger: { trigger: "#gallery", start: "top bottom", end: "top center", scrub: true },
    });
  } else {
    // No-GSAP fallback: just show everything.
    doc.querySelectorAll(".reveal, .reveal-line").forEach(function (el) {
      el.classList.add("is-in");
    });
    var cap2 = doc.getElementById("expand-caption");
    if (cap2) cap2.style.opacity = 1;
  }

  /* ── Lottie: the anchor pulse, plays when in view ────────────── */
  if (hasLottie) {
    var mount = doc.getElementById("lottie-anchor");
    if (mount) {
      var anim = lottie.loadAnimation({
        container: mount, renderer: "svg", loop: true, autoplay: false,
        path: "/assets/anchor-pulse.json",
      });
      var played = false;
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting && !played) { anim.play(); played = true; }
        });
      }, { threshold: 0.4 });
      io.observe(mount);
    }
  }

  // Refresh ST after images load (heights settle).
  window.addEventListener("load", function () {
    if (hasST) ScrollTrigger.refresh();
    setProgress();
  });
})();
