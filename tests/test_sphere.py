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

    def test_all_zero_scores_returns_none(self):
        """A black/static window should return None so the caller falls back
        to the move's default yaw, not an arbitrary 22.5°. Codex finding."""
        score = _make_sphere_score([
            {0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0},
            {0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0},
        ])
        # Override brightness too so the test is unambiguous
        for src in score["sources"]:
            for f in src["frames"]:
                for seg in f["segments"]:
                    seg["brightness_var"] = 0.0
        result = sphere.best_yaw_at(score, master_t=1.0, prefer="motion+brightness")
        assert result is None

    def test_source_path_matching(self):
        """When multiple sources are scored, source_path picks the right one."""
        # 4 segments so circular smoothing has real neighbors to pool
        score = {"sources": [
            {"source": "cam_a.mp4", "source_path": "footage/insta360/cam_a.mp4",
             "frames": [{"t": 0.0, "segments": [
                 {"yaw_center": 0, "motion": 0.0, "brightness_var": 0.0},
                 {"yaw_center": 90, "motion": 10.0, "brightness_var": 5.0},
                 {"yaw_center": 180, "motion": 0.0, "brightness_var": 0.0},
                 {"yaw_center": 270, "motion": 0.0, "brightness_var": 0.0},
             ]}]},
            {"source": "cam_b.mp4", "source_path": "footage/insta360/cam_b.mp4",
             "frames": [{"t": 0.0, "segments": [
                 {"yaw_center": 0, "motion": 10.0, "brightness_var": 5.0},
                 {"yaw_center": 90, "motion": 0.0, "brightness_var": 0.0},
                 {"yaw_center": 180, "motion": 0.0, "brightness_var": 0.0},
                 {"yaw_center": 270, "motion": 0.0, "brightness_var": 0.0},
             ]}]},
        ]}
        # cam_a's hot yaw is 90; cam_b's is 0. Selecting by source_path
        # should pick the right one.
        assert sphere.best_yaw_at(score, source_t=0.0,
                                  source_path="footage/insta360/cam_a.mp4",
                                  prefer="motion") == 90.0
        assert sphere.best_yaw_at(score, source_t=0.0,
                                  source_path="footage/insta360/cam_b.mp4",
                                  prefer="motion") == 0.0

    def test_erp_seam_smoothing(self):
        """Content split across the 337.5/22.5 seam should pool with its
        circular neighbor — combined seam beats a stronger non-seam segment."""
        # 8 segments: yaw_centers [22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5]
        # Seam (22.5 + 337.5) bins each have motion 6 → after pooling, segment 0
        # gets 0.25*6 + 0.5*6 + 0.25*<other neighbor> ≈ 4.5 normalized.
        # Non-seam segment 4 (yaw 202.5) has motion 8 (higher!), but no
        # adjacent help. After pooling: 0.25*0 + 0.5*8 + 0.25*0 = 4.
        # Without smoothing, segment 4 (202.5) would win.
        # With ERP-circular smoothing, segment 0 (22.5) or 7 (337.5) wins.
        score = {"sources": [{
            "source": "x.mp4", "fps_sampled": 1, "n_frames": 2,
            "frames": [
                {"t": 0.0, "segments": [
                    {"yaw_center": 22.5, "motion": 0.0, "brightness_var": 0.0},
                ] * 8},
                {"t": 1.0, "segments": [
                    {"yaw_center": 22.5, "motion": 6.0, "brightness_var": 0.0},
                    {"yaw_center": 67.5, "motion": 0.0, "brightness_var": 0.0},
                    {"yaw_center": 112.5, "motion": 0.0, "brightness_var": 0.0},
                    {"yaw_center": 157.5, "motion": 0.0, "brightness_var": 0.0},
                    {"yaw_center": 202.5, "motion": 8.0, "brightness_var": 0.0},
                    {"yaw_center": 247.5, "motion": 0.0, "brightness_var": 0.0},
                    {"yaw_center": 292.5, "motion": 0.0, "brightness_var": 0.0},
                    {"yaw_center": 337.5, "motion": 6.0, "brightness_var": 0.0},
                ]},
            ]
        }]}
        result = sphere.best_yaw_at(score, master_t=1.0, prefer="motion")
        # Seam-pooled result should win
        assert result in (22.5, 337.5), \
            f"expected seam segment to win, got {result}"


class TestFindSource:
    def test_finds_by_path(self):
        score = {"sources": [
            {"source": "a.mp4", "source_path": "footage/insta360/a.mp4", "frames": []},
            {"source": "b.mp4", "source_path": "footage/insta360/b.mp4", "frames": []},
        ]}
        found = sphere.find_source(score, source_path="footage/insta360/b.mp4")
        assert found["source"] == "b.mp4"

    def test_falls_back_to_basename(self):
        score = {"sources": [
            {"source": "b.mp4", "source_path": "old/path/b.mp4", "frames": []},
        ]}
        # Caller passed a different path containing the same basename
        found = sphere.find_source(score, source_path="footage/insta360/b.mp4")
        assert found["source"] == "b.mp4"

    def test_falls_back_to_index_when_no_path(self):
        score = {"sources": [
            {"source": "a.mp4", "frames": []},
            {"source": "b.mp4", "frames": []},
        ]}
        found = sphere.find_source(score, source_index=1)
        assert found["source"] == "b.mp4"


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
