from __future__ import annotations

import argparse
import json
import numpy as np
import pytest
import yaml

import src.main as main
from src.calibration import CalibrationError, load_calibration_bundle, refresh_empty_reference
from src.camera import CameraError
from src.configuration import ConfigError, load_config
from src.estimator import calculate_band_occupancy, fuse_scores, map_score_to_label

try:
    import cv2
except ImportError:  # pragma: no cover - dependency-limited environments
    cv2 = None


class StubFrameSource:
    def __init__(self, frames: list[np.ndarray]):
        self.frames = [frame.copy() for frame in frames]
        self.index = 0

    def read(self) -> np.ndarray:
        if self.index >= len(self.frames):
            raise CameraError("No more frames available in stub frame source.")
        frame = self.frames[self.index]
        self.index += 1
        return frame.copy()

    def close(self) -> None:
        return None


def _write_test_config(tmp_path, overrides: dict | None = None):
    config_path = tmp_path / "config.yaml"
    base_override = {
        "processing": {
            "width": 4,
            "height": 4,
            "blur_kernel_size": 1,
        },
        "camera": {
            "capture_resolution": [4, 4],
            "preview_resolution": [4, 4],
            "warmup_seconds": 0,
        },
        "mission": {
            "reference_frame_count": 3,
            "reference_settle_frames": 1,
        },
        "runtime": {
            "recovery_delay_seconds": 0,
            "max_consecutive_failures": 2,
        },
        "paths": {
            "calibration_dir": str(tmp_path / "calibration"),
            "empty_reference_image": str(tmp_path / "calibration" / "empty_reference.png"),
            "roi_mask": str(tmp_path / "calibration" / "roi_mask.png"),
            "calibration_data": str(tmp_path / "calibration" / "calibration.json"),
            "debug_dir": str(tmp_path / "debug"),
        },
    }

    if overrides:
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(base_override.get(key), dict):
                base_override[key].update(value)
            else:
                base_override[key] = value

    config_path.write_text(yaml.safe_dump(base_override), encoding="utf-8")
    return config_path


