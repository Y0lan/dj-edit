#!/usr/bin/env python3
"""build-edl.py — produce edl_9x16.json and edl_16x9.json.

Walks the master timeline, assigns event-driven treatments + framings, and
respects camera coverage. In single-camera mode, switches *virtual framings*
(crop/zoom within a 4K source) at cut points to fake multicam variation.
"""
from __future__ import annotations
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.selector import (
    cameras_at, pick_camera, framing_for_event, FRAMINGS,
    pick_move_for_event, load_pool_overrides,
)
from lib import moves as moves_lib
from lib import sphere as sphere_lib


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


def _generate_move_cmd(project: Path, aspect: str, move_name: str, duration: float,
                       bpm: float, intensity: float, target_yaw: float | None = None,
                       clip_index: int = 0) -> str:
    """Generate a per-clip .cmd file for an Insta360 move, return its absolute path.

    aspect is included in the path so 9x16 and 16x9 builds don't collide on
    the same `cmds/move_NNNN_<name>.cmd` filenames (clip_index_counter resets
    per build() call).

    target_yaw (v0.3) — when sphere_score yields a content-aware yaw, the
    caller passes it here. Used by drop_impact directly; used as start_yaw
    on orbit / crowd_reveal / bpm_sync_orbit / breakdown_drift.

    For Tier B moves that need BPM, pass it as a kwarg. Others ignore it.
    """
    cmds_dir = project / "cmds" / aspect
    cmds_dir.mkdir(parents=True, exist_ok=True)
    cmd_path = cmds_dir / f"move_{clip_index:04d}_{move_name}.cmd"

    intensity = max(0.0, min(1.0, float(intensity)))
    params = {}

    # Default yaw — used when caller didn't pass a content-aware one
    content_yaw = float(target_yaw) if target_yaw is not None else None

    if move_name == "bpm_sync_orbit":
        params = {"bpm": bpm, "bars_per_rotation": 4}
        if content_yaw is not None:
            params["start_yaw"] = content_yaw
    elif move_name == "kick_pulse_fov":
        params = {"bpm": bpm, "amp": 2 + intensity * 4}
        if content_yaw is not None:
            params["yaw"] = content_yaw
    elif move_name == "drop_impact":
        # `is None` check, NOT `or 60.0` — target_yaw=0.0 is a valid explicit yaw.
        params = {"target_yaw": 60.0 if content_yaw is None else content_yaw}
    elif move_name == "build_tension":
        params = {"fov_start": 95, "fov_end": 55 + (1 - intensity) * 10}
    elif move_name == "breakdown_drift":
        params = {"yaw_speed": 8.0, "pitch_end": -12.0}
        if content_yaw is not None:
            params["start_yaw"] = content_yaw
    elif move_name == "orbit":
        if content_yaw is not None:
            params["start_yaw"] = content_yaw
    elif move_name == "crowd_reveal":
        if content_yaw is not None:
            params["start_yaw"] = content_yaw
    elif move_name == "tilt_reveal":
        if content_yaw is not None:
            params["yaw"] = content_yaw
    elif move_name == "dolly_zoom":
        if content_yaw is not None:
            params["yaw"] = content_yaw
    elif move_name == "dolly_with_drift":
        # uses yaw_drift FROM start; if content yaw is provided we can't
        # easily wire it (function uses yaw=0 baseline). Leave default.
        pass

    cmd_text = moves_lib.generate(move_name, duration, params)
    cmd_path.write_text(cmd_text + "\n")
    # Return ABSOLUTE path so render.sh works regardless of cwd it's invoked from
    # (sendcmd=f=<path> is read by ffmpeg relative to its working dir).
    return str(cmd_path.resolve())


