# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


import asyncio

from ntgcalls import (ConnectionNotFound, TelegramServerError,
                      RTMPStreamingUnsupported, ConnectionError)
from pyrogram.errors import (ChatSendMediaForbidden, ChatSendPhotosForbidden,
                             MessageIdInvalid)
from pyrogram.types import InputMediaPhoto, Message
from pytgcalls import PyTgCalls, exceptions, types
from pytgcalls.pytgcalls_session import PyTgCallsSession
from pytgcalls.types.raw import AudioParameters

from zefron import (app, config, db, lang, logger,
                   queue, thumb, userbot, yt)
from zefron.helpers import Media, Track, buttons


class TgCall(PyTgCalls):
    def __init__(self):
        self.clients = []
        # A stream can emit more than one end/update event while Telegram is
        # switching streams.  Keep one queue transition per chat so two
        # handlers cannot pop two songs at once.
        self._next_in_progress: set[int] = set()
        self._next_tasks: dict[int, asyncio.Task] = {}
        self._play_generation: dict[int, int] = {}
        self._play_locks: dict[int, asyncio.Lock] = {}
        self._card_tasks: dict[int, asyncio.Task] = {}

    def _schedule_next(self, chat_id: int) -> None:
        """Schedule one reliable queue transition after the current one ends."""
        current = self._next_tasks.get(chat_id)
        if current and not current.done():
            return

        async def _advance():
            try:
                # ``play_media`` can be called from inside ``_play_next``.
                # Wait for that transition to release its guard instead of
                # racing it and silently losing the queue advance.
                while chat_id in self._next_in_progress:
                    await asyncio.sleep(0.05)
                await self.play_next(chat_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduled queue transition failed for %s", chat_id)
            finally:
                task = asyncio.current_task()
                if self._next_tasks.get(chat_id) is task:
                    self._next_tasks.pop(chat_id, None)

        self._next_tasks[chat_id] = asyncio.create_task(_advance())

    def _generation(self, chat_id: int) -> int:
        return self._play_generation.get(chat_id, 0)

    def _play_lock(self, chat_id: int) -> asyncio.Lock:
        return self._play_locks.setdefault(chat_id, asyncio.Lock())

    def _cancel_card_task(self, chat_id: int) -> None:
        task = self._card_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    def _cancel_next_task(self, chat_id: int) -> None:
        task = self._next_tasks.pop(chat_id, None)
        # ``stop`` can be reached from inside the scheduled transition itself.
        # Never cancel the current task while it is unwinding its own queue
        # transition.
        if task is not asyncio.current_task() and task and not task.done():
            task.cancel()

    async def prepare_call(self, chat_id: int) -> bool:
        """Join the voice chat before media preparation finishes.

        PyTgCalls accepts an empty stream and keeps the call connection alive.
        The later ``play_media`` call then replaces that empty source instead
        of waiting for the assistant to join after a download completes.
        """
        if await db.get_call(chat_id):
            return True

        client = await db.get_assistant(chat_id)
        try:
            async with self._play_lock(chat_id):
                if await db.get_call(chat_id):
                    return True
                await client.play(
                    chat_id=chat_id,
                    stream=None,
                    config=types.GroupCallConfig(auto_start=True),
                )
                await db.add_call(chat_id)
            return True
        except Exception:
            logger.exception("Could not pre-join voice chat %s", chat_id)
            return False

    async def _recover_shruti_video(
        self,
        chat_id: int,
        message: Message,
        media: Media | Track,
        seek_time: int,
        allow_shruti_retry: bool,
    ) -> bool:
        """Recover a Shruti video after ffprobe rejects the remote stream."""
        if not (
            isinstance(media, Track)
            and media.video
            and isinstance(media.file_path, str)
            and media.file_path.startswith("https://api01.shrutibots.site/")
        ):
            return False

        if allow_shruti_retry:
            from zefron.core.youtube import shruti_video_stream_urls

            current_path = media.file_path
            for candidate in shruti_video_stream_urls(media.id):
                if candidate == current_path:
                    continue
                logger.warning(
                    "ShrutiAPI video source rejected for %s; retrying Shruti key",
                    media.id,
                )
                media.file_path = candidate
                await self.play_media(
                    chat_id,
                    message,
                    media,
                    seek_time,
                    allow_shruti_retry=False,
                )
                return True

        # Legacy queued items may still contain a direct URL. Materialize the
        # same ShrutiAPI response locally so ffprobe does not probe the remote
        # stream and hit PyTgCalls' timeout.
        logger.warning(
            "ShrutiAPI direct video timed out for %s; downloading via ShrutiAPI",
            media.id,
        )
        media.file_path = await yt.download(media.id, video=True, direct=False)
        if media.file_path:
            await self.play_media(
                chat_id,
                message,
                media,
                seek_time,
                allow_shruti_retry=False,
            )
            return True
        return False

    async def _update_now_playing(
        self, chat_id: int, message: Message, media: Media | Track, _lang
    ) -> None:
        """Update the card after playback has already started.

        Card/thumbnail generation is network and CPU work.  It must not delay
        ``client.play``; if Telegram rejects the card update, playback should
        continue normally.
        """
        try:
            _thumb = (
                await thumb.generate(media)
                if isinstance(media, Track)
                else config.DEFAULT_THUMB
            ) if config.THUMB_GEN else None

            # Do not let a slow thumbnail overwrite the next song's card.
            if queue.get_current(chat_id) is not media:
                return

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
                        media=InputMediaPhoto(media=_thumb, caption=text),
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
                if queue.get_current(chat_id) is media:
                    media.message_id = sent.id
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.warning("Now-playing card update failed for %s: %s", chat_id, ex)

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
        self._play_generation[chat_id] = self._generation(chat_id) + 1
        self._cancel_card_task(chat_id)
        self._cancel_next_task(chat_id)
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
        allow_shruti_retry: bool = True,
    ) -> None:
        # Language is only needed for UI/error messages.  Resolve the
        # assistant first so a cold chat does not wait on two database lookups
        # before the first audio packet can be sent to the VC.
        client = await db.get_assistant(chat_id)
        _lang = lang.languages.get(config.LANG_CODE)
        if _lang is None:
            _lang = next(iter(lang.languages.values()), {})

        if not media.file_path:
            await message.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
            self._schedule_next(chat_id)
            return

        is_radio = getattr(media, "duration", "") == "∞"
        is_remote = isinstance(media.file_path, str) and media.file_path.startswith(
            ("http://", "https://")
        )
        if is_radio or is_remote:
            # YoutubeAPI and radio URLs are live HTTP inputs.  Keep a bounded
            # demuxer queue so short CDN jitter is absorbed without allowing
            # FFmpeg to grow memory forever.  The audio section also forces
            # one clean stream and repairs timestamps before PCM conversion.
            # These options are understood by PyTgCalls 2.3.x. ``--audio``
            # selects the audio command group and ``---mid`` places options
            # after the input URL; both markers are consumed by PyTgCalls.
            _ffmpeg = (
                "-reconnect 1 -reconnect_streamed 1 -reconnect_at_eof 1 "
                "-reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx "
                "-reconnect_delay_max 2 -rw_timeout 20000000 "
                "-thread_queue_size 4096 -probesize 2M -analyzeduration 2M "
                "-fflags +discardcorrupt+genpts"
            )
            if not media.video:
                _ffmpeg += (
                    " --audio ---mid -map 0:a:0? -vn "
                    "-af aresample=async=1:min_hard_comp=0.100:first_pts=0"
                )
            if seek_time > 1:
                _ffmpeg = f"-ss {seek_time} {_ffmpeg}"
        elif seek_time > 1:
            _ffmpeg = f"-ss {seek_time}"
        else:
            _ffmpeg = None

        is_video = bool(media.video)
        stream = types.MediaStream(
            media_path=media.file_path,
            # Keep the VC contract explicit: PyTgCalls emits signed 16-bit
            # little-endian PCM at 48 kHz stereo from these parameters.
            audio_parameters=AudioParameters(
                bitrate=config.AUDIO_SAMPLE_RATE,
                channels=config.AUDIO_CHANNELS,
            ),
            video_parameters=types.VideoQuality.HD_720p,
            # Shruti video responses can be video-only or expose an audio
            # codec ffmpeg cannot decode. Video playback must not be rejected
            # as "Audio source unavailable" in either case.
            audio_flags=(
                types.MediaStream.Flags.AUTO_DETECT
                if is_video
                else types.MediaStream.Flags.REQUIRED
            ),
            video_flags=(
                types.MediaStream.Flags.REQUIRED
                if is_video
                else types.MediaStream.Flags.IGNORE
            ),
            ffmpeg_parameters=_ffmpeg,
        )
        # Invalidate an older connection attempt before waiting for Telegram.
        generation = self._generation(chat_id) + 1
        self._play_generation[chat_id] = generation
        try:
            async with self._play_lock(chat_id):
                if generation != self._generation(chat_id):
                    return
                await client.play(
                    chat_id=chat_id,
                    stream=stream,
                    # Let PyTgCalls start the group call immediately when
                    # needed instead of requiring a second start round trip.
                    config=types.GroupCallConfig(auto_start=True),
                )
            # A stop/force transition may have happened while Telegram was
            # connecting.  Do not mark an old stream as active afterwards.
            if generation != self._generation(chat_id):
                return
            if not seek_time:
                media.time = 1
                await db.add_call(chat_id)
                # Refresh the per-chat language only after playback has
                # started.  A language-cache/database hiccup must never turn a
                # successful stream into a failed queue transition.
                try:
                    _lang = await lang.get_lang(chat_id)
                except Exception:
                    pass
                # Start VC playback first; card generation/editing is not on
                # the latency-critical path.
                self._cancel_card_task(chat_id)
                self._card_tasks[chat_id] = asyncio.create_task(
                    self._update_now_playing(chat_id, message, media, _lang)
                )
        except asyncio.TimeoutError:
            if generation != self._generation(chat_id):
                return
            if await self._recover_shruti_video(
                chat_id,
                message,
                media,
                seek_time,
                allow_shruti_retry,
            ):
                return
            logger.warning("Playback source check timed out in chat %s", chat_id)
            await message.edit_text(_lang["error_no_audio"])
            self._schedule_next(chat_id)
        except FileNotFoundError:
            if generation != self._generation(chat_id):
                return
            await message.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
            self._schedule_next(chat_id)
        except exceptions.NoActiveGroupCall:
            if generation != self._generation(chat_id):
                return
            await self.stop(chat_id)
            await message.edit_text(_lang["error_no_call"])
        except exceptions.NoAudioSourceFound:
            if generation != self._generation(chat_id):
                return
            # Audio may use the local provider fallback. Video must remain
            # ShrutiAPI-only; never silently switch /vplay to another provider.
            if await self._recover_shruti_video(
                chat_id,
                message,
                media,
                seek_time,
                allow_shruti_retry,
            ):
                return
            if (
                isinstance(media, Track)
                and not media.video
                and isinstance(media.file_path, str)
                and media.file_path.startswith("https://youtubeapi")
            ):
                logger.warning(
                    "Direct stream rejected for %s; trying audio fallback",
                    media.id,
                )
                media.file_path = await yt.download(
                    media.id, video=False, direct=False
                )
                if media.file_path:
                    return await self.play_media(
                        chat_id, message, media, seek_time
                    )
            await message.edit_text(_lang["error_no_audio"])
            self._schedule_next(chat_id)
        except (ConnectionError, ConnectionNotFound, TelegramServerError):
            if generation != self._generation(chat_id):
                return
            await self.stop(chat_id)
            await message.edit_text(_lang["error_tg_server"])
        except RTMPStreamingUnsupported:
            if generation != self._generation(chat_id):
                return
            await self.stop(chat_id)
            await message.edit_text(_lang["error_rtmp"])
        except Exception as ex:
            if generation != self._generation(chat_id):
                return
            logger.exception("Playback failed in chat %s: %s", chat_id, ex)
            self._schedule_next(chat_id)


    async def replay(self, chat_id: int) -> None:
        if not await db.get_call(chat_id):
            return

        media = queue.get_current(chat_id)
        if not media:
            await self.stop(chat_id)
            return
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
        track.file_path = await yt.direct_stream(track.id)
        if not track.file_path:
            queue.remove_current(chat_id)
            await db.reset_autoplay(chat_id)
            await self.stop(chat_id)
            return

        track.message_id = msg.id
        await self.play_media(chat_id, msg, track)

    async def play_next(self, chat_id: int) -> None:
        if chat_id in self._next_in_progress:
            return

        self._next_in_progress.add(chat_id)
        try:
            await self._play_next(chat_id)
        finally:
            self._next_in_progress.discard(chat_id)

    async def _play_next(self, chat_id: int) -> None:
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
            media.file_path = (
                await yt.download(media.id, video=True)
                if media.video
                else await yt.direct_stream(media.id)
            )
            if not media.file_path:
                self._schedule_next(chat_id)
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
