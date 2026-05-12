"""Unit tests for lib/selector.py — camera/framing selection + crop math."""
import re

from lib import selector


class TestBuildCropFilter:
    def _parse_crop(self, chain: str) -> tuple[int, int, int, int]:
        """Parse `crop=W:H:X:Y,scale=...` → (W, H, X, Y)."""
        m = re.search(r"crop=(\d+):(\d+):(\d+):(\d+)", chain)
        assert m, f"no crop in {chain}"
        return tuple(int(g) for g in m.groups())

    def test_all_dimensions_even(self):
        """YUV420 encoders require even crop_w, crop_h, cx, cy. Odd values
        cause 'invalid argument' from h264 encoders."""
        for framing in ("wide", "medium", "tight", "left", "right", "up", "punch"):
            for (sw, sh) in [(3840, 2160), (1920, 1080), (5760, 2880)]:
                for (ow, oh) in [(1080, 1920), (1920, 1080), (1080, 1080)]:
                    chain = selector.build_crop_filter(framing, sw, sh, ow, oh)
                    w, h, x, y = self._parse_crop(chain)
                    assert w % 2 == 0, f"{framing} {sw}x{sh}→{ow}x{oh}: w={w} odd"
                    assert h % 2 == 0, f"{framing} {sw}x{sh}→{ow}x{oh}: h={h} odd"
                    assert x % 2 == 0, f"{framing} {sw}x{sh}→{ow}x{oh}: x={x} odd"
                    assert y % 2 == 0, f"{framing} {sw}x{sh}→{ow}x{oh}: y={y} odd"

    def test_crop_within_source(self):
        """crop+offset must stay inside source bounds."""
        for framing in selector.FRAMINGS.keys():
            chain = selector.build_crop_filter(framing, 3840, 2160, 1080, 1920)
            w, h, x, y = self._parse_crop(chain)
            assert 0 <= x and x + w <= 3840, f"{framing}: x+w={x+w} > 3840"
            assert 0 <= y and y + h <= 2160, f"{framing}: y+h={y+h} > 2160"

    def test_unknown_framing_falls_back_to_medium(self):
        chain = selector.build_crop_filter("nonexistent", 3840, 2160, 1080, 1920)
        # Should produce a valid crop, not crash
        w, h, x, y = self._parse_crop(chain)
        assert w > 0 and h > 0

    def test_scale_to_output_dims(self):
        chain = selector.build_crop_filter("wide", 3840, 2160, 1080, 1920)
        assert "scale=1080:1920" in chain

    def test_punch_zooms_in(self):
        """punch framing has higher zoom factor than wide."""
        wide = selector.build_crop_filter("wide", 3840, 2160, 1080, 1920)
        punch = selector.build_crop_filter("punch", 3840, 2160, 1080, 1920)
        ww, _, _, _ = self._parse_crop(wide)
        pw, _, _, _ = self._parse_crop(punch)
        assert pw < ww, f"punch crop_w ({pw}) should be < wide ({ww})"


class TestFramingForEvent:
    def test_drop_index_0_is_punch(self):
        assert selector.framing_for_event("drop", 0) == "punch"

    def test_breakdown_is_wide(self):
        assert selector.framing_for_event("breakdown", 0) == "wide"

    def test_peak_cycles(self):
        """peak cycles through multiple framings — consecutive calls vary."""
        framings = [selector.framing_for_event("peak", i) for i in range(6)]
        assert len(set(framings)) > 1, "peak should cycle, not stay on one framing"


class TestPickCamera:
    def test_no_cameras_returns_none(self):
        result = selector.pick_camera({}, {}, 100.0)
        assert result is None

    def test_single_camera(self):
        offsets = {"a7iii": [{"path": "C0006.MP4", "coverage": [0.0, 1000.0],
                              "a": 1.0, "b": 0.0, "duration": 1000.0}]}
        cam, finfo = selector.pick_camera(offsets, {}, 500.0)
        assert cam == "a7iii"
        assert finfo["path"] == "C0006.MP4"

    def test_outside_coverage_returns_none(self):
        offsets = {"a7iii": [{"path": "C0006.MP4", "coverage": [0.0, 100.0],
                              "a": 1.0, "b": 0.0, "duration": 100.0}]}
        result = selector.pick_camera(offsets, {}, 500.0)
        assert result is None
