# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


import os
import re
import yt_dlp
import random
import asyncio
import aiohttp
import threading
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from py_yt import Playlist, VideosSearch

from zefron import config, logger
from zefron.core.http import get_http_session
from zefron.core.metrics import log_timing
from zefron.helpers import Track, utils


# ── ShrutiMusic API ────────────────────────────────────────────────────────
# /vplay provider details are supplied through Replit Secrets/environment.
# Never keep provider credentials in source code.
SHRUTI_API_URL = config.SHRUTI_API_URL.rstrip("/")
SHRUTI_API_KEYS = tuple(
    key for key in (config.SHRUTI_API_KEY, config.SHRUTI_API_KEY2) if key
)

# Both audio providers run concurrently.  YouTubeAPI is only the deterministic
# tie-breaker when both finish in the same event-loop turn.
YOUTUBE_API_MEDIA_URL = "https://youtubeapi-production-1c84.up.railway.app/api/media"

# ── OneGrab API (search / metadata) ───────────────────────────────────────
ONEGRAB_BASE = "https://api.onegrab.fun"

DOWNLOAD_DIR = "downloads"

# ── WAV conversion helpers ─────────────────────────────────────────────────

_conv_locks: dict = {}
_conv_lock_guard = threading.Lock()
_download_slots = asyncio.Semaphore(4)
_download_inflight: dict[str, asyncio.Task] = {}
_search_inflight: dict[str, asyncio.Task] = {}
_search_cache: dict[str, tuple[float, Track]] = {}
_SEARCH_CACHE_TTL = 300
_SEARCH_CACHE_LIMIT = 256
_background_tasks: set[asyncio.Task] = set()


async def _write_response_to_file(
    response: aiohttp.ClientResponse, path: str
) -> int:
    """Write streamed response chunks with minimal per-chunk overhead.

    A buffered local file write is already short and releases no useful
    concurrency when moved to a worker thread.  The old thread-hop for every
    128 KiB chunk added measurable overhead to every provider download.
    """
    total = 0
    with open(path, "wb") as output:
        async for chunk in response.content.iter_chunked(131072):
            total += len(chunk)
            output.write(chunk)
    return total


def _track_background(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _schedule_cache_cleanup() -> None:
    _track_background(asyncio.create_task(
        asyncio.to_thread(_cleanup_wav_cache, 25),
        name="wav-cache-cleanup",
    ))


def _schedule_wav_conversion(mp3_path: str) -> None:
    """Warm the normalized cache without blocking the first playback."""
    _track_background(asyncio.create_task(
        _convert_to_wav(mp3_path),
        name=f"wav-convert:{Path(mp3_path).stem}",
    ))


async def _deduplicated_download(key: str, operation):
    """Share one bounded download task among all callers for the same media."""
    task = _download_inflight.get(key)
    if task is None or task.done():
        async def _run():
            async with _download_slots:
                return await operation()

        task = asyncio.create_task(_run(), name=f"download:{key}")
        _download_inflight[key] = task

    try:
        # A cancelled Telegram handler must not cancel a download another
        # handler is waiting for.
        return await asyncio.shield(task)
    finally:
        if task.done() and _download_inflight.get(key) is task:
            _download_inflight.pop(key, None)


async def _deduplicated_search(key: str, operation):
    """Share one search request when users ask for the same song together."""
    task = _search_inflight.get(key)
    if task is None or task.done():
        task = asyncio.create_task(operation(), name=f"search:{key[:48]}")
        _search_inflight[key] = task

    try:
        return await asyncio.shield(task)
    finally:
        if task.done() and _search_inflight.get(key) is task:
            _search_inflight.pop(key, None)


def _get_conv_lock(path: str) -> asyncio.Lock:
    with _conv_lock_guard:
        if path not in _conv_locks:
            _conv_locks[path] = asyncio.Lock()
        return _conv_locks[path]


def _wav_path(mp3: str) -> str:
    return mp3.replace(".mp3", ".wav")


def _tmp_wav_path(mp3: str) -> str:
    return mp3.replace(".mp3", ".wav.tmp")


async def _convert_to_wav(mp3_path: str) -> str:
    """Pre-convert MP3 → 48 kHz stereo PCM WAV for zero-overhead streaming."""
    wav = _wav_path(mp3_path)
    tmp = _tmp_wav_path(mp3_path)

    if os.path.exists(wav) and os.path.getsize(wav) > 0:
        return wav

    lock = _get_conv_lock(mp3_path)
    async with lock:
        if os.path.exists(wav) and os.path.getsize(wav) > 0:
            return wav
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-threads",
                "0",
                "-i",
                mp3_path,
                "-ar",
                "48000",
                "-ac",
                "2",
                "-acodec",
                "pcm_s16le",
                "-f",
                "wav",
                "-map_metadata",
                "-1",
                tmp,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, wav)
                return wav
        except Exception:
            pass
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    return mp3_path  # fallback: stream MP3 if conversion failed


