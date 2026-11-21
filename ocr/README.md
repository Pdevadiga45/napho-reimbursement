# Standalone CPU receipt OCR

`word_total.py` reads a local receipt image and prints JSON. OCR runs on your CPU without uploading the receipt. The web app does not run OCR, and this CLI does not support PDF input.

Run the commands below from `ocr/` with `uv` installed. Model setup downloads three checksum-verified ONNX files into `models/`.

### Windows (PowerShell)

```powershell
uv venv --python 3.14
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
.\.venv\Scripts\python.exe setup_models.py
.\.venv\Scripts\python.exe word_total.py "receipt.jpg" --claim-paise 125000
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

### macOS and Linux

```sh
uv venv --python 3.14
uv pip install --python .venv/bin/python -r requirements.txt
./.venv/bin/python setup_models.py
./.venv/bin/python word_total.py "receipt.jpg" --claim-paise 125000
./.venv/bin/python -m unittest discover -s tests -v
```

Replace `receipt.jpg` with the path to your image. JSON with `"reason": "verified"` includes the detected INR total in `amount_paise`. The optional `--claim-paise` is an integer amount in paise. A verified result includes the advisory `"status": "Unusual amount"` only if the claim exceeds the detected total.

[Evaluation evidence](EVALUATION.md)
