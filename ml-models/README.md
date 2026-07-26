# trash-crab
Computer Science Senior Design Project - Low Cost Marine "Roomba"-Style Cleanup ASV

## ml-models

Two independent perception subsystems live here:

| Subsystem | Looks at | Approach | Docs |
|---|---|---|---|
| [`trash-detection/`](trash-detection/) | Outward, at the water | YOLOv8n on the OAK-D Lite | [trash-detection/README.md](trash-detection/README.md) |
| [`src/`](src/) | Inward, at the basket | Classical CV, no neural net | [algoREADME.md](algoREADME.md) |

They have separate dependency sets and separate virtualenvs — `trash-detection/` wants `torch` and
`depthai`, the fullness estimator wants `picamera2`. Do not install them into the same environment.