async def _probe_duration(file_path: str) -> int:
    """Read the actual duration of a downloaded media file in seconds."""
    if not file_path or (
        isinstance(file_path, str)
        and file_path.startswith(("http://", "https://"))
    ):
        return 0

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await proc.communicate()
        if proc.returncode == 0:
            return max(0, round(float(output.decode().strip())))
    except (OSError, ValueError, TypeError):
        pass
    return 0


async def _has_media_stream(file_path: str, video: bool = False) -> bool:
    """Check that a local file contains the stream PyTgCalls will consume."""
    if not file_path or (
        isinstance(file_path, str)
        and file_path.startswith(("http://", "https://"))
    ):
        return bool(file_path)
    if not os.path.isfile(file_path) or os.path.getsize(file_path) <= 0:
        return False

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0" if video else "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await proc.communicate()
        return proc.returncode == 0 and bool(output.strip())
    except (OSError, asyncio.CancelledError):
        raise
    except Exception:
        return False


def _remove_invalid_media(file_path: str | None) -> None:
    """Remove a bad provider cache entry and its normalized companion."""
    if not file_path or (
        isinstance(file_path, str)
        and file_path.startswith(("http://", "https://"))
    ):
        return
    candidates = {
        file_path,
        file_path.replace(".wav", ".mp3"),
        file_path.replace(".mp3", ".wav"),
        f"{file_path}.dl",
        f"{file_path}.part",
    }
    for candidate in candidates:
        try:
            if os.path.isfile(candidate):
                os.remove(candidate)
        except OSError:
            pass


def _cleanup_wav_cache(keep: int = 25) -> None:
    """Keep only the <keep> most-recently-used WAV files."""
    try:
        wavs = [
            os.path.join(DOWNLOAD_DIR, f)
            for f in os.listdir(DOWNLOAD_DIR)
            if f.endswith(".wav")
        ]
        if len(wavs) <= keep:
            return
        wavs.sort(key=lambda p: os.path.getatime(p))
        for old in wavs[: len(wavs) - keep]:
            try:
                os.remove(old)
                mp3 = old.replace(".wav", ".mp3")
                if os.path.exists(mp3):
                    os.remove(mp3)
            except Exception:
                pass
    except Exception:
        pass


# ── ShrutiAPI download helpers ─────────────────────────────────────────────


