from __future__ import annotations

import time
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - exercised only in dependency-limited environments
    cv2 = None


class CameraError(Exception):
    """The selected frame source could not be opened or read."""


def require_cv2() -> None:
    if cv2 is None:
        raise CameraError("OpenCV (cv2) is required to read and rotate frames. Install the requirements first.")


class BaseFrameSource:
    def read(self) -> np.ndarray:
        raise NotImplementedError

    def close(self) -> None:
        return None

    def __enter__(self) -> "BaseFrameSource":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _rotate_frame(frame: np.ndarray, rotation: int) -> np.ndarray:
    require_cv2()
    rotation = rotation % 360
    if rotation == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if rotation == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if rotation == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


class Picamera2FrameSource(BaseFrameSource):
    def __init__(self, config: dict):
        require_cv2()
        try:
            from picamera2 import Picamera2
        except ImportError as exc:
            raise CameraError(
                "Picamera2 is not available. Install the Raspberry Pi camera stack, or use --source image/directory while developing."
            ) from exc

        capture_resolution = tuple(int(v) for v in config["camera"]["capture_resolution"])
        self.rotation = int(config["camera"].get("rotation", 0))
        self.picam2 = Picamera2()
        camera_config = self.picam2.create_preview_configuration(
            main={"size": capture_resolution, "format": "RGB888"}
        )
        self.picam2.configure(camera_config)
        self.picam2.start()
        time.sleep(float(config["camera"].get("warmup_seconds", 2.0)))

    def read(self) -> np.ndarray:
        frame_rgb = self.picam2.capture_array()
        if frame_rgb is None:
            raise CameraError("Picamera2 returned an empty frame.")
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        return _rotate_frame(frame_bgr, self.rotation)

    def close(self) -> None:
        self.picam2.stop()


class ImageFileFrameSource(BaseFrameSource):
    def __init__(self, image_path: str | Path, rotation: int = 0):
        require_cv2()
        self.image_path = Path(image_path)
        self.rotation = rotation
        if not self.image_path.exists():
            raise CameraError(f"Could not find the input image: {self.image_path}")

        frame = cv2.imread(str(self.image_path))
        if frame is None:
            raise CameraError(f"OpenCV could not read the input image: {self.image_path}")
        self.frame = _rotate_frame(frame, self.rotation)

    def read(self) -> np.ndarray:
        return self.frame.copy()


class DirectoryFrameSource(BaseFrameSource):
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

    def __init__(self, directory_path: str | Path, rotation: int = 0, loop: bool = True):
        require_cv2()
        self.directory_path = Path(directory_path)
        self.rotation = rotation
        self.loop = loop

        if not self.directory_path.exists() or not self.directory_path.is_dir():
            raise CameraError(f"Could not find the input directory: {self.directory_path}")

        self.image_paths = sorted(
            path for path in self.directory_path.iterdir() if path.suffix.lower() in self.IMAGE_EXTENSIONS
        )
        if not self.image_paths:
            raise CameraError(f"No supported image files were found in: {self.directory_path}")
        self.index = 0

    def read(self) -> np.ndarray:
        if self.index >= len(self.image_paths):
            if not self.loop:
                raise CameraError("There are no more images left in the directory frame source.")
            self.index = 0

        image_path = self.image_paths[self.index]
        self.index += 1

        frame = cv2.imread(str(image_path))
        if frame is None:
            raise CameraError(f"OpenCV could not read the input image: {image_path}")
        return _rotate_frame(frame, self.rotation)


def open_frame_source(
    config: dict,
    source_override: str | None = None,
    input_image: str | None = None,
    input_dir: str | None = None,
) -> BaseFrameSource:
    source = source_override or config["camera"]["source"]
    rotation = int(config["camera"].get("rotation", 0))

    if input_image:
        return ImageFileFrameSource(input_image, rotation=rotation)
    if input_dir:
        return DirectoryFrameSource(input_dir, rotation=rotation)

    if source == "picamera2":
        return Picamera2FrameSource(config)
    if source == "image":
        raise CameraError("You selected the image source, but did not pass --input-image.")
    if source == "directory":
        raise CameraError("You selected the directory source, but did not pass --input-dir.")

    raise CameraError(f"Unsupported frame source: {source}")
