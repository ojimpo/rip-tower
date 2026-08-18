"""CDDB metadata source — queries GnuDB for disc ID lookup.

Speaks the CDDB HTTP protocol (cddb query / cddb read) against the server
configured in integrations.gnudb_url. Note: GnuDB serves /~cddb/* over
plain HTTP only — https returns 404 — so the configured URL must use the
http scheme.

Ported from ~/dev/openclaw-cd-rip/scripts/metadata_resolver.py (_cddb_lookup).
"""

import json
import logging
import urllib.parse
from typing import Any

import httpx

from backend.config import get_config
from backend.metadata.sources.base import MetadataSource

logger = logging.getLogger(__name__)

CDDB_HELLO = "kouki arigato-nas rip-tower 0.1"

# How many query matches to follow up with a `cddb read`. Multi-match
# responses list the same disc under several categories; a couple of reads
# is enough to surface the real record without hammering the server.
MAX_MATCHES = 3

# Confidence levels. A record whose frame offsets match the physical disc's
# TOC is proven by the disc itself, so it must outrank MusicBrainz's *fuzzy*
# TOC-submission matches (confidence 90, tolerating offsets thousands of
# frames off) — a flat 60 let an unrelated MB fuzzy match beat GnuDB entries
# that agreed with the disc frame-for-frame (Todoist 6hHpxQm5p2RphP9m).
# Records we can't verify (offsets missing or diverging) keep the old 60:
# disc IDs collide across unrelated discs, so an unverified match is decent
# evidence but not proof.
_CONF_TOC_VERIFIED = 92
_CONF_UNVERIFIED = 60

# Max per-track drift (frames, 75/sec) still counted as the same TOC after
# removing the lead-in shift. Same-pressing submissions differ by at most a
# few frames; a colliding entry for a different disc is off by thousands.
_TOC_DRIFT_TOLERANCE = 150


