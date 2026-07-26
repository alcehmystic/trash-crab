from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.yaml"


class ConfigError(Exception):
    """Something is off in the config file or the merged settings."""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in config file {path}: {exc}") from exc

    if not isinstance(content, dict):
        raise ConfigError(f"Config file {path} must contain a YAML object at the top level.")
    return content


def ensure_odd(value: int) -> int:
    return value if value % 2 == 1 else value + 1


def resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    default_config = _load_yaml(DEFAULT_CONFIG_PATH)

    if config_path is None:
        config = default_config
    else:
        override_path = Path(config_path)
        override_config = _load_yaml(override_path)
        config = _deep_merge(default_config, override_config)

    normalize_config(config)
    validate_config(config)
    return config


def normalize_config(config: dict[str, Any]) -> None:
    processing = config.setdefault("processing", {})
    smoothing = config.setdefault("smoothing", {})
    estimation = config.setdefault("estimation", {})
    mission = config.setdefault("mission", {})
    runtime = config.setdefault("runtime", {})

    processing["blur_kernel_size"] = ensure_odd(int(processing.get("blur_kernel_size", 5)))
    processing["morph_open_kernel_size"] = ensure_odd(int(processing.get("morph_open_kernel_size", 3)))
    processing["morph_close_kernel_size"] = ensure_odd(int(processing.get("morph_close_kernel_size", 5)))
    smoothing["window_size"] = int(smoothing.get("window_size", 5))
    estimation["band_count"] = int(estimation.get("band_count", 5))
    mission["reference_frame_count"] = int(mission.get("reference_frame_count", 5))
    mission["reference_settle_frames"] = int(mission.get("reference_settle_frames", 2))
    runtime["recovery_delay_seconds"] = float(runtime.get("recovery_delay_seconds", 1.0))
    runtime["max_consecutive_failures"] = int(runtime.get("max_consecutive_failures", 0))


def validate_config(config: dict[str, Any]) -> None:
    try:
        processing = config["processing"]
        estimation = config["estimation"]
        smoothing = config["smoothing"]
        mission = config["mission"]
        camera = config["camera"]
        paths = config["paths"]
        runtime = config["runtime"]
    except KeyError as exc:
        raise ConfigError(f"Missing required config section: {exc}") from exc

    for key in ("width", "height", "blur_kernel_size", "diff_threshold", "min_contour_area"):
        if float(processing[key]) <= 0:
            raise ConfigError(f"processing.{key} must be positive.")

    if float(processing["area_normalization"]) <= 0:
        raise ConfigError("processing.area_normalization must be positive.")

    if not 0 <= float(processing["edge_weight"]) <= 1:
        raise ConfigError("processing.edge_weight must be between 0 and 1.")

    empty_threshold = float(estimation["empty_threshold"])
    full_threshold = float(estimation["full_threshold"])
    if not 0 <= empty_threshold < full_threshold <= 1:
        raise ConfigError("estimation.empty_threshold must be < estimation.full_threshold, both between 0 and 1.")

    if int(estimation["band_count"]) <= 0:
        raise ConfigError("estimation.band_count must be greater than 0.")

    band_weights = estimation.get("band_weights")
    if band_weights is not None and len(band_weights) != int(estimation["band_count"]):
        raise ConfigError("estimation.band_weights length must match estimation.band_count.")

    if float(estimation["alpha"]) < 0 or float(estimation["beta"]) < 0:
        raise ConfigError("estimation.alpha and estimation.beta must be non-negative.")

    if float(estimation["alpha"]) + float(estimation["beta"]) == 0:
        raise ConfigError("estimation.alpha and estimation.beta cannot both be zero.")

    if smoothing["method"] not in {"median", "mean"}:
        raise ConfigError("smoothing.method must be either 'median' or 'mean'.")

    if int(smoothing["window_size"]) <= 0:
        raise ConfigError("smoothing.window_size must be greater than 0.")

    if float(smoothing["hysteresis_margin"]) < 0:
        raise ConfigError("smoothing.hysteresis_margin must be non-negative.")

    if int(mission["reference_frame_count"]) <= 0:
        raise ConfigError("mission.reference_frame_count must be greater than 0.")

    if int(mission["reference_settle_frames"]) < 0:
        raise ConfigError("mission.reference_settle_frames cannot be negative.")

    if camera["source"] not in {"picamera2", "image", "directory"}:
        raise ConfigError("camera.source must be one of: picamera2, image, directory.")

    for key in ("capture_resolution", "preview_resolution"):
        if len(camera[key]) != 2 or min(int(camera[key][0]), int(camera[key][1])) <= 0:
            raise ConfigError(f"camera.{key} must contain two positive integers.")

    if float(camera["processing_interval_seconds"]) <= 0:
        raise ConfigError("camera.processing_interval_seconds must be positive.")

    if float(camera["warmup_seconds"]) < 0:
        raise ConfigError("camera.warmup_seconds cannot be negative.")

    if float(runtime["recovery_delay_seconds"]) < 0:
        raise ConfigError("runtime.recovery_delay_seconds cannot be negative.")

    if int(runtime["max_consecutive_failures"]) < 0:
        raise ConfigError("runtime.max_consecutive_failures cannot be negative.")

    for key in ("calibration_dir", "empty_reference_image", "roi_mask", "calibration_data", "debug_dir"):
        if not paths.get(key):
            raise ConfigError(f"paths.{key} must be provided.")
