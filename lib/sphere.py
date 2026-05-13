"""sphere.py — pure helpers for querying sphere_score.json.

The data shape (produced by bin/score-360.py):
  {"sources": [
    {"source": "...", "fps_sampled": 1, "n_frames": N,
     "frames": [
       {"t": 0.0, "segments": [{"yaw_center": 22.5, "motion": 0.0,
                                "brightness_var": 12.3}, ...]},
       ...
     ]
    },
    ...
  ]}

The build-edl step queries `best_yaw_at(sphere_score, master_t, prefer=...)`
to pick which yaw to point the Insta360 camera at for a given musical moment.

Pure functions, easy to unit test. No I/O.
"""
from __future__ import annotations


def normalize_weights(prefer: str) -> tuple[float, float]:
    """Parse a `prefer=` string into (motion_weight, brightness_weight).

    Examples:
      "motion"             -> (1.0, 0.0)
      "brightness"         -> (0.0, 1.0)
      "motion+brightness"  -> (0.7, 0.3) — motion dominates, brightness tiebreaks
      "balanced"           -> (0.5, 0.5)
    """
    p = prefer.lower().strip()
    if p == "motion":
        return (1.0, 0.0)
    if p == "brightness":
        return (0.0, 1.0)
    if p == "balanced":
        return (0.5, 0.5)
    # default + "motion+brightness"
    return (0.7, 0.3)


def find_source(sphere_score: dict, source_path: str | None = None,
                source_index: int = 0) -> dict | None:
    """Return the matching source dict (by project-relative path, then basename,
    then index fallback). Used by build-edl to pick the right Insta360 source.

    Returns None if no source can be located.
    """
    sources = sphere_score.get("sources", [])
    if not sources:
        return None
    if source_path:
        # Match by source_path (project-relative, preferred)
        for s in sources:
            if s.get("source_path") == source_path:
                return s
        # Fallback: match by basename
        import os
        base = os.path.basename(source_path)
        for s in sources:
            if s.get("source") == base:
                return s
    if source_index < len(sources):
        return sources[source_index]
    return None


