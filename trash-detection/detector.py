from pathlib import Path
from ultralytics import YOLO


class TrashDetector:
    def __init__(self, model_path=None, conf=0.4):
        #load model
        if model_path is None:
            model_path = Path(__file__).parent / "models" / "best.pt"

        self.model = YOLO(str(model_path))
        self.conf = conf

    #run inference on a single frame
    def detect(self, frame):
        results = self.model(frame, conf=self.conf, verbose=False)

        detections = []

        for result in results:
            for box in result.boxes:
                class_id = int(box.cls[0])

                x1, y1, x2, y2 = box.xyxy[0]

                detections.append({
                    "class_id": class_id,
                    "class_name": self.model.names[class_id],
                    "confidence": float(box.conf[0]),
                    "bbox": [float(x1), float(y1), float(x2), float(y2)],
                    "center": [
                        float((x1 + x2) / 2),
                        float((y1 + y2) / 2)
                    ]
                })

        return detections