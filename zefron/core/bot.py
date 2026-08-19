# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


import os
import time
import asyncio
import pyrogram
from zefron import config, logger
from zefron.core.database import init_db_pool
from zefron.core.metrics import record_incoming, record_outgoing, traffic_snapshot

DOWNLOAD_RETENTION_SECONDS = 60 * 60
DOWNLOAD_CLEANUP_INTERVAL = 15 * 60


def _protected_downloads() -> set[str]:
    """Return local media paths still referenced by the playback queue."""
    protected: set[str] = set()
    try:
        # Import lazily because zefron creates Bot before Queue during startup.
        from zefron import queue

        for items in queue.queues.values():
            for media in items:
                path = getattr(media, "file_path", None)
                if path and not str(path).startswith(("http://", "https://")):
                    protected.add(os.path.realpath(path))
    except Exception:
        # Cleanup must never prevent the bot from booting.
        pass
    return protected


def cleanup_downloads_once() -> int:
    """Delete unreferenced downloaded media older than one hour."""
    downloads_dir = "downloads"
    if not os.path.isdir(downloads_dir):
        return 0

    current_time = time.time()
    protected = _protected_downloads()
    deleted = 0

    for entry in os.scandir(downloads_dir):
        if not entry.is_file():
            continue
        file_path = entry.path
        if os.path.realpath(file_path) in protected:
            continue
        try:
            # mtime reflects the last completed download/conversion, unlike
            # ctime which can change for unrelated filesystem metadata work.
            if current_time - entry.stat().st_mtime > DOWNLOAD_RETENTION_SECONDS:
                os.remove(file_path)
                deleted += 1
        except FileNotFoundError:
            pass
        except OSError as ex:
            logger.warning("Could not delete old download %s: %s", file_path, ex)
    return deleted


async def auto_clean_downloads():
    """Keep downloaded media for one hour, without interrupting playback."""
    while True:
        try:
            count = await asyncio.to_thread(cleanup_downloads_once)
            if count > 0:
                logger.info(
                    "Storage Saver: automatically deleted %s old download(s).",
                    count,
                )
        except Exception as e:
            logger.error("Cleanup Error: %s", e)

        await asyncio.sleep(DOWNLOAD_CLEANUP_INTERVAL)


async def _traffic_reporter():
    """Log a cheap rolling Telegram traffic summary once per minute."""
    while True:
        await asyncio.sleep(60)
        stats = traffic_snapshot()
        logger.info(
            "telegram traffic last_60s incoming=%s outgoing=%s avg_api_ms=%.1f",
            stats["incoming_per_minute"],
            stats["outgoing_per_minute"],
            stats["api_avg_ms"],
        )


class Bot(pyrogram.Client):
    def __init__(self):
        super().__init__(
            name="zefron",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            parse_mode=pyrogram.enums.ParseMode.HTML,
            max_concurrent_transmissions=config.PYROGRAM_MAX_CONCURRENT_TRANSMISSIONS,
            link_preview_options=pyrogram.types.LinkPreviewOptions(is_disabled=True),
        )
        self.owner = config.OWNER_ID
        self.logger = config.LOGGER_ID
        self.bl_users = pyrogram.filters.user()
        self.sudoers = pyrogram.filters.user(self.owner)
        self._cleanup_task: asyncio.Task | None = None
        self._traffic_task: asyncio.Task | None = None

    async def handle_updates(self, updates):
        """Count raw incoming Telegram updates without touching handlers."""
        if hasattr(updates, "updates"):
            record_incoming(len(updates.updates))
        else:
            record_incoming()
        return await super().handle_updates(updates)

    async def invoke(self, query, *args, **kwargs):
        """Measure every outgoing Telegram API request at the client boundary."""
        started = time.monotonic()
        try:
            return await super().invoke(query, *args, **kwargs)
        finally:
            record_outgoing(started)

    async def boot(self):
        """
        Starts the bot and performs initial setup.

        Raises:
            SystemExit: If the bot fails to access the log group or is not an administrator in the logger group.
        """

        self._cleanup_task = asyncio.create_task(
            auto_clean_downloads(), name="download-cleanup"
        )
        self._traffic_task = asyncio.create_task(
            _traffic_reporter(), name="telegram-traffic-reporter"
        )
        await init_db_pool()
        await super().start()
        self.id = self.me.id
        self.name = self.me.first_name
        self.username = self.me.username
        self.mention = self.me.mention

        try:
            await self.send_message(self.logger, "Bot Started")
            get = await self.get_chat_member(self.logger, self.id)
        except Exception as ex:
            raise SystemExit(f"Bot has failed to access the log group: {self.logger}\nReason: {ex}")

        if get.status != pyrogram.enums.ChatMemberStatus.ADMINISTRATOR:
            raise SystemExit("Please promote the bot as an admin in logger group.")
        logger.info(f"Bot started as @{self.username}")

    async def exit(self):
        """
        Asynchronously stops the bot.
        """
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        self._cleanup_task = None
        if self._traffic_task and not self._traffic_task.done():
            self._traffic_task.cancel()
            try:
                await self._traffic_task
            except asyncio.CancelledError:
                pass
        self._traffic_task = None
        await super().stop()
        logger.info("Bot stopped.")
