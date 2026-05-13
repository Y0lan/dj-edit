#!/usr/bin/env python3
"""sendcmd .cmd file generators for v360@mv named instance.

Critical sendcmd-expression facts (verified against ffmpeg eval.c + sendcmd docs):
  - T  = absolute timestamp in seconds (NOT clip-local — TS-based)
  - TS = command interval start time (seconds)
  - TE = command interval end time (seconds)
  - TI = NORMALIZED progress in [0, 1] = (T - TS) / (TE - TS)
  - PI, cos, sin, abs, sqrt, exp, log are valid; lt, gt, between exist.
  - COMMAS inside expressions (pow(x,2), if(lt(...),a,b)) BREAK sendcmd parsing.

This module follows two strict rules:
  1. Use `T` (seconds) directly; never depend on TI for math that scales with duration.
  2. For piecewise motion, emit MULTIPLE time-windowed commands rather than nested
     `if(lt(...), ...)` — each command line spans one interval and the parser is happy.

These rules avoid the two failure modes codex caught in v0.2 alpha:
  - 45*TI gave 45 degrees TOTAL (not 45 deg/sec) because TI is normalized.
  - if(lt(TI,p1),0,...) broke parsing due to embedded commas.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path


def _entry(t0: float, t1: float, param: str, expr: str) -> str:
    return f"{t0:.4f}-{t1:.4f} [expr] v360@mv {param} {expr};"


# ───────────────────────── Tier A: eased primitives ─────────────────────────


def orbit(duration: float, yaw_speed: float = 45.0, pitch: float = -5.0,
          h_fov: float = 90.0, ease: bool = True, start_yaw: float = 0.0) -> str:
    """Continuous yaw rotation from `start_yaw` over `duration` seconds.

    yaw_speed in deg/sec. Total angle covered = yaw_speed * duration.

    start_yaw is content-aware (v0.3+): set by sphere_score lookup so the
    rotation BEGINS at the interesting region instead of always at yaw 0.
    """
    if duration <= 0:
        return _entry(0.0, 0.01, "yaw", f"{start_yaw}")
    total = yaw_speed * duration
    if ease:
        yaw_expr = f"{start_yaw}+{total}*((1-cos(PI*T/{duration:.4f}))/2)"
    else:
        yaw_expr = f"{start_yaw}+{yaw_speed}*T"
    return "\n".join([
        _entry(0, duration, "yaw", yaw_expr),
        _entry(0, duration, "pitch", f"{pitch}"),
        _entry(0, duration, "h_fov", f"{h_fov}"),
    ])


def tilt_reveal(duration: float, pitch_start: float = -60.0,
                pitch_end: float = 0.0, yaw: float = 0.0,
                h_fov: float = 90.0, ease: bool = True) -> str:
    """Pitch sweep + subtle yaw drift."""
    if duration <= 0:
        return _entry(0.0, 0.01, "pitch", f"{pitch_end}")
    delta = pitch_end - pitch_start
    if ease:
        # Ease-out: sin(PI*T/(2*duration)) goes 0 → 1
        pitch_expr = f"{pitch_start}+{delta}*sin(PI*T/{2*duration:.4f})"
    else:
        pitch_expr = f"{pitch_start}+{delta/duration:.6f}*T"
    yaw_drift = 3.0
    yaw_expr = f"{yaw}+{yaw_drift/duration:.6f}*T"
    return "\n".join([
        _entry(0, duration, "pitch", pitch_expr),
        _entry(0, duration, "yaw", yaw_expr),
        _entry(0, duration, "h_fov", f"{h_fov}"),
    ])


def dolly_zoom(duration: float, fov_start: float = 90.0,
               fov_end: float = 55.0, yaw: float = 0.0,
               pitch: float = -5.0, ease: bool = True) -> str:
    """h_fov push-in with cosine ease."""
    if duration <= 0:
        return _entry(0.0, 0.01, "h_fov", f"{fov_end}")
    delta = fov_end - fov_start
    if ease:
        fov_expr = f"{fov_start}+{delta}*((1-cos(PI*T/{duration:.4f}))/2)"
    else:
        fov_expr = f"{fov_start}+{delta/duration:.6f}*T"
    return "\n".join([
        _entry(0, duration, "h_fov", fov_expr),
        _entry(0, duration, "yaw", f"{yaw}"),
        _entry(0, duration, "pitch", f"{pitch}"),
    ])


def whip_pan(duration: float, yaw_speed: float = 900.0,
             pitch: float = 0.0, h_fov: float = 110.0) -> str:
    """Fast yaw snap — 900 deg/sec default for transition glue."""
    return "\n".join([
        _entry(0, duration, "yaw", f"{yaw_speed}*T"),
        _entry(0, duration, "pitch", f"{pitch}"),
        _entry(0, duration, "h_fov", f"{h_fov}"),
    ])


def crowd_reveal(duration: float, yaw_speed: float = 20.0,
                 fov_start: float = 70.0, fov_end: float = 110.0,
                 pitch: float = -10.0, start_yaw: float = 0.0) -> str:
    """Slow yaw + h_fov widen + subtle pitch sway, starting from start_yaw.

    Content-aware: start_yaw is the yaw of the crowd/dancefloor segment
    (from sphere_score). Camera reveals OUT from there as the fov widens.
    """
    if duration <= 0:
        return _entry(0.0, 0.01, "h_fov", f"{fov_end}")
    fov_rate = (fov_end - fov_start) / duration
    pitch_expr = f"{pitch}+2*sin(2*PI*T/{duration:.4f})"
    return "\n".join([
        _entry(0, duration, "yaw", f"{start_yaw}+{yaw_speed}*T"),
        _entry(0, duration, "h_fov", f"{fov_start}+{fov_rate:.6f}*T"),
        _entry(0, duration, "pitch", pitch_expr),
    ])


def counter_motion(duration: float, yaw_speed: float = 30.0,
                   pitch_amp: float = 8.0, fov_breath: float = 8.0,
                   h_fov_base: float = 90.0) -> str:
    """Three orthogonal motions — yaw turns, pitch sin-oscillates, h_fov breathes."""
    if duration <= 0:
        return _entry(0.0, 0.01, "yaw", "0")
    yaw_expr = f"{yaw_speed}*T"
    # Pitch: half-sin cycle over duration, oscillating around 0
    pitch_expr = f"{pitch_amp}*sin(PI*T/{duration:.4f})-{pitch_amp/2}"
    # h_fov: half-sin breath in and out
    fov_expr = f"{h_fov_base}-{fov_breath}*sin(PI*T/{duration:.4f})"
    return "\n".join([
        _entry(0, duration, "yaw", yaw_expr),
        _entry(0, duration, "pitch", pitch_expr),
        _entry(0, duration, "h_fov", fov_expr),
    ])


def dolly_with_drift(duration: float, fov_start: float = 90.0,
                     fov_end: float = 60.0, yaw_drift: float = 15.0,
                     pitch: float = -5.0, ease: bool = True) -> str:
    """Push-in with slow yaw drift for subject-lock feel."""
    if duration <= 0:
        return _entry(0.0, 0.01, "h_fov", f"{fov_end}")
    delta_fov = fov_end - fov_start
    if ease:
        fov_expr = f"{fov_start}+{delta_fov}*((1-cos(PI*T/{duration:.4f}))/2)"
    else:
        fov_expr = f"{fov_start}+{delta_fov/duration:.6f}*T"
    yaw_expr = f"{yaw_drift/duration:.6f}*T"
    return "\n".join([
        _entry(0, duration, "h_fov", fov_expr),
        _entry(0, duration, "yaw", yaw_expr),
        _entry(0, duration, "pitch", f"{pitch}"),
    ])


# ─────────────────── Tier B: music-driven motion primitives ──────────────────


def bpm_sync_orbit(duration: float, bpm: float = 128.0,
                   bars_per_rotation: int = 4, pitch: float = -5.0,
                   h_fov: float = 90.0, start_yaw: float = 0.0) -> str:
    """Yaw rotates one full turn per `bars_per_rotation` musical bars,
    starting from start_yaw (content-aware in v0.3+)."""
    if duration <= 0:
        return _entry(0.0, 0.01, "yaw", f"{start_yaw}")
    safe_bpm = max(float(bpm), 30.0)
    bar_duration = 60.0 / safe_bpm * 4
    rotation_duration = bar_duration * max(int(bars_per_rotation), 1)
    yaw_speed = 360.0 / rotation_duration
    return "\n".join([
        _entry(0, duration, "yaw", f"{start_yaw}+{yaw_speed:.6f}*T"),
        _entry(0, duration, "pitch", f"{pitch}"),
        _entry(0, duration, "h_fov", f"{h_fov}"),
    ])


def kick_pulse_fov(duration: float, bpm: float = 128.0,
                   base_fov: float = 90.0, amp: float = 3.0,
                   yaw: float = 0.0, pitch: float = -5.0) -> str:
    """h_fov pumps with the kick — `amp` degrees of breathing at BPM frequency."""
    if duration <= 0:
        return _entry(0.0, 0.01, "h_fov", f"{base_fov}")
    safe_bpm = max(float(bpm), 30.0)
    beat_period = 60.0 / safe_bpm
    # abs(sin(PI*T/beat_period)) — single pulse per beat, in [0, 1]
    fov_expr = f"{base_fov}-{amp}*abs(sin(PI*T/{beat_period:.6f}))"
    return "\n".join([
        _entry(0, duration, "h_fov", fov_expr),
        _entry(0, duration, "yaw", f"{yaw}"),
        _entry(0, duration, "pitch", f"{pitch}"),
    ])


def drop_impact(duration: float = 0.8, target_yaw: float = 90.0,
                start_yaw: float = 0.0,
                fov_start: float = 110.0, fov_end: float = 65.0,
                pitch: float = -5.0) -> str:
    """The drop preset — three time-windowed commands (NO nested-if commas).

    Phase 1 [0, p1):    yaw=start_yaw, h_fov=fov_start.
    Phase 2 [p1, p2):   yaw whips start_yaw → target_yaw via SHORTEST path,
                        h_fov holds.
    Phase 3 [p2, p3):   yaw holds target_yaw, h_fov dolly fov_start → fov_end.
    Phase 4 [p3, end]:  hold target_yaw + fov_end.

    Shortest-path delta: a target_yaw of 337.5° from start_yaw=0° should whip
    -22.5° (counter-clockwise) not +337.5° (full spin). Computed as
    `((target - start + 180) % 360) - 180`, which lands in [-180, 180].
    """
    if duration < 0.1:
        # Too short for choreography — just hold target.
        return "\n".join([
            _entry(0, max(duration, 0.01), "yaw", f"{target_yaw}"),
            _entry(0, max(duration, 0.01), "pitch", f"{pitch}"),
            _entry(0, max(duration, 0.01), "h_fov", f"{fov_end}"),
        ])
    p1 = duration * 0.2
    p2 = duration * 0.4
    p3 = duration * 0.7
    fov_delta = fov_end - fov_start

    # Shortest-path yaw delta in [-180, 180]
    delta_yaw = ((target_yaw - start_yaw + 540.0) % 360.0) - 180.0

    lines: list[str] = []
    # YAW phases
    lines.append(_entry(0.0, p1, "yaw", f"{start_yaw}"))
    lines.append(_entry(p1, p2, "yaw",
                        f"{start_yaw}+{delta_yaw}*((T-{p1:.4f})/{(p2-p1):.6f})"))
    lines.append(_entry(p2, duration, "yaw", f"{target_yaw}"))
    # PITCH static across all phases
    lines.append(_entry(0.0, duration, "pitch", f"{pitch}"))
    # H_FOV phases
    lines.append(_entry(0.0, p2, "h_fov", f"{fov_start}"))
    lines.append(_entry(p2, p3, "h_fov",
                        f"{fov_start}+{fov_delta}*((T-{p2:.4f})/{(p3-p2):.6f})"))
    lines.append(_entry(p3, duration, "h_fov", f"{fov_end}"))
    return "\n".join(lines)


def build_tension(duration: float, fov_start: float = 95.0,
                  fov_end: float = 58.0, yaw_drift: float = 8.0,
                  pitch: float = -5.0) -> str:
    """Build-up tension — h_fov tightens with quadratic ease-in (T*T)."""
    if duration <= 0:
        return _entry(0.0, 0.01, "h_fov", f"{fov_end}")
    delta_fov = fov_end - fov_start
    # Quadratic ease-in via (T/duration)^2 = T*T/duration^2 — no comma needed
    dur_sq = duration * duration
    fov_expr = f"{fov_start}+{delta_fov}*((T*T)/{dur_sq:.6f})"
    yaw_expr = f"{yaw_drift}*((T*T)/{dur_sq:.6f})"
    return "\n".join([
        _entry(0, duration, "h_fov", fov_expr),
        _entry(0, duration, "yaw", yaw_expr),
        _entry(0, duration, "pitch", f"{pitch}"),
    ])


def breakdown_drift(duration: float, yaw_speed: float = 8.0,
                    pitch_start: float = 0.0, pitch_end: float = -15.0,
                    h_fov: float = 95.0, start_yaw: float = 0.0) -> str:
    """Floating breakdown — ultra-slow yaw + slow up-tilt + wide-ish fov.

    start_yaw (v0.3+) anchors the drift to a content-aware region.
    """
    if duration <= 0:
        return _entry(0.0, 0.01, "yaw", f"{start_yaw}")
    pitch_delta = pitch_end - pitch_start
    pitch_expr = f"{pitch_start}+{pitch_delta}*sin(PI*T/{2*duration:.4f})"
    return "\n".join([
        _entry(0, duration, "yaw", f"{start_yaw}+{yaw_speed}*T"),
        _entry(0, duration, "pitch", pitch_expr),
        _entry(0, duration, "h_fov", f"{h_fov}"),
    ])


# ─────────────────────────────── Move registry ───────────────────────────────


MOVES = {
    "orbit": orbit,
    "tilt_reveal": tilt_reveal,
    "dolly_zoom": dolly_zoom,
    "whip_pan": whip_pan,
    "crowd_reveal": crowd_reveal,
    "counter_motion": counter_motion,
    "dolly_with_drift": dolly_with_drift,
    "bpm_sync_orbit": bpm_sync_orbit,
    "kick_pulse_fov": kick_pulse_fov,
    "drop_impact": drop_impact,
    "build_tension": build_tension,
    "breakdown_drift": breakdown_drift,
}


def generate(name: str, duration: float, params: dict | None = None) -> str:
    if name not in MOVES:
        raise ValueError(f"unknown move {name!r}; available: {sorted(MOVES)}")
    fn = MOVES[name]
    return fn(duration, **(params or {}))


def filter_chain(cmd_path: str, w: int, h: int,
                 yaw0: float = 0.0, pitch0: float = -5.0,
                 hfov0: float = 90.0) -> str:
    """Build the ffmpeg filter chain fragment for a sendcmd-driven v360@mv."""
    return (
        f"sendcmd=f={cmd_path},"
        f"v360@mv=input=e:output=flat:"
        f"yaw={yaw0}:pitch={pitch0}:h_fov={hfov0}:w={w}:h={h}"
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Generate v360 sendcmd .cmd files")
    p.add_argument("move", choices=sorted(MOVES.keys()))
    p.add_argument("--duration", type=float, required=True)
    p.add_argument("--params", type=str, default=None)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    params = json.loads(args.params) if args.params else None
    cmd_text = generate(args.move, args.duration, params)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(cmd_text + "\n")
    print(f"wrote {args.out} ({args.move}, {args.duration}s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
