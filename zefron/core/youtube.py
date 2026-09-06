import asyncio
import glob
import logging
import os
import re
import time
from urllib.parse import parse_qs, urlparse
from typing import Union
import yt_dlp
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from py_yt import VideosSearch, Playlist
import aiohttp
from zefron.core.http import get_http_session
from zefron.core.metrics import log_timing

API_URL = (
    os.environ.get("ApiUrl")
    or os.environ.get("SHRUTI_API_URL")
    or "https://shrutibots.site"
).rstrip("/")
API_URL_FALLBACKS = (
    "https://shrutibots.site",
    "https://api.shrutibots.site",
)

# SHRUTI_API_KEY is the canonical credential. APIKey is kept as a legacy
# alias, but must not override a valid Shruti-specific key.
API_KEY = os.environ.get("SHRUTI_API_KEY") or os.environ.get("APIKey", "")
YUKI_STREAM_URL = "https://music.yukiapi.site/stream"
YUKI_API_KEY = os.environ.get("YUKI_API_KEY", "")
BAKAGAME_DOWNLOAD_URL = "https://ytapi.bakagame.online/api/download"

DOWNLOAD_DIR = "downloads"
MEDIA_RETENTION_SECONDS = 60 * 60
LOGGER = logging.getLogger(__name__)


def time_to_seconds(time):
    stringt = str(time)
    return sum(int(x) * 60**i for i, x in enumerate(reversed(stringt.split(":"))))


def _secs_to_str(seconds: int) -> str:
    minutes, seconds = divmod(int(seconds or 0), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


async def download_song(link: str) -> str:
    started = time.perf_counter()
    video_id = _video_id(link)
    if not video_id or len(video_id) < 3:
        return None

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3")
    if await _is_valid_media_file(file_path):
        _log_download_timing(
            "song",
            started,
            video_id,
            file_path,
            "cache",
        )
        return file_path

    if await _download_from_sources(video_id, "audio", file_path, timeout=300):
        _log_download_timing(
            "song",
            started,
            video_id,
            file_path,
            "provider",
        )
        return file_path

    downloaded = await _download_with_ytdlp(video_id, video=False)
    _log_download_timing(
        "song",
        started,
        video_id,
        downloaded,
        "ytdlp" if downloaded else "failed",
    )
    return downloaded


async def download_video(link: str) -> str:
    started = time.perf_counter()
    video_id = _video_id(link)
    if not video_id or len(video_id) < 3:
        return None

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp4")
    if await _is_valid_media_file(file_path):
        _log_download_timing(
            "video",
            started,
            video_id,
            file_path,
            "cache",
        )
        return file_path

    if await _download_from_sources(video_id, "video", file_path, timeout=600):
        _log_download_timing(
            "video",
            started,
            video_id,
            file_path,
            "provider",
        )
        return file_path

    downloaded = await _download_with_ytdlp(video_id, video=True)
    _log_download_timing(
        "video",
        started,
        video_id,
        downloaded,
        "ytdlp" if downloaded else "failed",
    )
    return downloaded


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
    speed_mbps = size_mb / elapsed_seconds
    log_timing(
        f"download.{media_type}",
        started,
        media_id=video_id,
        result=result,
        size_mb=round(size_mb, 2),
        speed_mbps=round(speed_mbps, 2),
    )


def _provider_urls() -> list[str]:
    """Return configured and known provider hosts without duplicates."""
    urls = [API_URL, *API_URL_FALLBACKS]
    return list(dict.fromkeys(url.rstrip("/") for url in urls if url))


async def _download_source(
    session: aiohttp.ClientSession,
    url: str,
    params: dict[str, str],
    headers: dict[str, str],
    temp_path: str,
    timeout: int,
    source_name: str,
) -> str | None:
    """Download one source into an isolated temp file and return it if valid."""
    downloaded = False
    _remove_file(temp_path)
    try:
        async with session.get(
            url,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            if response.status != 200:
                LOGGER.warning(
                    "%s download failed: HTTP %s",
                    source_name,
                    response.status,
                )
                return None
            with open(temp_path, "wb") as output:
                async for chunk in response.content.iter_chunked(131072):
                    output.write(chunk)

        if await _is_valid_media_file(temp_path):
            downloaded = True
            return temp_path
        # In a parallel race, a losing provider can legitimately return an
        # unusable response while another provider succeeds. Keep this detail
        # out of warning-level logs unless every source fails.
        LOGGER.debug("%s returned invalid media", source_name)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOGGER.warning(
            "%s request failed: %s",
            source_name,
            type(exc).__name__,
        )
    finally:
        if not downloaded:
            _remove_file(temp_path)
    return None


async def _download_from_sources(
    video_id: str,
    media_type: str,
    file_path: str,
    timeout: int,
) -> bool:
    """Race all configured media sources and keep the first valid result."""
    session = await get_http_session()
    sources: list[tuple[str, str, dict[str, str], dict[str, str]]] = []
    accept = "audio/mpeg" if media_type == "audio" else "video/*"

    for base_url in _provider_urls():
        sources.append(
            (
                f"ShrutiAPI {base_url}",
                f"{base_url}/download",
                {
                    "url": video_id,
                    "type": media_type,
                    "api_key": API_KEY,
                },
                {"Accept": accept},
            )
        )

    # BakaGame returns a direct audio redirect and does not need an API key.
    # It is an audio source because its endpoint returns MP3 media.
    if media_type == "audio":
        sources.append(
            (
                "BakaGame API",
                BAKAGAME_DOWNLOAD_URL,
                {"id": video_id},
                {"Accept": "audio/mpeg"},
            )
        )
        if YUKI_API_KEY:
            sources.append(
                (
                    "YukiAPI",
                    f"{YUKI_STREAM_URL}/{video_id}",
                    {"key": YUKI_API_KEY},
                    {"Accept": "audio/*"},
                )
            )

    source_paths: dict[str, str] = {}
    tasks = []
    for index, (source_name, url, params, headers) in enumerate(sources):
        temp_path = f"{file_path}.{index}.part"
        source_paths[temp_path] = source_name
        tasks.append(
            asyncio.create_task(
                _download_source(
                    session,
                    url,
                    params,
                    headers,
                    temp_path,
                    timeout,
                    source_name,
                )
            )
        )
    pending = set(tasks)
    winner_temp: str | None = None
    winner_moved = False

    try:
        while pending and winner_temp is None:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                try:
                    result = task.result()
                except asyncio.CancelledError:
                    continue
                except Exception as exc:
                    LOGGER.warning(
                        "Media source task failed: %s",
                        type(exc).__name__,
                    )
                    continue
                if result:
                    winner_temp = result
                    break

        if winner_temp:
            _remove_file(file_path)
            os.replace(winner_temp, file_path)
            winner_moved = True
            LOGGER.info(
                "Selected first valid %s source=%s for %s",
                media_type,
                source_paths.get(winner_temp, "unknown"),
                video_id,
            )
            return True
        return False
    finally:
        for task in pending:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, str) and result != winner_temp:
                _remove_file(result)
        if winner_temp and not winner_moved:
            _remove_file(winner_temp)


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
    except FileNotFoundError:
        pass
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


def _download_with_ytdlp_sync(video_id: str, video: bool) -> str | None:
    """Last-resort local download when the media provider returns bad data."""
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
        options.update(
            {
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ]
            }
        )
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
        candidates = sorted(glob.glob(os.path.join(DOWNLOAD_DIR, f"{video_id}.*")))
        candidates = [
            path
            for path in candidates
            if not path.endswith(".part") and os.path.getsize(path) > 0
        ]
        if video:
            return candidates[0] if candidates else None
        candidate = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3")
        return (
            candidate
            if os.path.isfile(candidate) and os.path.getsize(candidate)
            else None
        )
    except Exception:
        return None


