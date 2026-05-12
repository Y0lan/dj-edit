"""selector.py — camera/framing selection for the EDL builder.

In multi-camera mode: choose which physical camera covers a given timeline moment,
preferring shots with high motion energy and avoiding immediate repetition.

In single-camera mode: choose a *virtual framing* (wide/medium/tight) within the
source frame. Different framings act like different cuts when the source is 4K.
"""
from __future__ import annotations
from typing import Optional


# Virtual framings for single-camera mode. (crop_w_ratio, crop_h_ratio, x_offset_ratio, y_offset_ratio)
# These crop a 4K source into a 1080p-ish window with intentional re-framing.
FRAMINGS = {
    "wide":     {"zoom": 1.0,  "dx": 0.0,   "dy": 0.0,  "speed": 1.0},
    "medium":   {"zoom": 1.4,  "dx": 0.0,   "dy": 0.0,  "speed": 1.0},
    "tight":    {"zoom": 2.0,  "dx": 0.0,   "dy": 0.0,  "speed": 1.0},
    "left":     {"zoom": 1.6,  "dx": -0.15, "dy": 0.0,  "speed": 1.0},
    "right":    {"zoom": 1.6,  "dx": 0.15,  "dy": 0.0,  "speed": 1.0},
    "up":       {"zoom": 1.6,  "dx": 0.0,   "dy": -0.12,"speed": 1.0},
    "punch":    {"zoom": 2.5,  "dx": 0.0,   "dy": 0.0,  "speed": 1.0},  # for drop impact
}


def cameras_at(offsets: dict, t: float) -> list[tuple[str, dict]]:
    """All cameras whose coverage interval contains master time t."""
    out = []
    for cam, files in offsets.items():
        for f in files:
            cs, ce = f["coverage"]
            if cs <= t <= ce:
                out.append((cam, f))
    return out


def score_at(shot_scores: dict, cam: str, src_path: str, master_t: float) -> float:
    """Return motion-energy score for a given camera/file at master time t.
    Falls back to 0.5 if no data."""
    for c, files in shot_scores.items():
        if c != cam:
            continue
        for f in files:
            if f["path"] != src_path:
                continue
            scores = f.get("scores", [])
            if not scores:
                return 0.5
            # Nearest sample
            best = min(scores, key=lambda s: abs(s["t"] - master_t))
            return best["motion"] / 32.0  # signalstats YAVG ~0-32 range, normalize
    return 0.5


def pick_camera(offsets: dict, shot_scores: dict, t: float,
                last_pick: Optional[tuple[str, str]] = None,
                avoid_repeat_window: float = 4.0,
                last_pick_time: float = -999.0) -> Optional[tuple[str, dict]]:
    """Best camera + file at time t, avoiding immediate repetition during multicam."""
    available = cameras_at(offsets, t)
    if not available:
        return None
    if len(available) == 1:
        return available[0]

    scored = []
    for cam, finfo in available:
        score = score_at(shot_scores, cam, finfo["path"], t)
        # Penalty for repeating the last camera within the avoid window
        if last_pick and (cam, finfo["path"]) == last_pick:
            if (t - last_pick_time) < avoid_repeat_window:
                score *= 0.4
        scored.append((score, cam, finfo))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1], scored[0][2]


def framing_for_event(event_type: str, sequence_index: int = 0) -> str:
    """Pick a virtual framing for an event (single-camera mode).

    sequence_index helps vary framings within the same event type so consecutive
    cuts don't all show the same crop."""
    if event_type == "drop":
        return "punch" if sequence_index == 0 else "wide"
    if event_type == "peak":
        cycle = ["medium", "tight", "left", "right", "wide", "up"]
        return cycle[sequence_index % len(cycle)]
    if event_type == "build":
        return "tight" if sequence_index % 2 == 0 else "medium"
    if event_type == "breakdown":
        return "wide"
    if event_type == "hook":
        return "tight"
    # Lull / fallback
    return "medium"


def build_crop_filter(framing: str, src_w: int, src_h: int,
                      out_w: int, out_h: int) -> str:
    """Build an ffmpeg crop+scale filter chain for a virtual framing.

    Returns something like: `crop=<w>:<h>:<x>:<y>,scale=<out_w>:<out_h>:flags=lanczos`
    """
    f = FRAMINGS.get(framing, FRAMINGS["medium"])
    zoom = f["zoom"]
    # Desired output aspect dictates crop aspect on the source.
    out_aspect = out_w / out_h
    # Crop dimensions in source: width and height such that w/h == out_aspect
    # and crop area = src_area / (zoom*zoom approximately for the visible scale).
    # We treat zoom as "how many times into the source we punch."
    if src_w / src_h > out_aspect:
        # Source wider than output — crop height-driven
        crop_h = int(src_h / zoom)
        crop_w = int(crop_h * out_aspect)
    else:
        crop_w = int(src_w / zoom)
        crop_h = int(crop_w / out_aspect)
    crop_w = min(crop_w, src_w)
    crop_h = min(crop_h, src_h)
    cx = int((src_w - crop_w) / 2 + f["dx"] * src_w)
    cy = int((src_h - crop_h) / 2 + f["dy"] * src_h)
    cx = max(0, min(cx, src_w - crop_w))
    cy = max(0, min(cy, src_h - crop_h))
    # YUV420 H.264 encoders require even crop dimensions AND even offsets.
    # Round each down to even.
    crop_w -= crop_w % 2
    crop_h -= crop_h % 2
    cx -= cx % 2
    cy -= cy % 2
    return f"crop={crop_w}:{crop_h}:{cx}:{cy},scale={out_w}:{out_h}:flags=lanczos"
