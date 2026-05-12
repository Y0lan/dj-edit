#!/usr/bin/env python3
"""pick-hook.py — choose a scroll-stopping cold-open clip.

Reads events.json + offsets.json. Picks the best "peak" or "drop" event
(highest intensity × camera coverage × not-too-close-to-end). Emits hook.json
with src + src_in + duration + treatment.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path


def cameras_covering(offsets: dict, t: float) -> list[tuple[str, dict]]:
    """Return (camera_type, file_info) pairs whose coverage contains master time t."""
    result = []
    for cam, files in offsets.items():
        for f in files:
            cs, ce = f["coverage"]
            if cs <= t <= ce:
                result.append((cam, f))
    return result


def cam_priority(cam: str) -> int:
    # Order of preference for the hook frame: a7iii first (best image quality), then dji, then insta360
    return {"a7iii": 0, "dji": 1, "insta360": 2}.get(cam, 99)


def main() -> int:
    p = argparse.ArgumentParser(description="Pick cold-open hook clip")
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--duration", type=float, default=2.0,
                   help="hook clip duration (s)")
    p.add_argument("--title", type=str, default=None,
                   help="optional title overlay text")
    args = p.parse_args()

    proj = args.project
    events_data = json.loads((proj / "events.json").read_text())
    offsets = json.loads((proj / "offsets.json").read_text())
    events = events_data["events"]
    master_dur = events_data["meta"].get("duration", 0)

    # Candidates: drops + peaks, scored.
    # Coverage check uses the FULL hook duration, not just the instant —
    # otherwise hooks near a camera's end edge render short or fail.
    candidates = []
    for e in events:
        if e["type"] not in ("drop", "peak"):
            continue
        # Verify camera covers [e.t, e.t + hook_duration]
        covering = []
        for cam, files in offsets.items():
            for f in files:
                cs, ce = f["coverage"]
                if cs <= e["t"] and (e["t"] + args.duration + 0.5) <= ce:
                    covering.append((cam, f))
        if not covering:
            continue
        # Don't pick a hook within the last 2 min of the set
        time_remaining = master_dur - e["t"]
        if time_remaining < 120:
            continue
        score = e["intensity"] * (1 + 0.2 * len(covering)) * e.get("confidence", 0.5)
        candidates.append({"event": e, "covering": covering, "score": score})

    if not candidates:
        print("ERROR: no candidate hook events (no drop/peak with coverage)", file=sys.stderr)
        return 2

    candidates.sort(key=lambda c: c["score"], reverse=True)
    winner = candidates[0]
    e = winner["event"]
    covering = sorted(winner["covering"], key=lambda c: cam_priority(c[0]))
    cam_type, file_info = covering[0]

    # Compute src_in: timeline_t (master) → camera local time via affine
    a, b = file_info["a"], file_info["b"]
    src_in_local = (e["t"] - b) / a if a else 0.0

    hook = {
        "src": file_info["path"],
        "camera": cam_type,
        "master_t": float(e["t"]),
        "src_in": max(0.0, float(src_in_local)),
        "duration": float(args.duration),
        "treatment": "flash_freeze_title" if args.title else "flash_freeze",
        "title": args.title,
        "event_type": e["type"],
        "event_intensity": e["intensity"],
        "score": winner["score"],
    }
    (proj / "hook.json").write_text(json.dumps(hook, indent=2))
    print(f"hook: {cam_type}/{file_info['path']} @ src_in={hook['src_in']:.2f}s "
          f"(master t={e['t']:.1f}s, {e['type']}, score={winner['score']:.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
