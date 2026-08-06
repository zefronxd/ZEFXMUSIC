# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


from ntgcalls import (ConnectionNotFound, TelegramServerError,
                      RTMPStreamingUnsupported, ConnectionError)
from pyrogram.errors import (ChatSendMediaForbidden, ChatSendPhotosForbidden,
                             MessageIdInvalid)
from pyrogram.types import InputMediaPhoto, Message
from pytgcalls import PyTgCalls, exceptions, types
from pytgcalls.pytgcalls_session import PyTgCallsSession

from zefron import (app, config, db, lang, logger,
                   queue, thumb, userbot, yt)
from zefron.helpers import Media, Track, buttons


class TgCall(PyTgCalls):
    def __init__(self):
        self.clients = []

    async def pause(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        await db.playing(chat_id, paused=True)
        return await client.pause(chat_id)

    async def resume(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        await db.playing(chat_id, paused=False)
        return await client.resume(chat_id)

    async def stop(self, chat_id: int) -> None:
        client = await db.get_assistant(chat_id)
        queue.clear(chat_id)
        await db.remove_call(chat_id)
        await db.set_loop(chat_id, 0)
        await db.reset_autoplay(chat_id)

        try:
            await client.leave_call(chat_id, close=False)
        except Exception:
            pass


    async def play_media(
        self,
        chat_id: int,
        message: Message,
        media: Media | Track,
        seek_time: int = 0,
    ) -> None:
        client = await db.get_assistant(chat_id)
        _lang = await lang.get_lang(chat_id)
        _thumb = (
            await thumb.generate(media)
            if isinstance(media, Track)
            else config.DEFAULT_THUMB
        ) if config.THUMB_GEN else None

        if not media.file_path:
            await message.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
            return await self.play_next(chat_id)

        is_radio = getattr(media, "duration", "") == "∞"
        if is_radio:
            _ffmpeg = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        elif seek_time > 1:
            _ffmpeg = f"-ss {seek_time}"
        else:
            _ffmpeg = None

        stream = types.MediaStream(
            media_path=media.file_path,
            audio_parameters=types.AudioQuality.HIGH,
            video_parameters=types.VideoQuality.HD_720p,
            audio_flags=types.MediaStream.Flags.REQUIRED,
            video_flags=(
                types.MediaStream.Flags.AUTO_DETECT
                if media.video
                else types.MediaStream.Flags.IGNORE
            ),
            ffmpeg_parameters=_ffmpeg,
        )
        try:
            await client.play(
                chat_id=chat_id,
                stream=stream,
                config=types.GroupCallConfig(auto_start=False),
            )
            if not seek_time:
                media.time = 1
                await db.add_call(chat_id)
                text = _lang["play_media"].format(
                    media.url,
                    media.title,
                    media.duration,
                    media.user,
                )
                ap_on = (await db.get_autoplay(chat_id))["on"]
                keyboard = buttons.controls(
                    chat_id,
                    autoplay_on=ap_on,
                    track_id=media.id if isinstance(media, Track) else "",
                )
                try:
                    if _thumb:
                        await message.edit_media(
                            media=InputMediaPhoto(
                                media=_thumb,
                                caption=text,
                            ),
                            reply_markup=keyboard,
                        )
                    else:
                        await message.edit_text(text, reply_markup=keyboard)
                except (ChatSendMediaForbidden, ChatSendPhotosForbidden, MessageIdInvalid):
                    if _thumb:
                        sent = await app.send_photo(
                            chat_id=chat_id,
                            photo=_thumb,
                            caption=text,
                            reply_markup=keyboard,
                        )
                    else:
                        sent = await app.send_message(
                            chat_id=chat_id,
                            text=text,
                            reply_markup=keyboard,
                        )
                    media.message_id = sent.id
        except FileNotFoundError:
            await message.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
            await self.play_next(chat_id)
        except exceptions.NoActiveGroupCall:
            await self.stop(chat_id)
            await message.edit_text(_lang["error_no_call"])
        except exceptions.NoAudioSourceFound:
            await message.edit_text(_lang["error_no_audio"])
            await self.play_next(chat_id)
        except (ConnectionError, ConnectionNotFound, TelegramServerError):
            await self.stop(chat_id)
            await message.edit_text(_lang["error_tg_server"])
        except RTMPStreamingUnsupported:
            await self.stop(chat_id)
            await message.edit_text(_lang["error_rtmp"])


    async def replay(self, chat_id: int) -> None:
        if not await db.get_call(chat_id):
            return

        media = queue.get_current(chat_id)
        _lang = await lang.get_lang(chat_id)
        # Keep the original radio now-playing message across reconnects.
        # Reusing it avoids a new message on every stream reconnect while
        # still allowing a fallback if a user or admin removed it.
        msg = None
        if getattr(media, "radio", False) and media.message_id:
            try:
                msg = await app.get_messages(chat_id, media.message_id)
            except Exception:
                pass
        if msg is None:
            msg = await app.send_message(chat_id=chat_id, text=_lang["play_again"])
        media.message_id = msg.id
        await self.play_media(chat_id, msg, media)


    async def _autoplay_next(self, chat_id: int, last_track) -> None:
        """Queue and play one song-based recommended song for autoplay."""
        import random
        from zefron.helpers.recommend import fetch_related_songs, cache_recommendations

        _lang = await lang.get_lang(chat_id)
        title    = getattr(last_track, "title",        "") or ""
        channel  = getattr(last_track, "channel_name", "") or ""
        video_id = getattr(last_track, "id",           "") or ""

        # ── Fetch recs for the song that just finished ────────────────────
        songs = await fetch_related_songs(video_id, title, channel)

        # ── If current song has no recs, fall back to seed song's recs ───
        if not songs:
            ap_state = await db.get_autoplay(chat_id)
            seed = ap_state.get("seed")
            if seed and seed.get("video_id") and seed["video_id"] != video_id:
                logger.info(
                    "Autoplay: no recs for %r — falling back to seed song %r",
                    title, seed["title"],
                )
                songs = await fetch_related_songs(
                    seed["video_id"], seed["title"], seed["channel"]
                )

        if not songs:
            await db.reset_autoplay(chat_id)
            await self.stop(chat_id)
            return

        # ── Update in-memory cache so /suggest shows recs for current song ─
        cache_recommendations(chat_id, title, songs)

        # ── Pick from full list, skip already-played this session ────────
        ap_state = await db.get_autoplay(chat_id)
        played_ids = ap_state.get("played_ids", set())

        pool = [s for s in songs if s["id"] not in played_ids]
        if not pool:
            # All songs exhausted — fetch fresh recs for the last track
            songs = await fetch_related_songs(video_id, title, channel)
            if not songs:
                await db.reset_autoplay(chat_id)
                await self.stop(chat_id)
                return
            await db.autoplay_reset_played(chat_id)
            pool = [s for s in songs if s["id"] not in played_ids] or songs
            cache_recommendations(chat_id, title, songs)

        random.shuffle(pool)
        song = pool[0]
        await db.autoplay_mark_played(chat_id, song["id"])

        track = Track(
            id=song["id"],
            title=song["title"],
            url=song["url"],
            duration=song.get("duration", "0:00"),
            duration_sec=song.get("duration_sec", 0),
            thumbnail=song.get("thumbnail", ""),
            channel_name=song.get("channel_name", ""),
            view_count=song.get("view_count", ""),
            user="🤖 Autoplay",
            video=False,
        )

        queue.add(chat_id, track)
        await db.add_call(chat_id)

        msg = await app.send_message(chat_id=chat_id, text=_lang["play_next"])
        track.file_path = await yt.download(track.id, video=False)
        if not track.file_path:
            queue.remove_current(chat_id)
            await db.reset_autoplay(chat_id)
            await self.stop(chat_id)
            return

        track.message_id = msg.id
        await self.play_media(chat_id, msg, track)

    async def play_next(self, chat_id: int) -> None:
        if loop := await db.get_loop(chat_id):
            await db.set_loop(chat_id, loop - 1)
            return await self.replay(chat_id)

        # Remember what was playing before popping the queue (needed for autoplay)
        last_track = queue.get_current(chat_id)

        media = queue.get_next(chat_id)
        try:
            # Radio announcements are intentionally persistent.  Normal
            # song cards are cleaned up as the queue advances.
            if media and media.message_id and not getattr(media, "radio", False):
                await app.delete_messages(
                    chat_id=chat_id,
                    message_ids=media.message_id,
                    revoke=True,
                )
                media.message_id = 0
        except Exception:
            pass

        if not media:
            # ── Autoplay: play up to 5 more songs, then stop ──────────────────
            ap = await db.get_autoplay(chat_id)
            if ap["on"] and ap["count"] < 5 and last_track:
                count = await db.autoplay_tick(chat_id)
                if count <= 5:
                    # On the very first autoplay song, save the seed so we can
                    # fall back to its reliable recommendations if later songs
                    # have no Last.fm data.
                    if count == 1:
                        await db.autoplay_set_seed(
                            chat_id,
                            getattr(last_track, "id",           "") or "",
                            getattr(last_track, "title",        "") or "",
                            getattr(last_track, "channel_name", "") or "",
                        )
                    await self._autoplay_next(chat_id, last_track)
                    return
                else:
                    await db.reset_autoplay(chat_id)

            await self.stop(chat_id)
            from zefron.helpers.recommend import show_recommendations
            await show_recommendations(app, chat_id)
            return

        _lang = await lang.get_lang(chat_id)
        msg = None
        if getattr(media, "radio", False) and media.message_id:
            try:
                msg = await app.get_messages(chat_id, media.message_id)
            except Exception:
                pass
        if msg is None:
            msg = await app.send_message(chat_id=chat_id, text=_lang["play_next"])
        if not media.file_path:
            media.file_path = await yt.download(media.id, video=media.video)
            if not media.file_path:
                await self.play_next(chat_id)
                return await msg.edit_text(
                    _lang["error_no_file"].format(config.SUPPORT_CHAT)
                )

        media.message_id = msg.id
        await self.play_media(chat_id, msg, media)

        # Update recommendation cache based on the song that just started playing
        # so that /suggest and autoplay always use recs from the current track.
        from zefron.helpers import Track as _Track
        if isinstance(media, _Track) and media.id:
            import asyncio as _asyncio
            from zefron.helpers.recommend import fetch_and_cache as _fac
            _asyncio.create_task(
                _fac(chat_id, media.id, media.title, getattr(media, "channel_name", "") or "")
            )


    async def ping(self) -> float:
        pings = [client.ping for client in self.clients]
        return round(sum(pings) / len(pings), 2)


    async def decorators(self, client: PyTgCalls) -> None:
        @client.on_update()
        async def update_handler(_, update: types.Update) -> None:
            if isinstance(update, types.StreamEnded):
                if update.stream_type == types.StreamEnded.Type.AUDIO:
                    # Radio streams (duration == "∞") should auto-reconnect
                    current = queue.get_current(update.chat_id)
                    if current and getattr(current, "duration", "") == "∞":
                        try:
                            await self.replay(update.chat_id)
                        except Exception:
                            await self.play_next(update.chat_id)
                    else:
                        await self.play_next(update.chat_id)
            elif isinstance(update, types.ChatUpdate):
                if update.status in [
                    types.ChatUpdate.Status.KICKED,
                    types.ChatUpdate.Status.LEFT_GROUP,
                    types.ChatUpdate.Status.CLOSED_VOICE_CHAT,
                ]:
                    await self.stop(update.chat_id)


    async def boot(self) -> None:
        PyTgCallsSession.notice_displayed = True
        for ub in userbot.clients:
            client = PyTgCalls(ub, cache_duration=100)
            await client.start()
            self.clients.append(client)
            await self.decorators(client)
        logger.info("PyTgCalls client(s) started.")
