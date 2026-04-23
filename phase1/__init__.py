# Expose only the high-level pipeline API at package level.
# Individual core classes are importable directly from their modules,
# e.g.:  from phase1.core.chunker import SemanticChunker
# Keeping this minimal prevents import-time crashes when optional
# dependencies (fitz, easyocr, arabic_reshaper …) are not yet installed.

from .pipeline import Phase1Pipeline, Phase1Config, Phase1Result  # noqa: F401

__all__ = ["Phase1Pipeline", "Phase1Config", "Phase1Result"]
