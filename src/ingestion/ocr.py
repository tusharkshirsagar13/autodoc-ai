"""OCR support for scanned PDF pages via Tesseract (pytesseract).

Tesseract is a system program, not a Python package. When it is missing we
report that clearly and give platform-specific installation instructions
instead of pretending pages were read.
"""

from __future__ import annotations

import functools
import io
import platform

from src.utils.errors import OCRUnavailableError

INSTALL_HELP = {
    "Windows": "Install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki, then set "
               "TESSERACT_CMD=C:\\Program Files\\Tesseract-OCR\\tesseract.exe in .env.",
    "Darwin": "Run `brew install tesseract`, then restart AutoDoc AI.",
    "Linux": "Run `sudo apt-get install tesseract-ocr` (Debian/Ubuntu) or your distribution's equivalent.",
}


def install_instructions() -> str:
    return INSTALL_HELP.get(platform.system(), INSTALL_HELP["Linux"])


@functools.lru_cache(maxsize=4)
def ocr_status(tesseract_cmd: str = "") -> dict:
    """Return availability information for OCR. Cached per tesseract path."""
    try:
        import pytesseract
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        return {"available": False, "reason": f"Python package missing: {exc.name}. Run `pip install -r requirements.txt`.",
                "version": None}
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    try:
        version = str(pytesseract.get_tesseract_version())
    except Exception:
        return {"available": False, "reason": "Tesseract executable not found. " + install_instructions(),
                "version": None}
    return {"available": True, "reason": "", "version": version}


def ocr_page(page, dpi: int = 200, language: str = "eng", tesseract_cmd: str = "") -> str:
    """Run OCR on a PyMuPDF page and return the recognised text."""
    status = ocr_status(tesseract_cmd)
    if not status["available"]:
        raise OCRUnavailableError("OCR is not available.", status["reason"])
    import pytesseract
    from PIL import Image

    pixmap = page.get_pixmap(dpi=dpi)
    image = Image.open(io.BytesIO(pixmap.tobytes("png")))
    try:
        return pytesseract.image_to_string(image, lang=language, timeout=120)
    except RuntimeError as exc:  # pytesseract raises RuntimeError on timeout
        raise OCRUnavailableError("OCR timed out on a page.", "Try a lower OCR_DPI value.") from exc
    except pytesseract.TesseractError as exc:
        raise OCRUnavailableError(f"Tesseract failed: {exc}",
                                  f"Check that the '{language}' language data is installed.") from exc
