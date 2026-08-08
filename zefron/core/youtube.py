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
from pathlib import Path

from py_yt import Playlist, VideosSearch

from zefron import config, logger
from zefron.helpers import Track, utils


# ── ShrutiMusic API ────────────────────────────────────────────────────────
# /vplay must always use these Shruti credentials.  Do not read the generic
# APIKey/ApiUrl environment values here because imported projects may use
# those names for a different service.
SHRUTI_API_URL = "https://api01.shrutibots.site"
SHRUTI_API_KEYS = (
    "ShrutiBotsdZvv4iyovLZxFlVzZhv4",
    "ShrutiBotswVCjLFds5hj34MDSFTxI",
)

# ── OneGrab API (search / metadata) ───────────────────────────────────────
ONEGRAB_BASE = "https://api.onegrab.fun"

DOWNLOAD_DIR = "downloads"

# ── WAV conversion helpers ─────────────────────────────────────────────────

_conv_locks: dict = {}
_conv_lock_guard = threading.Lock()


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


# ── YoutubeAPI (primary audio source) ─────────────────────────────────────
YOUTUBEAPI_BASE = "https://youtubeapi--urlovsr.replit.app"


def _youtubeapi_direct_url(video_id: str) -> str:
    """Build the live audio endpoint without downloading or probing it."""
    return (
        f"{YOUTUBEAPI_BASE}/api/media"
        f"?url=https://youtu.be/{video_id}&type=audio&quality=360"
    )


# ── ShrutiAPI download helpers ─────────────────────────────────────────────


async def _shruti_download_audio(video_id: str) -> str | None:
    """Download audio as MP3 from ShrutiAPI, pre-convert to WAV."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    mp3_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3")
    wav_path = _wav_path(mp3_path)

    # Return cached WAV immediately
    if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
        return wav_path

    # Download MP3 if not cached
    if not (os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0):
        downloaded = False
        for api_key in SHRUTI_API_KEYS:
            if downloaded:
                break
            for attempt in range(2):
                try:
                    async with aiohttp.ClientSession() as session:
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
                                    "ShrutiAPI audio %s key=%s → HTTP %s",
                                    video_id,
                                    api_key[:12],
                                    resp.status,
                                )
                                break
                            tmp_dl = mp3_path + ".dl"
                            with open(tmp_dl, "wb") as f:
                                async for chunk in resp.content.iter_chunked(131072):
                                    f.write(chunk)
                            if os.path.exists(tmp_dl) and os.path.getsize(tmp_dl) > 0:
                                os.replace(tmp_dl, mp3_path)
                                downloaded = True
                                break
                except Exception as e:
                    logger.warning(
                        "ShrutiAPI audio key=%s attempt %s failed: %s",
                        api_key[:12],
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

    # Pre-convert to WAV
    result = await _convert_to_wav(mp3_path)

    # Background cache housekeeping
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _cleanup_wav_cache, 25)

    return result


async def _shruti_download_video(video_id: str) -> str | None:
    """Download video as MP4 from ShrutiAPI."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp4")

    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return file_path

    for api_key in SHRUTI_API_KEYS:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{SHRUTI_API_URL}/download",
                    params={"url": video_id, "type": "video", "api_key": api_key},
                    timeout=aiohttp.ClientTimeout(total=600),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "ShrutiAPI video %s key=%s → HTTP %s",
                            video_id,
                            api_key[:12],
                            resp.status,
                        )
                        continue
                    with open(file_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(131072):
                            f.write(chunk)
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                return file_path
        except Exception as e:
            logger.warning("ShrutiAPI video key=%s failed: %s", api_key[:12], e)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
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
        async with aiohttp.ClientSession() as session:
            for url in urls:
                name = url.split("/")[-1]
                link = "https://batbin.me/raw/" + name
                async with session.get(link) as resp:
                    resp.raise_for_status()
                    with open(f"{self.cookie_dir}/{name}.txt", "wb") as fw:
                        fw.write(await resp.read())
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
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{ONEGRAB_BASE}{path}", params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            logger.warning("OneGrab API error: %s", e)
        return None

    # ── Search ─────────────────────────────────────────────────────────────

    async def search(self, query: str, m_id: int, video: bool = False) -> Track | None:
        # OneGrab search
        if config.ONEGRAB_API_KEY:
            data = await self._api_get("/api/search", {"query": query})
            if data and data.get("results"):
                r = data["results"][0]
                yt_url = r.get("url", "")
                vid_id = r.get("id", "")
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

        # Fallback: py_yt
        try:
            _search = VideosSearch(query, limit=1, with_live=False)
            results = await _search.next()
        except Exception:
            return None
        if results and results["result"]:
            data = results["result"][0]
            return Track(
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
            )
        return None

    # ── Track metadata from URL ────────────────────────────────────────────

    async def get_track(self, url: str, m_id: int, video: bool = False) -> Track | None:
        data = await self._api_get("/api/get_url", {"url": url})
        if data and data.get("results"):
            r = data["results"][0]
            yt_url = r.get("url", url)
            vid_id = r.get("id", "")
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
        return None

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

    async def direct_stream(self, video_id: str) -> str | None:
        """Return a remote audio stream immediately; never create a local file."""
        if not video_id:
            return None
        url = _youtubeapi_direct_url(video_id)
        logger.info("Using direct audio stream: %s", video_id)
        return url

    async def download(
        self, video_id: str, video: bool = False, direct: bool = True
    ) -> str | None:
        # ── Audio fallback: direct stream → local providers ────────────────
        if not video and direct:
            result = await self.direct_stream(video_id)
            if result:
                return result
            logger.warning(
                "Direct audio stream unavailable for %s, trying local fallback",
                video_id,
            )
            result = await _shruti_download_audio(video_id)
            if result:
                logger.info("ShrutiAPI audio ready: %s", result)
                return result
            logger.warning(
                "ShrutiAPI audio failed for %s, falling back to yt-dlp", video_id
            )

        # ── Video: direct ShrutiAPI stream ────────────────────────────────
        if video:
            if direct:
                result = _shruti_video_stream_url(video_id)
                if result:
                    logger.info("Using direct ShrutiAPI video stream: %s", video_id)
                    return result
                logger.error("ShrutiAPI key is not configured; cannot play video")
                return None

            # Direct streaming already failed. Keep the fallback on
            # ShrutiAPI, but materialize a local MP4 so ffmpeg does not have
            # to parse the provider's live response again.
            result = await _shruti_download_video(video_id)
            if result:
                logger.info("ShrutiAPI video fallback ready: %s", video_id)
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
