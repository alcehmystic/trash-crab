from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - exercised only in dependency-limited environments
    cv2 = None

from .configuration import resolve_path
from .preprocessing import preprocess_frame, resize_frame


class CalibrationError(Exception):
    """Something went wrong with calibration or the saved ROI files."""


def require_cv2() -> None:
    if cv2 is None:
        raise CalibrationError("OpenCV (cv2) is required for calibration. Install the project requirements first.")


@dataclass
class CalibrationBundle:
    empty_reference_bgr: np.ndarray | None
    roi_mask: np.ndarray
    calibration_data: dict[str, Any]
    band_masks: list[np.ndarray]
    band_metadata: list[dict[str, Any]]


def build_band_masks(roi_mask: np.ndarray, band_count: int) -> tuple[list[np.ndarray], list[dict[str, Any]]]:
    require_cv2()
    if band_count <= 0:
        raise CalibrationError("Band count has to be greater than zero.")

    occupied_rows = np.where(np.any(roi_mask > 0, axis=1))[0]
    if occupied_rows.size == 0:
        raise CalibrationError("The ROI mask is empty. Re-run calibration and pick a real basket region.")

    top_row = int(occupied_rows.min())
    bottom_row = int(occupied_rows.max()) + 1
    row_edges = np.linspace(top_row, bottom_row, band_count + 1, dtype=int)

    band_masks: list[np.ndarray] = []
    metadata: list[dict[str, Any]] = []
    for index in range(band_count):
        start_row = int(row_edges[index])
        end_row = int(row_edges[index + 1])
        band_slice_mask = np.zeros_like(roi_mask, dtype=np.uint8)
        band_slice_mask[start_row:end_row, :] = 255
        band_mask = cv2.bitwise_and(roi_mask, band_slice_mask)
        band_masks.append(band_mask)
        metadata.append(
            {
                "band_index": index,
                "row_start": start_row,
                "row_end": end_row,
                "roi_pixels": int(np.count_nonzero(band_mask)),
            }
        )

    return band_masks, metadata


def _build_mask_from_polygon(frame_shape: tuple[int, int, int], points: list[tuple[int, int]]) -> np.ndarray:
    require_cv2()
    mask = np.zeros(frame_shape[:2], dtype=np.uint8)
    polygon = np.array(points, dtype=np.int32)
    cv2.fillPoly(mask, [polygon], 255)
    return mask


