from os import getenv
from dotenv import load_dotenv

load_dotenv()


class Config:
    def __init__(self):
        self.API_ID = int(getenv("API_ID", 0))
        self.API_HASH = getenv("API_HASH")

        self.BOT_TOKEN = getenv("BOT_TOKEN") or getenv("TOKEN")
        self.MONGO_URL = getenv("MONGO_URL") or getenv("MONGO_URI")

        self.LOGGER_ID = int(getenv("LOGGER_ID", 0))
        self.OWNER_ID = int(getenv("OWNER_ID", 0))

        self.DURATION_LIMIT = (
            int(getenv("DURATION_LIMIT") or getenv("SONG_DURATION_LIMIT", 60)) * 60
        )
        self.QUEUE_LIMIT = int(getenv("QUEUE_LIMIT", 20))
        self.PLAYLIST_LIMIT = int(getenv("PLAYLIST_LIMIT", 20))
        self.SPOTIFY_QUEUE_LIMIT = 100
        self.PYROGRAM_MAX_CONCURRENT_TRANSMISSIONS = int(
            getenv("PYROGRAM_MAX_CONCURRENT_TRANSMISSIONS", 32)
        )
        self.MEDIA_DOWNLOAD_CONCURRENCY = int(
            getenv("MEDIA_DOWNLOAD_CONCURRENCY", 12)
        )

        self.SESSION1 = getenv("SESSION") or getenv("STRING1") or None
        self.SESSION2 = getenv("SESSION2", None)
        self.SESSION3 = getenv("SESSION3", None)

        self.SUPPORT_CHANNEL = getenv(
            "SUPPORT_CHANNEL", "https://t.me/zefron_musicworld"
        )
        self.SUPPORT_CHAT = getenv("SUPPORT_CHAT", "https://t.me/+urALmsBSdB9hYWE5")

        # Assistant cleanup compatibility with the imported auto-leave
        # feature. Keep AUTO_LEAVE working for existing deployments.
        self.AUTO_LEAVING_ASSISTANT: bool = (
            getenv(
                "AUTO_LEAVING_ASSISTANT",
                getenv("AUTO_LEAVE", "False"),
            ).lower()
            == "true"
        )
        self.AUTO_LEAVE = self.AUTO_LEAVING_ASSISTANT
        self.AUTO_LEAVE_ASSISTANT_TIME = max(
            int(getenv("AUTO_LEAVE_ASSISTANT_TIME", 3600)),
            300,
        )
        self.AUTO_END: bool = getenv("AUTO_END", "False").lower() == "true"

        self.THUMB_GEN: bool = getenv("THUMB_GEN", "True").lower() == "true"
        self.VIDEO_PLAY: bool = getenv("VIDEO_PLAY", "True").lower() == "true"

        # Telegram voice-chat baseline: PyTgCalls HIGH is 48 kHz stereo.
        # Keep these explicit so every playback path uses the same contract.
        self.AUDIO_SAMPLE_RATE = 48000
        self.AUDIO_CHANNELS = 2

        self.LANG_CODE = getenv("LANG_CODE", "en")

        # Spotify Web API Client Credentials flow. Keep both values in
        # Replit Secrets/environment variables; never hardcode credentials.
        self.SPOTIFY_CLIENT_ID = getenv("SPOTIFY_CLIENT_ID")
        self.SPOTIFY_CLIENT_SECRET = getenv("SPOTIFY_CLIENT_SECRET")
        # Kept for the non-Spotify search fallback used by older imports.
        self.ONEGRAB_API_KEY = getenv("ONEGRAB_API_KEY")
        # ShrutiAPI is the only provider used for /vplay.  Keep its
        # connection details in environment variables so the API can be
        # changed without touching the playback code.
        self.SHRUTI_API_URL = (
            getenv("ApiUrl")
            or getenv("SHRUTI_API_URL")
            or "https://api01.shrutibots.site"
        )
        self.SHRUTI_API_KEY = "ShrutiBotsYqjNN7t6SuBLh3oi4edd"
        self.SHRUTI_API_KEY2 = "ShrutiBotsdZvv4iyovLZxFlVzZhv4"

        self.LASTFM_API_KEY = "d983c47914d76833e419d2dd8fdcf4ce"
        self.LASTFM_SHARED_SECRET = "b76e8a96fb70385fd2e024a0ac21dbb2"
        self.LASTFM_API_KEY_2 = "557160c9e176a2d56f5fc66278e88927"
        self.LASTFM_SHARED_SECRET_2 = "44805e4f0c2083b83a987d7e877a3483"

        self.COOKIES_URL = [
            url
            for url in getenv("COOKIES_URL", "").split(" ")
            if url and "batbin.me" in url
        ]
        self.DEFAULT_THUMB = getenv("DEFAULT_THUMB", "https://d.uguu.se/UBQqgQsK.jpg")
        self.PING_IMG = getenv("PING_IMG", "https://d.uguu.se/UBQqgQsK.jpg")
        self._primary_start_img = "https://d.uguu.se/UBQqgQsK.jpg"
        _start_imgs = [
            "https://files.catbox.moe/x5lytj.jpg",
            "https://files.catbox.moe/psya34.jpg",
            "https://files.catbox.moe/leaexg.jpg",
            "https://files.catbox.moe/b0e4vk.jpg",
            "https://files.catbox.moe/1b1wap.jpg",
            "https://files.catbox.moe/ommjjk.jpg",
            "https://files.catbox.moe/onurxm.jpg",
            "https://files.catbox.moe/97v75k.jpg",
            "https://files.catbox.moe/t833zy.jpg",
            "https://files.catbox.moe/472piq.jpg",
            "https://files.catbox.moe/qwjeyk.jpg",
            "https://files.catbox.moe/t0hopv.jpg",
            "https://files.catbox.moe/u5ux0j.jpg",
            "https://files.catbox.moe/h1yk4w.jpg",
            "https://files.catbox.moe/gl5rg8.jpg",
        ]
        from random import choice as _choice

        self.START_IMG = getenv("START_IMG") or _choice(_start_imgs)
        self._start_imgs = _start_imgs  # kept for per-request random picks

    def check(self):
        missing = [
            var
            for var in [
                "API_ID",
                "API_HASH",
                "BOT_TOKEN",
                "MONGO_URL",
                "LOGGER_ID",
                "OWNER_ID",
                "SESSION1",
            ]
            if not getattr(self, var)
        ]
        if missing:
            raise SystemExit(
                f"Missing required environment variables: {', '.join(missing)}"
            )
