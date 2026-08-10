# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import asyncio
import html
import json
import re
import time

import aiohttp

from zefron import config, logger
from zefron.helpers._dataclass import Track


# Spotify Web API Client Credentials flow. Credentials are read from the
# environment and never stored in this source file.
_SPOTIFY_ACCOUNTS = "https://accounts.spotify.com"
_SPOTIFY_API = "https://api.spotify.com/v1"
_TIMEOUT = aiohttp.ClientTimeout(total=15)
_token_lock = asyncio.Lock()
_access_token: str | None = None
_token_expires_at = 0.0


SPOTIFY_REGEX = re.compile(
    r"https?://open\.spotify\.com/"
    r"(?:intl-[a-z]+/)?"
    r"(track|playlist|album|episode)/"
    r"([A-Za-z0-9]+)"
    r"|spotify:(track|playlist|album|episode):([A-Za-z0-9]+)"
)


def is_spotify(url: str) -> bool:
    return bool(SPOTIFY_REGEX.search(url))


def is_spotify_playlist(url: str) -> bool:
    return bool(
        re.search(r"open\.spotify\.com/(?:intl-[a-z]+/)?(playlist|album)/", url)
        or re.search(r"spotify:(playlist|album):", url)
    )


def _sp_cache_key(url: str) -> str | None:
    """Return a versioned cache key for official Spotify collection metadata."""
    match = SPOTIFY_REGEX.search(url)
    if not match:
        return None
    kind = match.group(1) or match.group(3)
    spotify_id = match.group(2) or match.group(4)
    if kind in ("playlist", "album") and spotify_id:
        # v2 invalidates caches created before collection pagination.
        return f"spotify-api-v2:{kind}:{spotify_id}"
    return None


async def _get_access_token() -> str | None:
    """Get and cache an app-only Spotify access token."""
    global _access_token, _token_expires_at

    client_id = config.SPOTIFY_CLIENT_ID
    client_secret = config.SPOTIFY_CLIENT_SECRET
    if not client_id or not client_secret:
        logger.warning(
            "Spotify API credentials missing; set SPOTIFY_CLIENT_ID and "
            "SPOTIFY_CLIENT_SECRET"
        )
        return None

    if _access_token and time.monotonic() < _token_expires_at - 30:
        return _access_token

    async with _token_lock:
        if _access_token and time.monotonic() < _token_expires_at - 30:
            return _access_token
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.post(
                    f"{_SPOTIFY_ACCOUNTS}/api/token",
                    auth=aiohttp.BasicAuth(client_id, client_secret),
                    data={"grant_type": "client_credentials"},
                ) as response:
                    if response.status != 200:
                        logger.warning(
                            "Spotify token request failed: HTTP %s",
                            response.status,
                        )
                        return None
                    data = await response.json(content_type=None)
        except Exception as ex:
            logger.warning("Spotify token request failed: %s", ex)
            return None

        _access_token = data.get("access_token")
        _token_expires_at = time.monotonic() + int(data.get("expires_in", 3600))
        return _access_token


