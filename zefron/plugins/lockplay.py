# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


from pyrogram import StopPropagation, filters, types

from zefron import app, db, lang


# ── Global lock guard — runs before every other group handler ────────────────
_cmd_filter = filters.create(
    lambda _, __, m: bool(m.text and m.text.lstrip().startswith("/"))
)


@app.on_message(_cmd_filter & filters.group & ~app.bl_users, group=-1)
@lang.language()
async def _lock_guard(_, m: types.Message):
    if not db.is_play_locked():
        return  # not locked — let all other handlers run normally

    user_id = m.from_user.id if m.from_user else None
    if user_id and user_id in app.sudoers:
        return  # bot owner/sudo → pass through

    # Everyone else
    try:
        await m.delete()
    except Exception:
        pass
    await m.reply_text(m.lang["play_locked_msg"])
    raise StopPropagation


# ── /lock_play — works from anywhere, owner only ─────────────────────────────
@app.on_message(filters.command(["lock_play", "lockplay"]) & app.sudoers)
@lang.language()
async def _lock_play(_, m: types.Message):
    if db.is_play_locked():
        return await m.reply_text(m.lang["play_already_locked"])
    db.lock_play()
    await m.reply_text(m.lang["play_locked_on"])


# ── /unlock_play — works from anywhere, owner only ───────────────────────────
@app.on_message(filters.command(["unlock_play", "unlockplay"]) & app.sudoers)
@lang.language()
async def _unlock_play(_, m: types.Message):
    if not db.is_play_locked():
        return await m.reply_text(m.lang["play_not_locked"])
    db.unlock_play()
    await m.reply_text(m.lang["play_locked_off"])
