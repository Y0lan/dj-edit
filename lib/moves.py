#!/usr/bin/env python3
"""sendcmd .cmd file generators for v360@mv named instance.

Each move emits ffmpeg sendcmd syntax using continuous expressions:
  <t_start>-<t_end> [expr] v360@mv <param> <expression>;

T = timeline time (seconds), TI = interval time within this sendcmd entry.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path


def _entry(t0: float, t1: float, param: str, expr: str) -> str:
    return f"{t0:.3f}-{t1:.3f} [expr] v360@mv {param} {expr};"


def orbit(duration: float, yaw_speed: float = 45.0, pitch: float = -5.0,
          h_fov: float = 90.0) -> str:
    """Continuous yaw rotation. yaw_speed in deg/sec."""
    lines = [
        _entry(0, duration, "yaw", f"{yaw_speed}*TI"),
        _entry(0, duration, "pitch", f"{pitch}"),
        _entry(0, duration, "h_fov", f"{h_fov}"),
    ]
    return "\n".join(lines)


def tilt_reveal(duration: float, pitch_start: float = -60.0,
                pitch_end: float = 0.0, yaw: float = 0.0,
                h_fov: float = 90.0) -> str:
    """Pitch sweep from start to end (ceiling reveal)."""
    rate = (pitch_end - pitch_start) / duration
    lines = [
        _entry(0, duration, "pitch", f"{pitch_start}+{rate:.4f}*TI"),
        _entry(0, duration, "yaw", f"{yaw}"),
        _entry(0, duration, "h_fov", f"{h_fov}"),
    ]
    return "\n".join(lines)


def dolly_zoom(duration: float, fov_start: float = 90.0,
               fov_end: float = 55.0, yaw: float = 0.0,
               pitch: float = -5.0) -> str:
    """h_fov interpolated (push-in tension)."""
    rate = (fov_end - fov_start) / duration
    lines = [
        _entry(0, duration, "h_fov", f"{fov_start}+{rate:.4f}*TI"),
        _entry(0, duration, "yaw", f"{yaw}"),
        _entry(0, duration, "pitch", f"{pitch}"),
    ]
    return "\n".join(lines)


def whip_pan(duration: float, yaw_speed: float = 900.0,
             pitch: float = 0.0, h_fov: float = 110.0) -> str:
    """Fast yaw snap. Default 900 deg/sec — meant as a 0.3-0.5s transition glue clip."""
    lines = [
        _entry(0, duration, "yaw", f"{yaw_speed}*TI"),
        _entry(0, duration, "pitch", f"{pitch}"),
        _entry(0, duration, "h_fov", f"{h_fov}"),
    ]
    return "\n".join(lines)


def crowd_reveal(duration: float, yaw_speed: float = 20.0,
                 fov_start: float = 70.0, fov_end: float = 110.0,
                 pitch: float = -10.0) -> str:
    """Slow yaw + h_fov widen, like pulling back from DJ to crowd."""
    fov_rate = (fov_end - fov_start) / duration
    lines = [
        _entry(0, duration, "yaw", f"{yaw_speed}*TI"),
        _entry(0, duration, "h_fov", f"{fov_start}+{fov_rate:.4f}*TI"),
        _entry(0, duration, "pitch", f"{pitch}"),
    ]
    return "\n".join(lines)


MOVES = {
    "orbit": orbit,
    "tilt_reveal": tilt_reveal,
    "dolly_zoom": dolly_zoom,
    "whip_pan": whip_pan,
    "crowd_reveal": crowd_reveal,
}


def generate(name: str, duration: float, params: dict | None = None) -> str:
    if name not in MOVES:
        raise ValueError(f"unknown move {name!r}; available: {list(MOVES)}")
    fn = MOVES[name]
    return fn(duration, **(params or {}))


def filter_chain(cmd_path: str, w: int, h: int,
                 yaw0: float = 0.0, pitch0: float = -5.0,
                 hfov0: float = 90.0) -> str:
    """Build the ffmpeg filter chain fragment for a sendcmd-driven v360@mv.

    The output is `sendcmd=f=<path>,v360@mv=...` ready to insert in a filter graph.
    """
    return (
        f"sendcmd=f={cmd_path},"
        f"v360@mv=input=e:output=flat:"
        f"yaw={yaw0}:pitch={pitch0}:h_fov={hfov0}:w={w}:h={h}"
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Generate v360 sendcmd .cmd files")
    p.add_argument("move", choices=sorted(MOVES.keys()))
    p.add_argument("--duration", type=float, required=True, help="seconds")
    p.add_argument("--params", type=str, default=None,
                   help='JSON dict of move-specific kwargs, e.g. \'{"yaw_speed":60}\'')
    p.add_argument("--out", type=Path, required=True, help="path to .cmd file")
    args = p.parse_args()

    params = json.loads(args.params) if args.params else None
    cmd_text = generate(args.move, args.duration, params)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(cmd_text + "\n")
    print(f"wrote {args.out} ({args.move}, {args.duration}s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
