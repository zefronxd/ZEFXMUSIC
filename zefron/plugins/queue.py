# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


from pyrogram import filters, types

from zefron import app, db, lang, queue
from zefron.helpers import buttons
from zefron.helpers._queue_view import page_count, queue_page_text


@app.on_message(filters.command(["queue", "playing"]) & filters.group & ~app.bl_users)
@lang.language()
async def _queue_func(_, m: types.Message):
    if not await db.get_call(m.chat.id):
        return await m.reply_text(m.lang["not_playing"])

    _reply = await m.reply_text(m.lang["queue_fetching"])
    _queue = queue.get_queue(m.chat.id)
    _media = _queue[0]
    _text = queue_page_text(m.lang, _queue, 0)

    _playing = await db.playing(m.chat.id)
    _buttons = buttons.queue_markup(
        m.chat.id,
        m.lang["playing"] if _playing else m.lang["paused"],
        _playing,
        0,
        page_count(max(1, len(_queue) - 1)),
    )
    await _reply.edit_text(text=_text, reply_markup=_buttons)
