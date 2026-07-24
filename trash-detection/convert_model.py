from pathlib import Path
from hubai_sdk import HubAIClient

ROOT = Path(__file__).resolve().parent

MODEL_PATH = ROOT / "models" / "best.pt"
OUTPUT_DIR = ROOT / "models" / "converted"

if not MODEL_PATH.exists():
    raise FileNotFoundError(f"Model not found: {MODEL_PATH}")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

client = HubAIClient()

response = client.convert.RVC2(
    path=str(MODEL_PATH),
    name="trash-crab-yolov8n",
    output_dir=str(OUTPUT_DIR),

    # Your training configuration
    yolo_version="yolov8",
    yolo_input_shape=[640, 640],

    # Change this to the exact class name from your data.yaml
    yolo_class_names=["waste"],

    # OAK-D Lite / RVC2 options
    number_of_shaves=6,
    compress_to_fp16=True,
    superblob=True,
)

print(f"Converted model saved to: {response.downloaded_path}")