def _write_calibration_assets(tmp_path, *, width: int = 4, height: int = 4, band_count: int = 5) -> None:
    if cv2 is None:  # pragma: no cover - dependency-limited environments
        pytest.skip("OpenCV is required for calibration asset tests.")

    calibration_dir = tmp_path / "calibration"
    calibration_dir.mkdir(parents=True, exist_ok=True)
    roi_mask = np.full((height, width), 255, dtype=np.uint8)
    empty_reference = np.full((height, width, 3), 10, dtype=np.uint8)

    cv2.imwrite(str(calibration_dir / "roi_mask.png"), roi_mask)
    cv2.imwrite(str(calibration_dir / "empty_reference.png"), empty_reference)
    (calibration_dir / "calibration.json").write_text(
        json.dumps(
            {
                "processing_resolution": {"width": width, "height": height},
                "band_count": band_count,
                "roi_pixels": int(np.count_nonzero(roi_mask)),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_map_score_to_label_uses_thresholds_and_hysteresis() -> None:
    assert map_score_to_label(0.1, 0.25, 0.75) == 1
    assert map_score_to_label(0.5, 0.25, 0.75) == 2
    assert map_score_to_label(0.9, 0.25, 0.75) == 3
    assert map_score_to_label(0.27, 0.25, 0.75, last_label_id=1, hysteresis_margin=0.05) == 1


def test_fuse_scores_respects_weighted_average() -> None:
    fused = fuse_scores(height_score=0.8, area_score=0.2, alpha=0.6, beta=0.4)
    assert fused == pytest.approx(0.56)


def test_calculate_band_occupancy_returns_fraction_inside_each_band() -> None:
    foreground_mask = np.array(
        [
            [255, 0, 0, 0],
            [255, 255, 0, 0],
            [0, 0, 255, 255],
            [0, 0, 0, 255],
        ],
        dtype=np.uint8,
    )
    top_band = np.array(
        [
            [255, 255, 255, 255],
            [255, 255, 255, 255],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ],
        dtype=np.uint8,
    )
    bottom_band = np.array(
        [
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [255, 255, 255, 255],
            [255, 255, 255, 255],
        ],
        dtype=np.uint8,
    )

    occupancy = calculate_band_occupancy(foreground_mask, [top_band, bottom_band])
    assert occupancy[0] == pytest.approx(3 / 8)
    assert occupancy[1] == pytest.approx(3 / 8)


def test_load_config_merges_overrides(tmp_path) -> None:
    config_path = tmp_path / "custom.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "estimation": {
                    "alpha": 0.7,
                    "beta": 0.3,
                },
                "smoothing": {
                    "window_size": 3,
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert config["estimation"]["alpha"] == 0.7
    assert config["estimation"]["beta"] == 0.3
    assert config["smoothing"]["window_size"] == 3
    assert config["processing"]["width"] == 640


def test_load_config_rejects_invalid_thresholds(tmp_path) -> None:
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "estimation": {
                    "empty_threshold": 0.8,
                    "full_threshold": 0.2,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_load_calibration_bundle_rejects_stale_resolution(tmp_path) -> None:
    config_path = _write_test_config(tmp_path)
    _write_calibration_assets(tmp_path)

    calibration_data_path = tmp_path / "calibration" / "calibration.json"
    calibration_data = json.loads(calibration_data_path.read_text(encoding="utf-8"))
    calibration_data["processing_resolution"] = {"width": 8, "height": 8}
    calibration_data_path.write_text(json.dumps(calibration_data, indent=2), encoding="utf-8")

    config = load_config(config_path)
    with pytest.raises(CalibrationError, match="Calibration resolution does not match"):
        load_calibration_bundle(config)


def test_refresh_empty_reference_uses_median_frame_and_updates_metadata(tmp_path) -> None:
    if cv2 is None:  # pragma: no cover - dependency-limited environments
        pytest.skip("OpenCV is required for reference refresh tests.")

    config_path = _write_test_config(tmp_path)
    _write_calibration_assets(tmp_path)
    config = load_config(config_path)

    frames = [
        np.full((4, 4, 3), 1, dtype=np.uint8),   # throw away one frame to let things settle
        np.full((4, 4, 3), 10, dtype=np.uint8),
        np.full((4, 4, 3), 30, dtype=np.uint8),
        np.full((4, 4, 3), 20, dtype=np.uint8),
    ]
    result = refresh_empty_reference(StubFrameSource(frames), config)

    saved_reference = cv2.imread(str(tmp_path / "calibration" / "empty_reference.png"))
    saved_metadata = json.loads((tmp_path / "calibration" / "calibration.json").read_text(encoding="utf-8"))

    assert result["status"] == "reference_refreshed"
    assert result["captured_frames"] == 3
    assert int(saved_reference[0, 0, 0]) == 20
    assert "reference_updated_at" in saved_metadata


def test_run_loop_command_recovers_after_transient_camera_error(tmp_path, monkeypatch, capsys) -> None:
    config_path = _write_test_config(
        tmp_path,
        overrides={
            "mission": {"refresh_reference_on_run": False},
        },
    )

    class FailingOnceFrameSource:
        def __init__(self, should_fail: bool):
            self.should_fail = should_fail
            self.failed = False

        def read(self) -> np.ndarray:
            if self.should_fail and not self.failed:
                self.failed = True
                raise CameraError("Transient camera hiccup.")
            return np.full((4, 4, 3), 25, dtype=np.uint8)

        def close(self) -> None:
            return None

    open_calls = {"count": 0}

    def fake_open_frame_source(*_args, **_kwargs):
        open_calls["count"] += 1
        return FailingOnceFrameSource(should_fail=open_calls["count"] == 1)

    class DummyEstimator:
        def __init__(self, config_path: str | None = None):
            self.config = load_config(config_path)

        def analyze_frame(self, frame_bgr: np.ndarray, save_debug: bool | None = None):
            del save_debug
            return {"pixel": int(frame_bgr[0, 0, 0])}

    monkeypatch.setattr(main, "open_frame_source", fake_open_frame_source)
    monkeypatch.setattr(main, "CaddyFullnessEstimator", DummyEstimator)
    monkeypatch.setattr(main.time, "sleep", lambda *_args, **_kwargs: None)

    args = argparse.Namespace(
        config=str(config_path),
        source=None,
        input_image=None,
        input_dir=None,
        debug=False,
        max_iterations=1,
        refresh_reference=False,
        skip_reference_refresh=True,
    )

    exit_code = main.run_loop_command(args)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Recoverable runtime error" in captured.err
    assert '"pixel": 25' in captured.out
