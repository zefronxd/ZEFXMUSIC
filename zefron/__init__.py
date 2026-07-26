# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


import time
import asyncio
import logging
from logging.handlers import RotatingFileHandler

logging.basicConfig(
    format="[%(asctime)s - %(levelname)s] - %(name)s: %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    handlers=[
        RotatingFileHandler("log.txt", maxBytes=10485760, backupCount=5),
        logging.StreamHandler(),
    ],
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("ntgcalls").setLevel(logging.CRITICAL)
logging.getLogger("pymongo").setLevel(logging.ERROR)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("pytgcalls").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)


__version__ = "3.0.3"

from config import Config

config = Config()
config.check()
tasks = []
boot = time.time()

from zefron.core.bot import Bot
app = Bot()

from zefron.core.dir import ensure_dirs
ensure_dirs()

from zefron.core.userbot import Userbot
userbot = Userbot()

from zefron.core.mongo import MongoDB
db = MongoDB()

from zefron.core.lang import Language
lang = Language()

from zefron.core.telegram import Telegram
from zefron.core.youtube import YouTube
tg = Telegram()
yt = YouTube()

from zefron.helpers import Queue, Thumbnail
queue = Queue()
thumb = Thumbnail()

from zefron.core.calls import TgCall
zefx = TgCall()


async def stop() -> None:
    logger.info("Stopping...")
    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.exceptions.CancelledError:
            pass

    await app.exit()
    await userbot.exit()
    await db.close()
    await thumb.close()

    logger.info("Stopped.\n")
