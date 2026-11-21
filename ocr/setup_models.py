"""Download the pinned ONNX models for standalone CPU OCR."""

import hashlib
import tempfile
from pathlib import Path
from urllib.request import HTTPRedirectHandler, build_opener


BASE_URL = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/"
MODELS_DIR = Path(__file__).resolve().parent / "models"
TIMEOUT_SECONDS = 30
CHUNK_BYTES = 1024 * 1024
MAX_MODEL_BYTES = 128 * 1024 * 1024
MODELS = (
    (
        "PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
    ),
    (
        "PP-OCRv6/det/PP-OCRv6_det_small.onnx",
        "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    ),
    (
        "PP-OCRv6/rec/PP-OCRv6_rec_small.onnx",
        "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
    ),
)


class HTTPSRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise ValueError("Model download redirected to a non-HTTPS URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_model(opener, model_path, expected_hash):
    destination = MODELS_DIR / Path(model_path).name
    if destination.is_file():
        with destination.open("rb") as existing:
            if hashlib.file_digest(existing, "sha256").hexdigest() == expected_hash:
                print(f"Using {destination.name}")
                return

    temporary_path = None
    digest = hashlib.sha256()
    total_bytes = 0
    try:
        with opener.open(BASE_URL + model_path, timeout=TIMEOUT_SECONDS) as response:
            with tempfile.NamedTemporaryFile(dir=MODELS_DIR, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                while chunk := response.read(CHUNK_BYTES):
                    total_bytes += len(chunk)
                    if total_bytes > MAX_MODEL_BYTES:
                        raise ValueError(f"Download exceeds size limit for {destination.name}")
                    digest.update(chunk)
                    temporary.write(chunk)
        if digest.hexdigest() != expected_hash:
            raise ValueError(f"SHA-256 mismatch for {destination.name}")
        temporary_path.replace(destination)
        print(f"Downloaded {destination.name}")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main():
    MODELS_DIR.mkdir(exist_ok=True)
    opener = build_opener(HTTPSRedirectHandler())
    for model_path, expected_hash in MODELS:
        download_model(opener, model_path, expected_hash)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Model setup failed: {exc}") from exc
