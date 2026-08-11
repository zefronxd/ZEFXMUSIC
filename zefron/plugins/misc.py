# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


import time
import asyncio

from pyrogram import enums, errors, filters, types

from zefron import zefx, app, config, db, lang, logger, queue, tasks, userbot, yt
from zefron.helpers import buttons


_prefetch_tasks: dict[int, asyncio.Task] = {}
_prefetch_slots = asyncio.Semaphore(3)
_ui_slots = asyncio.Semaphore(6)
_watch_slots = asyncio.Semaphore(6)


def _schedule_prefetch(chat_id: int, media) -> None:
    """Prefetch video media without blocking timer updates or playback."""
    old = _prefetch_tasks.get(chat_id)
    if old and not old.done():
        return

    async def _prefetch():
        try:
            if media.file_path:
                return
            # Keep expensive local video downloads from saturating CPU, disk,
            # and the remote provider when many VCs approach the next track at
            # the same time.  Direct audio URLs return immediately.
            async with _prefetch_slots:
                if media.file_path:
                    return
                media.file_path = (
                    await yt.download(media.id, video=True)
                    if media.video
                    else await yt.direct_stream(media.id)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            media.file_path = None
        finally:
            _prefetch_tasks.pop(chat_id, None)

    _prefetch_tasks[chat_id] = asyncio.create_task(_prefetch())


_GROUP_ONLY_CMDS = [
    "play", "playforce", "vplay", "vplayforce",
    "pause", "resume", "skip", "next", "stop", "end",
    "seek", "seekback", "loop", "queue", "playing",
    "auth", "unauth", "authlist", "admincache", "reload",
    "blacklist", "unblacklist", "playmode", "settings", "stats",
]

@app.on_message(filters.command(_GROUP_ONLY_CMDS) & filters.private)
async def _group_only_notice(_, m: types.Message):
    await m.reply_text(
        "⚠️ This command can only be used in a group."
    )


@app.on_message(filters.video_chat_started, group=19)
@app.on_message(filters.video_chat_ended, group=20)
async def _watcher_vc(_, m: types.Message):
    await zefx.stop(m.chat.id)


async def auto_leave():
    while True:
        await asyncio.sleep(config.AUTO_LEAVE_ASSISTANT_TIME)
        for ub in list(userbot.clients):
            try:
                left = 0
                async for dialog in ub.get_dialogs():
                    chat = dialog.chat
                    if chat.type not in [
                        enums.ChatType.GROUP,
                        enums.ChatType.SUPERGROUP,
                        enums.ChatType.CHANNEL,
                    ]:
                        continue
                    if chat.id in {
                        config.LOGGER_ID,
                        -1001686672798,
                        -1001549206010,
                    }:
                        continue
                    if chat.id in db.active_calls:
                        continue
                    if left >= 20:
                        break
                    try:
                        await ub.leave_chat(chat.id)
                        left += 1
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        continue
                    await asyncio.sleep(12)
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logger.warning(
                    "Assistant inactive-chat cleanup failed: %s",
                    type(ex).__name__,
                )


async def track_time():
    while True:
        await asyncio.sleep(1)
        for chat_id in list(db.active_calls):
            if not db.active_calls.get(chat_id):
                continue
            media = queue.get_current(chat_id)
            if not media:
                continue
            media.time += 1


async def update_timer(length=10, sleep=12):
    async def _update_chat(chat_id: int) -> None:
        if not db.active_calls.get(chat_id):
            return
        try:
            media = queue.get_current(chat_id)
            if not media:
                return
            duration, message_id = media.duration_sec, media.message_id
            if not duration or not message_id or not media.time:
                return
            played = media.time
            remaining = max(duration - played, 0)
            pos = min(int((played / duration) * length), length - 1)
            timer = "—" * pos + "◉" + "—" * (length - pos - 1)

            if remaining <= 30:
                next_media = queue.get_next(chat_id, check=True)
                if next_media and not next_media.file_path:
                    _schedule_prefetch(chat_id, next_media)

            if remaining < 10:
                remove = True
            else:
                if config.THUMB_GEN:
                    timer = (
                        f"{time.strftime('%M:%S', time.gmtime(played))} | "
                        f"{timer} | -{time.strftime('%M:%S', time.gmtime(remaining))}"
                    )
                else:
                    timer = None
                remove = False

            if not timer and not remove:
                return

            async with _ui_slots:
                await app.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=buttons.controls(
                        chat_id=chat_id, timer=timer, remove=remove
                    ),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    while True:
        await asyncio.sleep(sleep)
        await asyncio.gather(
            *(_update_chat(chat_id) for chat_id in list(db.active_calls)),
            return_exceptions=True,
        )


async def vc_watcher(sleep=15):
    async def _watch_chat(chat_id: int) -> None:
        async with _watch_slots:
            try:
                client = await db.get_assistant(chat_id)
                media = queue.get_current(chat_id)
                if not media:
                    return
                participants = await client.get_participants(chat_id)
                if len(participants) >= 2 or media.time <= 30:
                    return

                _lang = await lang.get_lang(chat_id)
                sent = await app.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=media.message_id,
                    reply_markup=buttons.controls(
                        chat_id=chat_id, status=_lang["stopped"], remove=True
                    ),
                )
                await zefx.stop(chat_id)
                await sent.reply_text(_lang["auto_left"])
            except errors.MessageIdInvalid:
                pass
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    while True:
        await asyncio.sleep(sleep)
        await asyncio.gather(
            *(_watch_chat(chat_id) for chat_id in list(db.active_calls)),
            return_exceptions=True,
        )


if config.AUTO_END:
    tasks.append(asyncio.create_task(vc_watcher()))
if config.AUTO_LEAVING_ASSISTANT:
    tasks.append(asyncio.create_task(auto_leave()))
tasks.append(asyncio.create_task(track_time()))
tasks.append(asyncio.create_task(update_timer()))
