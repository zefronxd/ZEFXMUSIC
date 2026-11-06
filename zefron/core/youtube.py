import asyncio
import glob
import logging
import os
import re
import time
from urllib.parse import parse_qs, urlparse
from typing import Union

import aiofiles
import aiohttp
import yt_dlp
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from py_yt import VideosSearch, Playlist

from zefron.core.http import get_http_session
from zefron.core.metrics import log_timing

try:
    from ytmusicapi import YTMusic
except Exception:  # ytmusicapi not installed
    YTMusic = None

LOGGER = logging.getLogger(__name__)

# ------------------------------------------------------------ Zefron API
# GET {ZEFRON_API_URL}/download?url=VIDEO_ID&type=audio|video&api_key=KEY
ZEFRON_API_URL = (os.environ.get("ZEFRON_API_URL") or "https://zefronapi.me").rstrip("/")
ZEFRON_API_KEY = (os.environ.get("ZEFRON_API_KEY") or "").strip()
ZEFRON_RETRIES = int(os.environ.get("ZEFRON_RETRIES", "2"))

# Song search hard limit (seconds). Whole search (incl. retries) never exceeds this.
SEARCH_TIMEOUT = float(os.environ.get("SEARCH_TIMEOUT", "10"))
SEARCH_ATTEMPTS = max(1, int(os.environ.get("SEARCH_ATTEMPTS", "2")))

DOWNLOAD_DIR = "downloads"
DOWNLOAD_CONCURRENCY = int(os.environ.get("DOWNLOAD_CONCURRENCY", "4"))
MEDIA_RETENTION_SECONDS = 60 * 60
_semaphore = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    return _semaphore


def _env_dir(name: str) -> str:
    value = os.environ.get(name, "").strip()
    return os.path.abspath(os.path.expanduser(value)) if value else ""


AUDIO_DOWNLOAD_PATH = _env_dir("AUDIO_DOWNLOAD_PATH")
VIDEO_DOWNLOAD_PATH = _env_dir("VIDEO_DOWNLOAD_PATH")
AUDIO_EXTENSIONS = ("webm", "m4a", "mp3", "ogg")
VIDEO_EXTENSIONS = ("mp4", "mkv", "webm")


def is_external_path(path) -> bool:
    if not path:
        return False
    full = os.path.abspath(str(path))
    for base in (AUDIO_DOWNLOAD_PATH, VIDEO_DOWNLOAD_PATH):
        if base and full.startswith(base + os.sep):
            return True
    return False


def _find_external(directory: str, video_id: str, extensions):
    for ext in extensions:
        path = os.path.join(directory, f"{video_id}.{ext}")
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    return None


def time_to_seconds(time):
    stringt = str(time)
    return sum(int(x) * 60**i for i, x in enumerate(reversed(stringt.split(":"))))