async def _shruti_download_audio(video_id: str) -> str | None:
    """Download audio from ShrutiAPI and return the fastest usable source."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    mp3_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3")
    wav_path = _wav_path(mp3_path)

    # Return cached WAV immediately only when it is a real audio file. This
    # prevents an HTML/JSON provider error saved with a non-zero size from
    # reaching PyTgCalls as if it were an MP3.
    if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
        if await _has_media_stream(wav_path):
            return wav_path
        _remove_invalid_media(wav_path)

    # Download MP3 if not cached or if a stale cache entry failed validation.
    if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
        if not await _has_media_stream(mp3_path):
            _remove_invalid_media(mp3_path)

    if not (os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0):
        downloaded = False
        for api_key_index, api_key in enumerate(SHRUTI_API_KEYS):
            if downloaded:
                break
            for attempt in range(2):
                try:
                    session = await get_http_session()
                    async with session.get(
                        f"{SHRUTI_API_URL}/download",
                        params={
                            "url": video_id,
                            "type": "audio",
                            "api_key": api_key,
                        },
                        timeout=aiohttp.ClientTimeout(total=300),
                    ) as resp:
                        if resp.status != 200:
                            logger.warning(
                                "ShrutiAPI audio %s provider_key=%s → HTTP %s",
                                video_id,
                                api_key_index + 1,
                                resp.status,
                            )
                            break
                        tmp_dl = mp3_path + ".dl"
                        await _write_response_to_file(resp, tmp_dl)
                        if os.path.exists(tmp_dl) and os.path.getsize(tmp_dl) > 0:
                            os.replace(tmp_dl, mp3_path)
                            downloaded = True
                            break
                except Exception as e:
                    logger.warning(
                        "ShrutiAPI audio provider_key=%s attempt %s failed: %s",
                        api_key_index + 1,
                        attempt + 1,
                        e,
                    )
                    for leftover in [mp3_path + ".dl"]:
                        if os.path.exists(leftover):
                            try:
                                os.remove(leftover)
                            except Exception:
                                pass
                    if attempt == 0:
                        await asyncio.sleep(2)

        if not downloaded or not (
            os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0
        ):
            return None

    if not await _has_media_stream(mp3_path):
        logger.warning("ShrutiAPI returned an invalid audio file for %s", video_id)
        _remove_invalid_media(mp3_path)
        return None

    # Start playback from the compressed local file immediately.  The
    # normalized 48 kHz WAV remains a warm cache for the next request.
    result = mp3_path
    _schedule_wav_conversion(mp3_path)

    # Background cache housekeeping
    _schedule_cache_cleanup()

    return result


async def _youtubeapi_download_audio(video_id: str) -> str | None:
    """Download audio from youtubeapi and return the fastest usable source."""
    if not video_id:
        return None

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    # Keep a provider-specific cache so it can never be confused with the
    # Shruti MP3 cache for the same YouTube ID.
    media_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.youtubeapi.mp3")
    wav_path = _wav_path(media_path)

    if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
        if await _has_media_stream(wav_path):
            return wav_path
        _remove_invalid_media(wav_path)

    if os.path.exists(media_path) and os.path.getsize(media_path) > 0:
        if not await _has_media_stream(media_path):
            _remove_invalid_media(media_path)

    if not (os.path.exists(media_path) and os.path.getsize(media_path) > 0):
        temp_path = media_path + ".dl"
        try:
            params = {
                "url": f"https://youtu.be/{video_id}",
                "type": "audio",
                "quality": "360",
            }
            timeout = aiohttp.ClientTimeout(total=300)
            session = await get_http_session()
            async with session.get(
                YOUTUBE_API_MEDIA_URL, params=params, timeout=timeout
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "youtubeapi audio %s → HTTP %s",
                        video_id,
                        resp.status,
                    )
                    return None

                content_type = resp.headers.get("content-type", "").lower()
                if "json" in content_type or content_type.startswith("text/"):
                    logger.warning(
                        "youtubeapi audio %s returned non-media content (%s)",
                        video_id,
                        content_type or "unknown",
                    )
                    return None

                await _write_response_to_file(resp, temp_path)

            if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
                return None
            os.replace(temp_path, media_path)
        except Exception as ex:
            logger.warning("youtubeapi audio %s failed: %s", video_id, ex)
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

    if not os.path.exists(media_path) or os.path.getsize(media_path) == 0:
        return None
    if not await _has_media_stream(media_path):
        logger.warning("youtubeapi returned an invalid audio file for %s", video_id)
        _remove_invalid_media(media_path)
        return None

    # Avoid making a first-time request wait for a full PCM conversion.
    result = media_path
    _schedule_wav_conversion(media_path)
    _schedule_cache_cleanup()
    return result


async def _shruti_download_video(video_id: str) -> str | None:
    """Download ShrutiAPI's video/x-matroska response locally."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    # ShrutiAPI returns an MKV/WebM container (AV1 + Opus), not MP4.
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mkv")
    temp_path = file_path + ".part"

    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return file_path

    for api_key_index, api_key in enumerate(SHRUTI_API_KEYS):
        try:
            session = await get_http_session()
            async with session.get(
                f"{SHRUTI_API_URL}/download",
                params={"url": video_id, "type": "video", "api_key": api_key},
                timeout=aiohttp.ClientTimeout(total=600),
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "ShrutiAPI video %s provider_key=%s → HTTP %s",
                        video_id,
                        api_key_index + 1,
                        resp.status,
                    )
                    continue
                content_type = resp.headers.get("content-type", "").lower()
                if "json" in content_type or "text/" in content_type:
                    logger.warning(
                        "ShrutiAPI video provider_key=%s returned non-media content",
                        api_key_index + 1,
                    )
                    continue
                await _write_response_to_file(resp, temp_path)
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                os.replace(temp_path, file_path)
                return file_path
        except Exception as e:
            logger.warning(
                "ShrutiAPI video provider_key=%s failed: %s",
                api_key_index + 1,
                e,
            )
            for path in (temp_path, file_path):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
    return None


