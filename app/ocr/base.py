"""OCR adapter interface.

Every OCR backend (local Tesseract today; a cloud OCR API or an LLM-based
extractor later) implements this same interface, so swapping the backend
never touches ingestion, parsing, or any rule engine downstream.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class OCRWord:
    text: str
    confidence: float  # 0-100, native Tesseract scale
    left: int
    top: int
    width: int
    height: int


@dataclass
class OCRResult:
    text: str
    confidence: float  # 0-100 overall average confidence
    words: list[OCRWord] = field(default_factory=list)


class OCRAdapter(ABC):
    @abstractmethod
    def extract(self, image_path: Path) -> OCRResult:
        """Runs OCR over a single image and returns text + confidence + word boxes."""
        raise NotImplementedError