def _secs_to_str(seconds: int) -> str:
    minutes, seconds = divmod(int(seconds or 0), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _video_id(link: str) -> str:
    """Extract a safe YouTube ID from an ID or common YouTube URL."""
    value = str(link or "").strip()
    if not value:
        return ""
    if "://" not in value:
        return value.split("&", 1)[0]
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        return parsed.path.strip("/").split("/", 1)[0]
    if host.endswith("youtube.com"):
        query_id = parse_qs(parsed.query).get("v", [""])[0]
        if query_id:
            return query_id
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
            return parts[1]
    return ""


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


async def _is_valid_media_file(path: str) -> bool:
    """Reject provider error pages/JSON saved with an audio/video extension."""
    if not path or not os.path.isfile(path) or os.path.getsize(path) <= 0:
        return False
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=format_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        return process.returncode == 0 and bool(stdout.strip())
    except (OSError, asyncio.CancelledError):
        return False


def _log_download_timing(
    media_type: str,
    started: float,
    video_id: str,
    file_path: str | None,
    result: str,
) -> None:
    """Log total download time and the resulting file's approximate rate."""
    size_bytes = 0
    if file_path and os.path.isfile(file_path):
        try:
            size_bytes = os.path.getsize(file_path)
        except OSError:
            pass
    elapsed_seconds = max(time.perf_counter() - started, 0.001)
    size_mb = size_bytes / (1024 * 1024)
    log_timing(
        f"download.{media_type}",
        started,
        media_id=video_id,
        result=result,
        size_mb=round(size_mb, 2),
        speed_mbps=round(size_mb / elapsed_seconds, 2),
    )


# ------------------------------------------------------------ downloading
# Errors that retrying cannot fix: a bad / expired key or an used-up daily limit.
_FATAL_STATUS = {
    400: "bad request",
    401: "API key invalid or expired - generate a new one in the Zefron bot (/apikey)",
    429: "daily download limit reached",
}


async def _download_zefron(session, video_id, path, kind) -> bool:
    """Download one file from the Zefron API. Returns True when `path` was written."""
    temp_path = f"{path}.part"
    timeout = aiohttp.ClientTimeout(
        total=300 if kind == "audio" else 600,
        connect=10,
        sock_connect=10,
        sock_read=60,
    )
    params = {"url": video_id, "type": kind, "api_key": ZEFRON_API_KEY}
    try:
        async with session.get(
            f"{ZEFRON_API_URL}/download", params=params, timeout=timeout
        ) as response:
            if response.status in _FATAL_STATUS:
                LOGGER.warning(
                    "Zefron API %s: %s", response.status, _FATAL_STATUS[response.status]
                )
                raise PermissionError(response.status)
            if response.status != 200:
                LOGGER.warning(
                    "Zefron API returned %s for %s (%s)", response.status, video_id, kind
                )
                return False
            content_type = response.headers.get("Content-Type", "").lower()
            if "json" in content_type or content_type.startswith("text/"):
                return False
            if kind == "audio" and content_type.startswith("video/"):
                return False
            async with aiofiles.open(temp_path, "wb") as output:
                async for chunk in response.content.iter_chunked(131072):
                    await output.write(chunk)
        if await _is_valid_media_file(temp_path):
            os.replace(temp_path, path)
            return True
    except PermissionError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        pass
    finally:
        if os.path.exists(temp_path):
            await asyncio.to_thread(_remove_file, temp_path)
    return False


async def _download_unlimited(link: str, kind: str) -> str | None:
    started = time.perf_counter()
    video_id = _video_id(link)
    if not video_id or len(video_id) < 3:
        return None

    is_audio = kind == "audio"
    external = AUDIO_DOWNLOAD_PATH if is_audio else VIDEO_DOWNLOAD_PATH
    extensions = AUDIO_EXTENSIONS if is_audio else VIDEO_EXTENSIONS

    if external:
        found = _find_external(external, video_id, extensions)
        if found:
            _log_download_timing(kind, started, video_id, found, "external")
            return found

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    path = os.path.join(DOWNLOAD_DIR, f"{video_id}.{'mp3' if is_audio else 'mp4'}")
    if await _is_valid_media_file(path):
        _log_download_timing(kind, started, video_id, path, "cache")
        return path

    if not ZEFRON_API_KEY:
        LOGGER.error("ZEFRON_API_KEY is not set")
    else:
        session = await get_http_session()
        attempts = max(1, ZEFRON_RETRIES)
        for attempt in range(1, attempts + 1):
            try:
                if await _download_zefron(session, video_id, path, kind):
                    _log_download_timing(kind, started, video_id, path, "provider")
                    return path
            except PermissionError:
                break  # retrying will not help
            if attempt < attempts:
                await asyncio.sleep(1)

    # Last resort: local yt-dlp download (remove this block to disable it).
    downloaded = await _download_with_ytdlp(video_id, video=not is_audio)
    _log_download_timing(
        kind, started, video_id, downloaded, "ytdlp" if downloaded else "failed"
    )
    return downloaded


async def _download(link: str, kind: str) -> str | None:
    async with _get_semaphore():
        return await _download_unlimited(link, kind)


async def download_song(link: str) -> str | None:
    return await _download(link, "audio")


async def download_video(link: str) -> str | None:
    return await _download(link, "video")


def _download_with_ytdlp_sync(video_id: str, video: bool) -> str | None:
    url = f"https://www.youtube.com/watch?v={video_id}"
    output = os.path.join(DOWNLOAD_DIR, f"{video_id}.%(ext)s")
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": output,
        "format": "bestvideo*+bestaudio/best" if video else "bestaudio/best",
        "js_runtimes": {"deno": {}},
    }
    cookie_file = os.environ.get("YOUTUBE_COOKIES") or os.environ.get("YT_COOKIES")
    if cookie_file and os.path.isfile(cookie_file):
        options["cookiefile"] = cookie_file
    if not video:
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
        if video:
            candidates = sorted(glob.glob(os.path.join(DOWNLOAD_DIR, f"{video_id}.*")))
            candidates = [
                p for p in candidates if not p.endswith(".part") and os.path.getsize(p) > 0
            ]
            return candidates[0] if candidates else None
        candidate = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3")
        if os.path.isfile(candidate) and os.path.getsize(candidate):
            return candidate
        return None
    except Exception:
        return None