def _shruti_video_keys() -> list[str]:
    """Return distinct Shruti keys in preferred order.

    ``APIKey`` is used by some imported projects for another service, so it
    is intentionally not treated as a Shruti key for direct video playback.
    An explicit ``SHRUTI_API_KEY`` can still override the compatibility keys.
    """
    return list(SHRUTI_API_KEYS)


def shruti_video_stream_urls(video_id: str) -> list[str]:
    """Build direct ShrutiAPI video sources for ffmpeg.

    The endpoint is intentionally passed to PyTgCalls instead of first
    downloading the complete MP4.  This lets the voice call connect while
    ShrutiAPI is still delivering the first bytes.
    """
    from urllib.parse import urlencode

    return [
        f"{SHRUTI_API_URL}/download?"
        f"{urlencode({'url': video_id, 'type': 'video', 'api_key': api_key})}"
        for api_key in _shruti_video_keys()
    ]


def shruti_audio_stream_urls(video_id: str) -> list[str]:
    """Build direct ShrutiAPI audio sources for low-latency playback."""
    from urllib.parse import urlencode

    return [
        f"{SHRUTI_API_URL}/download?"
        f"{urlencode({'url': video_id, 'type': 'audio', 'api_key': api_key})}"
        for api_key in _shruti_video_keys()
    ]


def is_shruti_audio_stream(url: str | None) -> bool:
    """Return whether a source is one of our direct Shruti audio URLs."""
    return bool(
        url
        and url.startswith(f"{SHRUTI_API_URL}/download")
        and "type=audio" in url
    )


def _shruti_video_stream_url(video_id: str) -> str:
    """Return the first direct ShrutiAPI video source."""
    urls = shruti_video_stream_urls(video_id)
    return urls[0] if urls else None


