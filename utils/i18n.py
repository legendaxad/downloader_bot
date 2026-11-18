"""Tiny i18n layer: JSON catalogs in ``locales/<lang>.json``.

``t(key, lang, **params)`` returns the translated, formatted string, falling
back to English (and finally to the key itself) so a missing translation never
breaks a message.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from utils.logger import logger

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
DEFAULT_LANGUAGE = "en"

# Order = order of buttons in the language picker.
LANGUAGES: dict[str, str] = {
    "en": "🇬🇧 English",
    "ru": "🇷🇺 Русский",
    "uz": "🇺🇿 Oʻzbekcha",
    "ko": "🇰🇷 한국어",
}


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{lang}.json"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def normalize_language(code: str | None) -> str:
    """Map anything (``"ru-RU"``, ``None``, ``"de"``) to a supported language code."""
    if not code:
        return DEFAULT_LANGUAGE
    short = code.lower().split("-")[0].split("_")[0]
    return short if short in LANGUAGES else DEFAULT_LANGUAGE


def t(key: str, lang: str | None = DEFAULT_LANGUAGE, **params: object) -> str:
    lang = normalize_language(lang)
    template = catalog(lang).get(key) or catalog(DEFAULT_LANGUAGE).get(key)
    if template is None:
        logger.warning("Missing translation key: %s", key)
        return key
    try:
        return template.format(**params) if params else template
    except (KeyError, IndexError, ValueError):
        logger.warning("Bad translation params for %s/%s", lang, key)
        return catalog(DEFAULT_LANGUAGE).get(key, key)
