#!/usr/bin/env python3
"""score-360.py — content-aware Insta360 yaw scoring.

For each Insta360 ERP source in the project, samples 1 fps frames in grayscale
at 720x360, slices each frame horizontally into 8 yaw segments (45° each), and
computes per-segment motion (frame-to-frame absolute diff) + brightness
variance.

Emits `<project>/sphere_score.json` per source.

The build-edl step queries this data to pick a `target_yaw` (or `start_yaw`)
for each Insta360 reveal — instead of hardcoding yaw=0 and risking pointing
at a wall, the camera move now targets the yaw where motion or lighting is
strongest at that moment.

Single-decode pipe-to-Python approach: zero disk for video pixels (only the
current + previous frame are held in memory, ~1MB combined). The per-frame
metadata list grows linearly with source length — at 1 fps × ~140 bytes/frame
× 8 segments, a 90-minute source yields ~600KB of metadata.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np


SEGMENT_COUNT = 8  # 45° per segment
FRAME_W = 720
FRAME_H = 360
SAMPLE_FPS = 1  # one frame per second of source
BYTES_PER_FRAME = FRAME_W * FRAME_H  # grayscale, 1 byte per pixel


def score_source(source_path: Path, source_path_rel: str | None = None) -> dict:
    """Pipe sampled grayscale frames from ffmpeg through numpy.

    Args:
      source_path: absolute filesystem path to the ERP video file.
      source_path_rel: project-relative path string as it appears in
        manifest.json (e.g. "footage/insta360/foo.mp4"). build-edl.py uses
        this to match the right sphere_score entry per Insta360 clip.

    Returns dict shaped:
      {"source": "<basename>", "source_path": "<project-relative>",
       "fps_sampled": 1, "n_frames": N,
       "frames": [{"t": float, "segments": [{"yaw_center", "motion", "brightness_var"}...]}, ...]}

    stderr is sent to DEVNULL: with stderr=PIPE we deadlock on long sources
    where ffmpeg emits decode warnings faster than Python reads stdout.
    """
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-v", "error",
        "-i", str(source_path),
        "-vf", f"fps={SAMPLE_FPS},scale={FRAME_W}:{FRAME_H}:flags=fast_bilinear,format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    strip_w = FRAME_W // SEGMENT_COUNT  # 90 px per segment
    # yaw_center for segment s: center of the [s/N, (s+1)/N] strip in degrees.
    # ERP convention: leftmost column = yaw 0 wrapping right. Segment 0 covers
    # yaw [0, 45], its center is 22.5°.
    yaw_centers = [(s + 0.5) * (360.0 / SEGMENT_COUNT) for s in range(SEGMENT_COUNT)]

    frames = []
    prev = None
    frame_idx = 0
    while True:
        chunk = proc.stdout.read(BYTES_PER_FRAME)
        if len(chunk) < BYTES_PER_FRAME:
            break  # EOF or partial frame
        img = np.frombuffer(chunk, dtype=np.uint8).reshape(FRAME_H, FRAME_W)

        seg_data = []
        for s in range(SEGMENT_COUNT):
            strip = img[:, s * strip_w:(s + 1) * strip_w]
            brightness_var = float(strip.std())
            if prev is None:
                motion = 0.0
            else:
                prev_strip = prev[:, s * strip_w:(s + 1) * strip_w]
                motion = float(
                    np.abs(strip.astype(np.int16) - prev_strip.astype(np.int16)).mean()
                )
            seg_data.append({
                "yaw_center": round(yaw_centers[s], 1),
                "motion": round(motion, 3),
                "brightness_var": round(brightness_var, 3),
            })

        frames.append({"t": float(frame_idx), "segments": seg_data})
        prev = img
        frame_idx += 1

    proc.stdout.close()
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed on {source_path} (exit {rc})")

    return {
        "source": source_path.name,
        "source_path": source_path_rel or source_path.name,
        "fps_sampled": SAMPLE_FPS,
        "n_frames": len(frames),
        "frames": frames,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Content-aware yaw scoring for Insta360 ERP sources"
    )
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--source", type=Path, default=None,
                   help="explicit ERP file (default: read from manifest)")
    p.add_argument("--out", type=Path, default=None,
                   help="output JSON path (default: <project>/sphere_score.json)")
    args = p.parse_args()

    proj = args.project
    out_path = args.out or (proj / "sphere_score.json")

    # Build a list of (abs_path, project_relative_path) pairs to score.
    sources: list[tuple[Path, str]] = []
    if args.source:
        sources.append((args.source, args.source.name))
    else:
        manifest_path = proj / "manifest.json"
        if not manifest_path.exists():
            print(f"ERROR: {manifest_path} not found — run dj-edit ingest first",
                  file=sys.stderr)
            return 2
        manifest = json.loads(manifest_path.read_text())
        insta_list = manifest.get("cameras", {}).get("insta360", [])
        if not insta_list:
            print("[score-360] no Insta360 sources in manifest — skipping",
                  file=sys.stderr)
            out_path.write_text(json.dumps({"sources": []}, indent=2))
            return 0
        for s in insta_list:
            rel = s["path"]  # project-relative path as stored in manifest
            sources.append((proj / rel, rel))

    all_results = []
    for src, rel in sources:
        if not src.exists():
            cand = proj / "footage" / "insta360" / src.name
            if cand.exists():
                src = cand
            else:
                print(f"WARN: {src} not found — skipping", file=sys.stderr)
                continue
        print(f"[score-360] analyzing {src.name}...", file=sys.stderr)
        try:
            result = score_source(src, source_path_rel=rel)
            all_results.append(result)
            print(f"  scored {result['n_frames']} frames × {SEGMENT_COUNT} segments",
                  file=sys.stderr)
        except RuntimeError as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            continue

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"sources": all_results}, indent=2))
    print(f"[score-360] wrote {out_path} ({len(all_results)} source(s))",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
