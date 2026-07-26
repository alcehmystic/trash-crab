from __future__ import annotations

from collections import deque
from datetime import datetime
from statistics import fmean, median, pstdev
from typing import Any

import numpy as np

from .calibration import CalibrationBundle, CalibrationError, load_calibration_bundle
from .camera import open_frame_source
from .configuration import ConfigError, load_config
from .preprocessing import compute_edge_map, preprocess_frame
from .visualization import save_debug_artifacts

try:
    import cv2
except ImportError:  # pragma: no cover - exercised only in dependency-limited environments
    cv2 = None

LABEL_MAP = {
    1: "empty",
    2: "partially_full",
    3: "full",
}


class EstimatorError(Exception):
    """The estimator could not make sense of the frame it was given."""


def require_cv2() -> None:
    if cv2 is None:
        raise EstimatorError("OpenCV (cv2) is required for frame analysis. Install the project requirements first.")


def resolve_band_weights(band_count: int, band_weights: list[float] | None = None) -> list[float]:
    if band_weights is None:
        return [float(band_count - index) for index in range(band_count)]
    return [float(weight) for weight in band_weights]


def calculate_band_occupancy(foreground_mask: np.ndarray, band_masks: list[np.ndarray]) -> list[float]:
    occupancy: list[float] = []
    for band_mask in band_masks:
        band_pixels = int(np.count_nonzero(band_mask))
        if band_pixels == 0:
            occupancy.append(0.0)
            continue
        occupied = int(np.count_nonzero((foreground_mask > 0) & (band_mask > 0)))
        occupancy.append(occupied / band_pixels)
    return occupancy


def calculate_height_score(
    band_occupancy: list[float],
    occupancy_threshold: float,
    band_weights: list[float],
) -> tuple[float, int | None]:
    if not band_occupancy:
        return 0.0, None

    weighted_score = sum(score * weight for score, weight in zip(band_occupancy, band_weights)) / sum(band_weights)
    highest_occupied_band = next((index for index, value in enumerate(band_occupancy) if value >= occupancy_threshold), None)

    if highest_occupied_band is None:
        return float(np.clip(weighted_score, 0.0, 1.0)), None

    band_count = len(band_occupancy)
    highest_band_component = (band_count - highest_occupied_band) / band_count
    height_score = 0.7 * weighted_score + 0.3 * highest_band_component
    return float(np.clip(height_score, 0.0, 1.0)), highest_occupied_band


def fuse_scores(height_score: float, area_score: float, alpha: float, beta: float) -> float:
    weight_total = alpha + beta
    if weight_total <= 0:
        raise EstimatorError("Alpha and beta need to add up to more than zero.")
    return float(np.clip(((alpha * height_score) + (beta * area_score)) / weight_total, 0.0, 1.0))


def map_score_to_label(
    score: float,
    empty_threshold: float,
    full_threshold: float,
    last_label_id: int | None = None,
    hysteresis_margin: float = 0.0,
) -> int:
    if last_label_id == 1 and score < empty_threshold + hysteresis_margin:
        return 1
    if last_label_id == 3 and score > full_threshold - hysteresis_margin:
        return 3
    if last_label_id == 2:
        if score < empty_threshold - hysteresis_margin:
            return 1
        if score > full_threshold + hysteresis_margin:
            return 3
        return 2

    if score < empty_threshold:
        return 1
    if score >= full_threshold:
        return 3
    return 2


