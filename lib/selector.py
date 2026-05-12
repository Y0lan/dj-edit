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
    return "medium"


# ─────────────────────────── Move pools (Tier D) ───────────────────────────
# Each event type maps to a weighted pool of Insta360 move names. Weights are
# baseline preferences; --style and --reference packs can override them.
# Last 3 picks are tracked to avoid immediate repetition (see pick_move_for_event).

MOVE_POOLS_DEFAULT = {
    "breakdown": [
        ("breakdown_drift", 3),
        ("orbit", 2),
        ("crowd_reveal", 2),
        ("tilt_reveal", 1),
        ("counter_motion", 2),
    ],
    "build": [
        ("build_tension", 4),
        ("dolly_zoom", 2),
        ("dolly_with_drift", 2),
        ("kick_pulse_fov", 1),
    ],
    "drop": [
        ("drop_impact", 5),
        ("whip_pan", 1),
    ],
    "peak": [
        ("bpm_sync_orbit", 3),
        ("kick_pulse_fov", 2),
        ("orbit", 2),
        ("counter_motion", 1),
    ],
    "lull": [
        ("orbit", 2),
        ("crowd_reveal", 1),
        ("bpm_sync_orbit", 1),
    ],
    "hook": [
        ("dolly_with_drift", 3),
        ("counter_motion", 1),
    ],
}


def pick_move_for_event(event_type: str, intensity: float = 0.5,
                        rng=None, recent_picks: list[str] | None = None,
                        pool_override: dict | None = None) -> str:
    """Weighted pick from MOVE_POOLS, with avoid-immediate-repetition.

    - intensity in [0,1] biases toward "bigger" moves (drop_impact, build_tension,
      bpm_sync_orbit) when high. Low intensity biases toward subtle moves.
    - recent_picks: last N move names; the current pick will avoid them if possible.
    - pool_override: optional dict from a --style or --reference pack to replace
      the default pool for this event type.

    Returns a move name from MOVES.
    """
    import random
    if rng is None:
        rng = random.Random()
    pools = pool_override if pool_override is not None else MOVE_POOLS_DEFAULT
    pool = pools.get(event_type) or MOVE_POOLS_DEFAULT.get(event_type)
    if not pool:
        return "orbit"
    recent = set(recent_picks or [])
    # Intensity-biased weights — "big" moves get a boost when intensity is high
    BIG_MOVES = {"drop_impact", "build_tension", "bpm_sync_orbit", "whip_pan", "kick_pulse_fov"}
    weighted = []
    for name, base_w in pool:
        w = float(base_w)
        if name in BIG_MOVES:
            w *= 0.5 + intensity  # 0.5x at intensity=0, 1.5x at intensity=1
        if name in recent:
            w *= 0.2  # heavy penalty for immediate repeat
        weighted.append((name, w))
    total = sum(w for _, w in weighted)
    if total <= 0:
        return pool[0][0]
    r = rng.random() * total
    acc = 0.0
    for name, w in weighted:
        acc += w
        if r <= acc:
            return name
    return pool[-1][0]


def load_pool_overrides(style: str | None = None,
                        reference: str | None = None,
                        presets_root: str = "presets",
                        valid_moves: set | None = None) -> dict | None:
    """Load style and/or reference JSON files, merge their move pools.

    Reference overrides style; style overrides default. Returns None if neither
    is given.

    Warns to stderr (NOT silent):
      - missing pack file (typo in --style/--reference)
      - malformed JSON
      - pool entry referencing a move not in `valid_moves`

    `valid_moves` should be set(MOVES.keys()) from lib.moves for validation.
    """
    import json
    import sys as _sys
    from pathlib import Path

    root = Path(presets_root)
    merged: dict[str, list] = {}
    for kind, name in (("styles", style), ("references", reference)):
        if not name:
            continue
        p = root / kind / f"{name}.json"
        if not p.exists():
            print(f"WARN: {kind[:-1]} pack '{name}' not found at {p} — "
                  f"using built-in defaults", file=_sys.stderr)
            continue
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            print(f"WARN: {kind[:-1]} pack '{name}' is malformed JSON ({e}) — "
                  f"using built-in defaults", file=_sys.stderr)
            continue
        except OSError as e:
            print(f"WARN: {kind[:-1]} pack '{name}' could not be read ({e})",
                  file=_sys.stderr)
            continue
        pack_pools = data.get("move_pools", {})
        if not isinstance(pack_pools, dict):
            print(f"WARN: {kind[:-1]} pack '{name}' has invalid move_pools shape",
                  file=_sys.stderr)
            continue
        for event_type, pool in pack_pools.items():
            validated = []
            for m in pool:
                mname = m.get("name") if isinstance(m, dict) else None
                if not mname:
                    continue
                weight = float(m.get("weight", 1))
                if weight <= 0:
                    print(f"WARN: {kind[:-1]}/{name}: zero/negative weight for "
                          f"'{mname}' in {event_type} — skipping", file=_sys.stderr)
                    continue
                if valid_moves is not None and mname not in valid_moves:
                    print(f"WARN: {kind[:-1]}/{name}: '{mname}' is not a known "
                          f"move (in {event_type}) — skipping", file=_sys.stderr)
                    continue
                validated.append((mname, weight))
            if validated:
                merged[event_type] = validated
            else:
                # All entries failed validation — distinct from "key absent"
                print(f"WARN: {kind[:-1]}/{name}: event '{event_type}' "
                      f"has zero valid moves after filter; using built-in pool",
                      file=_sys.stderr)
        print(f"[edl] loaded {kind[:-1]} pack '{name}' "
              f"({len(merged)} event types)", file=_sys.stderr)
    return merged if merged else None


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
