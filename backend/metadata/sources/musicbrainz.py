"""MusicBrainz metadata source — disc ID lookup and text search."""

import asyncio
import json
import logging
from collections import Counter
from typing import Any

import httpx

from backend.metadata.normalize import norm, normalize_various_artists, similarity
from backend.metadata.sources.base import MetadataSource

logger = logging.getLogger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2"
HEADERS = {"User-Agent": "RipTower/0.1.0 (https://github.com/kouki/rip-tower)"}
RATE_LIMIT = 1.0  # 1 request per second

# Cache of release_id -> (artist_aliases, release_aliases). Aliases are stable,
# so one fetch per release per process is plenty.
_ALIAS_CACHE: dict[str, tuple[list[str], list[str]]] = {}


async def fetch_release_artist_aliases(
    release_id: str,
) -> tuple[list[str], list[str]]:
    """Return (artist_aliases, release_aliases) for a MusicBrainz release.

    Used by the kashidashi cross-check to match across scripts: a borrowed-CD
    record written in Japanese ("エイミー・ワインハウス") won't string-match an
    English MB candidate ("Amy Winehouse"), but MB stores the Japanese form as a
    locale alias. Aliases include the canonical name itself. Returns ([], []) on
    any failure so callers can fall back to plain text matching.
    """
    if not release_id:
        return [], []
    if release_id in _ALIAS_CACHE:
        return _ALIAS_CACHE[release_id]

    artist_aliases: list[str] = []
    release_aliases: list[str] = []
    try:
        await asyncio.sleep(RATE_LIMIT)
        async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
            resp = await client.get(
                f"{MB_BASE}/release/{release_id}",
                params={"fmt": "json", "inc": "artist-credits+aliases"},
            )
            artist_ids: list[str] = []
            if resp.status_code == 200:
                data = resp.json()
                release_aliases = [
                    a["name"] for a in data.get("aliases", []) if a.get("name")
                ]
                for credit in data.get("artist-credit", []):
                    artist = credit.get("artist", {})
                    if artist.get("name"):
                        artist_aliases.append(artist["name"])
                    if artist.get("id"):
                        artist_ids.append(artist["id"])

            for artist_id in artist_ids[:2]:  # joint credits are rare; cap fetches
                await asyncio.sleep(RATE_LIMIT)
                ar = await client.get(
                    f"{MB_BASE}/artist/{artist_id}",
                    params={"fmt": "json", "inc": "aliases"},
                )
                if ar.status_code == 200:
                    adata = ar.json()
                    artist_aliases.extend(
                        a["name"] for a in adata.get("aliases", []) if a.get("name")
                    )
    except Exception:
        logger.debug("MB alias fetch failed for release %s", release_id, exc_info=True)

    # De-dupe while preserving order.
    artist_aliases = list(dict.fromkeys(artist_aliases))
    release_aliases = list(dict.fromkeys(release_aliases))
    _ALIAS_CACHE[release_id] = (artist_aliases, release_aliases)
    return artist_aliases, release_aliases

# CD-family media we'll count as discs of a release. The exact-match
# `format == "CD"` filter we used to apply silently dropped SHM-CD/HQCD/etc.
# releases — and the fallback (count all media) then pulled DVD-Video bonus
# discs into total_discs, so 2-disc CD sets with a DVD got tagged total_discs=3.
_CD_FORMATS = frozenset({
    "CD",
    "CD-R",
    "8cm CD",
    "Data CD",  # ripper will still skip non-audio tracks; keep counted as a disc
    "Enhanced CD",
    "Copy Control CD",
    "HDCD",
    "HQCD",
    "SHM-CD",
    "UHQCD",
    "Blu-spec CD",
    "Blu-spec CD2",
    "CD+G",
    "DTS CD",
    "Hybrid SACD",  # has a CD layer; ripable
    "XRCD",
})


def _join_artist_credit(ac: Any) -> str:
    """Join a MusicBrainz artist-credit array into a display string.

    Honours joinphrase so collaborations come back as "A feat. B". Accepts both
    the release/recording shape ({"name": ..., "joinphrase": ...}) and bare
    strings, returning "" when nothing usable is present.
    """
    if not isinstance(ac, list):
        return ""
    parts: list[str] = []
    for cr in ac:
        if isinstance(cr, dict):
            name = cr.get("name") or (cr.get("artist") or {}).get("name") or ""
            parts.append(name)
            parts.append(cr.get("joinphrase", "") or "")
        else:
            parts.append(str(cr))
    return "".join(parts).strip()


