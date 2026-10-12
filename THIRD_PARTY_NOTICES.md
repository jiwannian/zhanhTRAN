# Third-Party Notices

zhanhTRAN first-party code is licensed under GNU AGPL-3.0-only. Copyright (c) 2026 zhanh.

## Bundled code

- `src/vendor/eventsource-parser.js`: eventsource-parser by Espen Hovlandsdal, MIT. The browser export name is adapted for this plugin. Its complete copyright and permission notice is retained in `src/vendor/eventsource-parser.LICENSE.txt` and in the XPI.

## Separately installed Python dependencies

The XPI contains Python scripts, not dependency binaries or OCR model weights. Install dependencies from their published distributions, retaining their bundled notices. Direct dependencies include:

| Dependency | License | Project |
| --- | --- | --- |
| PyMuPDF / MuPDF | AGPL-3.0 or Artifex commercial license | https://github.com/pymupdf/PyMuPDF |
| Pillow | MIT-CMU | https://github.com/python-pillow/Pillow |
| NumPy | BSD-3-Clause, with distribution-specific notices | https://github.com/numpy/numpy |
| RapidOCR ONNX Runtime | Apache-2.0 | https://github.com/RapidAI/RapidOCR |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| img2table | MIT | https://github.com/xavctn/img2table |
| OpenCV Python contrib | Apache-2.0, with bundled third-party notices | https://github.com/opencv/opencv-python |

OCR weights and transitive dependencies have their own notices. This list does not replace those notices or relicense any dependency. PyMuPDF is used under its AGPL option for this open-source distribution.

Development-only dependencies include Playwright (Apache-2.0) and coverage.py (Apache-2.0). Fonts used by tests are supplied by the test environment, not redistributed by this repository.
