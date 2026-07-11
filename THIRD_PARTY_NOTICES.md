# Third-party models

The Chinese license-plate detector and recognizer are derived from:

- https://github.com/we0091234/Chinese_license_plate_detection_recognition
- Its current upstream successor: https://github.com/we0091234/yolo26-plate

Included model files:

- `weights/yolo26s-plate-detect.pt`
- `weights/plate_rec_color.pth`

The model architecture, model files, and adapted inference logic are covered by
the upstream AGPL-3.0 license. A copy is stored at
`third_party/Chinese_license_plate_detection_recognition.LICENSE`.

Projects that distribute or provide network access to this application must
review the AGPL-3.0 obligations and independently verify the licensing of the
training data and model weights.
