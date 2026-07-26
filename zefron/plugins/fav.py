# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import asyncio

from pyrogram import filters, types

from zefron import zefx, app, db, lang, queue, yt, config
from zefron.helpers import Track, buttons
from zefron.helpers.recommend import fetch_and_cache

_FAV_LIMIT = 10   # show latest 10


# ── Channel tag from config ────────────────────────────────────────────────

def _channel_tag() -> str:
    url = getattr(config, "SUPPORT_CHANNEL", "") or ""
    if "t.me/" in url:
        username = url.rstrip("/").split("t.me/")[-1]
        return f"@{username}"
    return ""


# ── Fav list text builder ──────────────────────────────────────────────────

def _build_fav_text(songs: list[dict], target_name: str, is_own: bool, total: int) -> str:
    header = f"💕 <b>{target_name}'s fav songs</b>\n\n"

    lines = []
    for i, song in enumerate(songs, start=1):
        title = song.get("title", "Unknown")
        if len(title) > 28:
            title = title[:27] + "…"
        url = song.get("url", "")
        lines.append(f"{i}. <a href='{url}'>{title}</a>")

    if is_own:
        tips = (
            "👉 /playfav  — queue specific fav song\n"
            "👉 /del_fav  — remove song #1"
        )
    else:
        tips = "👉 Use /fav to see your own list"

    body = "\n".join(lines) + "\n\n" + tips
    return header + "<blockquote>" + body + "</blockquote>"


# ── /fav command ───────────────────────────────────────────────────────────

@app.on_message(
    filters.command(["fav", "favourites", "favorites"])
    & ~app.bl_users
)
@lang.language()
async def fav_cmd(_, m: types.Message):
    is_own      = True
    target_user = m.from_user
    target_name = m.from_user.first_name

    if m.reply_to_message and m.reply_to_message.from_user:
        target_user = m.reply_to_message.from_user
        target_name = target_user.first_name
        is_own      = (target_user.id == m.from_user.id)

    user_id   = target_user.id
    all_songs = await db.get_favourites(user_id)

    if not all_songs:
        if is_own:
            return await m.reply_text(
                "❤️ <b>You have no favourite songs yet!</b>\n\n"
                "Press the <b>🤍</b> button while a song is playing to save it here.",
                quote=False,
            )
        return await m.reply_text(
            f"❤️ <b>{target_name} has no favourite songs yet.</b>",
            quote=False,
        )

    latest = list(reversed(all_songs))[:_FAV_LIMIT]
    text   = _build_fav_text(latest, target_name, is_own, len(all_songs))
    await m.reply_text(text, quote=False)


# ── /playfav command ───────────────────────────────────────────────────────