def _secs_to_str(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ── Main YouTube class ─────────────────────────────────────────────────────


class YouTube:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.cookies = []
        self.checked = False
        self.cookie_dir = "zefron/cookies"
        self.warned = False
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )
        self.iregex = re.compile(
            r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)"
            r"(?!/(watch\?v=[A-Za-z0-9_-]{11}|shorts/[A-Za-z0-9_-]{11}"
            r"|playlist\?list=PL[A-Za-z0-9_-]+|[A-Za-z0-9_-]{11}))\S*"
        )

    @staticmethod
    def video_id(url: str) -> str | None:
        """Extract a YouTube video ID without treating ``-``/``_`` specially.

        A few valid YouTube IDs begin with a dash or underscore.  Splitting
        URLs by punctuation or passing the whole URL through a search API can
        turn those links into unrelated search results, so direct links are
        parsed locally first.
        """
        if not isinstance(url, str) or not url.strip():
            return None

        value = url.strip()
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = (parsed.hostname or "").lower().rstrip(".")
        if host in {"youtu.be", "www.youtu.be"}:
            candidate = parsed.path.strip("/").split("/", 1)[0]
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if parsed.path == "/watch":
                candidate = parse_qs(parsed.query).get("v", [""])[0]
            elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
                candidate = parsed.path.split("/", 2)[2]
            else:
                candidate = ""
        else:
            return None

        candidate = candidate.split("?", 1)[0].split("&", 1)[0]
        return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else None

    @classmethod
    def canonical_url(cls, url: str) -> str | None:
        video_id = cls.video_id(url)
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else None

    # ── Cookies (yt-dlp fallback only) ────────────────────────────────────

    def get_cookies(self):
        if not self.checked:
            for file in os.listdir(self.cookie_dir):
                if file.endswith(".txt"):
                    self.cookies.append(f"{self.cookie_dir}/{file}")
            self.checked = True
        if not self.cookies:
            if not self.warned:
                self.warned = True
                logger.warning("Cookies are missing; downloads might fail.")
            return None
        return random.choice(self.cookies)

    async def save_cookies(self, urls: list[str]) -> None:
        logger.info("Saving cookies from urls...")
        session = await get_http_session()
        for url in urls:
            name = url.split("/")[-1]
            link = "https://batbin.me/raw/" + name
            async with session.get(link) as resp:
                resp.raise_for_status()
                data = await resp.read()
                await asyncio.to_thread(
                    Path(f"{self.cookie_dir}/{name}.txt").write_bytes, data
                )
        logger.info(f"Cookies saved in {self.cookie_dir}.")

    # ── URL validation ─────────────────────────────────────────────────────

    def valid(self, url: str) -> bool:
        return bool(re.match(self.regex, url))

    def invalid(self, url: str) -> bool:
        return bool(re.match(self.iregex, url))

    # ── OneGrab API helper ─────────────────────────────────────────────────

    async def _api_get(self, path: str, params: dict) -> dict | None:
        if config.ONEGRAB_API_KEY:
            params = {**params, "api_key": config.ONEGRAB_API_KEY}
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            session = await get_http_session()
            async with session.get(
                f"{ONEGRAB_BASE}{path}", params=params, timeout=timeout
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.warning("OneGrab API error: %s", e)
        return None

    # ── Search ─────────────────────────────────────────────────────────────

    async def search(self, query: str, m_id: int, video: bool = False) -> Track | None:
        """Resolve a query with a short-lived in-memory result cache."""
        started = time.perf_counter()
        key = f"{query.strip().casefold()}::{int(video)}"
        cached = _search_cache.get(key)
        now = time.monotonic()
        if cached:
            cached_at, cached_track = cached
            if now - cached_at < _SEARCH_CACHE_TTL:
                log_timing(
                    "youtube.search",
                    started,
                    source="memory_cache",
                    found=True,
                )
                return replace(cached_track, message_id=m_id, video=video)
            _search_cache.pop(key, None)

        track = await _deduplicated_search(
            key,
            lambda: self._search_uncached(query, m_id=0, video=video),
        )
        if track:
            # Store a message-independent copy.  Telegram message IDs belong
            # to the caller and must never leak between concurrent requests.
            _search_cache[key] = (time.monotonic(), replace(track, message_id=0))
            if len(_search_cache) > _SEARCH_CACHE_LIMIT:
                oldest_key = min(_search_cache, key=lambda item: _search_cache[item][0])
                _search_cache.pop(oldest_key, None)
            track = replace(track, message_id=m_id, video=video)
        return track

    async def _search_uncached(
        self, query: str, m_id: int, video: bool = False
    ) -> Track | None:
        started = time.perf_counter()

        def _finish(track: Track | None, source: str) -> Track | None:
            log_timing("youtube.search", started, source=source, found=bool(track))
            return track

        # A direct URL is not a search query.  In particular, a URL whose ID
        # starts with ``-`` or ``_`` must resolve to that exact video.
        if self.video_id(query):
            return _finish(
                await self.get_track(query, m_id, video=video), "direct_url"
            )

        # OneGrab search
        if config.ONEGRAB_API_KEY:
            data = await self._api_get("/api/search", {"query": query})
            if data and data.get("results"):
                r = data["results"][0]
                yt_url = r.get("url", "")
                vid_id = r.get("id", "")
                dur_sec = int(r.get("duration") or 0)
                return _finish(Track(
                    id=vid_id,
                    channel_name=r.get("channel"),
                    duration=_secs_to_str(dur_sec),
                    duration_sec=dur_sec,
                    message_id=m_id,
                    title=(r.get("title") or "")[:25],
                    thumbnail=(r.get("thumbnail") or "").split("?")[0],
                    url=yt_url,
                    view_count=r.get("views"),
                    video=video,
                ), "onegrab")

        # Fallback: py_yt
        try:
            _search = VideosSearch(query, limit=1, with_live=False)
            results = await _search.next()
        except Exception:
            return _finish(None, "py_yt_error")
        if results and results["result"]:
            data = results["result"][0]
            return _finish(Track(
                id=data.get("id"),
                channel_name=data.get("channel", {}).get("name"),
                duration=data.get("duration"),
                duration_sec=utils.to_seconds(data.get("duration")),
                message_id=m_id,
                title=data.get("title", "")[:25],
                thumbnail=data.get("thumbnails", [{}])[-1].get("url", "").split("?")[0],
                url=data.get("link"),
                view_count=data.get("viewCount", {}).get("short"),
                video=video,
            ), "py_yt")
        return _finish(None, "not_found")

    # ── Track metadata from URL ────────────────────────────────────────────

    async def get_track(self, url: str, m_id: int, video: bool = False) -> Track | None:
        canonical = self.canonical_url(url) or url
        requested_id = self.video_id(canonical)
        data = await self._api_get("/api/get_url", {"url": canonical})
        if data and data.get("results"):
            r = data["results"][0]
            yt_url = r.get("url", canonical)
            vid_id = r.get("id", "") or self.video_id(canonical)
            # Never let a flaky metadata/search response replace a direct
            # link with another video.
            if requested_id and vid_id != requested_id:
                data = None
            else:
                dur_sec = int(r.get("duration") or 0)
                return Track(
                    id=vid_id,
                    channel_name=r.get("channel"),
                    duration=_secs_to_str(dur_sec),
                    duration_sec=dur_sec,
                    message_id=m_id,
                    title=(r.get("title") or "")[:25],
                    thumbnail=(r.get("thumbnail") or "").split("?")[0],
                    url=yt_url,
                    view_count=r.get("views"),
                    video=video,
                )

        # OneGrab is useful for duration metadata, but it is not always
        # available.  YouTube's oEmbed endpoint still gives us the exact
        # title/channel/thumbnail while the media provider handles playback.
        video_id = self.video_id(canonical)
        if not video_id:
            return None

        title = f"YouTube video {video_id}"
        channel = ""
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            session = await get_http_session()
            async with session.get(
                "https://www.youtube.com/oembed",
                params={"url": canonical, "format": "json"},
                timeout=timeout,
            ) as response:
                if response.status == 200:
                    metadata = await response.json()
                    title = metadata.get("title") or title
                    channel = metadata.get("author_name") or ""
                    thumbnail = metadata.get("thumbnail_url") or thumbnail
        except Exception as ex:
            logger.warning("YouTube oEmbed lookup failed for %s: %s", video_id, ex)

        return Track(
            id=video_id,
            channel_name=channel,
            duration="0:00",
            duration_sec=0,
            message_id=m_id,
            title=title[:25],
            thumbnail=thumbnail.split("?")[0],
            url=canonical,
            view_count="",
            video=video,
        )

    # ── Playlist ───────────────────────────────────────────────────────────

    async def playlist(
        self, limit: int, user: str, url: str, video: bool
    ) -> list[Track | None]:
        tracks = []
        try:
            plist = await Playlist.get(url)
            for data in plist["videos"][:limit]:
                track = Track(
                    id=data.get("id"),
                    channel_name=data.get("channel", {}).get("name", ""),
                    duration=data.get("duration"),
                    duration_sec=utils.to_seconds(data.get("duration")),
                    title=data.get("title", "")[:25],
                    thumbnail=data.get("thumbnails", [{}])[-1]
                    .get("url", "")
                    .split("?")[0],
                    url=data.get("link", "").split("&list=")[0],
                    user=user,
                    view_count="",
                    video=video,
                )
                tracks.append(track)
        except Exception:
            pass
        return tracks

    # ── Download ───────────────────────────────────────────────────────────

    async def _direct_stream_uncached(self, video_id: str) -> str | None:
        """Race both audio providers and return the first usable source.

        Both providers are started at the same time.  The winner is always a
        source that has completed successfully, so a provider cannot win just
        because its URL was cheap to construct while its media is still
        unavailable.
        """
        if not video_id:
            return None

        primary = asyncio.create_task(
            _youtubeapi_download_audio(video_id),
            name=f"youtubeapi:{video_id}",
        )
        fallback = asyncio.create_task(
            _shruti_download_audio(video_id),
            name=f"shruti:{video_id}",
        )
        try:
            done, pending = await asyncio.wait(
                {primary, fallback},
                return_when=asyncio.FIRST_COMPLETED,
            )
            # If both complete in the same loop turn, prefer youtubeapi as a
            # deterministic tie-breaker. Otherwise completion order decides.
            completed_tasks = [
                task for task in (primary, fallback) if task in done
            ]
            for completed in completed_tasks:
                try:
                    result = completed.result()
                except Exception as ex:
                    provider = "youtubeapi" if completed is primary else "ShrutiBot"
                    logger.warning("%s audio race failed for %s: %s", provider, video_id, ex)
                    result = None
                if result:
                    source = "youtubeapi" if completed is primary else "ShrutiBot"
                    logger.info("Using %s audio source: %s", source, video_id)
                    return result

            # The first provider failed; allow the other provider to finish.
            for completed in pending:
                try:
                    result = await completed
                except Exception as ex:
                    provider = "youtubeapi" if completed is primary else "ShrutiBot"
                    logger.warning("%s audio race failed for %s: %s", provider, video_id, ex)
                    result = None
                if result:
                    source = "youtubeapi" if completed is primary else "ShrutiBot"
                    logger.info("Using %s audio source: %s", source, video_id)
                    return result
            return None
        finally:
            for task in (primary, fallback):
                if task and not task.done():
                    task.cancel()
            tasks = [task for task in (primary, fallback) if task]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def direct_stream(self, video_id: str) -> str | None:
        """Return one shared, bounded audio download for a video ID."""
        if not video_id:
            return None
        started = time.perf_counter()
        result = await _deduplicated_download(
            f"audio:{video_id}",
            lambda: self._direct_stream_uncached(video_id),
        )
        log_timing("media.download", started, media_id=video_id, video=False)
        return result

    async def probe_duration(self, file_path: str) -> int:
        """Return a local media file's duration for playback UI/timers."""
        return await _probe_duration(file_path)

    async def has_media_stream(self, file_path: str, video: bool = False) -> bool:
        """Return whether a local source has a usable audio/video stream."""
        return await _has_media_stream(file_path, video=video)

    def invalidate_media(self, file_path: str | None) -> None:
        """Drop a failed local source before requesting a replacement."""
        _remove_invalid_media(file_path)

    async def download(
        self, video_id: str, video: bool = False, direct: bool = True
    ) -> str | None:
        # ── Audio source: concurrent youtubeapi + ShrutiBot race ────────────
        if not video and not direct:
            # Used only as recovery when a direct provider URL is rejected.
            return await _deduplicated_download(
                f"audio-local:{video_id}",
                lambda: _shruti_download_audio(video_id),
            )
        if not video:
            return await self.direct_stream(video_id)

        # ── Video: local ShrutiAPI MKV ─────────────────────────────────────
        if video:
            started = time.perf_counter()
            result = await _deduplicated_download(
                f"video:{video_id}",
                lambda: _shruti_download_video(video_id),
            )
            log_timing("media.download", started, media_id=video_id, video=True)
            if result:
                logger.info("ShrutiAPI video ready locally: %s", video_id)
            return result

        # ── yt-dlp fallback (audio only) ────────────────────────────────────
        yt_url = self.base + video_id
        ext = "mp4" if video else "webm"
        filename = f"downloads/{video_id}.{ext}"

        if Path(filename).exists():
            return filename

        cookie = self.get_cookies()
        base_opts = {
            "outtmpl": "downloads/%(id)s.%(ext)s",
            "quiet": True,
            "noplaylist": True,
            "geo_bypass": True,
            "no_warnings": True,
            "overwrites": False,
            "nocheckcertificate": True,
            "cookiefile": cookie,
        }

        if video:
            ydl_opts = {
                **base_opts,
                "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio)",
                "merge_output_format": "mp4",
            }
        else:
            ydl_opts = {
                **base_opts,
                "format": "bestaudio[ext=webm][acodec=opus]",
            }

        def _dl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    ydl.download([yt_url])
                except (yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError):
                    return None
                except Exception as ex:
                    logger.warning("yt-dlp failed: %s", ex)
                    return None
            return filename

        return await asyncio.to_thread(_dl)

    async def close(self) -> None:
        """Cancel media housekeeping/download tasks during shutdown."""
        tasks_to_cancel = [
            task for task in (*_background_tasks, *_download_inflight.values())
            if not task.done()
        ]
        for task in tasks_to_cancel:
            task.cancel()
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        _background_tasks.clear()
        _download_inflight.clear()
        _search_inflight.clear()
        _search_cache.clear()
