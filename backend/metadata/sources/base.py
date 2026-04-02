"""Abstract base class for metadata sources."""

from abc import ABC, abstractmethod
from typing import Any


class MetadataSource(ABC):
    """Base class for all metadata sources.

    Each source must be independent and idempotent.
    Sources are queried in parallel.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique source identifier."""
        ...

    @abstractmethod
    async def search(self, identity: Any, hints: dict | None = None) -> list[dict]:
        """Search for metadata candidates.

        Returns a list of candidate dicts with keys:
            artist, album, year, genre, track_titles (JSON),
            confidence (0-100), source_url, evidence (JSON)
        """
        ...
