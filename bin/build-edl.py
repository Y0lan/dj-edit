#!/usr/bin/env python3
"""build-edl.py — produce edl_9x16.json and edl_16x9.json.

Walks the master timeline, assigns event-driven treatments + framings, and
respects camera coverage. In single-camera mode, switches *virtual framings*
(crop/zoom within a 4K source) at cut points to fake multicam variation.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.selector import (
    cameras_at, pick_camera, framing_for_event, FRAMINGS,
)


def nearest_beat(t: float, beats: list[float], window: float = 0.2) -> float:
    if not beats:
        return t
    idx = min(range(len(beats)), key=lambda i: abs(beats[i] - t))
    if abs(beats[idx] - t) <= window:
        return beats[idx]
    return t


def to_src_in(t_master: float, file_info: dict) -> float:
    a, b = file_info.get("a", 1.0), file_info.get("b", 0.0)
    if a == 0:
        return 0.0
    return max(0.0, (t_master - b) / a)


def coverage_ok(file_info: dict, t_master: float, duration: float,
                guard: float = 0.2) -> bool:
    cs, ce = file_info.get("coverage", [0.0, file_info.get("duration", 0)])
    cam_dur = file_info.get("duration", 0)
    src_in = to_src_in(t_master, file_info)
    src_out = src_in + duration / file_info.get("a", 1.0)
    return cs <= t_master and (t_master + duration) <= ce and (src_out + guard) <= cam_dur


def find_cuts(t_start: float, t_end: float, beats: list[float],
              subdivision: int = 4, min_cut: float = 0.4) -> list[float]:
    """Beat-snapped cut points in [t_start, t_end], with sub-beat subdivision."""
    if not beats:
        return [t_start, t_end]
    # Inject subdivisions between adjacent beats
    refined = []
    for i in range(len(beats) - 1):
        b0, b1 = beats[i], beats[i + 1]
        if b0 > t_end:
            break
        if b1 < t_start:
            continue
        refined.append(b0)
        if subdivision > 1:
            step = (b1 - b0) / subdivision
            for k in range(1, subdivision):
                refined.append(b0 + k * step)
    refined.append(beats[-1])
    cuts = [t for t in refined if t_start <= t <= t_end]
    if not cuts or cuts[0] > t_start:
        cuts.insert(0, t_start)
    if cuts[-1] < t_end:
        cuts.append(t_end)
    # Enforce min_cut spacing
    deduped = [cuts[0]]
    for t in cuts[1:]:
        if t - deduped[-1] >= min_cut:
            deduped.append(t)
    if deduped[-1] < t_end:
        deduped.append(t_end)
    return deduped


def build(project: Path, aspect: str, target_fps: int,
          src_w: int = 3840, src_h: int = 2160) -> dict:
    """Build EDL for given aspect ratio.

    Returns a dict: {clips: [...], audio_master_start: float, audio_master_end: float}.

    The audio_master_start/end define the master-audio window that maps onto the
    rendered video timeline. render.sh trims master audio to this window so audio
    and video stay aligned even when some master regions have no camera coverage.
    """
    if aspect == "9x16":
        out_w, out_h = 1080, 1920
    elif aspect == "16x9":
        out_w, out_h = 1920, 1080
    elif aspect == "1x1":
        out_w, out_h = 1080, 1080
    else:
        raise ValueError(f"unknown aspect {aspect}")

    events_data = json.loads((project / "events.json").read_text())
    beats_data = json.loads((project / "beats.json").read_text())
    offsets = json.loads((project / "offsets.json").read_text())
    shot_scores: dict = {}
    if (project / "shot_scores.json").exists():
        shot_scores = json.loads((project / "shot_scores.json").read_text())
    # Hook is intentionally NOT prepended to the main EDL (it would desync
    # against the master audio which starts at master_t=0). Hook lives in
    # the separate highlight render (v1.1).

    events = events_data["events"]
    beats = [b["t"] for b in beats_data.get("beats", [])]
    master_duration = events_data["meta"].get("duration", 0)

    edl: list[dict] = []
    pos = 0.0
    last_pick: tuple[str, str] | None = None
    last_pick_t = -999.0
    event_seq_counter: dict[str, int] = {}
    first_master_t: float | None = None
    last_master_t: float = 0.0

    def emit(c0: float, c1: float, finfo: dict, camera: str,
             framing: str, treatment: str | None = None,
             move: str | None = None,
             transition_out: str = "hard", title: str | None = None) -> None:
        """Append an EDL entry, track master timestamps, advance pos.

        Monotonic: c0 is clamped to last_master_t to prevent overlapping events
        from double-emitting master coverage (which would make video duration
        > audio window). src_in is computed AFTER the clamp so video frame
        timing matches the clamped master_t exactly.
        """
        nonlocal pos, first_master_t, last_master_t, last_pick, last_pick_t
        c0 = max(c0, last_master_t)
        dur = c1 - c0
        if dur <= 0.05:
            return
        # Coverage check on the (possibly clamped) c0
        if not coverage_ok(finfo, c0, dur):
            return
        src_in = to_src_in(c0, finfo)
        entry: dict = {
            "in": pos, "out": pos + dur,
            "master_in": c0, "master_out": c0 + dur,
            "src": finfo["path"], "camera": camera,
            "src_in": src_in,
            "framing": framing, "treatment": treatment,
            "transition_out": transition_out,
        }
        if move is not None:
            entry["move"] = move
        if title is not None:
            entry["title"] = title
        edl.append(entry)
        pos += dur
        if first_master_t is None:
            first_master_t = c0
        last_master_t = c0 + dur
        last_pick = (camera, finfo["path"])
        last_pick_t = c0

    # --- 1) Main timeline walk ---
    timeline_t = 0.0
    sorted_events = sorted(events, key=lambda e: e["t"])
    upcoming = list(sorted_events)

    while timeline_t < master_duration:
        future = [e for e in upcoming if e["t"] >= timeline_t]
        if not future:
            break
        ev = future[0]
        ev_t = ev["t"]
        gap = ev_t - timeline_t

        # Lull fill before the event — beat-snapped wide cuts
        if gap > 1.0:
            cam_pick = pick_camera(offsets, shot_scores, timeline_t + gap / 2,
                                   last_pick=last_pick, last_pick_time=last_pick_t)
            if cam_pick:
                cam, finfo = cam_pick
                cuts = find_cuts(timeline_t, ev_t, beats, subdivision=1, min_cut=3.5)
                for i in range(len(cuts) - 1):
                    emit(cuts[i], cuts[i + 1], finfo, cam,
                         framing_for_event("lull", i))

        event_seq_counter[ev["type"]] = event_seq_counter.get(ev["type"], 0) + 1
        ev_dur = max(0.5, float(ev.get("duration", 1.0)))

        if ev["type"] == "breakdown":
            cam_pick = pick_camera(offsets, shot_scores, ev_t + ev_dur / 2,
                                   last_pick=last_pick, last_pick_time=last_pick_t)
            if cam_pick:
                cam, finfo = cam_pick
                emit(ev_t, ev_t + ev_dur, finfo, cam,
                     "wide", move="orbit", transition_out="soft")
        elif ev["type"] == "build":
            cam_pick = pick_camera(offsets, shot_scores, ev_t + ev_dur / 2,
                                   last_pick=last_pick, last_pick_time=last_pick_t)
            if cam_pick:
                cam, finfo = cam_pick
                cuts = find_cuts(ev_t, ev_t + ev_dur, beats, subdivision=4, min_cut=0.4)
                for i in range(len(cuts) - 1):
                    emit(cuts[i], cuts[i + 1], finfo, cam,
                         framing_for_event("build", i))
        elif ev["type"] == "drop":
            cam_pick = pick_camera(offsets, shot_scores, ev_t,
                                   last_pick=last_pick, last_pick_time=last_pick_t)
            if cam_pick:
                cam, finfo = cam_pick
                impact_dur = 0.4
                emit(ev_t, ev_t + impact_dur, finfo, cam,
                     "punch", treatment="flash_freeze")
                post_t0 = ev_t + impact_dur
                post_t1 = min(post_t0 + 6.0, master_duration)
                cuts = find_cuts(post_t0, post_t1, beats, subdivision=2, min_cut=0.4)
                for i in range(len(cuts) - 1):
                    c0, c1 = cuts[i], cuts[i + 1]
                    cp = pick_camera(offsets, shot_scores, c0,
                                     last_pick=last_pick, last_pick_time=last_pick_t,
                                     avoid_repeat_window=1.5)
                    if not cp:
                        continue
                    cam2, fi2 = cp
                    emit(c0, c1, fi2, cam2, framing_for_event("peak", i))
        elif ev["type"] == "peak":
            cuts = find_cuts(ev_t, ev_t + ev_dur, beats, subdivision=1, min_cut=2.0)
            for i in range(len(cuts) - 1):
                c0, c1 = cuts[i], cuts[i + 1]
                cp = pick_camera(offsets, shot_scores, c0,
                                 last_pick=last_pick, last_pick_time=last_pick_t)
                if not cp:
                    continue
                cam, finfo = cp
                emit(c0, c1, finfo, cam, framing_for_event("peak", i))

        timeline_t = max(timeline_t + 0.5, ev_t + ev_dur, last_master_t)
        upcoming = [e for e in upcoming if e["t"] >= timeline_t]

    # --- 2) Tail fill: after the last event, fill remaining covered master ---
    if last_master_t < master_duration - 1.0:
        cam_pick = pick_camera(offsets, shot_scores, last_master_t + 1.0,
                               last_pick=last_pick, last_pick_time=last_pick_t)
        if cam_pick:
            cam, finfo = cam_pick
            tail_end = min(master_duration, last_master_t + 60.0)
            cuts = find_cuts(last_master_t, tail_end, beats, subdivision=1, min_cut=4.0)
            for i in range(len(cuts) - 1):
                emit(cuts[i], cuts[i + 1], finfo, cam,
                     framing_for_event("lull", i))

    return {
        "clips": edl,
        "audio_master_start": float(first_master_t) if first_master_t is not None else 0.0,
        "audio_master_end": float(last_master_t),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Build aspect-specific EDLs")
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--aspects", type=str, default="9x16,16x9",
                   help="comma-separated: 9x16,16x9,1x1")
    p.add_argument("--style", type=str, default="aggressive",
                   choices=["aggressive", "clean", "signature"])
    args = p.parse_args()

    manifest = json.loads((args.project / "manifest.json").read_text())
    target_fps = manifest.get("target_fps", 30)

    # Source resolution (use first available video)
    src_w, src_h = 3840, 2160
    for cam_list in manifest.get("cameras", {}).values():
        if cam_list:
            src_w = cam_list[0].get("width", 3840)
            src_h = cam_list[0].get("height", 2160)
            break

    for aspect in args.aspects.split(","):
        aspect = aspect.strip()
        result = build(args.project, aspect, target_fps, src_w=src_w, src_h=src_h)
        clips = result["clips"]
        if not clips:
            print(f"ERROR ({aspect}): zero clips. No camera coverage overlaps with detected events. "
                  f"Re-check sync (offsets.json) and event timestamps (events.json).",
                  file=sys.stderr)
            return 2
        out_path = args.project / f"edl_{aspect}.json"
        out_path.write_text(json.dumps({
            "aspect": aspect,
            "target_fps": target_fps,
            "src_w": src_w, "src_h": src_h,
            "style": args.style,
            "audio_master_start": result["audio_master_start"],
            "audio_master_end": result["audio_master_end"],
            "clips": clips,
        }, indent=2))
        total_dur = clips[-1]["out"]
        audio_dur = result["audio_master_end"] - result["audio_master_start"]
        print(f"  {aspect}: {len(clips)} clips, {total_dur:.1f}s video / {audio_dur:.1f}s audio window "
              f"[{result['audio_master_start']:.1f}, {result['audio_master_end']:.1f}] -> {out_path.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