def _track_artist(track: dict) -> str:
    """Per-track performer from a MusicBrainz track (track-level credit first)."""
    return (
        _join_artist_credit(track.get("artist-credit"))
        or _join_artist_credit((track.get("recording") or {}).get("artist-credit"))
    )


def _track_entries(tracks: list[dict], release_artist: str) -> list[str]:
    """Build track_titles, prefixing "artist / title" when the disc is multi-artist.

    MusicBrainz credits every track to its own artist, but rip-tower previously
    kept only the release-level artist-credit — so a Various-Artists compilation
    came out tagged to a single contributing artist, and Plex then showed that
    one performer as the album artist instead of "Various Artists" (Todoist
    6gp628r73WQ8576F: "Various のアルバムアーティストが平沢進になる").

    When the disc is genuinely multi-artist (the release is credited to Various,
    or no single performer accounts for ≥60% of the tracks) we emit the
    "artist / title" form the sanitizer already understands as a compilation —
    it splits per-track artists out and forces ALBUMARTIST=Various Artists. For a
    normal single-artist album (one dominant performer) we return plain titles so
    a lone guest feature doesn't flip the whole album to a compilation.
    """
    titles: list[str] = []
    per_artist: list[str] = []
    for t in tracks:
        rec = t.get("recording") or {}
        titles.append(rec.get("title") or t.get("title", ""))
        per_artist.append(_track_artist(t))

    counts = Counter(norm(a) for a in per_artist if a)
    total_credited = sum(counts.values())
    dominant_share = counts.most_common(1)[0][1] / total_credited if total_credited else 0.0
    release_is_va = normalize_various_artists(release_artist or "") == "Various Artists"

    is_compilation = release_is_va or (len(counts) > 1 and dominant_share < 0.6)
    if not is_compilation:
        return titles

    out: list[str] = []
    for title, ta in zip(titles, per_artist):
        out.append(f"{ta} / {title}" if ta else title)
    return out


def _is_cd_medium(medium: dict) -> bool:
    """Whether a MusicBrainz medium represents a ripable CD-family disc.

    Releases routinely bundle a DVD-Video or Blu-ray bonus disc with the audio
    CDs; counting them inflates total_discs and confuses album_group/disc tags.
    Treat missing/unknown format as CD (conservative — most releases that
    omit format are plain CDs).
    """
    fmt = medium.get("format")
    if not fmt:
        return True
    return fmt in _CD_FORMATS