async def _spotify_get(
    path: str,
    params: dict | None = None,
    retry_on_unauthorized: bool = True,
) -> dict | None:
    """GET a Spotify Web API resource, refreshing the token once on 401."""
    global _access_token, _token_expires_at

    token = await _get_access_token()
    if not token:
        return None

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(
                f"{_SPOTIFY_API}{path}",
                params=params or {},
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                if response.status == 401 and retry_on_unauthorized:
                    _access_token = None
                    _token_expires_at = 0
                    return await _spotify_get(
                        path, params, retry_on_unauthorized=False
                    )
                if response.status != 200:
                    logger.warning(
                        "Spotify API %s failed: HTTP %s", path, response.status
                    )
                    return None
                return await response.json(content_type=None)
    except Exception as ex:
        logger.warning("Spotify API %s failed: %s", path, ex)
        return None


async def _fetch_public_meta(kind: str, spotify_id: str, spotify_url: str) -> list[dict]:
    """Read track metadata from Spotify's public embed page.

    Spotify occasionally returns 403 for the Web API track endpoint even when
    the track is public and playable. The embed page is a public, read-only
    metadata surface and does not require an app token, so use it as a fallback
    for individual tracks and episodes.
    """
    try:
        embed_url = f"https://open.spotify.com/embed/{kind}/{spotify_id}"
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(
                embed_url,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "Spotify public embed %s failed: HTTP %s",
                        spotify_id,
                        response.status,
                    )
                    return []
                page = await response.text()

        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            page,
            re.DOTALL,
        )
        if not match:
            return []

        payload = json.loads(html.unescape(match.group(1)))
        entity = (
            payload.get("props", {})
            .get("pageProps", {})
            .get("state", {})
            .get("data", {})
            .get("entity", {})
        )
        if not isinstance(entity, dict) or entity.get("type") != kind:
            return []

        if kind in ("playlist", "album"):
            cover_art = entity.get("coverArt") or {}
            sources = cover_art.get("sources") or []
            thumbnail = next(
                (
                    source.get("url", "")
                    for source in sources
                    if isinstance(source, dict) and source.get("url")
                ),
                "",
            )
            results = []
            for track in entity.get("trackList") or []:
                if not isinstance(track, dict) or track.get("entityType") != "track":
                    continue
                track_uri = track.get("uri") or ""
                track_id = track_uri.rsplit(":", 1)[-1]
                title = (track.get("title") or "").strip()
                if not track_id or not title:
                    continue
                results.append(
                    {
                        "title": title,
                        "channel": (track.get("subtitle") or "").replace("\xa0", " ").strip(),
                        "url": f"https://open.spotify.com/track/{track_id}",
                        "thumbnail": thumbnail,
                        "duration": int(track.get("duration") or 0) // 1000,
                    }
                )
            return results

        title = (entity.get("name") or entity.get("title") or "").strip()
        if not title:
            return []

        artists = entity.get("artists") or []
        artist = ", ".join(
            artist.get("name", "").strip()
            for artist in artists
            if isinstance(artist, dict) and artist.get("name")
        )
        visual_identity = entity.get("visualIdentity") or {}
        images = visual_identity.get("image") or []
        thumbnail = next(
            (
                image.get("url", "")
                for image in images
                if isinstance(image, dict) and image.get("url")
            ),
            "",
        )
        duration_ms = int(entity.get("duration") or 0)
        return [
            {
                "title": title,
                "channel": artist,
                "url": spotify_url,
                "thumbnail": thumbnail,
                "duration": duration_ms // 1000,
            }
        ]
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError, ValueError) as ex:
        logger.warning("Spotify public metadata failed for %s: %s", spotify_id, ex)
        return []


def _track_item(item: dict, album: dict | None = None) -> dict | None:
    """Convert a Spotify track object to the metadata used by playback."""
    if not item or item.get("is_local"):
        return None

    track_id = item.get("id")
    title = (item.get("name") or "").strip()
    if not track_id or not title:
        return None

    artists = item.get("artists") or []
    artist = ", ".join(
        artist.get("name", "").strip()
        for artist in artists
        if artist.get("name")
    )
    album_data = album or item.get("album") or {}
    images = album_data.get("images") or []
    spotify_url = (item.get("external_urls") or {}).get(
        "spotify", f"spotify:track:{track_id}"
    )
    return {
        "title": title,
        "channel": artist,
        "url": spotify_url,
        "thumbnail": images[0].get("url", "") if images else "",
        "duration": int(item.get("duration_ms") or 0) // 1000,
    }


def _episode_item(item: dict) -> dict | None:
    """Convert a Spotify episode to the same searchable metadata shape."""
    episode_id = item.get("id")
    title = (item.get("name") or "").strip()
    if not episode_id or not title:
        return None

    show = item.get("show") or {}
    images = show.get("images") or []
    spotify_url = (item.get("external_urls") or {}).get(
        "spotify", f"spotify:episode:{episode_id}"
    )
    return {
        "title": title,
        "channel": (show.get("publisher") or "").strip(),
        "url": spotify_url,
        "thumbnail": images[0].get("url", "") if images else "",
        "duration": int(item.get("duration_ms") or 0) // 1000,
    }


async def _fetch_collection_meta(spotify_url: str) -> list[dict]:
    """Fetch every public Spotify playlist or album track."""
    match = SPOTIFY_REGEX.search(spotify_url)
    if not match:
        return []

    kind = match.group(1) or match.group(3)
    spotify_id = match.group(2) or match.group(4)
    if kind == "playlist":
        # Spotify consistently returns 403 for playlist tracks in this
        # environment. Use the public embed data directly instead of making a
        # request that can only produce a noisy warning and no metadata.
        return await _fetch_public_meta(kind, spotify_id, spotify_url)
    else:
        data = await _spotify_get(f"/albums/{spotify_id}")
        if not data:
            return []
        path = f"/albums/{spotify_id}/tracks"
        params = {"limit": 50, "offset": 0}
        album = {"images": data.get("images") or []}

    metadata_items: list[dict] = []
    while True:
        data = await _spotify_get(path, params)
        if not data:
            return await _fetch_public_meta(kind, spotify_id, spotify_url)
        entries = data.get("items", [])
        for entry in entries:
            item = entry.get("track") if kind == "playlist" else entry
            if metadata := _track_item(item, album):
                metadata_items.append(metadata)
        if not data.get("next") or not entries:
            break
        params["offset"] += params["limit"]

    return metadata_items


