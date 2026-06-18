from __future__ import annotations

import re
import unicodedata

_SUFFIX_PATTERN = re.compile(r"(?:[-/](?:TR|CT|ND|T|T&R|RL|REEL|TAPE))+$", re.IGNORECASE)
_SPACE_PATTERN = re.compile(r"\s+")
_BRACKET_TEXT_PATTERN = re.compile(r"[\(（][^\)）]*[\)）]")
_PRIMARY_DELIMITER_PATTERN = re.compile(r"[,，;；、]")
_CANDIDATE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9+:.#_\-/]*")
_LETTER_PATTERN = re.compile(r"[A-Za-z]")
_DIGIT_PATTERN = re.compile(r"\d")


def clean_mpn(mpn: str | None) -> str:
    if not mpn:
        return ""

    normalized = unicodedata.normalize("NFKC", str(mpn).strip())
    normalized = _BRACKET_TEXT_PATTERN.sub("", normalized)
    primary = _PRIMARY_DELIMITER_PATTERN.split(normalized, maxsplit=1)[0].strip()
    if primary:
        normalized = primary
    normalized = _select_best_mpn_candidate(normalized)
    normalized = _SPACE_PATTERN.sub("", normalized)
    return _SUFFIX_PATTERN.sub("", normalized)


def _select_best_mpn_candidate(value: str) -> str:
    candidates = _candidate_tokens(value)
    if not candidates:
        return value
    return max(candidates, key=_score_candidate)


def _candidate_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for match in _CANDIDATE_PATTERN.finditer(value):
        token = match.group().strip(".,;，；、")
        if token:
            tokens.append(token)
            tokens.extend(part for part in token.split("_") if part)
    return list(dict.fromkeys(tokens))


def _score_candidate(token: str) -> tuple[int, int, int, int, int]:
    has_letter = bool(_LETTER_PATTERN.search(token))
    has_digit = bool(_DIGIT_PATTERN.search(token))
    starts_with_letter = bool(token and token[0].isalpha())
    has_no_underscore = "_" not in token
    has_model_symbols = any(char in token for char in "-+:.#/")
    return (
        1 if has_letter and has_digit else 0,
        1 if starts_with_letter else 0,
        1 if has_no_underscore else 0,
        1 if has_model_symbols else 0,
        len(token),
    )