class CddbSource(MetadataSource):
    @property
    def name(self) -> str:
        return "cddb"

    async def search(self, identity: Any, hints: dict | None = None) -> list[dict]:
        """Query CDDB via GnuDB using disc ID and TOC."""
        if not identity or not identity.disc_id:
            return []
        if not identity.offsets or not identity.leadout:
            return []

        disc_id = identity.disc_id
        track_count = identity.track_count
        offsets = identity.offsets
        duration_secs = identity.leadout

        # Step 1: CDDB query — find matching category/disc ID pairs
        try:
            query_resp = await self._cddb_request(
                f"cddb query {disc_id} {track_count} "
                f"{' '.join(map(str, offsets))} {duration_secs}"
            )
        except Exception:
            logger.exception("CDDB query failed for disc_id=%s", disc_id)
            return []

        matches = self._parse_query_response(query_resp, disc_id)

        candidates: list[dict] = []
        for cat, did in matches[:MAX_MATCHES]:
            # Step 2: CDDB read — fetch full record
            try:
                read_resp = await self._cddb_request(f"cddb read {cat} {did}")
            except Exception:
                logger.exception("CDDB read failed for %s/%s", cat, did)
                continue
            candidate = self._parse_read_response(
                read_resp, cat, did, track_count, offsets
            )
            if candidate:
                candidates.append(candidate)

        return candidates

    @staticmethod
    def _parse_query_response(resp: str, disc_id: str) -> list[tuple[str, str]]:
        """Extract (category, discid) matches from a `cddb query` response.

        Response codes:
          200 <cat> <discid> <title>  — single exact match, inline
          210 / 211                   — exact / inexact matches on the
                                        following lines, terminated by "."
          202                         — no match
        """
        lines = [line.strip() for line in resp.splitlines() if line.strip()]
        if not lines:
            return []

        head = lines[0].split(" ", 3)
        code = head[0]

        if code == "200" and len(head) >= 3:
            return [(head[1], head[2])]

        if code in ("210", "211"):
            matches: list[tuple[str, str]] = []
            for line in lines[1:]:
                if line == ".":
                    break
                parts = line.split(" ", 2)
                if len(parts) >= 2:
                    matches.append((parts[0], parts[1]))
            return matches

        logger.debug("CDDB: no matches for disc_id=%s (code=%s)", disc_id, code)
        return []

    @staticmethod
    def _parse_frame_offsets(lines: list[str]) -> list[int]:
        """Track frame offsets from the xmcd comment header of a read response.

        The block looks like:
            # Track frame offsets:
            #        150
            #        25075
            #
        and ends at the first comment line that isn't a bare number.
        """
        offsets: list[int] = []
        in_block = False
        for line in lines:
            if not line.startswith("#"):
                if in_block:
                    break
                continue
            body = line[1:].strip()
            if not in_block:
                if body.lower().startswith("track frame offsets"):
                    in_block = True
                continue
            if body.isdigit():
                offsets.append(int(body))
            else:
                break
        return offsets

    @staticmethod
    def _toc_matches(record_offsets: list[int], disc_offsets: list[int]) -> bool:
        """Whether a record's frame offsets describe the physical disc's TOC.

        Shift-invariant: submitters' drives report different lead-in gaps, so
        a constant offset across all tracks still means the same disc.
        """
        if not disc_offsets or len(record_offsets) != len(disc_offsets):
            return False
        shift = record_offsets[0] - disc_offsets[0]
        return all(
            abs(r - shift - d) <= _TOC_DRIFT_TOLERANCE
            for r, d in zip(record_offsets, disc_offsets)
        )

    @staticmethod
    def _parse_read_response(
        resp: str,
        cat: str,
        did: str,
        track_count: int,
        disc_offsets: list[int] | None = None,
    ) -> dict | None:
        """Parse a `cddb read` response into a candidate dict.

        Long DTITLE/TTITLE values are split by the server into repeated
        `KEY=` lines that must be concatenated — treating them as separate
        entries would misalign every track after the split.
        """
        lines = resp.splitlines()
        if not lines or not lines[0].startswith("210"):
            logger.debug(
                "CDDB read %s/%s: unexpected response %r", cat, did, lines[:1]
            )
            return None

        dtitle = ""
        year = ""
        genre = ""
        titles: dict[int, str] = {}

        for line in lines:
            if line.startswith("DTITLE="):
                dtitle += line.split("=", 1)[1]
            elif line.startswith("DYEAR="):
                year = line.split("=", 1)[1].strip()
            elif line.startswith("DGENRE="):
                genre = line.split("=", 1)[1].strip()
            elif line.startswith("TTITLE"):
                k, v = line.split("=", 1)
                try:
                    idx = int(k[len("TTITLE"):])
                except ValueError:
                    continue
                titles[idx] = titles.get(idx, "") + v

        if " / " in dtitle:
            artist, album = dtitle.split(" / ", 1)
        else:
            artist, album = "", dtitle

        track_titles = [v for _, v in sorted(titles.items())][:track_count]

        conf = _CONF_UNVERIFIED
        evidence: dict[str, Any] = {
            "cddb_cat": cat,
            "cddb_discid": did,
        }
        record_offsets = CddbSource._parse_frame_offsets(lines)
        if disc_offsets and CddbSource._toc_matches(record_offsets, disc_offsets):
            conf = _CONF_TOC_VERIFIED
            evidence["match"] = "cddb_exact"
            evidence["toc_verified"] = True
        if year:
            evidence["year"] = year
        if genre:
            evidence["genre"] = genre

        return {
            "artist": artist.strip(),
            "album": album.strip(),
            "year": year or None,
            "genre": genre or None,
            "track_titles": json.dumps(track_titles, ensure_ascii=False),
            "confidence": conf,
            "source_url": "",
            "evidence": json.dumps(evidence, ensure_ascii=False),
        }

    async def _cddb_request(self, cmd: str) -> str:
        """Send a CDDB protocol request to the configured GnuDB server."""
        base = get_config().integrations.gnudb_url.rstrip("/")
        params = {
            "cmd": cmd,
            "hello": CDDB_HELLO,
            "proto": "6",
        }
        url = (
            f"{base}/~cddb/cddb.cgi?"
            + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        )

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.content

        # Try multiple encodings — CDDB records often use Shift_JIS or EUC-JP
        for enc in ("utf-8", "shift_jis", "euc_jp", "latin-1"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                pass
        return raw.decode("latin-1", "replace")
