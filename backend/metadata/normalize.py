"""Shared normalization functions for metadata matching.

Ported from ~/dev/openclaw-cd-rip/scripts/normalize.py.
Consolidates norm/similarity/disc-info logic used across metadata sources.
"""

from __future__ import annotations

import re
import unicodedata

# ── Disc pattern ──

DISC_PAT = re.compile(
    r"[\s\-_]*(?:\[?\s*(?:disc|cd)\s*[-_\s]*(\d+)\s*\]?)",
    re.IGNORECASE,
)


def norm(s: str) -> str:
    """Normalize text for fuzzy matching.

    - Fullwidth -> halfwidth (NFKC)
    - Lowercase
    - Strip disc/cd suffixes
    - Collapse to alphanum + hiragana + kanji
    - Katakana -> hiragana
    """
    x = (s or "").strip()
    # Fullwidth -> halfwidth
    x = unicodedata.normalize("NFKC", x)
    x = x.lower()
    # Normalize wave dash variants
    x = x.replace("\u301c", "~").replace("\uff5e", "~").replace("\u3000", " ")
    # Strip disc/cd markers
    x = DISC_PAT.sub(" ", x)
    # Collapse to alphanum + hiragana + katakana + kanji
    x = re.sub(r"[^0-9a-z\u3041-\u3093\u30a1-\u30f6\u4e00-\u9fff]+", "", x)
    # Katakana (U+30A1..U+30F6) -> hiragana (U+3041..U+3096)
    x = "".join(
        chr(ord(c) - 0x60) if "\u30a1" <= c <= "\u30f6" else c for c in x
    )
    return x


def similarity(a: str, b: str) -> float:
    """Substring-based similarity between two strings (after normalization)."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.8
    common = sum(1 for c in na if c in nb)
    return common / max(len(na), len(nb))


def extract_disc_info(album: str) -> tuple[str, int | None]:
    """Extract base album name and disc number.

    Returns:
        (base_album, disc_number) -- disc_number is None if not found.

    Examples:
        "Album DISC 1" -> ("Album", 1)
        "Album [CD2]"  -> ("Album", 2)
        "Album"        -> ("Album", None)
    """
    m = DISC_PAT.search(album)
    if not m:
        return album.strip(), None
    base = DISC_PAT.sub("", album).strip(" -_[]()")
    disc_num = int(m.group(1))
    return base or album.strip(), disc_num


def detect_disc_hint(album: str) -> bool:
    """Check if album name contains a disc number pattern."""
    return bool(DISC_PAT.search(album))


def normalize_album_base(album: str) -> str:
    """Strip disc suffix to get base album name."""
    base, _ = extract_disc_info(album)
    return base


# ── Fullwidth/halfwidth helpers ──

def fullwidth_to_halfwidth(s: str) -> str:
    """Convert fullwidth ASCII characters to halfwidth."""
    return unicodedata.normalize("NFKC", s)


# ── Various Artists normalization ──

_VARIOUS_PATTERNS = re.compile(
    r"^(?:various\s*artists?|v\.?\s*a\.?|various)$",
    re.IGNORECASE,
)


def normalize_various_artists(artist: str) -> str:
    """Normalize Various Artists variants to canonical form."""
    if _VARIOUS_PATTERNS.match(artist.strip()):
        return "Various Artists"
    return artist
