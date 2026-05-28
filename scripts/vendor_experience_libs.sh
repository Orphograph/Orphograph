#!/usr/bin/env bash
# Refresh the vendored libraries used by web/experience.html.
# Pinned versions — bump here intentionally, never float.
# Keeps the experience route free of third-party runtime calls.
set -euo pipefail
cd "$(dirname "$0")/../web/vendor"

dl() { echo "→ $2"; curl -fsSL --max-time 60 -o "$2" "$1"; }

dl "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"            three.min.js
dl "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"                gsap.min.js
dl "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/ScrollTrigger.min.js"       ScrollTrigger.min.js
dl "https://cdn.jsdelivr.net/npm/@studio-freight/lenis@1.0.42/dist/lenis.min.js"   lenis.min.js
dl "https://cdnjs.cloudflare.com/ajax/libs/lottie-web/5.12.2/lottie.min.js"        lottie.min.js

echo "Vendored:"; ls -la *.js | awk '{print "  "$5, $NF}'
