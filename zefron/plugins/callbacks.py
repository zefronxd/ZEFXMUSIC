# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


import re

from pyrogram import enums, errors, filters, types

import asyncio

from zefron import zefx, app, config, db, lang, queue, tg, yt
from zefron.helpers import Track, admin_check, buttons, can_manage_vc
from zefron.helpers import recommend
from zefron.helpers.recommend import fetch_and_cache


@app.on_callback_query(filters.regex("cancel_dl") & ~app.bl_users)
@lang.language()
async def cancel_dl(_, query: types.CallbackQuery):
    await query.answer()
    await tg.cancel(query)


@app.on_callback_query(filters.regex("controls") & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _controls(_, query: types.CallbackQuery):
    args = query.data.split()
    action, chat_id = args[1], int(args[2])
    qaction = len(args) == 4
    user = query.from_user.mention

    # "force" (Play Now) must work even when the bot is not currently in VC,
    # so handle it before the not_playing guard.
    if action == "force":
        await query.answer(query.lang["processing"], show_alert=True)
        pos, media = queue.check_item(chat_id, args[3])
        if not media or pos == -1:
            return await query.edit_message_text(query.lang["play_expired"])

        current = queue.get_current(chat_id)
        m_id = current.message_id if current else None
        queue.force_add(chat_id, media, remove=pos)
        try:
            ids_to_del = [
                i for item, i in ((current, m_id), (media, media.message_id))
                if i and getattr(item, "duration", "") != "∞"
            ]
            if ids_to_del:
                await app.delete_messages(
                    chat_id=chat_id, message_ids=ids_to_del, revoke=True
                )
            media.message_id = None
        except Exception:
            pass

        msg = await app.send_message(chat_id=chat_id, text=query.lang["play_next"])
        if not media.file_path:
            media.file_path = await yt.download(media.id, video=media.video)
        media.message_id = msg.id
        return await zefx.play_media(chat_id, msg, media)

    if not await db.get_call(chat_id):
        try:
            return await query.answer(query.lang["not_playing"], show_alert=True)
        except errors.QueryIdInvalid:
            try:
                await query.message.delete()
            except Exception:
                pass
            return

    if action == "status":
        return await query.answer()
    await query.answer(query.lang["processing"], show_alert=True)

    if action == "pause":
        if not await db.playing(chat_id):
            return await query.answer(
                query.lang["play_already_paused"], show_alert=True
            )
        await zefx.pause(chat_id)
        if qaction:
            return await query.edit_message_reply_markup(
                reply_markup=buttons.queue_markup(chat_id, query.lang["paused"], False)
            )
        status = query.lang["paused"]
        reply = query.lang["play_paused"].format(user)

    elif action == "resume":
        if await db.playing(chat_id):
            return await query.answer(query.lang["play_not_paused"], show_alert=True)
        await zefx.resume(chat_id)
        if qaction:
            return await query.edit_message_reply_markup(
                reply_markup=buttons.queue_markup(chat_id, query.lang["playing"], True)
            )
        reply = query.lang["play_resumed"].format(user)

    elif action == "skip":
        await zefx.play_next(chat_id)
        status = query.lang["skipped"]
        reply = query.lang["play_skipped"].format(user)

    elif action == "replay":
        media = queue.get_current(chat_id)
        media.user = user
        await zefx.replay(chat_id)
        status = query.lang["replayed"]
        reply = query.lang["play_replayed"].format(user)

    elif action == "stop":
        await zefx.stop(chat_id)
        status = query.lang["stopped"]
        reply = query.lang["play_stopped"].format(user)

    try:
        if action in ["skip", "replay", "stop"]:
            await query.message.reply_text(reply, quote=False)
            await query.message.delete()
        else:
            mtext = re.sub(
                r"\n\n<blockquote>.*?</blockquote>",
                "",
                query.message.caption.html or query.message.text.html,
                flags=re.DOTALL,
            )
            keyboard = buttons.controls(
                chat_id, status=status if action != "resume" else None
            )
        await query.edit_message_text(
            f"{mtext}\n\n<blockquote>{reply}</blockquote>", reply_markup=keyboard
        )
    except Exception:
        pass


@app.on_callback_query(filters.regex("^help") & ~app.bl_users)
@lang.language()
async def _help(_, query: types.CallbackQuery):
    data = query.data.split()
    if len(data) == 1:
        # In groups: show help inline; in PM: open bot's PM
        if query.message.chat.type != enums.ChatType.PRIVATE:
            await query.answer()
            await app.send_message(
                chat_id=query.message.chat.id,
                text=query.lang["help_menu"],
                reply_markup=buttons.help_markup(query.lang),
            )
            return
        return await query.answer(url=f"https://t.me/{app.username}?start=help")

    if data[1] == "back":
        return await query.edit_message_text(
            text=query.lang["help_menu"], reply_markup=buttons.help_markup(query.lang)
        )
    elif data[1] == "close":
        try:
            await query.message.delete()
            return await query.message.reply_to_message.delete()
        except Exception:
            return

    await query.edit_message_text(
        text=query.lang[f"help_{data[1]}"],
        reply_markup=buttons.help_markup(query.lang, True),
    )


# ── Start-message help flow (edits the photo caption in-place) ─────────────

_HELP_CAT_MAP = {
    "admins": "help_admins",
    "auth":   "help_auth",
    "lang":   "help_lang",
    "ping":   "help_ping",
    "play":   "help_play",
    "queue":  "help_queue",
    "fav":    "help_fav",
}


@app.on_callback_query(filters.regex(r"^start_help") & ~app.bl_users)
@lang.language()
async def _start_help(_, query: types.CallbackQuery):
    data = query.data.split()
    await query.answer()

    async def _edit(caption: str, markup):
        try:
            await query.message.edit_caption(
                caption=caption,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

    # ── Show help category menu ──────────────────────────────────────────
    if len(data) == 1 or data[1] == "menu":
        await _edit(query.lang["help_menu"], buttons.start_help_markup(query.lang))
        return

    # ── Back → restore original start message ───────────────────────────
    if data[1] == "back":
        private = query.message.chat.type == enums.ChatType.PRIVATE
        _text = (
            query.lang["start_pm"].format(query.from_user.first_name, app.name)
            if private
            else query.lang["start_gp"].format(app.name)
        )
        await _edit(_text, buttons.start_key(query.lang, private))
        return

    # ── Show specific category ───────────────────────────────────────────
    key = _HELP_CAT_MAP.get(data[1])
    if key:
        await _edit(query.lang[key], buttons.start_help_cat_markup(query.lang))


@app.on_callback_query(filters.regex(r"^rec_show") & ~app.bl_users)
@lang.language()
async def _rec_show(_, query: types.CallbackQuery):
    """Show recommendations when the 🎵 Recommend button is tapped."""
    chat_id = int(query.data.split()[1])
    cache = recommend.get_cache(chat_id)
    if not cache or not cache.get("songs"):
        return await query.answer(
            "No recommendations yet — they load in the background. Try again in a moment.",
            show_alert=True,
        )
    await query.answer()
    await recommend.show_recommendations(app, chat_id)


@app.on_callback_query(filters.regex(r"^rec_") & ~app.bl_users)
@lang.language()
async def _rec_callback(_, query: types.CallbackQuery):
    """Handle recommendation buttons: rec_play|rec_force|rec_next|rec_prev."""
    parts = query.data.split("_")
    action = parts[1]  # "play" | "force" | "next" | "prev"

    # ── Shared auth helper ─────────────────────────────────────────────────
    async def _check_play_mode(chat_id: int) -> bool:
        """Return True (deny) when play_mode is on and user lacks permission."""
        if not await db.get_play_mode(chat_id):
            return False
        uid = query.from_user.id
        if uid in app.sudoers:
            return False
        if await db.is_auth(chat_id, uid):
            return False
        admins = await db.get_admins(chat_id)
        return uid not in admins

    # ── Shared track builder ───────────────────────────────────────────────
    def _make_track(song: dict) -> "Track":
        return Track(
            id=song["id"],
            title=song["title"],
            url=song["url"],
            duration=song.get("duration", "0:00"),
            duration_sec=song.get("duration_sec", 0),
            thumbnail=song.get("thumbnail", ""),
            channel_name=song.get("channel_name", ""),
            view_count=song.get("view_count", ""),
            user=query.from_user.mention,
            video=False,
        )

    if action == "force":
        # ── Play Now: interrupts current song immediately ──────────────────
        idx     = int(parts[2])
        chat_id = int(parts[3])

        if await _check_play_mode(chat_id):
            return await query.answer(query.lang["play_admin"], show_alert=True)

        cache = recommend.get_cache(chat_id)
        if not cache or idx >= len(cache["songs"]):
            return await query.answer("Recommendation expired.", show_alert=True)

        await query.answer(query.lang["processing"], show_alert=True)
        track = _make_track(cache["songs"][idx])

        is_active = await db.get_call(chat_id)
        if not is_active:
            # Nothing playing — start fresh
            try:
                await query.message.delete()
            except Exception:
                pass
            queue.add(chat_id, track)
            msg = await app.send_message(chat_id=chat_id, text=query.lang["play_downloading"])
            track.file_path = await yt.download(track.id, video=False)
            if not track.file_path:
                queue.remove_current(chat_id)
                return await msg.edit_text(query.lang["error_no_file"].format(config.SUPPORT_CHAT))
            track.message_id = msg.id
            await zefx.play_media(chat_id, msg, track)
        else:
            # Force to front — interrupts current song
            current = queue.get_current(chat_id)
            m_id = current.message_id if current else None
            queue.force_add(chat_id, track, remove=False)
            try:
                if m_id and getattr(current, "duration", "") != "∞":
                    await app.delete_messages(chat_id=chat_id, message_ids=[m_id], revoke=True)
            except Exception:
                pass
            msg = await app.send_message(chat_id=chat_id, text=query.lang["play_next"])
            track.file_path = await yt.download(track.id, video=False)
            if not track.file_path:
                queue.remove_current(chat_id)
                return await msg.edit_text(query.lang["error_no_file"].format(config.SUPPORT_CHAT))
            track.message_id = msg.id
            await zefx.play_media(chat_id, msg, track)

        asyncio.create_task(
            fetch_and_cache(chat_id, track.id, track.title, track.channel_name or "")
        )
        return

    if action == "play":
        idx     = int(parts[2])
        chat_id = int(parts[3])

        if await _check_play_mode(chat_id):
            return await query.answer(query.lang["play_admin"], show_alert=True)

        cache = recommend.get_cache(chat_id)
        if not cache or idx >= len(cache["songs"]):
            return await query.answer(
                "Recommendation expired. Use /play to search again.", show_alert=True
            )

        await query.answer(query.lang["processing"], show_alert=True)
        track    = _make_track(cache["songs"][idx])
        position = queue.add(chat_id, track)
        is_active = await db.get_call(chat_id)

        if position != 0 or is_active:
            queued_text = query.lang["play_queued"].format(
                position, track.url, track.title, track.duration, query.from_user.mention,
            )
            play_now_markup = buttons.play_queued(chat_id, track.id, "Play Now")
            try:
                await query.edit_message_text(queued_text, reply_markup=play_now_markup)
            except Exception:
                try:
                    await app.send_message(chat_id=chat_id, text=queued_text, reply_markup=play_now_markup)
                except Exception:
                    pass
            asyncio.create_task(
                fetch_and_cache(chat_id, track.id, track.title, track.channel_name or "")
            )
            return

        try:
            await query.message.delete()
        except Exception:
            pass

        msg = await app.send_message(chat_id=chat_id, text=query.lang["play_downloading"])
        track.file_path = await yt.download(track.id, video=False)
        if not track.file_path:
            queue.remove_current(chat_id)
            return await msg.edit_text(query.lang["error_no_file"].format(config.SUPPORT_CHAT))
        track.message_id = msg.id
        await zefx.play_media(chat_id, msg, track)
        asyncio.create_task(
            fetch_and_cache(chat_id, track.id, track.title, track.channel_name or "")
        )

    elif action in ("next", "prev"):
        chat_id = int(parts[2])
        cache = recommend.get_cache(chat_id)
        if not cache:
            return await query.answer(
                "Recommendations expired. Use /play to get new ones.",
                show_alert=True,
            )

        current_page = cache.get("page", 0)
        new_page = current_page + 1 if action == "next" else current_page - 1

        keyboard = recommend.build_recommend_keyboard(chat_id, new_page)
        if not keyboard:
            return await query.answer()

        recommend.set_page(chat_id, new_page)
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "rec page update failed for chat %s: %s", chat_id, e
            )
        await query.answer()


@app.on_callback_query(filters.regex(r"^autoplay_toggle") & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _autoplay_toggle(_, query: types.CallbackQuery):
    """Toggle autoplay on/off for the chat."""
    chat_id = int(query.data.split()[1])

    if not await db.get_call(chat_id):
        return await query.answer(query.lang["not_playing"], show_alert=True)

    new_state = await db.toggle_autoplay(chat_id)
    state_text = "🔁 Autoplay ON — next 5 songs will play automatically" if new_state \
                 else "🔁 Autoplay OFF"
    await query.answer(state_text, show_alert=True)

    from zefron.helpers import recommend as _rec
    keyboard = buttons.controls(chat_id, autoplay_on=new_state)
    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^fav_queue") & ~app.bl_users)
@lang.language()
async def _fav_queue(_, query: types.CallbackQuery):
    """Add a queued (not yet playing) song to favourites."""
    parts   = query.data.split()
    chat_id = int(parts[1])
    item_id = parts[2]
    user_id = query.from_user.id

    pos, media = queue.check_item(chat_id, item_id)
    if not media or pos == -1:
        return await query.answer("Song queue mein nahi mila.", show_alert=True)

    song_dict = {
        "id":           media.id,
        "title":        media.title,
        "url":          media.url,
        "duration":     media.duration,
        "duration_sec": media.duration_sec,
        "thumbnail":    getattr(media, "thumbnail", "") or "",
        "channel_name": getattr(media, "channel_name", "") or "",
    }

    already = await db.is_favourite(user_id, media.id)
    if already:
        await query.answer("Yeh song already aapke favourites mein hai! /fav se check karein.", show_alert=True)
    else:
        await db.add_favourite(user_id, song_dict)
        await query.answer("✅ Song favourites mein save ho gaya! /fav se check karein.", show_alert=True)


@app.on_callback_query(filters.regex(r"^fav_toggle") & ~app.bl_users)
@lang.language()
async def _fav_toggle(_, query: types.CallbackQuery):
    """Toggle favourite for the current playing song."""
    parts   = query.data.split()
    chat_id = int(parts[1])
    user_id = query.from_user.id

    # Get current playing track from queue
    current = queue.get_current(chat_id)
    if not current:
        return await query.answer("No song is playing right now.", show_alert=True)

    video_id = current.id
    song_dict = {
        "id":           current.id,
        "title":        current.title,
        "url":          current.url,
        "duration":     current.duration,
        "duration_sec": current.duration_sec,
        "thumbnail":    getattr(current, "thumbnail", "") or "",
        "channel_name": getattr(current, "channel_name", "") or "",
    }

    already = await db.is_favourite(user_id, video_id)
    if already:
        await db.remove_favourite(user_id, video_id)
        await query.answer("This song has been removed from your favourites! You can check your fav list using /fav command.", show_alert=True)
    else:
        await db.add_favourite(user_id, song_dict)
        await query.answer("✅ This song has been saved to your favourites! You can check your fav list using /fav command.", show_alert=True)

    # Update markup to reflect new fav state
    ap_on    = (await db.get_autoplay(chat_id))["on"]
    is_faved = not already
    new_kb   = buttons.controls(
        chat_id,
        autoplay_on=ap_on,
        track_id=video_id,
        is_faved=is_faved,
    )
    try:
        await query.edit_message_reply_markup(reply_markup=new_kb)
    except Exception:
        pass


@app.on_callback_query(filters.regex("settings") & ~app.bl_users)
@lang.language()
@admin_check
async def _settings_cb(_, query: types.CallbackQuery):
    cmd = query.data.split()
    if len(cmd) == 1:
        return await query.answer()
    await query.answer(query.lang["processing"], show_alert=True)

    chat_id = query.message.chat.id
    _admin = await db.get_play_mode(chat_id)
    _delete = await db.get_cmd_delete(chat_id)
    _language = await db.get_lang(chat_id)

    if cmd[1] == "delete":
        _delete = not _delete
        await db.set_cmd_delete(chat_id, _delete)
    elif cmd[1] == "play":
        await db.set_play_mode(chat_id, _admin)
        _admin = not _admin
    await query.edit_message_reply_markup(
        reply_markup=buttons.settings_markup(
            query.lang,
            _admin,
            _delete,
            _language,
            chat_id,
        )
    )
