# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import re
from datetime import datetime
import aiohttp

from pyrogram import enums, filters, types

from zefron import zefx, app, db, lang, queue, yt
from zefron.helpers import Media, Track


# Public radio streams used by the /radio command.
RADIO_STATIONS: dict[str, str] = {
    # ── Hindi / Bollywood ──────────────────────────────────────────────────
    "bollywood":   "https://drive.uber.radio/uber/bollywoodnow/icecast.audio",
    "bollywood_new": "https://drive.uber.radio/uber/bollywoodnow/icecast.audio",
    "new":         "https://drive.uber.radio/uber/bollywoodnow/icecast.audio",
    "hindi":       "https://azuracast.vibesounds.in:8010/radio.mp3",
    "mirchi":      "http://162.244.80.118:9460/stream.mp3",
    "love":        "https://nl4.mystreaming.net/uber/bollywoodlove/icecast.audio",
    "90s":         "https://2.mystreaming.net/uber/bollywood2000s/icecast.audio",
    "2000s":       "https://2.mystreaming.net/uber/bollywood2000s/icecast.audio",
    "old":         "https://2.mystreaming.net/uber/bollywood2000s/icecast.audio",
    "bollywood_old": "https://2.mystreaming.net/uber/bollywood2000s/icecast.audio",
    "2010s":       "https://drive.uber.radio/uber/bollywood2010s/icecast.audio",
    "desi":        "https://streamer.radio.co/se30891e37/listen",
    "air":         "http://air.pc.cdn.bitgravity.com/air/live/pbaudio001/chunklist.m3u8",
}

# These are song-based channels rather than one fixed live stream.  Searches
# deliberately include both "latest" and "trending" so the queue stays fresh
# when /radio is used again later.
RADIO_SONG_CHANNELS = {
    "punjabi": {
        "label": "Punjabi Latest & Trending",
        "queries": (
            "Punjabi latest new trending songs {year}",
            "Punjabi viral hit songs {year}",
        ),
    },
    "haryanvi": {
        "label": "Haryanvi Latest & Trending",
        "queries": (
            "Haryanvi latest new trending songs {year}",
            "Haryanvi viral hit songs {year}",
        ),
    },
    "english": {
        "label": "English Latest & Trending",
        "queries": (
            "English latest new trending songs {year}",
            "English viral hit songs {year}",
        ),
    },
    "rajasthani": {
        "label": "Rajasthani Latest & Trending",
        "queries": (
            "Rajasthani latest new trending songs {year}",
            "Rajasthani viral hit songs {year}",
        ),
    },
}
RADIO_SONG_ALIASES = {
    "haryani": "haryanvi",
    "rajashtani": "rajasthani",
}

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _station_keys() -> tuple[str, ...]:
    return (*RADIO_STATIONS.keys(), *RADIO_SONG_CHANNELS.keys())


def _resolve_station(query: str) -> tuple[str, str] | tuple[None, None]:
    """Return (display_name, url) for a known station or direct URL."""
    q = query.strip()
    if _URL_RE.match(q):
        return q, q
    key = q.lower().replace("-", "_").replace(" ", "_")
    song_key = RADIO_SONG_ALIASES.get(key, key)
    if song_key in RADIO_SONG_CHANNELS:
        channel = RADIO_SONG_CHANNELS[song_key]
        return channel["label"], None
    # Prefer an exact alias.  Without this, "punjabi_old" would match the
    # shorter "punjabi" entry first because the fallback below is fuzzy.
    if key in RADIO_STATIONS:
        return key.capitalize(), RADIO_STATIONS[key]
    for name, url in RADIO_STATIONS.items():
        if name in key or key in name:
            return name.capitalize(), url
    return None, None


async def _fresh_channel_tracks(channel: str, message_id: int, video: bool) -> list[Track]:
    """Search several fresh/trending terms and return unique playable tracks."""
    settings = RADIO_SONG_CHANNELS[channel]
    year = datetime.now().year
    tracks: list[Track] = []
    seen: set[str] = set()

    for template in settings["queries"]:
        try:
            results = await yt.search_many(
                template.format(year=year),
                limit=6,
                m_id=message_id,
                video=video,
            )
        except Exception:
            results = []
        for track in results:
            # Search providers can return mixes, live streams, or empty
            # duration entries which are not suitable for the song queue.
            if not track.id or track.id in seen or not track.duration_sec:
                continue
            seen.add(track.id)
            tracks.append(track)
            if len(tracks) >= 8:
                return tracks
    return tracks


@app.on_message(
    filters.command(["radio", "vradio"])
    & filters.group
    & ~app.bl_users
)
@lang.language()
async def radio_hndlr(_, m: types.Message) -> None:
    video = "v" in m.command[0].lower()

    if len(m.command) < 2:
        stations = "\n".join(f"  • <code>{k}</code>" for k in _station_keys())
        return await m.reply_text(m.lang["radio_usage"].format(stations))

    query = " ".join(m.command[1:]).strip()

    station_name, stream_url = _resolve_station(query)

    if not station_name:
        return await m.reply_text(
            m.lang["radio_not_found"].format(
                ", ".join(_station_keys())
            )
        )

    sent = await m.reply_text(m.lang["radio_connecting"].format(station_name))

    # Regional/English channels use fresh YouTube results so the command
    # actually plays songs (not a stale 24x7 stream).
    song_channel = query.lower().replace("-", "_").replace(" ", "_")
    song_channel = RADIO_SONG_ALIASES.get(song_channel, song_channel)
    if song_channel in RADIO_SONG_CHANNELS:
        tracks = await _fresh_channel_tracks(song_channel, sent.id, video)
        if not tracks:
            return await sent.edit_text(
                f"❌ {station_name} ke liye latest songs nahi mil paaye. "
                "Thodi der baad dobara try karein."
            )

        for track in tracks:
            track.user = m.from_user.mention
            track.radio = False

        if queue.get_queue(m.chat.id) or await db.get_call(m.chat.id):
            first_position = queue.add(m.chat.id, tracks[0])
            for track in tracks[1:]:
                queue.add(m.chat.id, track)
            return await sent.edit_text(
                f"📋 <b>Latest & Trending Queue</b>  •  Position #{first_position}\n\n"
                f"🎵 <b>{station_name}</b>\n"
                f"✅ {len(tracks)} fresh songs added to the queue."
            )

        queue.add(m.chat.id, tracks[0])
        for track in tracks[1:]:
            queue.add(m.chat.id, track)
        tracks[0].file_path = await yt.download(tracks[0].id, video=video)
        if not tracks[0].file_path:
            queue.remove_current(m.chat.id)
            return await sent.edit_text(
                "❌ Latest song download nahi ho paaya. Please dobara try karein."
            )
        await zefx.play_media(chat_id=m.chat.id, message=sent, media=tracks[0])
        return

    media = Media(
        id=f"radio_{station_name.lower()}",
        file_path=stream_url,
        message_id=sent.id,
        url=stream_url,
        title=f"📻 {station_name} Radio",
        duration="∞",
        video=video,
        user=m.from_user.mention,
        radio=True,
    )

    if queue.get_queue(m.chat.id) or await db.get_call(m.chat.id):
        position = queue.add(m.chat.id, media)
        return await sent.edit_text(
            m.lang["radio_queued"].format(position, station_name)
        )

    queue.add(m.chat.id, media)
    await zefx.play_media(chat_id=m.chat.id, message=sent, media=media)
