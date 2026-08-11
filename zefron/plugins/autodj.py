# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.

from pyrogram import filters, types

from zefron import app, db, lang
from zefron.helpers import can_manage_vc


@app.on_message(filters.command(["autodj", "smartdj"]) & filters.group & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _autodj(_, m: types.Message):
    """Control recommendation-powered Smart DJ playback."""
    if not await db.get_call(m.chat.id):
        return await m.reply_text(m.lang["not_playing"])

    state = await db.get_autoplay(m.chat.id)
    option = m.command[1].lower() if len(m.command) > 1 else None

    if option in {"on", "enable", "yes"}:
        if not state["on"]:
            await db.set_autoplay(m.chat.id, True)
        return await m.reply_text(
            "🤖 <b>Smart DJ enabled!</b>\n\n"
            "Queue khatam hone par current song ke similar tracks automatically play honge "
            "(up to 5 recommendations)."
        )

    if option in {"off", "disable", "no"}:
        if state["on"]:
            await db.set_autoplay(m.chat.id, False)
        return await m.reply_text(
            "⏹ <b>Smart DJ disabled.</b>\n\n"
            "Ab queue khatam hone par bot automatic recommendations play nahi karega."
        )

    if option in {"status", "info"}:
        status = "ON 🤖" if state["on"] else "OFF"
        return await m.reply_text(
            f"🎛 <b>Smart DJ:</b> {status}\n"
            f"🎶 <b>Auto-played this session:</b> {state.get('count', 0)}/5"
        )

    if option:
        return await m.reply_text(
            "🤖 <b>Smart DJ usage:</b>\n"
            "<code>/autodj on</code> — enable\n"
            "<code>/autodj off</code> — disable\n"
            "<code>/autodj status</code> — check status"
        )

    new_state = await db.toggle_autoplay(m.chat.id)
    if new_state:
        return await m.reply_text(
            "🤖 <b>Smart DJ enabled!</b> Similar tracks queue khatam hone ke baad automatically play honge."
        )
    return await m.reply_text("⏹ <b>Smart DJ disabled.</b>")