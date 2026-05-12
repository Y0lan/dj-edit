#!/usr/bin/env python3
"""score-shots.py — per-camera-per-second motion-energy scoring.

ffmpeg tblend=difference + signalstats extracts mean absolute frame difference.
v1 uses motion energy only. v2 will add OpenCV face/crowd detection.
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def score_video(video: Path, master_to_cam_a: float, master_to_cam_b: float,
                sample_fps: float = 1.0) -> list[tuple[float, float]]:
    """Return list of (master_t, motion_score) tuples at sample_fps."""
    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as logf:
        logpath = Path(logf.name)
    try:
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-i", str(video),
            "-vf",
            f"fps={sample_fps},tblend=all_mode=difference,signalstats,"
            f"metadata=mode=print:file={logpath}",
            "-an", "-f", "null", "-",
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=False)
        if not logpath.exists():
            return []
        # Parse: frame:N pts:T ... lavfi.signalstats.YAVG=<float>
        results: list[tuple[float, float]] = []
        current_pts: float | None = None
        with open(logpath) as f:
            for line in f:
                m = re.match(r"frame:\d+\s+pts:\d+\s+pts_time:([\d.]+)", line)
                if m:
                    current_pts = float(m.group(1))
                    continue
                m = re.search(r"lavfi\.signalstats\.YAVG=([\d.eE+-]+)", line)
                if m and current_pts is not None:
                    yavg = float(m.group(1))
                    master_t = master_to_cam_a * current_pts + master_to_cam_b
                    results.append((master_t, yavg))
        return results
    finally:
        try:
            logpath.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    p = argparse.ArgumentParser(description="Per-camera motion-energy scoring")
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--sample-fps", type=float, default=1.0,
                   help="how often to sample (default 1 fps)")
    args = p.parse_args()

    proj = args.project
    offsets = json.loads((proj / "offsets.json").read_text())

    out: dict[str, list[dict]] = {}
    for cam, files in offsets.items():
        out[cam] = []
        for finfo in files:
            video = proj / "footage" / cam / finfo["path"].split("/")[-1]
            if not video.exists():
                video = proj / finfo["path"]
            if not video.exists():
                print(f"WARN: missing {finfo['path']}", file=sys.stderr)
                continue
            print(f"  scoring {video.name}...", file=sys.stderr)
            scores = score_video(video, finfo["a"], finfo["b"],
                                 sample_fps=args.sample_fps)
            out[cam].append({
                "path": finfo["path"],
                "scores": [{"t": t, "motion": s} for t, s in scores],
            })
            if scores:
                vals = [s for _, s in scores]
                print(f"    {len(scores)} samples | motion mean={sum(vals)/len(vals):.2f} "
                      f"min={min(vals):.2f} max={max(vals):.2f}",
                      file=sys.stderr)

    (proj / "shot_scores.json").write_text(json.dumps(out, indent=2))
    print(f"wrote {proj / 'shot_scores.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
