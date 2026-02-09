"""Language detection, grammar loading, and extractor registry."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import LanguageExtractor

# Map file extension -> (tree-sitter language name, extractor language key)
_EXTENSION_MAP: dict[str, str] = {
    ".vue": "vue",
    ".svelte": "svelte",
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".hh": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "c_sharp",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".sc": "scala",
}

# Languages with dedicated extractors
_DEDICATED_EXTRACTORS = frozenset({
    "python", "javascript", "typescript", "tsx",
    "go", "rust", "java", "c", "cpp",
})

# All supported tree-sitter language names
_SUPPORTED_LANGUAGES = frozenset({
    "python", "javascript", "typescript", "tsx",
    "go", "rust", "java", "c", "cpp",
    "ruby", "php", "c_sharp", "kotlin", "swift", "scala",
    "vue", "svelte",
})


def get_language_for_file(path: str) -> str | None:
    """Determine the language for a file based on its extension.

    Returns the language name string, or None if unsupported.
    """
    _, ext = os.path.splitext(path)
    ext = ext.lower()
    return _EXTENSION_MAP.get(ext)


def get_ts_language(language: str):
    """Get a tree-sitter Language object from tree_sitter_language_pack.

    Args:
        language: Language name (e.g. 'python', 'javascript', 'c_sharp')

    Returns:
        A tree-sitter Language object ready for use with Parser.

    Raises:
        ValueError: If the language is not supported.
        ImportError: If tree_sitter_language_pack is not installed.
    """
    if language not in _SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {language}")

    from tree_sitter_language_pack import get_language
    return get_language(language)


@lru_cache(maxsize=None)
def _create_extractor(language: str) -> "LanguageExtractor":
    """Create and cache an extractor instance for a language."""
    if language == "python":
        from .python_lang import PythonExtractor
        return PythonExtractor()
    elif language == "javascript":
        from .javascript_lang import JavaScriptExtractor
        return JavaScriptExtractor()
    elif language in ("typescript", "tsx", "vue", "svelte"):
        from .typescript_lang import TypeScriptExtractor
        return TypeScriptExtractor()
    elif language == "go":
        from .go_lang import GoExtractor
        return GoExtractor()
    elif language == "rust":
        from .rust_lang import RustExtractor
        return RustExtractor()
    elif language == "java":
        from .java_lang import JavaExtractor
        return JavaExtractor()
    elif language == "c":
        from .c_lang import CExtractor
        return CExtractor()
    elif language == "cpp":
        from .c_lang import CppExtractor
        return CppExtractor()
    else:
        # Use generic extractor for tier-2 languages
        from .generic_lang import GenericExtractor
        return GenericExtractor(language=language)


def get_extractor(language: str) -> "LanguageExtractor":
    """Get an extractor instance for a language.

    Returns a dedicated extractor for tier-1 languages, or a GenericExtractor
    for tier-2 languages (Ruby, PHP, C#, Kotlin, Swift, Scala).

    Args:
        language: Language name string.

    Returns:
        A LanguageExtractor instance.

    Raises:
        ValueError: If the language is not supported.
    """
    if language not in _SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {language}")
    return _create_extractor(language)


def get_extractor_for_file(path: str) -> "LanguageExtractor | None":
    """Get an extractor instance for a file based on its extension.

    Returns None if the file type is not supported.
    """
    language = get_language_for_file(path)
    if language is None:
        return None
    return _create_extractor(language)


def get_supported_extensions() -> list[str]:
    """Return all supported file extensions."""
    return sorted(_EXTENSION_MAP.keys())


def get_supported_languages() -> list[str]:
    """Return all supported language names."""
    return sorted(_SUPPORTED_LANGUAGES)