class MusicBrainzSource(MetadataSource):
    """MusicBrainz source.

    `mode` controls which lookup strategies run:
      - "both" (default): disc ID lookup, fall back to text search if no hits
      - "disc_id": only disc ID lookup (Phase 1 of two-phase resolution)
      - "text_search": only text search (Phase 2 — uses enriched hints)
    """

    def __init__(self, mode: str = "both") -> None:
        self.mode = mode

    @property
    def name(self) -> str:
        return "musicbrainz"

    async def search(self, identity: Any, hints: dict | None = None) -> list[dict]:
        candidates = []
        track_count = identity.track_count if identity else 0
        total_seconds = getattr(identity, "total_seconds", 0) if identity else 0

        if self.mode in ("both", "disc_id") and identity:
            results = await self._lookup_discid(identity)
            candidates.extend(results)

        if self.mode == "text_search" or (
            self.mode == "both" and not candidates and hints
        ):
            if hints:
                results = await self._text_search(hints, track_count, total_seconds)
                candidates.extend(results)

        return candidates

    @staticmethod
    def _pick_medium(
        media: list[dict], track_count: int, total_seconds: int,
        target_disc: int = 0,
    ) -> dict | None:
        """Pick the medium that best matches our physical disc.

        Prefer track_count match (with duration tiebreak when several share
        the same count), then position == target_disc, then first medium.
        """
        if not media:
            return None

        def medium_seconds(m: dict) -> int:
            ms = sum(t.get("length") or 0 for t in m.get("tracks", []))
            return ms // 1000

        if track_count:
            matching = [m for m in media if m.get("track-count") == track_count]
            if len(matching) == 1:
                return matching[0]
            if matching:
                if total_seconds > 0:
                    return min(
                        matching,
                        key=lambda m: abs(medium_seconds(m) - total_seconds),
                    )
                target_match = next(
                    (m for m in matching if m.get("position") == target_disc), None,
                )
                if target_match:
                    return target_match
                return matching[0]
        if target_disc:
            target_match = next(
                (m for m in media if m.get("position") == target_disc), None,
            )
            if target_match:
                return target_match
        return media[0]

    async def _lookup_discid(self, identity: Any) -> list[dict]:
        """Look up MB releases by disc TOC.

        cd-discid produces a CDDB-style hex disc ID; MusicBrainz's
        /discid/{id} endpoint expects its own SHA1-based ID and returns
        HTTP 400 for our hex. Submit the TOC instead via /discid/-?toc=...,
        which is what /api/drives/<id>/identify already uses successfully.
        """
        track_count = identity.track_count if identity else 0
        offsets = list(getattr(identity, "offsets", None) or [])
        leadout_seconds = getattr(identity, "leadout", 0) or 0
        total_seconds = (
            getattr(identity, "total_seconds", 0) or leadout_seconds
        )

        if not offsets or not leadout_seconds or not track_count:
            # Older jobs may not have stored offsets/leadout — without them
            # we can't synthesize a TOC, so skip rather than 400 the API.
            return []

        # cd-discid reports leadout in seconds; MB wants sectors (75/sec)
        leadout_sectors = leadout_seconds * 75
        toc = f"1 {track_count} {leadout_sectors} {' '.join(str(o) for o in offsets)}"

        await asyncio.sleep(RATE_LIMIT)
        async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
            try:
                resp = await client.get(
                    f"{MB_BASE}/discid/-",
                    params={
                        "toc": toc,
                        "fmt": "json",
                        "inc": "recordings+artist-credits",
                    },
                )
                if resp.status_code == 404:
                    logger.debug("MB TOC %s: not found", toc)
                    return []
                if resp.status_code != 200:
                    logger.warning(
                        "MB TOC lookup error: HTTP %d (toc=%s)",
                        resp.status_code, toc,
                    )
                    return []

                data = resp.json()
                releases = data.get("releases", [])
                candidates = []

                for release in releases:
                    artist = ""
                    ac = release.get("artist-credit", [])
                    if ac:
                        artist = (
                            ac[0].get("name", "")
                            if isinstance(ac[0], dict) else str(ac[0])
                        )

                    media = [m for m in release.get("media", []) if _is_cd_medium(m)]
                    if not media:
                        media = release.get("media", [])
                    total_discs = len(media)
                    if not media:
                        continue

                    chosen = self._pick_medium(media, track_count, total_seconds)
                    if chosen is None:
                        continue
                    disc_number = chosen.get("position", 1)

                    tracks = _track_entries(chosen.get("tracks", []), artist)

                    candidates.append({
                        "artist": artist,
                        "album": release.get("title", ""),
                        "year": (release.get("date") or "")[:4] or None,
                        "genre": None,
                        "track_titles": json.dumps(
                            tracks[:track_count] if track_count else tracks,
                            ensure_ascii=False,
                        ),
                        "confidence": 90,
                        "disc_number": disc_number,
                        "total_discs": total_discs,
                        "source_url": (
                            f"https://musicbrainz.org/release/{release.get('id', '')}"
                        ),
                        "evidence": json.dumps({
                            "match": "toc_submission",
                            "toc": toc,
                            "mb_release": release.get("id", ""),
                            "disc_number": disc_number,
                            "total_discs": total_discs,
                        }, ensure_ascii=False),
                    })

                return candidates

            except Exception:
                logger.exception("MusicBrainz TOC lookup failed")
                return []

    async def _text_search(
        self, hints: dict, track_count: int = 0, total_seconds: int = 0,
    ) -> list[dict]:
        query_parts = []
        catalog = hints.get("catalog", "")
        title = hints.get("title", "")
        artist = hints.get("artist", "")
        target_disc = hints.get("disc_number") or 1

        if catalog:
            query_parts.append(f'catno:"{catalog}"')
        if title:
            query_parts.append(f'release:"{title}"')
        if artist:
            query_parts.append(f'artist:"{artist}"')

        if not query_parts:
            return []

        await asyncio.sleep(RATE_LIMIT)
        async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
            try:
                resp = await client.get(
                    f"{MB_BASE}/release/",
                    params={
                        "query": " AND ".join(query_parts),
                        "fmt": "json",
                        "limit": 10,
                    },
                )
                if resp.status_code != 200:
                    return []

                data = resp.json()
                releases = data.get("releases", [])

                # Score releases first to pick top candidates worth fetching tracks for
                scored: list[tuple[int, dict, dict]] = []
                for r in releases:
                    r_artist = ""
                    ac = r.get("artist-credit", [])
                    if ac and isinstance(ac[0], dict):
                        r_artist = ac[0].get("name", "")

                    conf = 40
                    evidence: dict[str, Any] = {
                        "match": "text_search",
                        "mb_release": r.get("id", ""),
                    }

                    if catalog and any(
                        li.get("catalog-number", "").upper() == catalog.upper()
                        for li in r.get("label-info", [])
                    ):
                        conf += 30
                        evidence["catno_match"] = catalog

                    if title and similarity(title, r.get("title", "")) >= 0.8:
                        conf += 15
                        evidence["title_match"] = True

                    if artist and similarity(artist, r_artist) >= 0.6:
                        conf += 10
                        evidence["artist_match"] = True

                    tc = sum(m.get("track-count", 0) for m in r.get("media", []))
                    if track_count and tc == track_count:
                        conf += 5
                        evidence["track_count_match"] = True

                    scored.append((conf, r, evidence))

                scored.sort(key=lambda x: x[0], reverse=True)

                candidates = []
                # Fetch full track listings for top 3 — cheap MB lookups, big quality gain
                for conf, r, evidence in scored[:3]:
                    r_artist = ""
                    ac = r.get("artist-credit", [])
                    if ac and isinstance(ac[0], dict):
                        r_artist = ac[0].get("name", "")

                    tracks, disc_number, total_discs = await self._fetch_tracks(
                        client, r.get("id", ""), target_disc, track_count,
                        total_seconds, r_artist,
                    )
                    if tracks and track_count and len(tracks) == track_count:
                        conf += 5
                        evidence["disc_track_match"] = True

                    candidates.append({
                        "artist": r_artist,
                        "album": r.get("title", ""),
                        "year": (r.get("date") or "")[:4] or None,
                        "confidence": min(conf, 85),
                        "track_titles": json.dumps(tracks, ensure_ascii=False) if tracks else None,
                        "disc_number": disc_number,
                        "total_discs": total_discs,
                        "source_url": f"https://musicbrainz.org/release/{r.get('id', '')}",
                        "evidence": json.dumps(
                            {**evidence, "disc_number": disc_number, "total_discs": total_discs},
                            ensure_ascii=False,
                        ),
                    })

                # Append remaining lower-ranked releases without track fetches
                for conf, r, evidence in scored[3:]:
                    r_artist = ""
                    ac = r.get("artist-credit", [])
                    if ac and isinstance(ac[0], dict):
                        r_artist = ac[0].get("name", "")
                    candidates.append({
                        "artist": r_artist,
                        "album": r.get("title", ""),
                        "year": (r.get("date") or "")[:4] or None,
                        "confidence": min(conf, 85),
                        "source_url": f"https://musicbrainz.org/release/{r.get('id', '')}",
                        "evidence": json.dumps(evidence, ensure_ascii=False),
                    })

                return candidates

            except Exception:
                logger.exception("MusicBrainz text search failed")
                return []

    async def _fetch_tracks(
        self,
        client: httpx.AsyncClient,
        release_id: str,
        target_disc: int,
        track_count: int,
        total_seconds: int = 0,
        release_artist: str = "",
    ) -> tuple[list[str], int, int]:
        """Fetch track listing for a release.

        For multi-disc sets where multiple media share the same track count
        (common for compilations like Singles I/II), tiebreak by total
        duration — the medium whose summed track lengths are closest to the
        physical disc's leadout time wins.
        """
        if not release_id:
            return [], 1, 1
        await asyncio.sleep(RATE_LIMIT)
        try:
            resp = await client.get(
                f"{MB_BASE}/release/{release_id}",
                params={"fmt": "json", "inc": "recordings+artist-credits+media"},
            )
            if resp.status_code != 200:
                return [], 1, 1
            data = resp.json()
        except Exception:
            logger.exception("MB release detail fetch failed for %s", release_id)
            return [], 1, 1

        media = [m for m in data.get("media", []) if _is_cd_medium(m)]
        if not media:
            media = data.get("media", [])
        total_discs = len(media)
        if not media:
            return [], 1, 1

        chosen = self._pick_medium(media, track_count, total_seconds, target_disc)
        if chosen is None:
            return [], 1, total_discs

        tracks = _track_entries(chosen.get("tracks", []), release_artist)

        return tracks, chosen.get("position", 1), total_discs