async def _fetch_meta(spotify_url: str) -> list[dict]:
    """Fetch Spotify track, episode, playlist, or album metadata officially."""
    match = SPOTIFY_REGEX.search(spotify_url)
    if not match:
        return []

    kind = match.group(1) or match.group(3)
    spotify_id = match.group(2) or match.group(4)
    if kind in ("playlist", "album"):
        return await _fetch_collection_meta(spotify_url)

    data = await _spotify_get(f"/{kind}s/{spotify_id}")
    if not data:
        return await _fetch_public_meta(kind, spotify_id, spotify_url)
    item = _episode_item(data) if kind == "episode" else _track_item(data)
    return [item] if item else []


async def _resolve_one(
    item: dict,
    m_id: int,
    video: bool,
    yt_instance,
) -> Track | None:
    """
    Resolve Spotify metadata to a downloadable YouTube track.

    Spotify's Web API provides metadata but not a playable audio URL. The
    existing YouTube search/download path is therefore used only to obtain
    playable audio after Spotify has identified the track.
    """
    title = (item.get("title") or "").strip()
    artist = (item.get("channel") or "").strip()
    query = f"{title} {artist}".strip() if artist else title
    if not query:
        return None

    yt_track = await yt_instance.search(query, m_id, video=video)
    if not yt_track:
        return None

    spotify_url = (item.get("url") or "").strip()
    if spotify_url and "spotify.com" in spotify_url:
        yt_track.url = spotify_url

    spotify_thumbnail = (item.get("thumbnail") or "").split("?")[0]
    if spotify_thumbnail:
        yt_track.thumbnail = spotify_thumbnail

    if not yt_track.duration_sec and item.get("duration"):
        from zefron.core.youtube import _secs_to_str

        duration = int(item["duration"])
        yt_track.duration_sec = duration
        yt_track.duration = _secs_to_str(duration)

    return yt_track


async def spotify_track(
    url: str,
    m_id: int,
    video: bool,
    yt_instance,
) -> Track | None:
    """Resolve one Spotify track or episode URL to a playable track."""
    results = await _fetch_meta(url)
    if not results:
        logger.warning("Spotify track: no metadata for %s", url)
        return None

    track = await _resolve_one(results[0], m_id, video, yt_instance)
    if track:
        logger.info("Spotify track resolved: %s", track.title)
    return track


async def spotify_playlist(
    url: str,
    limit: int | None,
    user: str,
    video: bool,
    yt_instance,
) -> list[Track]:
    """
    Resolve a Spotify playlist/album to YouTube-downloadable tracks.

    Spotify metadata is cached for seven days; playable audio resolution still
    runs fresh so stale media URLs are never persisted.
    """
    from zefron import db as mongo

    cache_key = _sp_cache_key(url)
    raw_results: list[dict] | None = None
    if cache_key:
        raw_results = await mongo.get_spotify_cache(cache_key)
        if raw_results:
            logger.info(
                "Spotify cache hit: %s (%d tracks)",
                cache_key,
                len(raw_results),
            )

    if not raw_results:
        raw_results = await _fetch_meta(url)
        if not raw_results:
            logger.warning("Spotify playlist: no metadata for %s", url)
            return []
        if cache_key:
            await mongo.set_spotify_cache(cache_key, raw_results)
            logger.info(
                "Spotify cache saved: %s (%d tracks)",
                cache_key,
                len(raw_results),
            )

    results = raw_results if limit is None else raw_results[:limit]
    tracks: list[Track] = []

    async def _resolve(item: dict) -> Track | None:
        track = await _resolve_one(item, 0, video, yt_instance)
        if track:
            track.user = user
        return track

    batch_size = 5
    for index in range(0, len(results), batch_size):
        batch = results[index : index + batch_size]
        settled = await asyncio.gather(*[_resolve(item) for item in batch])
        tracks.extend(track for track in settled if track)
        if index + batch_size < len(results):
            await asyncio.sleep(0.3)

    logger.info(
        "Spotify playlist resolved %d/%d tracks",
        len(tracks),
        len(results),
    )
    return tracks