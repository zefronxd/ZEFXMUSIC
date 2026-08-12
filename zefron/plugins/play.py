# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


import asyncio

from pyrogram import filters, types

from zefron import zefx, app, config, db, lang, queue, tg, yt
from zefron.helpers import buttons, utils
from zefron.helpers._play import checkUB
from zefron.helpers._playlist import remember as remember_playlist
from zefron.helpers._queue_view import page_count, playlist_page_text
from zefron.helpers.recommend import fetch_and_cache
from zefron.helpers.spotify import is_spotify, is_spotify_playlist, spotify_track, spotify_playlist


def playlist_to_queue(chat_id: int, tracks: list) -> str:
    for track in tracks:
        queue.add(chat_id, track)

@app.on_message(
    filters.command(["play", "vplay", "playforce", "vplayforce", "fvplay"])
    & filters.group
    & ~app.bl_users
)
@lang.language()
@checkUB
async def play_hndlr(
    _,
    m: types.Message,
    force: bool = False,
    m3u8: bool = False,
    video: bool = False,
    url: str = None,
) -> None:
    try:
        await m.delete()
    except Exception:
        pass
    sent = await m.reply_text(m.lang["play_searching"])
    file = None
    mention = m.from_user.mention
    media = tg.get_media(m.reply_to_message) if m.reply_to_message else None
    tracks = []

    if media:
        setattr(sent, "lang", m.lang)
        file = await tg.download(m.reply_to_message, sent)

    elif m3u8:
        file = await tg.process_m3u8(url, sent.id, video)

    elif url:
        # ── Spotify ───────────────────────────────────────────────────────
        if is_spotify(url):
            if is_spotify_playlist(url):
                await sent.edit_text(m.lang["playlist_fetch"])
                available = max(
                    0,
                    config.SPOTIFY_QUEUE_LIMIT
                    - len(queue.get_queue(m.chat.id)),
                )
                if not available:
                    return await sent.edit_text(
                        m.lang["play_queue_full"].format(config.SPOTIFY_QUEUE_LIMIT)
                    )
                tracks = await spotify_playlist(
                    url, available, mention, video, yt
                )
                if not tracks:
                    return await sent.edit_text(m.lang["playlist_error"])
                file = tracks[0]
                tracks.remove(file)
                file.message_id = sent.id
            else:
                file = await spotify_track(url, sent.id, video, yt)

        # ── YouTube playlist ───────────────────────────────────────────────
        elif "playlist" in url:
            await sent.edit_text(m.lang["playlist_fetch"])
            tracks = await yt.playlist(
                config.PLAYLIST_LIMIT, mention, url, video
            )
            if not tracks:
                return await sent.edit_text(m.lang["playlist_error"])
            file = tracks[0]
            tracks.remove(file)
            file.message_id = sent.id

        # ── YouTube direct URL ─────────────────────────────────────────────
        else:
            file = await yt.search(url, sent.id, video=video)

        if not file:
            return await sent.edit_text(
                m.lang["play_not_found"].format(config.SUPPORT_CHAT)
            )

    elif len(m.command) >= 2:
        query = " ".join(m.command[1:])
        file = await yt.search(query, sent.id, video=video)
        if not file:
            return await sent.edit_text(
                m.lang["play_not_found"].format(config.SUPPORT_CHAT)
            )

    if not file:
        return await sent.edit_text(m.lang["play_usage"])

    if file.duration_sec > config.DURATION_LIMIT:
        return await sent.edit_text(
            m.lang["play_duration_limit"].format(config.DURATION_LIMIT // 60)
        )

    if await db.is_logger():
        # Logging is not part of playback.  Keep it off the latency-critical
        # path so VC setup can begin immediately after track resolution.
        asyncio.create_task(
            utils.play_log(m, sent.link, file.title, file.duration)
        )

    file.user = mention

    # ── Kick off background recommendation fetch (overwrites cache) ─────────
    try:
        asyncio.create_task(fetch_and_cache(m.chat.id, file.id, file.title, file.channel_name or ""))
    except Exception:
        pass
    # ────────────────────────────────────────────────────────────────────────

    had_current = queue.get_current(m.chat.id) is not None
    was_active = await db.get_call(m.chat.id)

    if force:
        queue.force_add(m.chat.id, file)
    else:
        position = queue.add(m.chat.id, file)

        if position != 0 or (was_active and had_current):
            await sent.edit_text(
                m.lang["play_queued"].format(
                    position,
                    file.url,
                    file.title,
                    file.duration,
                    m.from_user.mention,
                ),
                reply_markup=buttons.play_queued(
                    m.chat.id, file.id, m.lang["play_now"]
                ),
            )
            if tracks:
                playlist_to_queue(m.chat.id, tracks)
                playlist_message = await app.send_message(
                    chat_id=m.chat.id,
                    text=playlist_page_text(
                        m.lang,
                        len(tracks),
                        [(track.title, track.duration) for track in tracks],
                        0,
                    ),
                )
                remember_playlist(m.chat.id, playlist_message.id, tracks)
                await playlist_message.edit_reply_markup(
                    buttons.playlist_pages(
                        m.chat.id,
                        playlist_message.id,
                        0,
                        page_count(len(tracks)),
                    )
                )
            return

    # Join the voice chat before the first track is downloaded. The actual
    # stream is attached by play_media once the media source is ready.
    if not was_active and queue.get_current(m.chat.id) is file:
        await zefx.prepare_call(m.chat.id)

    if not file.file_path:
        # Audio is normalized to a local WAV before playback so CDN jitter
        # cannot drain the VC input buffer. Video remains a local ShrutiAPI
        # download because it has different media requirements.
        if not video:
            file.file_path = await yt.direct_stream(file.id)
        else:
            file.file_path = await yt.download(file.id, video=True)

    await zefx.play_media(chat_id=m.chat.id, message=sent, media=file)
    if not tracks:
        return
    playlist_to_queue(m.chat.id, tracks)
    playlist_message = await app.send_message(
        chat_id=m.chat.id,
        text=playlist_page_text(
            m.lang,
            len(tracks),
            [(track.title, track.duration) for track in tracks],
            0,
        ),
    )
    remember_playlist(m.chat.id, playlist_message.id, tracks)
    await playlist_message.edit_reply_markup(
        buttons.playlist_pages(
            m.chat.id,
            playlist_message.id,
            0,
            page_count(len(tracks)),
        )
    )