def best_yaw_at(sphere_score: dict, master_t: float | None = None,
                prefer: str = "motion+brightness",
                smooth_window: int = 3,
                source_index: int = 0,
                source_t: float | None = None,
                source_path: str | None = None,
                min_score: float = 1e-6) -> float | None:
    """Return the yaw (degrees) with the highest weighted score at the given time.

    Args:
      sphere_score: parsed JSON dict from sphere_score.json
      master_t: time in seconds. NOTE: name kept for back-compat. Treated as
        source-local time when source_t is not provided. New callers should
        prefer source_t (since sphere_score frames are source-local).
      source_t: source-local time in seconds (preferred over master_t).
        Frames are sampled at 1 fps so we round to the nearest integer.
      prefer: weighting between motion and brightness_var
        (see normalize_weights for accepted values).
      smooth_window: number of consecutive sampled frames to consider.
        Default 3 → covers [t-1, t, t+1]. Window scoring uses MAX (not mean)
        to emphasize peak activity within the window.
      source_path: project-relative path of the Insta360 source to query.
        If provided, takes precedence over source_index.
      source_index: which source in sphere_score['sources'] to query
        (default 0). Ignored when source_path matches.
      min_score: yaw scoring must exceed this for the result to count.
        If the best segment scores below this, return None (caller falls back
        to default yaw — avoids picking arbitrary yaw=22.5 on all-black or
        static frames).

    Returns:
      yaw in degrees [0, 360), or None if data is missing / all-zero.
    """
    src = find_source(sphere_score, source_path=source_path,
                      source_index=source_index)
    if src is None:
        return None
    frames = src.get("frames", [])
    if not frames:
        return None

    motion_w, bright_w = normalize_weights(prefer)
    target_t = float(source_t) if source_t is not None else float(master_t or 0)
    n = len(frames)

    # Find the frame index nearest target_t (frames are 1 fps so t == idx)
    # Clamp to bounds.
    center_idx = max(0, min(n - 1, round(target_t)))

    # Average scores across [center - half, center + half] window.
    half = max(0, smooth_window // 2)
    lo = max(0, center_idx - half)
    hi = min(n, center_idx + half + 1)
    window = frames[lo:hi]
    if not window:
        return None

    # First frame in source has motion=0 (no baseline). Filter it out of
    # the window if there's anything else available, to avoid biasing the
    # average toward zero.
    if len(window) > 1 and lo == 0:
        window = [f for f in window if any(s.get("motion", 0) > 0 for s in f.get("segments", []))]
        if not window:
            window = frames[lo:hi]  # fallback

    # Aggregate per yaw: take MAX(motion) and MAX(brightness_var) across window.
    # Max (not mean) emphasizes peak activity, which is what we want for cuts.
    seg_count = len(window[0].get("segments", []))
    if seg_count == 0:
        return None

    yaw_centers: list[float] = [
        window[0]["segments"][s]["yaw_center"] for s in range(seg_count)
    ]
    motion_max = [0.0] * seg_count
    brightness_max = [0.0] * seg_count
    for f in window:
        for s, seg in enumerate(f.get("segments", [])):
            if s >= seg_count:
                break
            motion_max[s] = max(motion_max[s], float(seg.get("motion", 0)))
            brightness_max[s] = max(brightness_max[s], float(seg.get("brightness_var", 0)))

    # Normalize each metric to [0, 1] within this window so weights mean what
    # they say (without normalization, brightness_var values around 50 would
    # dominate motion values around 5 regardless of weights).
    def _norm(xs: list[float]) -> list[float]:
        mx = max(xs) if xs else 0.0
        return [(x / mx) if mx > 0 else 0.0 for x in xs]

    m_n = _norm(motion_max)
    b_n = _norm(brightness_max)
    raw_scores = [motion_w * m + bright_w * b for m, b in zip(m_n, b_n)]

    # ERP-circular smoothing — segment 0 (yaw 22.5) and segment N-1 (yaw
    # 337.5) are neighbors on the sphere. A subject straddling the seam
    # would otherwise lose to a tighter non-seam segment. [0.25, 0.5, 0.25]
    # cyclic kernel pools each segment with its two ERP-adjacent neighbors.
    scores = [
        0.25 * raw_scores[(i - 1) % seg_count]
        + 0.5 * raw_scores[i]
        + 0.25 * raw_scores[(i + 1) % seg_count]
        for i in range(seg_count)
    ]

    best_idx = max(range(seg_count), key=lambda i: scores[i])
    # All-zero (or near-zero) window — black frame, static shot, or first
    # frame only. Return None so the caller falls back to its move's default
    # yaw instead of an arbitrary 22.5°.
    if scores[best_idx] <= float(min_score):
        return None
    return float(yaw_centers[best_idx])


def coverage_summary(sphere_score: dict, source_index: int = 0) -> dict:
    """Returns a quick summary: duration, frame count, dominant yaw (overall).

    Useful for sanity-checking the scoring output before using it.
    """
    sources = sphere_score.get("sources", [])
    if not sources or source_index >= len(sources):
        return {"valid": False}
    src = sources[source_index]
    frames = src.get("frames", [])
    if not frames:
        return {"valid": False}

    seg_count = len(frames[0].get("segments", []))
    total_motion = [0.0] * seg_count
    total_brightness = [0.0] * seg_count
    for f in frames:
        for s, seg in enumerate(f.get("segments", [])):
            if s >= seg_count:
                break
            total_motion[s] += float(seg.get("motion", 0))
            total_brightness[s] += float(seg.get("brightness_var", 0))

    yaw_centers = [frames[0]["segments"][s]["yaw_center"] for s in range(seg_count)]
    dominant_motion_idx = max(range(seg_count), key=lambda i: total_motion[i])
    dominant_brightness_idx = max(range(seg_count), key=lambda i: total_brightness[i])

    return {
        "valid": True,
        "source": src.get("source"),
        "n_frames": len(frames),
        "duration_s": float(frames[-1].get("t", 0)) if frames else 0,
        "dominant_motion_yaw": float(yaw_centers[dominant_motion_idx]),
        "dominant_brightness_yaw": float(yaw_centers[dominant_brightness_idx]),
        "total_motion_per_yaw": dict(zip(yaw_centers, total_motion)),
        "total_brightness_per_yaw": dict(zip(yaw_centers, total_brightness)),
    }