def build(project: Path, aspect: str, target_fps: int,
          src_w: int = 3840, src_h: int = 2160,
          style: str | None = None, reference: str | None = None,
          rng_seed: int | None = None) -> dict:
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
    # Hook is intentionally NOT prepended to the main EDL (it would desync).

    events = events_data["events"]
    beats = [b["t"] for b in beats_data.get("beats", [])]
    master_duration = events_data["meta"].get("duration", 0)
    bpm = float(beats_data.get("tempo", 128.0)) or 128.0

    # Tier D: load move-pool overrides from --style / --reference packs.
    presets_root = str(Path(__file__).resolve().parent.parent / "presets")
    pool_override = load_pool_overrides(
        style=style, reference=reference, presets_root=presets_root,
        valid_moves=set(moves_lib.MOVES.keys()),
    )

    # v0.3: content-aware sphere scoring (Insta360 only). If sphere_score.json
    # exists, each Insta360 clip's move gets a target_yaw from the dominant
    # content segment at that master_t. Absent file = fall back to hardcoded
    # yaw defaults (degrades gracefully on single-cam / no-Insta360 setups).
    sphere_score = None
    sphere_path = project / "sphere_score.json"
    if sphere_path.exists():
        try:
            sphere_score = json.loads(sphere_path.read_text())
            n_sources = len(sphere_score.get("sources", []))
            if n_sources > 0:
                print(f"[edl] sphere_score loaded ({n_sources} Insta360 source(s))",
                      file=sys.stderr)
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARN: sphere_score.json unreadable ({e}) — Insta360 moves "
                  f"will use default yaws", file=sys.stderr)
            sphere_score = None
    rng = random.Random(rng_seed) if rng_seed is not None else random.Random()
    recent_moves: list[str] = []  # rolling window for avoid-repeat

    edl: list[dict] = []
    pos = 0.0
    last_pick: tuple[str, str] | None = None
    last_pick_t = -999.0
    event_seq_counter: dict[str, int] = {}
    first_master_t: float | None = None
    last_master_t: float = 0.0
    clip_index_counter = [0]  # mutable for closure access in emit()

    def emit(c0: float, c1: float, finfo: dict, camera: str,
             framing: str, treatment: str | None = None,
             move: str | None = None,
             transition_out: str = "hard", title: str | None = None,
             event_type: str | None = None, intensity: float = 0.5) -> None:
        """Append an EDL entry, track master timestamps, advance pos.

        For Insta360 entries (camera == "insta360"), pick a move from the
        configured pool (Tier D), generate a per-clip .cmd file (Tier A + B),
        and store its absolute path in entry["cmd_path"].
        """
        nonlocal pos, first_master_t, last_master_t, last_pick, last_pick_t
        c0 = max(c0, last_master_t)
        dur = c1 - c0
        if dur <= 0.05:
            return
        if not coverage_ok(finfo, c0, dur):
            return
        src_in = to_src_in(c0, finfo)

        cmd_path = None
        if camera == "insta360":
            # Pick a move (Tier D pool, intensity-biased, avoid-repeat)
            chosen_move = move or pick_move_for_event(
                event_type or "lull", intensity=intensity, rng=rng,
                recent_picks=recent_moves[-3:], pool_override=pool_override,
            )
            move = chosen_move

            # v0.3: content-aware target_yaw from sphere_score (if available).
            # `prefer` switches based on event type — drops want motion (where
            # the crowd is going off); breakdowns want brightness (lights/lasers).
            content_yaw = None
            if sphere_score is not None:
                prefer = "motion" if event_type in ("drop", "peak") else \
                         "balanced" if event_type == "breakdown" else \
                         "motion+brightness"
                content_yaw = sphere_lib.best_yaw_at(
                    sphere_score, master_t=c0, prefer=prefer,
                    smooth_window=3, source_index=0,
                )

            # Generate a per-clip .cmd file (Tier A + B math + v0.3 content-aware yaw)
            try:
                cmd_path = _generate_move_cmd(
                    project, aspect, chosen_move, dur, bpm, intensity,
                    target_yaw=content_yaw,
                    clip_index=clip_index_counter[0],
                )
            except (ValueError, OSError) as ex:
                print(f"WARN: move '{chosen_move}' generation failed ({ex}); "
                      f"falling back to default orbit preset", file=sys.stderr)
                move = "orbit"
                cmd_path = None
            recent_moves.append(chosen_move)
            if len(recent_moves) > 8:
                recent_moves.pop(0)

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
        if cmd_path is not None:
            entry["cmd_path"] = cmd_path
        if title is not None:
            entry["title"] = title
        edl.append(entry)
        pos += dur
        clip_index_counter[0] += 1
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
                         framing_for_event("lull", i),
                         event_type="lull", intensity=0.3)

        event_seq_counter[ev["type"]] = event_seq_counter.get(ev["type"], 0) + 1
        ev_dur = max(0.5, float(ev.get("duration", 1.0)))
        ev_intensity = float(ev.get("intensity", 0.5))

        if ev["type"] == "breakdown":
            cam_pick = pick_camera(offsets, shot_scores, ev_t + ev_dur / 2,
                                   last_pick=last_pick, last_pick_time=last_pick_t)
            if cam_pick:
                cam, finfo = cam_pick
                emit(ev_t, ev_t + ev_dur, finfo, cam,
                     "wide", transition_out="soft",
                     event_type="breakdown", intensity=ev_intensity)
        elif ev["type"] == "build":
            cam_pick = pick_camera(offsets, shot_scores, ev_t + ev_dur / 2,
                                   last_pick=last_pick, last_pick_time=last_pick_t)
            if cam_pick:
                cam, finfo = cam_pick
                cuts = find_cuts(ev_t, ev_t + ev_dur, beats, subdivision=4, min_cut=0.4)
                for i in range(len(cuts) - 1):
                    emit(cuts[i], cuts[i + 1], finfo, cam,
                         framing_for_event("build", i),
                         event_type="build", intensity=ev_intensity)
        elif ev["type"] == "drop":
            cam_pick = pick_camera(offsets, shot_scores, ev_t,
                                   last_pick=last_pick, last_pick_time=last_pick_t)
            if cam_pick:
                cam, finfo = cam_pick
                impact_dur = 0.4
                emit(ev_t, ev_t + impact_dur, finfo, cam,
                     "punch", treatment="flash_freeze",
                     event_type="drop", intensity=ev_intensity)
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
                    emit(c0, c1, fi2, cam2, framing_for_event("peak", i),
                         event_type="peak", intensity=ev_intensity)
        elif ev["type"] == "peak":
            cuts = find_cuts(ev_t, ev_t + ev_dur, beats, subdivision=1, min_cut=2.0)
            for i in range(len(cuts) - 1):
                c0, c1 = cuts[i], cuts[i + 1]
                cp = pick_camera(offsets, shot_scores, c0,
                                 last_pick=last_pick, last_pick_time=last_pick_t)
                if not cp:
                    continue
                cam, finfo = cp
                emit(c0, c1, finfo, cam, framing_for_event("peak", i),
                     event_type="peak", intensity=ev_intensity)

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
                     framing_for_event("lull", i),
                     event_type="lull", intensity=0.2)

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
    p.add_argument("--style", type=str, default="signature",
                   help="pool weights from presets/styles/<name>.json (e.g. aggressive, clean, signature)")
    p.add_argument("--reference", type=str, default=None,
                   help="artist-style pack from presets/references/<name>.json (e.g. anyma, fisher, rinse)")
    p.add_argument("--seed", type=int, default=None,
                   help="rng seed for reproducible move selection (omit for random)")
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
        result = build(args.project, aspect, target_fps, src_w=src_w, src_h=src_h,
                       style=args.style, reference=args.reference, rng_seed=args.seed)
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
