"""Bounded validation of untrusted receipt bytes."""

import hashlib
import io
import warnings
import unicodedata
from pathlib import PurePath

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 20_000_000


def validate_receipt(filename: str, content: bytes) -> tuple[str, str]:
    if (not filename or len(filename) > 200 or any(unicodedata.category(c) == 'Cc' or c in '/\\' for c in filename)):
        raise ValueError("Invalid filename")
    extension = PurePath(filename).suffix.lower()
    formats = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".pdf": "PDF"}
    expected = formats.get(extension)
    if expected is None:
        raise ValueError("Unsupported filename extension")
    try:
        if expected == "PDF":
            reader = PdfReader(io.BytesIO(content), strict=True)
            if reader.is_encrypted or len(reader.pages) > 10 or len(reader.pages) == 0:
                raise ValueError("Invalid PDF")
            content_type = "application/pdf"
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    if image.format != expected or image.width * image.height > MAX_PIXELS:
                        raise ValueError("Invalid image format or dimensions")
                    image.load()
            content_type = "image/jpeg" if expected == "JPEG" else "image/png"
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise ValueError("Invalid receipt content") from error
    except Exception as error:
        # PDF parsers can raise several parse-specific exceptions on hostile input.
        raise ValueError("Invalid receipt content") from error
    return content_type, hashlib.sha256(content).hexdigest()