async def _download_with_ytdlp(video_id: str, video: bool) -> str | None:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    return await asyncio.to_thread(_download_with_ytdlp_sync, video_id, video)


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
        """Return whether a URL points to YouTube media."""
        parsed = urlparse(str(link or "").strip())
        host = (parsed.hostname or "").lower()
        return host == "youtu.be" or host.endswith("youtube.com")

    def invalid(self, link: str) -> bool:
        """Return whether a supplied URL is malformed or not HTTP(S)."""
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
        return Track(
            id=video_id,
            channel_name=channel,
            duration=duration,
            duration_sec=YouTubeAPI._seconds(duration),
            title=result.get("title", ""),
            url=result.get("link") or f"https://www.youtube.com/watch?v={video_id}",
            message_id=message_id,
            thumbnail=thumbnail.split("?")[0],
            view_count=(result.get("viewCount") or {}).get("short")
            if isinstance(result.get("viewCount"), dict)
            else result.get("viewCount"),
            video=video,
        )

    async def search(self, query: str, m_id: int, video: bool = False):
        """Return the project Track object for the best YouTube result."""
        if not isinstance(query, str) or not query.strip():
            return None
        try:
            results = await VideosSearch(query.strip(), limit=1).next()
            matches = (results or {}).get("result") or []
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

    async def direct_stream(self, video_id: str) -> str:
        """Download the audio file expected by the project's player."""
        return await download_song(video_id)

    async def probe_duration(self, file_path: str) -> int:
        """Keep the playback adapter compatible when duration is unavailable."""
        return 0

    async def has_media_stream(self, file_path: str, video: bool = False) -> bool:
        if not file_path:
            return False
        if isinstance(file_path, str) and file_path.startswith(("http://", "https://")):
            return True
        return os.path.isfile(file_path) and os.path.getsize(file_path) > 0

    def invalidate_media(self, file_path: str | None) -> None:
        if file_path and os.path.isfile(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

    def schedule_delete(
        self, file_path: str | None, delay: int = MEDIA_RETENTION_SECONDS
    ) -> None:
        """Delete a completed local download after it has been played."""
        if not file_path or (
            isinstance(file_path, str) and file_path.startswith(("http://", "https://"))
        ):
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
        """The attached downloader does not require cookies."""
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
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
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
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            return result["title"]

    async def duration(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            return result["duration"]

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
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
        if videoid:
            if not project_call:
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
            track = self._result_track(
                data,
                message_id=0,
                video=project_video,
            )
            track.user = project_user
            tracks.append(track)
        return tracks if project_call else ids

    async def track(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title = result["title"]
            duration_min = result["duration"]
            vidid = result["id"]
            yturl = result["link"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
        track_details = {
            "title": title,
            "link": yturl,
            "vidid": vidid,
            "duration_min": duration_min,
            "thumb": thumbnail,
        }
        return track_details, vidid

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
        a = VideosSearch(link, limit=10)
        result = (await a.next()).get("result")
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
            if downloaded_file:
                return downloaded_file
            return None
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


# The main project creates this provider with ``YouTube()`` in zefron/__init__.py.
# Export the class here instead of an already-created instance so both module
# loading and the project's existing initialization path remain compatible.
YouTube = YouTubeAPI