@app.on_message(
    filters.command(["playfav", "playfavs"])
    & filters.group
    & ~app.bl_users
)
@lang.language()
async def playfav_cmd(_, m: types.Message):
    if len(m.command) < 2:
        return await m.reply_text(
            "❗ <b>Usage:</b>\n"
            "<blockquote>"
            "/playfav 1  — play fav #1\n"
            "/playfav 1 3 5  — queue specific songs\n"
            "/playfav all  — queue all favs"
            "</blockquote>",
            quote=True,
        )

    user_id   = m.from_user.id
    all_songs = await db.get_favourites(user_id)

    if not all_songs:
        return await m.reply_text(
            "❤️ <b>You have no favourite songs yet!</b>\n"
            "Press 🤍 while a song is playing to save it.",
            quote=True,
        )

    latest = list(reversed(all_songs))[:_FAV_LIMIT]

    # ── Parse arguments ────────────────────────────────────────────────────
    raw_args = m.command[1:]  # everything after /playfav

    # Support both "1 2 3" and "1/2/3" style
    parsed = []
    for a in raw_args:
        for part in a.split("/"):
            parsed.append(part.strip())

    if "all" in parsed:
        selected = list(range(len(latest)))          # all indices
    else:
        selected = []
        for p in parsed:
            try:
                n = int(p)
                if 1 <= n <= len(latest):
                    idx = n - 1
                    if idx not in selected:
                        selected.append(idx)
            except ValueError:
                pass

    if not selected:
        return await m.reply_text(
            f"⚠️ Invalid input. You have <b>{len(latest)}</b> fav songs.\n"
            f"Use numbers 1–{len(latest)} or <code>all</code>.",
            quote=True,
        )

    # ── Permission check ───────────────────────────────────────────────────
    if await db.get_play_mode(m.chat.id):
        uid = m.from_user.id
        if uid not in app.sudoers:
            if not await db.is_auth(m.chat.id, uid):
                admins = await db.get_admins(m.chat.id)
                if uid not in admins:
                    return await m.reply_text(m.lang["play_admin"], quote=True)

    chat_id = m.chat.id

    # ── Build track list ───────────────────────────────────────────────────
    tracks = []
    for idx in selected:
        song = latest[idx]
        tracks.append(Track(
            id=song["id"],
            title=song["title"],
            url=song["url"],
            duration=song.get("duration", "0:00"),
            duration_sec=song.get("duration_sec", 0),
            thumbnail=song.get("thumbnail", ""),
            channel_name=song.get("channel_name", ""),
            view_count="",
            user=m.from_user.mention,
            video=False,
        ))

    is_active = await db.get_call(chat_id)

    # ── Queue all tracks ───────────────────────────────────────────────────
    queued_titles = []
    first_track   = None

    for track in tracks:
        position = queue.add(chat_id, track)
        if position == 0 and not is_active and first_track is None:
            first_track = track   # will be played immediately
        else:
            queued_titles.append(f"<a href='{track.url}'>{track.title}</a>")

    # ── Result message ─────────────────────────────────────────────────────
    if len(tracks) == 1:
        status_line = f"🎵 <b>Playing:</b> <a href='{tracks[0].url}'>{tracks[0].title}</a>"
    else:
        status_line = (
            f"🎵 <b>Queued {len(tracks)} fav songs</b> for {m.from_user.mention}\n\n"
            + "\n".join(f"  {i+1}. <a href='{t.url}'>{t.title}</a>" for i, t in enumerate(tracks))
        )

    sent = await m.reply_text(status_line, quote=True)

    # ── Play first track if nothing active ────────────────────────────────
    if first_track:
        first_track.file_path = await yt.download(first_track.id, video=False)
        if not first_track.file_path:
            queue.remove_current(chat_id)
            return await sent.edit_text(m.lang["error_no_file"].format(config.SUPPORT_CHAT))
        first_track.message_id = sent.id
        await zefx.play_media(chat_id, sent, first_track)
        asyncio.create_task(
            fetch_and_cache(chat_id, first_track.id, first_track.title, first_track.channel_name or "")
        )

    # ── Background-cache remaining tracks ─────────────────────────────────
    for track in tracks:
        if track is not first_track:
            asyncio.create_task(
                fetch_and_cache(chat_id, track.id, track.title, track.channel_name or "")
            )


# ── /del_fav command ───────────────────────────────────────────────────────

