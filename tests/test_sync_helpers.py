"""Unit tests for sync-cameras helpers (robust_offset).

Tests the affine-offset estimator in isolation against synthetic point sets.
"""
import importlib.util
import sys
from pathlib import Path


# sync-cameras.py is in bin/ with a hyphen in the filename — load via importlib
ROOT = Path(__file__).resolve().parent.parent
SYNC_PATH = ROOT / "bin" / "sync-cameras.py"
spec = importlib.util.spec_from_file_location("sync_cameras", SYNC_PATH)
sync_cameras = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync_cameras)


class TestRobustOffset:
    def test_empty_returns_defaults(self):
        a, b, inliers = sync_cameras.robust_offset([], master_dur=1000.0)
        assert a == 1.0
        assert b == 0.0
        assert inliers == 0

    def test_constant_offset_recovered(self):
        """If every window agrees on the same offset, median should recover it."""
        # 10 windows, each at cam_t=N*60, with lag_s = N*60 + 500 (offset = 500)
        points = [(i * 60.0, i * 60.0 + 500.0, 0.5) for i in range(10)]
        a, b, inliers = sync_cameras.robust_offset(points, master_dur=10000.0)
        assert a == 1.0
        assert abs(b - 500.0) < 1.0
        assert inliers == 10

    def test_outliers_rejected(self):
        """Median is robust to up to 50% outliers."""
        # 6 inliers at offset=500, 4 wild outliers
        good = [(i * 60.0, i * 60.0 + 500.0, 0.5) for i in range(6)]
        bad = [(i * 60.0, i * 60.0 + 1500.0, 0.5) for i in range(6, 10)]
        a, b, inliers = sync_cameras.robust_offset(good + bad, master_dur=10000.0)
        # Median should pick the 500-offset cluster
        assert abs(b - 500.0) < 50.0
        assert inliers >= 6  # at least the 6 good ones inside threshold

    def test_clipped_at_master_end_excluded(self):
        """Windows whose offset would put cam coverage past master end are
        clipping artifacts. With cam_dur given, robust_offset filters them."""
        good = [(i * 60.0, i * 60.0 + 500.0, 0.5) for i in range(5)]
        # Clipped points all have lag = master_dur (5000); their offsets would
        # place cam coverage from offset to offset+cam_dur — past master_dur.
        clipped = [(i * 60.0, 5000.0, 0.5) for i in range(5, 10)]
        cam_dur = 1000.0  # camera is 1000s long
        a, b, inliers = sync_cameras.robust_offset(
            good + clipped, master_dur=5000.0, cam_dur=cam_dur,
        )
        assert abs(b - 500.0) < 100.0

    def test_drift_handled_via_median(self):
        """A small linear drift (cam slightly faster than master) gets
        approximated by the median offset. Won't be perfect but shouldn't
        explode."""
        # Slope a ≈ 1.0001 (100 ppm drift), b = 500
        points = [(i * 60.0, 1.0001 * (i * 60.0) + 500.0, 0.5) for i in range(10)]
        a, b, inliers = sync_cameras.robust_offset(points, master_dur=10000.0)
        # Won't recover the drift exactly (we model a=1 only) but b should
        # be close to the center-of-mass offset.
        assert 480 < b < 580
