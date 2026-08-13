# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


import os
import sys
import shutil
import asyncio
import logging

from pyrogram import filters, types

from zefron import app, db, lang


logger = logging.getLogger(__name__)


@app.on_message(filters.command(["logs"]) & app.sudoers)
@lang.language()
async def _logs(_, m: types.Message):
    sent = await m.reply_text(m.lang["log_fetch"])
    if not os.path.exists("log.txt"):
        return await sent.edit_text(m.lang["log_not_found"])
    await sent.edit_media(
        media=types.InputMediaDocument(
            media="log.txt",
            caption=m.lang["log_sent"].format(app.name),
        )
    )


@app.on_message(filters.command(["logger"]) & app.sudoers)
@lang.language()
async def _logger(_, m: types.Message):
    if len(m.command) < 2:
        return await m.reply_text(m.lang["logger_usage"].format(m.command[0]))
    if m.command[1] not in ("on", "off"):
        return await m.reply_text(m.lang["logger_usage"].format(m.command[0]))

    if m.command[1] == "on":
        await db.set_logger(True)
        await m.reply_text(m.lang["logger_on"])
    else:
        await db.set_logger(False)
        await m.reply_text(m.lang["logger_off"])


@app.on_message(filters.command(["restart"]) & app.sudoers)
@lang.language()
async def _restart(_, m: types.Message):
    sent = await m.reply_text(m.lang["restarting"])

    for directory in ["cache", "downloads"]:
        shutil.rmtree(directory, ignore_errors=True)

    await sent.edit_text(m.lang["restarted"])

    async def replace_process():
        # Do not call stop() from inside a Pyrogram update handler.  app.exit()
        # shuts down the dispatcher and can cancel this handler before exec()
        # is reached, leaving the bot stopped after the status message.
        await asyncio.sleep(1)
        try:
            os.remove("log.txt")
        except FileNotFoundError:
            pass
        except OSError as ex:
            logger.warning("Could not clear log before restart: %s", ex)

        logger.info("Replacing process for requested bot restart.")
        try:
            os.execv(sys.executable, [sys.executable, "-m", "zefron"])
        except Exception:
            logger.exception("Bot restart failed.")

    asyncio.create_task(replace_process())
