"""Application-specific exceptions with user-facing recovery hints."""

from __future__ import annotations


class AutoDocError(Exception):
    """Base error. `hint` is a short, actionable message safe to show users."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def user_message(self) -> str:
        return f"{self.message} {self.hint}" if self.hint else self.message


class ValidationError(AutoDocError):
    """Uploaded file or user input failed validation."""


class ExtractionError(AutoDocError):
    """Document text could not be extracted."""


class InsufficientTextError(ExtractionError):
    """The document has too little machine-readable text (likely scanned)."""


class OCRUnavailableError(AutoDocError):
    """OCR was requested but Tesseract / pytesseract is unavailable."""


class EmbeddingError(AutoDocError):
    """The embedding model could not be loaded or used."""


class LLMNotConfiguredError(AutoDocError):
    """No language model provider has been configured."""


class LLMProviderError(AutoDocError):
    """The configured LLM provider failed (network, auth, rate limit, timeout)."""


class NotFoundError(AutoDocError):
    """A requested record does not exist."""
