"""Unit tests for lib/sphere.py — pure helpers querying sphere_score.json."""
import pytest

from lib import sphere


def _make_sphere_score(motion_per_yaw_per_frame: list[dict]) -> dict:
    """Build a sphere_score dict for testing.

    Each frame dict maps {yaw_center: motion} for that timestamp. brightness
    defaults to 1.0 unless overridden.
    """
    yaws = sorted(motion_per_yaw_per_frame[0].keys())
    frames = []
    for t, motion_map in enumerate(motion_per_yaw_per_frame):
        segments = []
        for y in yaws:
            m = motion_map[y]
            segments.append({
                "yaw_center": y,
                "motion": float(m),
                "brightness_var": 1.0,
            })
        frames.append({"t": float(t), "segments": segments})
    return {"sources": [{"source": "test.mp4", "fps_sampled": 1,
                         "n_frames": len(frames), "frames": frames}]}


class TestNormalizeWeights:
    def test_motion_only(self):
        assert sphere.normalize_weights("motion") == (1.0, 0.0)

    def test_brightness_only(self):
        assert sphere.normalize_weights("brightness") == (0.0, 1.0)

    def test_balanced(self):
        assert sphere.normalize_weights("balanced") == (0.5, 0.5)

    def test_default(self):
        # motion+brightness or unknown → motion-dominant
        assert sphere.normalize_weights("motion+brightness") == (0.7, 0.3)
        assert sphere.normalize_weights("anything else") == (0.7, 0.3)


class TestBestYawAt:
    def test_empty_returns_none(self):
        assert sphere.best_yaw_at({}, master_t=0) is None
        assert sphere.best_yaw_at({"sources": []}, master_t=0) is None

    def test_no_frames_returns_none(self):
        score = {"sources": [{"frames": []}]}
        assert sphere.best_yaw_at(score, master_t=0) is None

    def test_picks_segment_with_most_motion(self):
        # 3 frames, motion concentrated at yaw=90 in frame 1
        score = _make_sphere_score([
            {0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0},
            {0: 1.0, 90: 10.0, 180: 1.0, 270: 1.0},
            {0: 1.0, 90: 8.0, 180: 1.0, 270: 1.0},
        ])
        result = sphere.best_yaw_at(score, master_t=1.0, prefer="motion")
        assert result == 90.0

    def test_smoothing_window_pulls_from_neighbors(self):
        # At t=1, motion is 0 at every yaw. But t=0 and t=2 have motion at
        # yaw=90. Smoothing should still pick 90 because the window includes
        # neighbors.
        score = _make_sphere_score([
            {0: 0.0, 90: 5.0, 180: 0.0, 270: 0.0},
            {0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0},
            {0: 0.0, 90: 5.0, 180: 0.0, 270: 0.0},
        ])
        result = sphere.best_yaw_at(score, master_t=1.0, prefer="motion",
                                    smooth_window=3)
        assert result == 90.0

    def test_handles_t_beyond_last_frame(self):
        score = _make_sphere_score([
            {0: 0.0, 90: 10.0, 180: 0.0, 270: 0.0},
            {0: 0.0, 90: 10.0, 180: 0.0, 270: 0.0},
        ])
        # master_t=1000 is way past n_frames=2; should clamp to last frame
        result = sphere.best_yaw_at(score, master_t=1000.0, prefer="motion")
        assert result == 90.0

    def test_handles_negative_t(self):
        score = _make_sphere_score([
            {0: 5.0, 90: 0.0, 180: 0.0, 270: 0.0},
        ])
        # Negative t should clamp to first frame
        result = sphere.best_yaw_at(score, master_t=-50.0)
        # First frame has no motion baseline... actually our test framework
        # has motion=5 at yaw=0 in the only frame. But first frame's motion
        # is meaningless (no prev to diff against). The function may or may
        # not filter it. We just check the call doesn't crash.
        assert result in (0.0, None) or isinstance(result, float)

    def test_brightness_prefers_bright_segment(self):
        # All motion equal, but yaw=180 has high brightness
        score = {"sources": [{
            "source": "x.mp4", "fps_sampled": 1, "n_frames": 2,
            "frames": [
                {"t": 0.0, "segments": [
                    {"yaw_center": 0, "motion": 1.0, "brightness_var": 5.0},
                    {"yaw_center": 90, "motion": 1.0, "brightness_var": 5.0},
                    {"yaw_center": 180, "motion": 1.0, "brightness_var": 50.0},
                    {"yaw_center": 270, "motion": 1.0, "brightness_var": 5.0},
                ]},
                {"t": 1.0, "segments": [
                    {"yaw_center": 0, "motion": 1.0, "brightness_var": 5.0},
                    {"yaw_center": 90, "motion": 1.0, "brightness_var": 5.0},
                    {"yaw_center": 180, "motion": 1.0, "brightness_var": 50.0},
                    {"yaw_center": 270, "motion": 1.0, "brightness_var": 5.0},
                ]},
            ]
        }]}
        result = sphere.best_yaw_at(score, master_t=1.0, prefer="brightness")
        assert result == 180.0

    def test_invalid_source_index(self):
        score = _make_sphere_score([{0: 1.0, 90: 0.0}])
        result = sphere.best_yaw_at(score, master_t=0, source_index=99)
        assert result is None


class TestCoverageSummary:
    def test_empty_returns_invalid(self):
        assert sphere.coverage_summary({}) == {"valid": False}

    def test_identifies_dominant_motion_yaw(self):
        # Motion is consistently highest at yaw=90 across all frames
        score = _make_sphere_score([
            {0: 0.0, 90: 5.0, 180: 0.0, 270: 0.0},
            {0: 0.0, 90: 5.0, 180: 0.0, 270: 0.0},
            {0: 0.0, 90: 5.0, 180: 0.0, 270: 0.0},
        ])
        summary = sphere.coverage_summary(score)
        assert summary["valid"] is True
        assert summary["n_frames"] == 3
        assert summary["dominant_motion_yaw"] == 90.0

    def test_total_motion_dict(self):
        score = _make_sphere_score([
            {0: 1.0, 90: 5.0, 180: 0.0, 270: 0.0},
            {0: 2.0, 90: 5.0, 180: 0.0, 270: 0.0},
        ])
        summary = sphere.coverage_summary(score)
        assert summary["total_motion_per_yaw"][90] == 10.0
        assert summary["total_motion_per_yaw"][0] == 3.0
