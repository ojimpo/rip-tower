"""LLM-assisted metadata correction.

Checks conditions (mojibake, contradictions, missing data) and if needed,
calls Claude API with all candidates + issues to get structured corrections.
Saves the result as a new MetadataCandidate with source="llm".

This is a fallback — only triggered when sanitizer detects issues that
deterministic rules cannot fix.
"""

import json
import logging
from typing import Any

import httpx
from sqlalchemy import select

from backend.config import get_config
from backend.database import async_session
from backend.models import JobMetadata, MetadataCandidate

logger = logging.getLogger(__name__)

# Issues that warrant LLM assistance
LLM_TRIGGER_ISSUES = {
    "mojibake",
    "artist_variant",
    "no_track_titles",
    "artist_contradiction",
    "album_contradiction",
    "parenthesized_variant",
}

# Model mapping for config shorthand
MODEL_MAP = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6-20260527",
    "opus": "claude-opus-4-6-20260527",
}


async def maybe_assist(job_id: str) -> bool:
    """Check if LLM assistance is needed and call if so.

    Returns True if an LLM candidate was added.

    Conditions checked:
    - Mojibake detected in artist/album/track titles
    - Artist/album contradictions between candidates
    - Missing track titles
    - Katakana-only artist name (might be English artist written in kana)
    - Parenthesized romanization/translation in artist or album name
    """
    config = get_config()
    api_key = config.integrations.llm_api_key
    if not api_key:
        logger.debug("LLM API key not configured, skipping LLM assist")
        return False

    # Load current metadata and check for trigger issues
    async with async_session() as session:
        meta = await session.get(JobMetadata, job_id)
        if not meta:
            return False

        issues = json.loads(meta.issues) if meta.issues else []

    # Only proceed if there are issues the LLM can help with
    trigger_issues = set(issues) & LLM_TRIGGER_ISSUES
    if not trigger_issues:
        logger.debug("No LLM-trigger issues for job %s", job_id)
        return False

    logger.info(
        "LLM assist triggered for job %s: issues=%s", job_id, trigger_issues
    )

    # Load all candidates for context
    async with async_session() as session:
        result = await session.execute(
            select(MetadataCandidate)
            .where(MetadataCandidate.job_id == job_id)
            .order_by(MetadataCandidate.confidence.desc())
        )
        candidates = list(result.scalars().all())

    if not candidates:
        return False

    # Safeguard: if no candidate has real evidence (top confidence < 20),
    # don't call LLM — it has no basis to answer and tends to justify whatever
    # weak hint is present, producing confident-looking wrong metadata.
    # Flag as unknown_disc so the review UI shows the disc needs manual identification.
    top_confidence = max((c.confidence or 0) for c in candidates)
    if top_confidence < 20:
        logger.info(
            "LLM assist skipped for job %s: no solid evidence (top confidence=%d)",
            job_id, top_confidence,
        )
        async with async_session() as session:
            m = await session.get(JobMetadata, job_id)
            if m:
                existing = json.loads(m.issues) if m.issues else []
                if "unknown_disc" not in existing:
                    existing.append("unknown_disc")
                    m.issues = json.dumps(existing, ensure_ascii=False)
                    m.needs_review = True
                    await session.commit()
        return False

    # Build prompt
    prompt = _build_prompt(meta, candidates, trigger_issues)

    # Call Claude API
    model_name = config.integrations.llm_model or "haiku"
    model_id = MODEL_MAP.get(model_name, model_name)

    try:
        response = await _call_claude(api_key, model_id, prompt)
    except Exception:
        logger.exception("LLM API call failed for job %s", job_id)
        return False

    if not response:
        return False

    # Save as new candidate
    async with async_session() as session:
        llm_candidate = MetadataCandidate(
            job_id=job_id,
            source="llm",
            artist=response.get("artist"),
            album=response.get("album"),
            year=response.get("year"),
            genre=response.get("genre"),
            track_titles=json.dumps(
                response.get("track_titles", []), ensure_ascii=False
            ) if response.get("track_titles") else None,
            confidence=response.get("confidence", 70),
            evidence=json.dumps({
                "model": model_id,
                "trigger_issues": list(trigger_issues),
                "reasoning": response.get("reasoning", ""),
            }, ensure_ascii=False),
        )
        session.add(llm_candidate)
        await session.commit()

    logger.info(
        "LLM assist saved candidate for job %s: artist=%r album=%r conf=%d",
        job_id,
        response.get("artist"),
        response.get("album"),
        response.get("confidence", 0),
    )
    return True


def _build_prompt(
    meta: JobMetadata,
    candidates: list[MetadataCandidate],
    issues: set[str],
) -> str:
    """Build the prompt for Claude with all candidate data and issues."""
    candidate_lines = []
    for c in candidates:
        candidate_lines.append(
            f"- Source: {c.source} (confidence: {c.confidence})\n"
            f"  Artist: {c.artist}\n"
            f"  Album: {c.album}\n"
            f"  Year: {c.year}\n"
            f"  Genre: {c.genre}\n"
            f"  Track titles: {c.track_titles}\n"
            f"  Evidence: {c.evidence}"
        )

    candidates_text = "\n".join(candidate_lines)
    issues_text = ", ".join(sorted(issues))

    return f"""You are a music metadata specialist. Analyze the following CD metadata candidates and fix issues.

## Current best metadata
- Artist: {meta.artist}
- Album: {meta.album}
- Year: {meta.year}
- Genre: {meta.genre}

## Issues detected
{issues_text}

## All candidates from different sources
{candidates_text}

## Instructions
Based on all the evidence, provide corrected metadata. Common fixes needed:
- "mojibake": The text has encoding corruption (e.g., Shift_JIS misread as UTF-8). Determine the correct Japanese/English text.
- "artist_variant": The artist name is in katakana but might be a well-known English artist. Provide the canonical name.
- "no_track_titles": Try to determine correct track titles from the available data.
- "artist_contradiction" / "album_contradiction": Multiple sources disagree. Determine the correct value.
- "parenthesized_variant": The artist or album name has a redundant romanization/translation in parentheses (e.g. "葉加瀬太郎 (Taro Hakase)"). Remove the parenthesized part and use the canonical name. For Japanese artists, use the Japanese name. For Western artists, use the Western name.

Respond with ONLY a JSON object (no markdown, no explanation outside the JSON):
{{
  "artist": "corrected artist name",
  "album": "corrected album name",
  "year": 2024,
  "genre": "genre",
  "track_titles": ["track 1", "track 2", ...],
  "confidence": 75,
  "reasoning": "brief explanation of corrections made"
}}

If a field doesn't need correction, use the value from the best candidate.
Only include track_titles if you can determine them; omit the field if unsure."""


async def _call_claude(api_key: str, model_id: str, prompt: str) -> dict | None:
    """Call Claude API and parse structured JSON response."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model_id,
                "max_tokens": 2048,
                "messages": [{"role": "user", "content": prompt}],
            },
        )

        if resp.status_code != 200:
            logger.warning("Claude API returned %d: %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()

    # Extract text content from response
    content_blocks = data.get("content", [])
    text = ""
    for block in content_blocks:
        if block.get("type") == "text":
            text += block.get("text", "")

    if not text:
        return None

    # Parse JSON from response (handle potential markdown wrapping)
    text = text.strip()
    if text.startswith("```"):
        # Strip markdown code blocks
        lines = text.split("\n")
        text = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        )

    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        logger.warning("Could not parse LLM response as JSON: %s", text[:200])
        return None