async def _download_with_ytdlp(video_id: str, video: bool) -> str | None:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    return await asyncio.to_thread(_download_with_ytdlp_sync, video_id, video)


# ----------------------------------------------------------------- search
async def _search(query: str, limit: int = 1) -> list:
    """YouTube search with a hard overall time limit (SEARCH_TIMEOUT seconds).

    The time budget is split between the attempts, so a slow / stuck request is
    cancelled and retried instead of making the user wait. Raises TimeoutError
    if nothing is found in time.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SEARCH_TIMEOUT
    for attempt in range(SEARCH_ATTEMPTS):
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        per_attempt = remaining / (SEARCH_ATTEMPTS - attempt)
        try:
            data = await asyncio.wait_for(
                VideosSearch(query, limit=limit).next(), per_attempt
            )
            result = (data or {}).get("result") or []
            if result:
                return result
        except Exception as e:
            LOGGER.warning("YouTube search attempt %s failed: %s", attempt + 1, e)
    raise TimeoutError(f"YouTube search failed within {SEARCH_TIMEOUT:g}s")


# --------------------------------------------------------------- autoplay
AUTOPLAY_REQUEST_TIMEOUT = 20
AUTOPLAY_MAX_RETRIES = 3
AUTOPLAY_RETRY_DELAY = 1
MAX_TRACK_SECONDS = 900

_ytmusic = None
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _length_to_seconds(value) -> int:
    try:
        parts = [int(x) for x in str(value).split(":")]
    except Exception:
        return 0
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _ytmusic_radio(video_id: str, limit: int) -> list:
    global _ytmusic
    if YTMusic is None:
        return []
    if _ytmusic is None:
        _ytmusic = YTMusic()
    data = _ytmusic.get_watch_playlist(videoId=video_id, radio=True, limit=limit)
    tracks = []
    for item in data.get("tracks", []):
        vid = item.get("videoId")
        if not vid or vid == video_id or not _ID_RE.match(vid):
            continue
        duration = item.get("duration_seconds") or _length_to_seconds(item.get("length"))
        if not duration or duration > MAX_TRACK_SECONDS:
            continue
        artists = ", ".join(
            a.get("name", "") for a in item.get("artists") or [] if a.get("name")
        )
        title = item.get("title") or "Autoplay Track"
        tracks.append(
            {
                "video_id": vid,
                "title": f"{title} - {artists}" if artists else title,
                "duration": int(duration),
            }
        )
    return tracks


async def get_autoplay(
    video_id: str,
    timeout: int = AUTOPLAY_REQUEST_TIMEOUT,
    retries: int = AUTOPLAY_MAX_RETRIES,
    seed_id: str = None,
) -> list:
    video_id = _video_id(video_id)
    if not video_id or len(video_id) < 3:
        return []

    loop = asyncio.get_running_loop()
    sources = [video_id]
    if seed_id and seed_id != video_id and _ID_RE.match(str(seed_id)):
        sources.append(seed_id)

    for attempt in range(1, retries + 1):
        try:
            results = []
            for source in sources:
                found = await asyncio.wait_for(
                    loop.run_in_executor(None, _ytmusic_radio, source, 25),
                    timeout,
                )
                results.append(found)
            merged, seen = [], set()
            for i in range(max((len(r) for r in results), default=0)):
                for r in results:
                    if i < len(r) and r[i]["video_id"] not in seen:
                        seen.add(r[i]["video_id"])
                        merged.append(r[i])
            if merged:
                return merged
        except Exception:
            pass
        if attempt < retries:
            await asyncio.sleep(AUTOPLAY_RETRY_DELAY)
    return []


class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.status = "https://www.youtube.com/oembed?url="
        self.listbase = "https://youtube.com/playlist?list="
        self.reg = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        self._cleanup_tasks = {}

    @staticmethod
    def _seconds(duration) -> int:
        if not duration:
            return 0
        try:
            return (
                int(duration) if str(duration).isdigit() else time_to_seconds(duration)
            )
        except (TypeError, ValueError):
            return 0

    def valid(self, link: str) -> bool:
        parsed = urlparse(str(link or "").strip())
        host = (parsed.hostname or "").lower()
        return host == "youtu.be" or host.endswith("youtube.com")

    def invalid(self, link: str) -> bool:
        parsed = urlparse(str(link or "").strip())
        return parsed.scheme not in {"http", "https"} or not parsed.netloc

    @staticmethod
    def _result_track(result, message_id: int, video: bool):
        from zefron.helpers._dataclass import Track

        channel = result.get("channel") or ""
        if isinstance(channel, dict):
            channel = channel.get("name", "")
        thumbnails = result.get("thumbnails") or []
        thumbnail = thumbnails[-1].get("url", "") if thumbnails else ""
        duration = result.get("duration") or "0:00"
        video_id = result.get("id")
        view_count = result.get("viewCount")
        if isinstance(view_count, dict):
            view_count = view_count.get("short")
        return Track(
            id=video_id,
            channel_name=channel,
            duration=duration,
            duration_sec=YouTubeAPI._seconds(duration),
            title=result.get("title", ""),
            url=result.get("link") or f"https://www.youtube.com/watch?v={video_id}",
            message_id=message_id,
            thumbnail=thumbnail.split("?")[0],
            view_count=view_count,
            video=video,
        )

    async def search(self, query: str, m_id: int, video: bool = False):
        """Return the project Track object for the best YouTube result."""
        if not isinstance(query, str) or not query.strip():
            return None
        try:
            matches = await _search(query.strip(), 1)
        except Exception:
            matches = []
        if not matches:
            try:
                matches = await asyncio.to_thread(self._ytdlp_search, query.strip())
            except Exception:
                matches = []
        if not matches:
            return None
        return self._result_track(matches[0], m_id, video)

    @staticmethod
    def _ytdlp_search(query: str) -> list[dict]:
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
            "noplaylist": True,
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            return info.get("entries") or []
        except Exception:
            return []

    async def autoplay(self, video_id: str, seed_id: str = None) -> list:
        """Related tracks (YouTube Music radio) for autoplay."""
        return await get_autoplay(video_id, seed_id=seed_id)

    async def direct_stream(self, video_id: str) -> str:
        return await download_song(video_id)

    async def probe_duration(self, file_path: str) -> int:
        return 0

    async def has_media_stream(self, file_path: str, video: bool = False) -> bool:
        if not file_path:
            return False
        if isinstance(file_path, str) and file_path.startswith(("http://", "https://")):
            return True
        return os.path.isfile(file_path) and os.path.getsize(file_path) > 0

    def invalidate_media(self, file_path: str | None) -> None:
        if file_path and os.path.isfile(file_path):
            _remove_file(file_path)

    def schedule_delete(
        self, file_path: str | None, delay: int = MEDIA_RETENTION_SECONDS
    ) -> None:
        """Delete a completed local download after it has been played."""
        if not file_path or (
            isinstance(file_path, str) and file_path.startswith(("http://", "https://"))
        ):
            return
        # Never delete files from the user's external download folders.
        if is_external_path(file_path):
            return

        previous = self._cleanup_tasks.pop(file_path, None)
        if previous and not previous.done():
            previous.cancel()

        async def _delete_later() -> None:
            try:
                await asyncio.sleep(delay)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except asyncio.CancelledError:
                raise
            except OSError:
                pass
            finally:
                if self._cleanup_tasks.get(file_path) is asyncio.current_task():
                    self._cleanup_tasks.pop(file_path, None)

        self._cleanup_tasks[file_path] = asyncio.create_task(
            _delete_later(), name=f"delete-media:{os.path.basename(file_path)}"
        )

    async def save_cookies(self, urls: list[str]) -> None:
        return None

    async def exists(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        return bool(re.search(self.regex, link))

    async def url(self, message_1: Message) -> Union[str, None]:
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        for message in messages:
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        text = message.text or message.caption
                        return text[entity.offset : entity.offset + entity.length]
            elif message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        return None

    async def details(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        result = (await _search(link, 1))[0]
        title = result["title"]
        duration_min = result["duration"]
        thumbnail = result["thumbnails"][0]["url"].split("?")[0]
        vidid = result["id"]
        duration_sec = int(time_to_seconds(duration_min)) if duration_min else 0
        return title, duration_min, duration_sec, thumbnail, vidid

    async def title(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        return (await _search(link, 1))[0]["title"]

    async def duration(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        return (await _search(link, 1))[0]["duration"]

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        result = (await _search(link, 1))[0]
        return result["thumbnails"][0]["url"].split("?")[0]

    async def video(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        try:
            downloaded_file = await download_video(link)
            if downloaded_file:
                return 1, downloaded_file
            return 0, "Video download failed"
        except Exception as e:
            return 0, f"Video download error: {e}"

    async def playlist(self, link, limit, user_id, videoid: Union[bool, str] = None):
        project_call = isinstance(link, int)
        if project_call:
            project_limit = link
            project_user = limit
            project_link = user_id
            project_video = bool(videoid)
            link = project_link
            limit = project_limit
        else:
            project_user = None
            project_video = False
        if videoid and not project_call:
            link = self.listbase + link
        if "&" in link:
            link = link.split("&")[0]
        try:
            plist = await Playlist.get(link)
        except Exception:
            return []
        videos = plist.get("videos") or []
        ids = []
        tracks = []
        for data in videos[:limit]:
            if not data:
                continue
            vid = data.get("id")
            if not vid:
                continue
            if not project_call:
                ids.append(vid)
                continue
            track = self._result_track(data, message_id=0, video=project_video)
            track.user = project_user
            tracks.append(track)
        return tracks if project_call else ids

    async def track(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        result = (await _search(link, 1))[0]
        track_details = {
            "title": result["title"],
            "link": result["link"],
            "vidid": result["id"],
            "duration_min": result["duration"],
            "thumb": result["thumbnails"][0]["url"].split("?")[0],
        }
        return track_details, result["id"]

    async def formats(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        ytdl_opts = {"quiet": True}
        ydl = yt_dlp.YoutubeDL(ytdl_opts)
        with ydl:
            formats_available = []
            r = ydl.extract_info(link, download=False)
            for format in r["formats"]:
                try:
                    if "dash" not in str(format["format"]).lower():
                        formats_available.append(
                            {
                                "format": format["format"],
                                "filesize": format.get("filesize"),
                                "format_id": format["format_id"],
                                "ext": format["ext"],
                                "format_note": format["format_note"],
                                "yturl": link,
                            }
                        )
                except Exception:
                    continue
        return formats_available, link

    async def slider(
        self, link: str, query_type: int, videoid: Union[bool, str] = None
    ):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        result = await _search(link, 10)
        query_type = max(0, min(query_type, len(result) - 1))
        title = result[query_type]["title"]
        duration_min = result[query_type]["duration"]
        vidid = result[query_type]["id"]
        thumbnail = result[query_type]["thumbnails"][0]["url"].split("?")[0]
        return title, duration_min, thumbnail, vidid

    async def download(
        self,
        link: str,
        mystic=None,
        video: Union[bool, str] = None,
        videoid: Union[bool, str] = None,
        songaudio: Union[bool, str] = None,
        songvideo: Union[bool, str] = None,
        format_id: Union[bool, str] = None,
        title: Union[bool, str] = None,
        direct: Union[bool, str] = None,
    ) -> str:
        if videoid:
            link = self.base + link
        try:
            if video:
                downloaded_file = await download_video(link)
            else:
                downloaded_file = await download_song(link)
            return downloaded_file or None
        except Exception:
            return None

    async def close(self) -> None:
        """Compatibility hook for the application's shutdown sequence."""
        tasks = [task for task in self._cleanup_tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._cleanup_tasks.clear()


# The main project creates this provider with ``YouTube()`` in zefron/__init__.py,
# so the class (not an instance) is exported here.
YouTube = YouTubeAPI