@app.on_message(
    filters.command(["del_fav", "delfav", "unfav"])
    & ~app.bl_users
)
@lang.language()
async def del_fav_cmd(_, m: types.Message):
    if len(m.command) < 2:
        return await m.reply_text(
            "❗ <b>Usage:</b>\n"
            "<blockquote>"
            "/del_fav 1  — remove fav #1\n"
            "/del_fav 1 3 5  — remove multiple\n"
            "/del_fav all  — clear all favs"
            "</blockquote>",
            quote=False,
        )

    user_id   = m.from_user.id
    all_songs = await db.get_favourites(user_id)

    if not all_songs:
        return await m.reply_text("❤️ You have no favourite songs to delete.", quote=False)

    latest = list(reversed(all_songs))[:_FAV_LIMIT]

    raw_args = m.command[1:]

    # ── "all" → remove every song in the displayed list ───────────────────
    if "all" in [a.lower() for a in raw_args]:
        count = 0
        for song in latest:
            if await db.remove_favourite(user_id, song["id"]):
                count += 1
        return await m.reply_text(
            f"🗑 <b>Cleared {count} song(s) from your favourites.</b>",
            quote=False,
        )

    # ── Parse numbers ──────────────────────────────────────────────────────
    selected = []
    for a in raw_args:
        try:
            n = int(a)
            if 1 <= n <= len(latest):
                idx = n - 1
                if idx not in selected:
                    selected.append(idx)
        except ValueError:
            pass

    if not selected:
        return await m.reply_text(
            f"⚠️ Invalid input. You have <b>{len(latest)}</b> fav song(s).\n"
            f"Use numbers 1–{len(latest)} or <code>all</code>.",
            quote=False,
        )

    # ── Remove selected (highest index first to preserve order) ───────────
    removed_songs = []
    failed        = 0
    for idx in sorted(selected, reverse=True):
        song = latest[idx]
        if await db.remove_favourite(user_id, song["id"]):
            removed_songs.append(song)
        else:
            failed += 1

    if not removed_songs:
        return await m.reply_text("⚠️ Couldn't remove those songs. Try again.", quote=False)

    if len(removed_songs) == 1:
        s = removed_songs[0]
        text = (
            f"🗑 <b>Removed from favourites:</b>\n"
            f"🎵 <a href='{s.get('url', '')}'>{s.get('title', 'Unknown')}</a>"
        )
    else:
        lines = "\n".join(
            f"  • <a href='{s.get('url', '')}'>{s.get('title', 'Unknown')}</a>"
            for s in removed_songs
        )
        text = f"🗑 <b>Removed {len(removed_songs)} song(s) from favourites:</b>\n{lines}"
        if failed:
            text += f"\n⚠️ {failed} song(s) couldn't be removed."

    await m.reply_text(text, quote=False)


# ── Song info popup ────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^fav_info") & ~app.bl_users)
@lang.language()
async def _fav_info(_, query: types.CallbackQuery):
    parts   = query.data.split()
    user_id = int(parts[1])
    idx     = int(parts[2])

    all_songs = await db.get_favourites(user_id)
    latest    = list(reversed(all_songs))[:_FAV_LIMIT]

    if not latest or idx >= len(latest):
        return await query.answer("Song not found.", show_alert=True)

    song = latest[idx]
    await query.answer(
        f"🎵 {song['title']}\n⏱ {song.get('duration', '?')}\n📺 {song.get('channel_name', '')}",
        show_alert=True,
    )


# ── Play from favourites callback ──────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^fav_play") & ~app.bl_users)
@lang.language()
async def _fav_play(_, query: types.CallbackQuery):
    parts   = query.data.split()
    user_id = int(parts[1])
    idx     = int(parts[2])
    chat_id = int(parts[3])

    all_songs = await db.get_favourites(user_id)
    latest    = list(reversed(all_songs))[:_FAV_LIMIT]

    if not latest or idx >= len(latest):
        return await query.answer("Song not found.", show_alert=True)

    if await db.get_play_mode(chat_id):
        uid = query.from_user.id
        if uid not in app.sudoers:
            if not await db.is_auth(chat_id, uid):
                admins = await db.get_admins(chat_id)
                if uid not in admins:
                    return await query.answer(query.lang["play_admin"], show_alert=True)

    song  = latest[idx]
    track = Track(
        id=song["id"],
        title=song["title"],
        url=song["url"],
        duration=song.get("duration", "0:00"),
        duration_sec=song.get("duration_sec", 0),
        thumbnail=song.get("thumbnail", ""),
        channel_name=song.get("channel_name", ""),
        view_count="",
        user=query.from_user.mention,
        video=False,
    )

    await query.answer(query.lang["processing"], show_alert=True)

    position  = queue.add(chat_id, track)
    is_active = await db.get_call(chat_id)

    if position != 0 or is_active:
        play_now_markup = buttons.play_queued(chat_id, track.id, "Play Now")
        queued_text = query.lang["play_queued"].format(
            position, track.url, track.title, track.duration, query.from_user.mention,
        )
        try:
            await app.send_message(chat_id=chat_id, text=queued_text, reply_markup=play_now_markup)
        except Exception:
            pass
        asyncio.create_task(
            fetch_and_cache(chat_id, track.id, track.title, track.channel_name or "")
        )
        return

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
