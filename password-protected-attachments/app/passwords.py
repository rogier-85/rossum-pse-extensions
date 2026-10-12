"""Extract explicitly labelled password candidates from email bodies."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

MAX_CANDIDATES = 20
MAX_PASSWORD_LENGTH = 128

_PASSWORD_PATTERN = re.compile(
    rf"""
    \b(?:password|passcode|pwd|pin|code)\b
    \s*(?:is\b\s*|[:=\-]\s*|\s+)
    (?:
        "(?P<double>[^"\r\n]{{1,{MAX_PASSWORD_LENGTH}}})"
        |'(?P<single>[^'\r\n]{{1,{MAX_PASSWORD_LENGTH}}})'
        |(?P<bare>\S{{1,{MAX_PASSWORD_LENGTH}}})
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def extract_password_candidates(plain_text: str | None, html_text: str | None) -> list[str]:
    """Return unique labelled candidates in their original order and casing."""
    sources: list[str] = []
    if plain_text:
        sources.append(plain_text)
    if html_text:
        sources.append(BeautifulSoup(html_text, "html.parser").get_text("\n"))

    candidates: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for match in _PASSWORD_PATTERN.finditer(source):
            candidate = next(value for value in match.groupdict().values() if value is not None)
            if candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
                if len(candidates) == MAX_CANDIDATES:
                    return candidates
    return candidates
