"""
Example showing how to run the converted RVC2 model
on a Luxonis OAK-D Lite using DepthAI.
"""

from pathlib import Path
import time

import cv2
import depthai as dai
import numpy as np



MODEL_PATH = (
    Path(__file__).resolve().parent
    / "models"
    / "converted"
    / "best.rvc2.tar.xz"
)


def frame_norm(frame, bbox):
    """Convert normalized box coordinates into pixel coordinates."""
    norm_values = np.full(len(bbox), frame.shape[0])
    norm_values[::2] = frame.shape[1]

    return (
        np.clip(np.array(bbox), 0, 1) * norm_values
    ).astype(int)


if not MODEL_PATH.exists():
    raise FileNotFoundError(f"Model archive not found: {MODEL_PATH}")


with dai.Pipeline() as pipeline:
    # Build the OAK-D Lite color camera.
    camera = pipeline.create(dai.node.Camera).build(
        dai.CameraBoardSocket.CAM_A
    )

    # Load the converted NN Archive.
    nn_archive = dai.NNArchive(str(MODEL_PATH))

    # Camera frames feed directly into the on-device detection network.
    detection_network = pipeline.create(
        dai.node.DetectionNetwork
    ).build(camera, nn_archive)

    detection_network.setConfidenceThreshold(0.4)
    detection_network.input.setBlocking(False)

    # Passthrough contains the exact frame used for inference.
    frame_queue = (
        detection_network.passthrough.createOutputQueue(
            maxSize=4,
            blocking=False,
        )
    )

    detection_queue = (
        detection_network.out.createOutputQueue(
            maxSize=4,
            blocking=False,
        )
    )

    labels = detection_network.getClasses()

    pipeline.start()

    frame = None
    detections = []
    detection_count = 0
    start_time = time.monotonic()

    while pipeline.isRunning():
        frame_message = frame_queue.tryGet()
        detection_message = detection_queue.tryGet()

        if frame_message is not None:
            frame = frame_message.getCvFrame()

        if detection_message is not None:
            detections = detection_message.detections
            detection_count += 1

        if frame is not None:
            for detection in detections:
                x1, y1, x2, y2 = frame_norm(
                    frame,
                    [
                        detection.xmin,
                        detection.ymin,
                        detection.xmax,
                        detection.ymax,
                    ],
                )

                class_id = int(detection.label)

                if 0 <= class_id < len(labels):
                    class_name = labels[class_id]
                else:
                    class_name = str(class_id)

                confidence = float(detection.confidence)

                cv2.rectangle(
                    frame,
                    (x1, y1),
                    (x2, y2),
                    (0, 255, 0),
                    2,
                )

                cv2.putText(
                    frame,
                    f"{class_name} {confidence:.2f}",
                    (x1, max(y1 - 10, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )

            elapsed = time.monotonic() - start_time
            fps = detection_count / elapsed if elapsed > 0 else 0

            cv2.putText(
                frame,
                f"NN FPS: {fps:.1f}",
                (10, frame.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                1,
            )

            cv2.imshow("OAK-D Lite Trash Detection", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            pipeline.stop()
            break

cv2.destroyAllWindows()