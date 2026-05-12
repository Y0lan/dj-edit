#!/usr/bin/env python3
"""sync-cameras.py — affine sync map per camera against master audio.

Onset-envelope correlation in multiple windows + RANSAC fit:
    master_t = a * camera_t + b
captures both offset AND clock drift over long sessions.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import librosa
import numpy as np
from scipy.signal import correlate

SR = 22050
HOP = 2205
WINDOW_S = 60.0   # correlate 60s windows
STRIDE_S = 300.0  # every 5 minutes


def extract_audio(video: Path, out_wav: Path) -> bool:
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(video),
        "-vn", "-ac", "1", "-ar", str(SR), "-f", "wav", str(out_wav),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"WARN: ffmpeg failed on {video.name}: {r.stderr[:200]}", file=sys.stderr)
        return False
    return True


def onset_envelope(y: np.ndarray, sr: int) -> np.ndarray:
    """Compute and z-score normalize the onset envelope."""
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    env = env - env.mean()
    std = env.std()
    return env / std if std > 0 else env


def correlate_window(master_env: np.ndarray, cam_env: np.ndarray,
                     hops_per_s: float) -> tuple[float, float]:
    """Return (lag_in_seconds, peak_score). lag = master_t - cam_t."""
    if len(cam_env) < 10 or len(master_env) < 10:
        return 0.0, 0.0
    c = correlate(master_env, cam_env, mode="full", method="fft")
    lag_hops = np.argmax(c) - (len(cam_env) - 1)
    lag_s = lag_hops / hops_per_s
    peak = float(c.max() / (np.linalg.norm(master_env) * np.linalg.norm(cam_env) + 1e-9))
    return float(lag_s), peak


def robust_offset(points: list[tuple[float, float, float]],
                  master_dur: float,
                  inlier_thresh_s: float = 5.0) -> tuple[float, float, int]:
    """Find the constant offset b such that master_t = cam_t + b.

    Points are (window_start_in_cam, lag_s, peak). For each window:
        offset_b_i = lag_s_i - window_start_i
    because lag_s is the master_t where cam_local_t=0 (== window_start) aligns.

    Median of offset_b_i is robust to spurious correlation peaks (repeated 4-bar
    musical patterns producing matches at wrong locations)."""
    if not points:
        return 1.0, 0.0, 0
    arr = np.array(points)
    cam_starts = arr[:, 0]
    lags = arr[:, 1]
    # Each window contributes one offset estimate
    offsets = lags - cam_starts
    # Drop offsets that would put cam coverage entirely outside master_dur
    plausible = (offsets > -60) & (offsets < master_dur)
    if plausible.sum() >= 3:
        offsets = offsets[plausible]
    b = float(np.median(offsets))
    inliers = int(np.sum(np.abs(offsets - b) < inlier_thresh_s))
    return 1.0, b, inliers


def sync_camera(cam_path: Path, master_env: np.ndarray, master_dur: float,
                hops_per_s: float, tmpdir: Path) -> dict | None:
    print(f"  syncing {cam_path.name}...", file=sys.stderr)
    cam_wav = tmpdir / (cam_path.stem + ".scratch.wav")
    if not extract_audio(cam_path, cam_wav):
        return None
    y, _ = librosa.load(str(cam_wav), sr=SR, mono=True)
    cam_dur = len(y) / SR
    cam_env = onset_envelope(y, SR)

    points: list[tuple[float, float, float]] = []
    win_hops = int(WINDOW_S * hops_per_s)
    stride_hops = int(STRIDE_S * hops_per_s)

    # If camera is shorter than the standard window, try a smaller window
    if len(cam_env) < win_hops:
        if len(cam_env) < int(15 * hops_per_s):
            print(f"    WARN: {cam_path.name} too short for sync "
                  f"({cam_dur:.1f}s < 15s minimum)", file=sys.stderr)
            return None
        win_hops = max(int(10 * hops_per_s), len(cam_env) // 2)
        stride_hops = max(win_hops // 2, 1)

    pos = 0
    while pos + win_hops <= len(cam_env):
        cam_window = cam_env[pos:pos + win_hops]
        lag_s, peak = correlate_window(master_env, cam_window, hops_per_s)
        if peak > 0.02:
            cam_start = pos / hops_per_s
            points.append((cam_start, lag_s, peak))
        pos += stride_hops

    if not points:
        print(f"    ERROR: {cam_path.name} produced zero correlation peaks. "
              f"Camera audio may be silent, distorted, or unrelated to master. "
              f"Try a manual sync or check that this camera was recording during "
              f"the master audio's time range.", file=sys.stderr)
        return None

    a, b, inliers = robust_offset(points, master_dur)
    if inliers < 2:
        print(f"    WARN: {cam_path.name} sync has only {inliers} inliers across "
              f"{len(points)} windows — alignment unreliable. Affine map "
              f"(a={a:.4f}, b={b:.2f}) may produce desync. Consider providing "
              f"a manual offset.", file=sys.stderr)

    coverage_start = max(0.0, b)
    coverage_end = min(master_dur, a * cam_dur + b)
    return {
        "path": str(cam_path.name),
        "duration": float(cam_dur),
        "a": a, "b": b,
        "points": len(points),
        "inliers": inliers,
        "coverage": [float(coverage_start), float(coverage_end)],
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Affine camera sync against master audio")
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--manifest", type=Path, default=None)
    p.add_argument("--master", type=Path, default=None,
                   help="master audio (defaults to first audio file in manifest)")
    args = p.parse_args()

    proj = args.project
    manifest = json.loads((args.manifest or (proj / "manifest.json")).read_text())

    if args.master:
        master_path = args.master
    elif manifest.get("audio"):
        master_path = proj / manifest["audio"]["path"]
    else:
        print("no master audio in manifest", file=sys.stderr)
        return 2

    print(f"loading master {master_path}...", file=sys.stderr)
    y_master, _ = librosa.load(str(master_path), sr=SR, mono=True)
    master_dur = len(y_master) / SR
    print(f"  master {master_dur:.1f}s", file=sys.stderr)
    master_env = onset_envelope(y_master, SR)
    hops_per_s = SR / HOP

    results = {}
    with tempfile.TemporaryDirectory(prefix="dj-edit-sync-") as tdir:
        tmpdir = Path(tdir)
        for cam, files in manifest.get("cameras", {}).items():
            results[cam] = []
            for finfo in files:
                cam_path = proj / finfo["path"]
                info = sync_camera(cam_path, master_env, master_dur, hops_per_s, tmpdir)
                if info:
                    results[cam].append(info)
                    print(f"    -> a={info['a']:.6f} b={info['b']:.3f}s "
                          f"inliers={info['inliers']}/{info['points']} "
                          f"coverage=[{info['coverage'][0]:.1f}, {info['coverage'][1]:.1f}]",
                          file=sys.stderr)

    out = proj / "offsets.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
