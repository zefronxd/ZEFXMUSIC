# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.

from pyrogram import filters, types

from zefron import app, db, lang, queue
from zefron.helpers import can_manage_vc


@app.on_message(filters.command(["qplay"]) & filters.group & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _qplay(_, m: types.Message):
    """Shuffle the remaining queue without interrupting the current track."""
    if not await db.get_call(m.chat.id):
        return await m.reply_text(m.lang["not_playing"])

    shuffled = queue.shuffle_queued(m.chat.id)
    if not shuffled:
        return await m.reply_text(
            "🔀 Queue mein random play ke liye kam se kam 2 queued songs chahiye."
        )

    await m.reply_text(
        f"🔀 Queue shuffled! {shuffled} queued tracks ka order random ho gaya. "
        "Current track unchanged hai."
    )