"""Smoke test: bin/score-360.py against a synthesized 4-second ERP fixture.

Verifies that:
  - The pipeline runs end-to-end with ffmpeg in PATH
  - Motion is localized to segments where pixels actually change between frames
  - The sphere_score.json shape matches what build-edl + sphere.best_yaw_at expect
  - source_path is written so multi-Insta360 source matching works

Skipped if ffmpeg is unavailable on PATH.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCORE_360 = ROOT / "bin" / "score-360.py"


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg not available — skipping score-360 smoke test",
)


def _make_synthetic_erp(out_path: Path, duration: int = 3) -> None:
    """Generate a 720x360 ERP with a white-box flash in the LEFT half each
    second. Left half (yaw 0-180°) → motion. Right half → near-zero motion."""
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi",
        "-i", f"color=c=black:s=720x360:d={duration}",
        "-vf",
        "drawbox=x=80:y=80:w=200:h=200:color=white:t=fill:enable='lt(mod(t,2),1)'",
        "-t", str(duration),
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)


def test_score_360_localizes_motion(tmp_path):
    erp = tmp_path / "erp.mp4"
    _make_synthetic_erp(erp, duration=4)
    assert erp.exists(), "ffmpeg failed to write the test ERP fixture"

    out_path = tmp_path / "score.json"
    result = subprocess.run([
        sys.executable, str(SCORE_360),
        "--project", str(tmp_path),
        "--source", str(erp),
        "--out", str(out_path),
    ], check=True, capture_output=True, text=True)

    assert out_path.exists(), f"score-360 didn't write output. stderr:\n{result.stderr}"
    data = json.loads(out_path.read_text())
    assert "sources" in data
    assert len(data["sources"]) == 1

    src = data["sources"][0]
    # source_path field is critical for multi-Insta360 matching in build-edl
    assert "source_path" in src, "source_path missing — multi-source matching will break"
    assert src["source"] == "erp.mp4"
    assert src["n_frames"] >= 3  # 4s @ 1fps, give or take fencepost

    # Motion: the moving box stays in the left half (x sweeps 0..360).
    # Segments 0-3 cover yaw 0-180° (left half of ERP).
    frames = src["frames"]
    # Skip first frame (no diff baseline)
    later_frames = frames[1:]

    left_motion = 0.0
    right_motion = 0.0
    for f in later_frames:
        for seg in f["segments"]:
            if seg["yaw_center"] < 180:
                left_motion += seg["motion"]
            else:
                right_motion += seg["motion"]

    assert left_motion > 0, "expected motion in the left half of the ERP"
    assert left_motion > right_motion * 3, (
        f"motion not localized to left half — left={left_motion:.1f} "
        f"right={right_motion:.1f}"
    )


def test_score_360_handles_missing_manifest(tmp_path):
    """Without a manifest and without --source, exit code 2 (graceful error)."""
    result = subprocess.run([
        sys.executable, str(SCORE_360),
        "--project", str(tmp_path),
        "--out", str(tmp_path / "score.json"),
    ], capture_output=True, text=True)
    assert result.returncode == 2
    assert "manifest" in result.stderr.lower()


def test_score_360_handles_no_insta_in_manifest(tmp_path):
    """When manifest exists but has no Insta360 sources, exit 0 with empty list."""
    manifest = {"cameras": {"a7iii": [{"path": "foo.mp4"}]}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    out_path = tmp_path / "score.json"
    result = subprocess.run([
        sys.executable, str(SCORE_360),
        "--project", str(tmp_path),
        "--out", str(out_path),
    ], capture_output=True, text=True)
    assert result.returncode == 0
    data = json.loads(out_path.read_text())
    assert data == {"sources": []}
