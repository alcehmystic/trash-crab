from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - exercised only in dependency-limited environments
    cv2 = None


@dataclass
class PreprocessedFrame:
    resized_bgr: np.ndarray
    normalized_gray: np.ndarray
    roi_mask: np.ndarray


def require_cv2() -> None:
    if cv2 is None:
        raise RuntimeError("OpenCV (cv2) is required for image preprocessing. Install dependencies from requirements.txt.")


def resize_frame(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    require_cv2()
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)


def ensure_mask_shape(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    require_cv2()
    if mask.shape[:2] == shape:
        return mask
    return cv2.resize(mask, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)


def normalize_frame(frame_bgr: np.ndarray, config: dict) -> np.ndarray:
    require_cv2()
    processing = config["processing"]

    lab_image = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab_image)

    tile_size = int(processing["clahe_tile_grid_size"])
    clahe = cv2.createCLAHE(
        clipLimit=float(processing["clahe_clip_limit"]),
        tileGridSize=(tile_size, tile_size),
    )
    normalized_l = clahe.apply(l_channel)
    merged = cv2.merge((normalized_l, a_channel, b_channel))
    normalized_bgr = cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
    normalized_gray = cv2.cvtColor(normalized_bgr, cv2.COLOR_BGR2GRAY)

    blur_size = int(processing["blur_kernel_size"])
    if blur_size > 1:
        normalized_gray = cv2.GaussianBlur(normalized_gray, (blur_size, blur_size), 0)

    return normalized_gray


def preprocess_frame(frame_bgr: np.ndarray, config: dict, roi_mask: np.ndarray | None = None) -> PreprocessedFrame:
    require_cv2()
    width = int(config["processing"]["width"])
    height = int(config["processing"]["height"])
    resized = resize_frame(frame_bgr, width, height)
    normalized_gray = normalize_frame(resized, config)

    if roi_mask is None:
        resolved_mask = np.full((height, width), 255, dtype=np.uint8)
    else:
        resolved_mask = ensure_mask_shape(roi_mask, normalized_gray.shape)

    masked_gray = cv2.bitwise_and(normalized_gray, normalized_gray, mask=resolved_mask)
    return PreprocessedFrame(resized_bgr=resized, normalized_gray=masked_gray, roi_mask=resolved_mask)


def compute_edge_map(gray_image: np.ndarray, config: dict) -> np.ndarray:
    require_cv2()
    processing = config["processing"]
    return cv2.Canny(
        gray_image,
        threshold1=int(processing["canny_low_threshold"]),
        threshold2=int(processing["canny_high_threshold"]),
    )
