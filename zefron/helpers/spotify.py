# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import re
import asyncio
import aiohttp

from zefron import logger
from zefron.helpers._dataclass import Track

# ── OneGrab (hardcoded for Spotify resolution only) ────────────────────────
_ONEGRAB_BASE = "https://api.onegrab.fun"
_ONEGRAB_KEYS = [
    "4f7c3a_jTyyRH8d0901mVRCL9363aeHkVERtycg",   # primary
    "d96384_tFt_LWW7flVH7Vq4ssBHPTSXHgMuvT3J",   # fallback 1
    "96892c_trF-NQn3IxkFcO7269t_oYmT8PWDHFTC",   # fallback 2
]
_TIMEOUT = aiohttp.ClientTimeout(total=15)

# ── Regex ──────────────────────────────────────────────────────────────────
SPOTIFY_REGEX = re.compile(
    r"https?://open\.spotify\.com/"
    r"(?:intl-[a-z]+/)?"
    r"(track|playlist|album|episode)/"
    r"([A-Za-z0-9]+)"
    r"|spotify:(track|playlist|album|episode):([A-Za-z0-9]+)"
)


# ── Public helpers ─────────────────────────────────────────────────────────

def is_spotify(url: str) -> bool:
    return bool(SPOTIFY_REGEX.search(url))


def is_spotify_playlist(url: str) -> bool:
    return bool(
        re.search(r"open\.spotify\.com/(?:intl-[a-z]+/)?(playlist|album)/", url)
        or re.search(r"spotify:(playlist|album):", url)
    )


def _sp_cache_key(url: str) -> str | None:
    """Return a stable MongoDB cache key like 'playlist:37i9dQZF1DX...' from a Spotify URL."""
    m = SPOTIFY_REGEX.search(url)
    if not m:
        return None
    kind = m.group(1) or m.group(3)
    sid  = m.group(2) or m.group(4)
    if kind in ("playlist", "album") and sid:
        return f"{kind}:{sid}"
    return None


# ── OneGrab metadata fetch ─────────────────────────────────────────────────

async def _fetch_meta(spotify_url: str) -> list[dict]:
    """
    Call OneGrab /api/get_url with Spotify URL.
    Tries each key in _ONEGRAB_KEYS until one succeeds.
    For a track → 1 item.  For a playlist → up to 50 items.
    """
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        for i, key in enumerate(_ONEGRAB_KEYS, 1):
            try:
                async with session.get(
                    f"{_ONEGRAB_BASE}/api/get_url",
                    params={"url": spotify_url, "api_key": key},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        results = data.get("results", [])
                        if results:
                            if i > 1:
                                logger.info("OneGrab Spotify: key %d succeeded", i)
                            return results
                    logger.warning("OneGrab Spotify key %d → HTTP %s", i, resp.status)
            except Exception as e:
                logger.warning("OneGrab Spotify key %d failed: %s", i, e)
    return []


# ── Internal: resolve one Spotify result → YouTube Track ──────────────────

async def _resolve_one(item: dict, m_id: int, video: bool, yt_instance) -> Track | None:
    """
    Take a Spotify metadata dict (title + channel/artist from OneGrab)
    and find the matching YouTube track via search.
    Keeps Spotify thumbnail and duration for display.
    """
    title   = (item.get("title") or "").strip()
    artist  = (item.get("channel") or "").strip()
    query   = f"{title} {artist}".strip() if artist else title
    if not query:
        return None

    yt_track = await yt_instance.search(query, m_id, video=video)
    if not yt_track:
        return None

    # Show Spotify URL in Now Playing (id stays as YouTube ID for download)
    sp_url = (item.get("url") or "").strip()
    if sp_url and "spotify.com" in sp_url:
        yt_track.url = sp_url

    # Prefer Spotify thumbnail (higher quality, no letterbox)
    sp_thumb = (item.get("thumbnail") or "").split("?")[0]
    if sp_thumb:
        yt_track.thumbnail = sp_thumb

    # Use Spotify duration if YouTube search returned 0
    if not yt_track.duration_sec and item.get("duration"):
        from zefron.core.youtube import _secs_to_str
        dur_sec = int(item["duration"])
        yt_track.duration_sec = dur_sec
        yt_track.duration     = _secs_to_str(dur_sec)

    return yt_track


# ── Public API ─────────────────────────────────────────────────────────────

async def spotify_track(url: str, m_id: int, video: bool, yt_instance) -> Track | None:
    """Resolve a single Spotify track URL → YouTube Track."""
    results = await _fetch_meta(url)
    if not results:
        logger.warning("Spotify track: no metadata from OneGrab for %s", url)
        return None

    track = await _resolve_one(results[0], m_id, video, yt_instance)
    if track:
        logger.info("Spotify track resolved: %s", track.title)
    return track


async def spotify_playlist(
    url: str, limit: int, user: str, video: bool, yt_instance
) -> list[Track]:
    """
    Resolve a Spotify playlist/album → list[Track] (YouTube-downloadable).
    OneGrab returns up to 50 tracks; we cap at `limit`.

    Cache strategy:
    - First check MongoDB (7-day TTL) using playlist/album ID as key.
    - On cache miss: fetch from OneGrab, then save raw results to MongoDB.
    - YouTube resolution (search) always runs fresh — only the OneGrab
      metadata (title, artist, thumbnail, duration) is cached.
    """
    from zefron import db as mongo

    cache_key = _sp_cache_key(url)

    # ── 1. Cache lookup ────────────────────────────────────────────────────
    raw_results: list[dict] | None = None
    if cache_key:
        raw_results = await mongo.get_spotify_cache(cache_key)
        if raw_results:
            logger.info("Spotify cache hit: %s (%d tracks)", cache_key, len(raw_results))

    # ── 2. Cache miss → call OneGrab ──────────────────────────────────────
    if not raw_results:
        raw_results = await _fetch_meta(url)
        if not raw_results:
            logger.warning("Spotify playlist: no metadata from OneGrab for %s", url)
            return []
        if cache_key:
            await mongo.set_spotify_cache(cache_key, raw_results)
            logger.info("Spotify cache saved: %s (%d tracks)", cache_key, len(raw_results))

    # ── 3. Resolve metadata → YouTube Tracks ──────────────────────────────
    results = raw_results[:limit]
    tracks: list[Track] = []

    async def _resolve(item: dict) -> Track | None:
        t = await _resolve_one(item, 0, video, yt_instance)
        if t:
            t.user = user
        return t

    # Concurrent resolution in batches of 5
    batch_size = 5
    for i in range(0, len(results), batch_size):
        batch   = results[i: i + batch_size]
        settled = await asyncio.gather(*[_resolve(item) for item in batch])
        tracks.extend(t for t in settled if t)
        if i + batch_size < len(results):
            await asyncio.sleep(0.3)

    logger.info("Spotify playlist resolved %d/%d tracks", len(tracks), len(results))
    return tracks