class CaddyFullnessEstimator:
    def __init__(self, config_path: str | None = None):
        require_cv2()
        self.config = load_config(config_path)
        self.calibration: CalibrationBundle = load_calibration_bundle(self.config)
        if self.calibration.empty_reference_bgr is None:
            raise CalibrationError("Calibration is missing an empty reference image. Capture a fresh reference first.")
        self.reference_processed = preprocess_frame(
            self.calibration.empty_reference_bgr,
            self.config,
            roi_mask=self.calibration.roi_mask,
        )

        window_size = int(self.config["smoothing"]["window_size"])
        self.score_history: deque[float] = deque(maxlen=window_size)
        self.last_label_id: int | None = None
        self.band_weights = resolve_band_weights(
            int(self.config["estimation"]["band_count"]),
            self.config["estimation"].get("band_weights"),
        )

    def _postprocess_foreground_mask(self, binary_mask: np.ndarray) -> tuple[np.ndarray, list[float]]:
        require_cv2()
        processing = self.config["processing"]
        open_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (int(processing["morph_open_kernel_size"]), int(processing["morph_open_kernel_size"])),
        )
        close_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (int(processing["morph_close_kernel_size"]), int(processing["morph_close_kernel_size"])),
        )
        cleaned = cv2.morphologyEx(binary_mask, cv2.MORPH_OPEN, open_kernel)
        cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, close_kernel)

        contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filtered_mask = np.zeros_like(cleaned)
        kept_areas: list[float] = []
        min_area = float(processing["min_contour_area"])

        for contour in contours:
            area = cv2.contourArea(contour)
            if area >= min_area:
                cv2.drawContours(filtered_mask, [contour], -1, 255, thickness=cv2.FILLED)
                kept_areas.append(float(area))

        filtered_mask = cv2.bitwise_and(filtered_mask, filtered_mask, mask=self.calibration.roi_mask)
        return filtered_mask, kept_areas

    def _smooth_score(self, score: float) -> float:
        self.score_history.append(score)
        if not self.config["smoothing"]["enabled"]:
            return score
        method = self.config["smoothing"]["method"]
        if method == "median":
            return float(median(self.score_history))
        return float(fmean(self.score_history))

    def _calculate_confidence(self, smoothed_score: float, area_score: float, height_score: float) -> float:
        empty_threshold = float(self.config["estimation"]["empty_threshold"])
        full_threshold = float(self.config["estimation"]["full_threshold"])
        max_distance = max(empty_threshold, 1.0 - full_threshold, full_threshold - empty_threshold, 0.01)
        boundary_distance = min(abs(smoothed_score - empty_threshold), abs(smoothed_score - full_threshold))
        threshold_confidence = min(boundary_distance / max_distance, 1.0)

        if len(self.score_history) > 1:
            stability = 1.0 - min(pstdev(self.score_history) / 0.2, 1.0)
        else:
            stability = 0.6

        evidence = max(area_score, height_score)
        confidence = 0.45 * evidence + 0.35 * stability + 0.20 * threshold_confidence
        return float(np.clip(confidence, 0.0, 1.0))

    def analyze_frame(self, frame_bgr: np.ndarray, save_debug: bool | None = None) -> dict[str, Any]:
        require_cv2()
        if frame_bgr is None or frame_bgr.size == 0:
            raise EstimatorError("Frame capture failed or returned an empty image.")

        current_processed = preprocess_frame(frame_bgr, self.config, roi_mask=self.calibration.roi_mask)
        diff_image = cv2.absdiff(current_processed.normalized_gray, self.reference_processed.normalized_gray)

        _, threshold_mask = cv2.threshold(
            diff_image,
            int(self.config["processing"]["diff_threshold"]),
            255,
            cv2.THRESH_BINARY,
        )
        threshold_mask = cv2.bitwise_and(threshold_mask, threshold_mask, mask=self.calibration.roi_mask)
        foreground_mask, contour_areas = self._postprocess_foreground_mask(threshold_mask)

        roi_pixels = int(np.count_nonzero(self.calibration.roi_mask))
        foreground_pixels = int(np.count_nonzero(foreground_mask))
        changed_area_ratio = foreground_pixels / roi_pixels if roi_pixels else 0.0

        current_edges = compute_edge_map(current_processed.normalized_gray, self.config)
        reference_edges = compute_edge_map(self.reference_processed.normalized_gray, self.config)
        edge_diff = cv2.absdiff(current_edges, reference_edges)
        edge_pixels = int(np.count_nonzero(cv2.bitwise_and(edge_diff, edge_diff, mask=self.calibration.roi_mask)))
        edge_ratio = edge_pixels / roi_pixels if roi_pixels else 0.0

        edge_weight = float(self.config["processing"]["edge_weight"])
        blended_area_signal = (1.0 - edge_weight) * changed_area_ratio + edge_weight * edge_ratio
        area_score = min(blended_area_signal / float(self.config["processing"]["area_normalization"]), 1.0)

        band_occupancy = calculate_band_occupancy(foreground_mask, self.calibration.band_masks)
        height_score, highest_occupied_band = calculate_height_score(
            band_occupancy,
            float(self.config["estimation"]["occupancy_threshold"]),
            self.band_weights,
        )

        raw_score = fuse_scores(
            height_score,
            area_score,
            float(self.config["estimation"]["alpha"]),
            float(self.config["estimation"]["beta"]),
        )
        smoothed_score = self._smooth_score(raw_score)

        label_id = map_score_to_label(
            smoothed_score,
            float(self.config["estimation"]["empty_threshold"]),
            float(self.config["estimation"]["full_threshold"]),
            last_label_id=self.last_label_id,
            hysteresis_margin=float(self.config["smoothing"]["hysteresis_margin"]),
        )
        self.last_label_id = label_id

        confidence = self._calculate_confidence(smoothed_score, area_score, height_score)
        result: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "label_id": label_id,
            "label": LABEL_MAP[label_id],
            "fullness_percent": round(smoothed_score * 100.0, 1),
            "raw_score": round(raw_score, 4),
            "smoothed_score": round(smoothed_score, 4),
            "area_score": round(float(area_score), 4),
            "height_score": round(float(height_score), 4),
            "confidence": round(confidence, 4),
            "changed_area_ratio": round(float(changed_area_ratio), 4),
            "edge_ratio": round(float(edge_ratio), 4),
            "band_occupancy": [round(float(value), 4) for value in band_occupancy],
            "highest_occupied_band": highest_occupied_band,
            "contour_area": round(float(sum(contour_areas)), 2),
        }

        debug_enabled = save_debug if save_debug is not None else bool(self.config["debug"]["enabled"])
        if debug_enabled and self.config["debug"].get("save_outputs", True):
            result["debug"] = save_debug_artifacts(
                self.config,
                current_processed.resized_bgr,
                self.calibration.roi_mask,
                diff_image,
                foreground_mask,
                self.calibration.band_masks,
                band_occupancy,
            )

        return result

    def analyze_once(
        self,
        frame_bgr: np.ndarray | None = None,
        save_debug: bool | None = None,
        source_override: str | None = None,
        input_image: str | None = None,
        input_dir: str | None = None,
    ) -> dict[str, Any]:
        if frame_bgr is not None:
            return self.analyze_frame(frame_bgr, save_debug=save_debug)

        with open_frame_source(
            self.config,
            source_override=source_override,
            input_image=input_image,
            input_dir=input_dir,
        ) as frame_source:
            frame = frame_source.read()
        return self.analyze_frame(frame, save_debug=save_debug)


__all__ = [
    "CaddyFullnessEstimator",
    "ConfigError",
    "CalibrationError",
    "EstimatorError",
    "calculate_band_occupancy",
    "calculate_height_score",
    "fuse_scores",
    "map_score_to_label",
]
