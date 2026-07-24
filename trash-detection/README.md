# Trash Detection

YOLOv8n floating debris detector developed for the UCF Senior Design project "Trash Crab."

## Hardware
- Luxonis OAK-D Lite
- Raspberry Pi 5

## Model

models/best.pt

Converted model:

models/converted/best.rvc2.tar.xz

## Files

detector.py
- Runs inference using the original YOLO model.

oak_detector_test.py
- Demonstrates deployment on the OAK-D Lite.

convert_model.py
- Converts the YOLO model to an RVC2 archive using HubAI.

## Performance

mAP@0.5 = 87.3%

## Dataset

Training performed using the FloW Dataset (ICCV 2021).