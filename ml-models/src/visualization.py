from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from .configuration import resolve_path

try:
    import cv2
except ImportError:  # pragma: no cover - exercised only in dependency-limited environments
    cv2 = None


def require_cv2() -> None:
    if cv2 is None:
        raise RuntimeError("OpenCV (cv2) is required for debug visualization. Install the project requirements first.")


def _save_image(path: Path, image: np.ndarray) -> str:
    require_cv2()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise RuntimeError(f"Could not save the debug image: {path}")
    return str(path)


def create_roi_overlay(frame_bgr: np.ndarray, roi_mask: np.ndarray, opacity: float = 0.4) -> np.ndarray:
    require_cv2()
    overlay = frame_bgr.copy()
    color_mask = np.zeros_like(frame_bgr)
    color_mask[:, :, 1] = roi_mask
    return cv2.addWeighted(overlay, 1.0, color_mask, opacity, 0)


def create_band_visualization(
    frame_bgr: np.ndarray,
    band_masks: list[np.ndarray],
    band_occupancy: list[float],
) -> np.ndarray:
    require_cv2()
    visualization = frame_bgr.copy()
    palette = [
        (255, 99, 71),
        (255, 165, 0),
        (255, 215, 0),
        (60, 179, 113),
        (70, 130, 180),
        (123, 104, 238),
    ]

    for index, (band_mask, occupancy) in enumerate(zip(band_masks, band_occupancy)):
        contours, _ = cv2.findContours(band_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        color = palette[index % len(palette)]
        cv2.drawContours(visualization, contours, -1, color, 2)

        moments = cv2.moments(band_mask)
        if moments["m00"]:
            center_x = int(moments["m10"] / moments["m00"])
            center_y = int(moments["m01"] / moments["m00"])
            cv2.putText(
                visualization,
                f"B{index}: {occupancy:.2f}",
                (center_x - 30, center_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                color,
                2,
                cv2.LINE_AA,
            )

    return visualization


def create_heatmap_overlay(frame_bgr: np.ndarray, diff_image: np.ndarray, roi_mask: np.ndarray) -> np.ndarray:
    require_cv2()
    masked_diff = cv2.bitwise_and(diff_image, diff_image, mask=roi_mask)
    heatmap = cv2.applyColorMap(masked_diff, cv2.COLORMAP_JET)
    return cv2.addWeighted(frame_bgr, 0.65, heatmap, 0.35, 0)


def save_debug_artifacts(
    config: dict,
    frame_bgr: np.ndarray,
    roi_mask: np.ndarray,
    diff_image: np.ndarray,
    foreground_mask: np.ndarray,
    band_masks: list[np.ndarray],
    band_occupancy: list[float],
) -> dict[str, str]:
    require_cv2()
    debug_root = resolve_path(config["paths"]["debug_dir"])
    debug_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    frame_dir = debug_root / stamp
    opacity = float(config["debug"].get("overlay_opacity", 0.4))

    return {
        "original_frame": _save_image(frame_dir / "frame.jpg", frame_bgr),
        "roi_overlay": _save_image(frame_dir / "roi_overlay.jpg", create_roi_overlay(frame_bgr, roi_mask, opacity)),
        "foreground_mask": _save_image(frame_dir / "foreground_mask.png", foreground_mask),
        "band_visualization": _save_image(
            frame_dir / "band_visualization.jpg",
            create_band_visualization(frame_bgr, band_masks, band_occupancy),
        ),
        "heatmap_overlay": _save_image(
            frame_dir / "heatmap_overlay.jpg",
            create_heatmap_overlay(frame_bgr, diff_image, roi_mask),
        ),
    }
