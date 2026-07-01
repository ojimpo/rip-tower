"""Safe access to MetadataCandidate.evidence JSON.

Evidence is stored as a JSON text column and read all over the resolver,
sanitizer, and kashidashi cross-check. This helper centralizes the
parse-or-empty-dict pattern so callers never trip on malformed rows.
"""

import json
from typing import Any


def parse_evidence(candidate: Any) -> dict:
    """Return the candidate's evidence as a dict; {} when absent or malformed."""
    raw = getattr(candidate, "evidence", None)
    if not raw:
        return {}
    try:
        ev = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return ev if isinstance(ev, dict) else {}