def _select_polygon_roi(frame_bgr: np.ndarray) -> np.ndarray:
    require_cv2()
    window_name = "Polygon ROI Calibration"
    points: list[tuple[int, int]] = []
    preview = frame_bgr.copy()

    def on_mouse(event, x, y, _flags, _param) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append((x, y))

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window_name, on_mouse)

    while True:
        preview[:] = frame_bgr
        if points:
            for point in points:
                cv2.circle(preview, point, 4, (0, 255, 0), -1)
            if len(points) > 1:
                cv2.polylines(preview, [np.array(points, dtype=np.int32)], False, (0, 255, 0), 2)

        cv2.putText(
            preview,
            "Left click: add points | Enter/Space: finish | r: reset | Esc: cancel",
            (10, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.imshow(window_name, preview)
        key = cv2.waitKey(20) & 0xFF

        if key in (13, 32):
            if len(points) < 3:
                raise CalibrationError("Polygon calibration needs at least three points.")
            cv2.destroyWindow(window_name)
            return _build_mask_from_polygon(frame_bgr.shape, points)
        if key == ord("r"):
            points.clear()
        if key == 27:
            cv2.destroyWindow(window_name)
            raise CalibrationError("Calibration was canceled.")


def _select_rectangle_roi(frame_bgr: np.ndarray) -> np.ndarray:
    require_cv2()
    window_name = "Rectangle ROI Calibration"
    rect = cv2.selectROI(window_name, frame_bgr, fromCenter=False, showCrosshair=True)
    cv2.destroyWindow(window_name)
    x, y, width, height = [int(v) for v in rect]
    if width <= 0 or height <= 0:
        raise CalibrationError("Rectangle calibration was canceled or produced an empty ROI.")

    mask = np.zeros(frame_bgr.shape[:2], dtype=np.uint8)
    mask[y : y + height, x : x + width] = 255
    return mask


def _manual_roi_mask(frame_shape: tuple[int, int, int], manual_roi: dict[str, Any]) -> np.ndarray:
    require_cv2()
    roi_type = manual_roi.get("type")
    if roi_type == "rectangle":
        x = int(manual_roi["x"])
        y = int(manual_roi["y"])
        width = int(manual_roi["width"])
        height = int(manual_roi["height"])
        if width <= 0 or height <= 0:
            raise CalibrationError("A manual rectangle ROI needs a positive width and height.")
        mask = np.zeros(frame_shape[:2], dtype=np.uint8)
        mask[y : y + height, x : x + width] = 255
        return mask

    if roi_type == "polygon":
        points = [tuple(int(v) for v in point) for point in manual_roi["points"]]
        if len(points) < 3:
            raise CalibrationError("A manual polygon ROI needs at least three points.")
        return _build_mask_from_polygon(frame_shape, points)

    raise CalibrationError("Manual ROI type must be either 'rectangle' or 'polygon'.")


def _save_image(path: Path, image: np.ndarray) -> None:
    require_cv2()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise CalibrationError(f"Could not save the image: {path}")


def run_calibration(
    frame_bgr: np.ndarray,
    config: dict,
    use_polygon: bool = True,
    headless: bool = False,
) -> dict[str, Any]:
    require_cv2()
    width = int(config["processing"]["width"])
    height = int(config["processing"]["height"])
    calibration_config = config["calibration"]
    paths = config["paths"]

    processed_frame = resize_frame(frame_bgr, width, height)
    manual_roi = calibration_config.get("manual_roi")

    if manual_roi:
        roi_mask = _manual_roi_mask(processed_frame.shape, manual_roi)
    else:
        try:
            if headless:
                raise CalibrationError("Headless calibration needs calibration.manual_roi in the config.")
            roi_mask = _select_polygon_roi(processed_frame) if use_polygon else _select_rectangle_roi(processed_frame)
        except (CalibrationError, cv2.error):
            if use_polygon and calibration_config.get("fallback_to_rectangle", True) and not headless:
                roi_mask = _select_rectangle_roi(processed_frame)
            else:
                raise

    if np.count_nonzero(roi_mask) == 0:
        raise CalibrationError("The selected ROI was empty. Re-run calibration and select the basket interior.")

    band_count = int(config["estimation"]["band_count"])
    band_masks, band_metadata = build_band_masks(roi_mask, band_count)

    empty_reference_path = resolve_path(paths["empty_reference_image"])
    roi_mask_path = resolve_path(paths["roi_mask"])
    calibration_data_path = resolve_path(paths["calibration_data"])

    _save_image(empty_reference_path, processed_frame)
    _save_image(roi_mask_path, roi_mask)

    calibration_data = {
        "processing_resolution": {"width": width, "height": height},
        "band_count": band_count,
        "band_metadata": band_metadata,
        "roi_pixels": int(np.count_nonzero(roi_mask)),
    }

    calibration_data_path.parent.mkdir(parents=True, exist_ok=True)
    calibration_data_path.write_text(json.dumps(calibration_data, indent=2), encoding="utf-8")

    if calibration_config.get("save_debug_preview", True):
        overlay = processed_frame.copy()
        overlay_mask = np.zeros_like(processed_frame)
        overlay_mask[:, :, 1] = roi_mask
        overlay = cv2.addWeighted(overlay, 1.0, overlay_mask, 0.35, 0)
        _save_image(resolve_path(paths["calibration_dir"]) / "roi_preview.jpg", overlay)

    return {
        "status": "calibrated",
        "empty_reference_image": str(empty_reference_path),
        "roi_mask": str(roi_mask_path),
        "calibration_data": str(calibration_data_path),
        "band_count": band_count,
        "roi_pixels": int(np.count_nonzero(roi_mask)),
    }


def _load_calibration_data(calibration_data_path: Path) -> dict[str, Any]:
    if not calibration_data_path.exists():
        raise CalibrationError(
            f"Could not find calibration data at {calibration_data_path}. Run `python -m src.main calibrate` first."
        )

    try:
        calibration_data = json.loads(calibration_data_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"The calibration JSON at {calibration_data_path} could not be parsed: {exc}") from exc

    if not isinstance(calibration_data, dict):
        raise CalibrationError(f"Calibration data in {calibration_data_path} must be a JSON object.")
    return calibration_data


def _validate_calibration_compatibility(
    calibration_data: dict[str, Any],
    width: int,
    height: int,
    band_count: int,
) -> None:
    processing_resolution = calibration_data.get("processing_resolution")
    if processing_resolution != {"width": width, "height": height}:
        raise CalibrationError(
            "The saved calibration resolution does not match the active config. Re-run `python -m src.main calibrate` "
            "for this processing resolution."
        )

    saved_band_count = calibration_data.get("band_count")
    if saved_band_count != band_count:
        raise CalibrationError(
            "The saved calibration band count does not match the active config. Re-run `python -m src.main calibrate` "
            "after changing estimation.band_count."
        )


def _load_roi_assets(config: dict) -> tuple[dict[str, Any], np.ndarray, list[np.ndarray], list[dict[str, Any]]]:
    require_cv2()
    paths = config["paths"]
    roi_mask_path = resolve_path(paths["roi_mask"])
    calibration_data_path = resolve_path(paths["calibration_data"])

    if not roi_mask_path.exists():
        raise CalibrationError(f"Could not find the ROI mask at {roi_mask_path}. Run `python -m src.main calibrate` first.")

    calibration_data = _load_calibration_data(calibration_data_path)
    roi_mask = cv2.imread(str(roi_mask_path), cv2.IMREAD_GRAYSCALE)

    if roi_mask is None:
        raise CalibrationError(f"OpenCV could not read the ROI mask image: {roi_mask_path}")

    width = int(config["processing"]["width"])
    height = int(config["processing"]["height"])
    band_count = int(config["estimation"]["band_count"])
    _validate_calibration_compatibility(calibration_data, width, height, band_count)

    if roi_mask.shape[:2] != (height, width):
        raise CalibrationError(
            "The saved ROI mask resolution does not match the active config. Re-run `python -m src.main calibrate`."
        )

    expected_roi_pixels = calibration_data.get("roi_pixels")
    if expected_roi_pixels is not None and int(expected_roi_pixels) != int(np.count_nonzero(roi_mask)):
        raise CalibrationError("The saved ROI mask does not match the stored calibration metadata. Re-run calibration.")

    band_masks, band_metadata = build_band_masks(roi_mask, band_count)
    return calibration_data, roi_mask, band_masks, band_metadata


def refresh_empty_reference(frame_source: Any, config: dict) -> dict[str, Any]:
    require_cv2()
    calibration_data, roi_mask, _, _ = _load_roi_assets(config)
    mission_config = config["mission"]
    reference_frame_count = int(mission_config["reference_frame_count"])
    settle_frames = int(mission_config.get("reference_settle_frames", 0))
    width = int(config["processing"]["width"])
    height = int(config["processing"]["height"])

    for _ in range(settle_frames):
        frame_source.read()

    captured_frames: list[np.ndarray] = []
    interframe_roi_diffs: list[float] = []
    previous_processed = None
    roi_selector = roi_mask > 0

    for _ in range(reference_frame_count):
        frame_bgr = frame_source.read()
        captured_frames.append(resize_frame(frame_bgr, width, height))

        processed = preprocess_frame(frame_bgr, config, roi_mask=roi_mask)
        if previous_processed is not None:
            diff_image = cv2.absdiff(processed.normalized_gray, previous_processed.normalized_gray)
            roi_values = diff_image[roi_selector]
            interframe_roi_diffs.append(float(roi_values.mean()) if roi_values.size else 0.0)
        previous_processed = processed

    reference_image = np.median(np.stack(captured_frames, axis=0), axis=0).astype(np.uint8)
    empty_reference_path = resolve_path(config["paths"]["empty_reference_image"])
    _save_image(empty_reference_path, reference_image)

    calibration_data_path = resolve_path(config["paths"]["calibration_data"])
    updated_calibration_data = dict(calibration_data)
    updated_calibration_data["reference_updated_at"] = f"{datetime.utcnow().isoformat(timespec='seconds')}Z"
    calibration_data_path.write_text(json.dumps(updated_calibration_data, indent=2), encoding="utf-8")

    return {
        "status": "reference_refreshed",
        "empty_reference_image": str(empty_reference_path),
        "captured_frames": reference_frame_count,
        "settle_frames": settle_frames,
        "mean_interframe_roi_diff": round(float(np.mean(interframe_roi_diffs)), 4) if interframe_roi_diffs else 0.0,
    }


def load_calibration_bundle(config: dict, require_reference: bool = True) -> CalibrationBundle:
    require_cv2()
    paths = config["paths"]
    empty_reference_path = resolve_path(paths["empty_reference_image"])
    calibration_data, roi_mask, band_masks, band_metadata = _load_roi_assets(config)

    empty_reference_bgr: np.ndarray | None = None
    if require_reference:
        if not empty_reference_path.exists():
            raise CalibrationError(
                f"Could not find the empty reference image at {empty_reference_path}. Capture one with "
                "`python -m src.main refresh-reference` or `python -m src.main run`."
            )

        empty_reference_bgr = cv2.imread(str(empty_reference_path))
        if empty_reference_bgr is None:
            raise CalibrationError(f"OpenCV could not read the empty reference image: {empty_reference_path}")

        width = int(config["processing"]["width"])
        height = int(config["processing"]["height"])
        if empty_reference_bgr.shape[:2] != (height, width):
            raise CalibrationError(
                "The saved empty reference resolution does not match the active config. Capture a fresh reference "
                "before running the estimator."
            )

    return CalibrationBundle(
        empty_reference_bgr=empty_reference_bgr,
        roi_mask=roi_mask,
        calibration_data=calibration_data,
        band_masks=band_masks,
        band_metadata=band_metadata,
    )
