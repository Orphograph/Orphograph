#!/usr/bin/env python3
"""Generate web/assets/anchor-pulse.json — a Lottie 'notary pulse'.

Hand-written Lottie keyframes without bezier i/o handles render with a
0x0 bbox in lottie-web. This builds keyframes in the AE-export shape
(each non-final keyframe carries i/o easing) so the shapes actually draw.

A central filled dot pulses; two stroked rings radiate outward and fade.
Cream-friendly warm-bronze palette. 120f @ 60fps = 2s clean loop.
"""
import json, os

W = H = 200
FR, OP = 60, 120
CENTER = [100, 100]

def kf(t, s, last=False):
    k = {"t": t, "s": s if isinstance(s, list) else [s]}
    if not last:
        k["i"] = {"x": [0.42], "y": [1]}
        k["o"] = {"x": [0.58], "y": [0]}
    return k

def anim(keys):
    return {"a": 1, "k": [kf(*k) for k in keys[:-1]] + [kf(keys[-1][0], keys[-1][1], last=True)]}

def stat(v):
    return {"a": 0, "k": v}

def ring(ind, color, width, phase):
    """phase 0 → expands in first half, phase 60 → second half."""
    o0, o1, o2 = phase, phase + 15, phase + 55
    return {
        "ddd": 0, "ind": ind, "ty": 4, "nm": "ring%d" % ind, "sr": 1,
        "ks": {
            "o": anim([(0, [0]), (o0, [0]), (o1, [85]), (o2, [0]), (OP, [0])]
                      if phase else [(0, [0]), (o1, [85]), (o2, [0]), (OP, [0])]),
            "r": stat(0),
            "p": stat(CENTER + [0]),
            "a": stat([0, 0, 0]),
            "s": anim([(0, [10, 10, 100]), (o0, [10, 10, 100]), (o2, [155, 155, 100]), (OP, [155, 155, 100])]
                      if phase else [(0, [10, 10, 100]), (o2, [155, 155, 100]), (OP, [155, 155, 100])]),
        },
        "shapes": [{"ty": "gr", "nm": "g", "it": [
            {"ty": "el", "d": 1, "s": stat([100, 100]), "p": stat([0, 0]), "nm": "e"},
            {"ty": "st", "c": stat(color + [1]), "o": stat(100), "w": stat(width),
             "lc": 2, "lj": 1, "ml": 4, "nm": "s"},
            {"ty": "tr", "p": stat([0, 0]), "a": stat([0, 0]), "s": stat([100, 100]),
             "r": stat(0), "o": stat(100)},
        ]}],
        "ip": 0, "op": OP, "st": 0, "bm": 0,
    }

dot = {
    "ddd": 0, "ind": 1, "ty": 4, "nm": "dot", "sr": 1,
    "ks": {
        "o": stat(90), "r": stat(0), "p": stat(CENTER + [0]), "a": stat([0, 0, 0]),
        "s": anim([(0, [100, 100, 100]), (60, [122, 122, 100]), (OP, [100, 100, 100])]),
    },
    "shapes": [{"ty": "gr", "nm": "g", "it": [
        {"ty": "el", "d": 1, "s": stat([26, 26]), "p": stat([0, 0]), "nm": "e"},
        {"ty": "fl", "c": stat([0.227, 0.184, 0.141, 1]), "o": stat(100), "nm": "f"},
        {"ty": "tr", "p": stat([0, 0]), "a": stat([0, 0]), "s": stat([100, 100]),
         "r": stat(0), "o": stat(100)},
    ]}],
    "ip": 0, "op": OP, "st": 0, "bm": 0,
}

data = {
    "v": "5.7.4", "fr": FR, "ip": 0, "op": OP, "w": W, "h": H,
    "nm": "anchor-pulse", "ddd": 0, "assets": [],
    "layers": [
        dot,
        ring(2, [0.451, 0.318, 0.196], 8, 0),
        ring(3, [0.624, 0.443, 0.275], 5, 60),
    ],
    "markers": [],
}

out = os.path.join(os.path.dirname(__file__), "..", "web", "assets", "anchor-pulse.json")
with open(out, "w") as f:
    json.dump(data, f, separators=(",", ":"))
print("wrote", os.path.relpath(out), "-", os.path.getsize(out), "bytes")
