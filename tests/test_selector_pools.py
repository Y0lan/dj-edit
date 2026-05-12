"""Unit tests for Tier D move-pool selection (lib/selector.py)."""
import random
import tempfile
import json
from pathlib import Path

from lib import selector


class TestPickMoveForEvent:
    def test_returns_move_from_pool(self):
        rng = random.Random(42)
        for event_type in ("breakdown", "build", "drop", "peak", "lull", "hook"):
            move = selector.pick_move_for_event(event_type, rng=rng)
            assert isinstance(move, str)
            assert move in {n for n, _ in selector.MOVE_POOLS_DEFAULT[event_type]}

    def test_unknown_event_falls_back_to_orbit(self):
        rng = random.Random(42)
        move = selector.pick_move_for_event("nonexistent_event", rng=rng)
        # Should not crash, returns a sensible default
        assert move == "orbit"

    def test_avoids_recent_picks_when_alternatives_exist(self):
        rng = random.Random(42)
        # Try 50 times with "drop_impact" in recent_picks — should usually pick something else
        # (drop pool has drop_impact + whip_pan, weights 5:1, so 80% of the time it'd
        # be drop_impact; with the 5x penalty, drop_impact's effective weight = 1, so 50/50)
        picks_other = 0
        for _ in range(100):
            move = selector.pick_move_for_event("drop", rng=rng,
                                                recent_picks=["drop_impact"])
            if move != "drop_impact":
                picks_other += 1
        # Heavy penalty should produce many non-repeats. >= 20 out of 100 is a low bar.
        assert picks_other >= 20

    def test_intensity_biases_toward_big_moves(self):
        """High intensity should pick 'big' moves more often than low intensity."""
        rng_high = random.Random(42)
        rng_low = random.Random(42)
        big_set = {"drop_impact", "build_tension", "bpm_sync_orbit", "whip_pan", "kick_pulse_fov"}
        high_big = sum(1 for _ in range(200)
                       if selector.pick_move_for_event("peak", intensity=1.0, rng=rng_high) in big_set)
        low_big = sum(1 for _ in range(200)
                      if selector.pick_move_for_event("peak", intensity=0.0, rng=rng_low) in big_set)
        # High-intensity should produce more big-move picks
        assert high_big > low_big

    def test_pool_override_used(self):
        rng = random.Random(42)
        # Override that ONLY has "orbit" in the pool
        override = {"drop": [("orbit", 10)]}
        for _ in range(20):
            move = selector.pick_move_for_event("drop", rng=rng,
                                                pool_override=override)
            assert move == "orbit"


class TestLoadPoolOverrides:
    def test_returns_none_for_no_args(self):
        result = selector.load_pool_overrides()
        assert result is None

    def test_returns_none_for_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpd:
            result = selector.load_pool_overrides(style="nonexistent", presets_root=tmpd)
            assert result is None

    def test_loads_real_file(self):
        with tempfile.TemporaryDirectory() as tmpd:
            styles_dir = Path(tmpd) / "styles"
            styles_dir.mkdir()
            (styles_dir / "test.json").write_text(json.dumps({
                "name": "test",
                "move_pools": {
                    "drop": [
                        {"name": "drop_impact", "weight": 5},
                        {"name": "whip_pan", "weight": 3},
                    ]
                }
            }))
            result = selector.load_pool_overrides(style="test", presets_root=tmpd)
            assert result is not None
            assert "drop" in result
            assert result["drop"] == [("drop_impact", 5), ("whip_pan", 3)]

    def test_reference_overrides_style(self):
        with tempfile.TemporaryDirectory() as tmpd:
            root = Path(tmpd)
            (root / "styles").mkdir()
            (root / "references").mkdir()
            (root / "styles" / "s1.json").write_text(json.dumps({
                "move_pools": {"drop": [{"name": "orbit", "weight": 1}]}
            }))
            (root / "references" / "r1.json").write_text(json.dumps({
                "move_pools": {"drop": [{"name": "drop_impact", "weight": 99}]}
            }))
            result = selector.load_pool_overrides(style="s1", reference="r1", presets_root=tmpd)
            # Reference loaded last → wins
            assert result["drop"] == [("drop_impact", 99)]

    def test_shipped_style_files_load(self):
        """Sanity: all shipped style + reference packs are valid JSON with the right shape."""
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent
        for kind in ("styles", "references"):
            for jf in (repo_root / "presets" / kind).glob("*.json"):
                data = json.loads(jf.read_text())
                assert "name" in data, f"{jf} missing 'name'"
                assert "move_pools" in data, f"{jf} missing 'move_pools'"
                for event_type, pool in data["move_pools"].items():
                    assert isinstance(pool, list), f"{jf}: pool[{event_type}] not a list"
                    for entry in pool:
                        assert "name" in entry, f"{jf}: pool entry missing 'name'"
