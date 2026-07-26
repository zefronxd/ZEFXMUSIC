# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


from pyrogram import filters, types

from zefron import zefx, app, db, lang, queue
from zefron.helpers import can_manage_vc


@app.on_message(filters.command(["seek", "seekback"]) & filters.group & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _seek(_, m: types.Message):
    if len(m.command) < 2:
        return await m.reply_text(m.lang["play_seek_usage"].format(m.command[0]))

    try:
        to_seek = int(m.command[1])
    except ValueError:
        return await m.reply_text(m.lang["play_seek_usage"].format(m.command[0]))
    if to_seek < 10:
        return await m.reply_text(m.lang["play_seek_min"])

    if not await db.get_call(m.chat.id):
        return await m.reply_text(m.lang["not_playing"])

    if not await db.playing(m.chat.id):
        return await m.reply_text(m.lang["play_already_paused"])

    media = queue.get_current(m.chat.id)
    if not media.duration_sec:
        return await m.reply_text(m.lang["play_seek_no_dur"])

    sent = await m.reply_text(m.lang["play_seeking"])
    start_from = max(1, to_seek)
    if start_from + 10 > media.duration_sec:
        start_from = media.duration_sec - 5
    stype = m.lang.get("forward", m.command[0])

    await zefx.play_media(m.chat.id, sent, media, start_from)
    media.time = start_from
    await sent.edit_text(
        m.lang["play_seeked"].format(stype, start_from, m.from_user.mention)
    )
