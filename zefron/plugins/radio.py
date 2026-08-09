# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import re
from html import escape

from pyrogram import filters, types

from zefron import zefx, app, db, lang, queue
from zefron.helpers import Media
from zefron.helpers._play import ensure_assistant_in_chat


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
    # ── Regional / International ──────────────────────────────────────────
    # These are direct radio streams, just like the Bollywood stations above.
    # Punjabi Virsa — verified direct Zeno FM MPEG endpoint. This intentionally
    # does not use YouTube or a playlist URL, so the station can rotate songs
    # independently and keep playing when the bot is left in a voice chat.
    "punjabi":     "https://stream.zeno.fm/1mwx0wv629duv",
    "haryanvi":    "https://stream.zeno.fm/ryektectsf9uv",
    "english":     "https://media-ssl.musicradio.com/CapitalUK",
    "rajasthani":  "https://streamasiacdn.atc-labs.com/jaipurradio.aac",
}

RADIO_STATION_ALIASES = {
    "haryani": "haryanvi",
    "rajashtani": "rajasthani",
    "virsa": "punjabi",
    "punjabivirsa": "punjabi",
    "punjabi_new": "punjabi",
    "punjabinew": "punjabi",
}
RADIO_STATION_LABELS = {
    "punjabi": "Punjabi New Hits (Punjabi Virsa)",
    "haryanvi": "Haryanvi Hits (Zeno FM)",
    "english": "English",
    "rajasthani": "Rajasthani",
}

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _station_keys() -> tuple[str, ...]:
    return (*RADIO_STATIONS.keys(), *RADIO_STATION_ALIASES.keys())


def _resolve_station(query: str) -> tuple[str, str] | tuple[None, None]:
    """Return (display_name, url) for a known station or direct URL."""
    q = query.strip()
    if _URL_RE.match(q):
        return q, q
    key = q.lower().replace("-", "_").replace(" ", "_")
    key = RADIO_STATION_ALIASES.get(key, key)
    # Prefer an exact alias.  Without this, "punjabi_old" would match the
    # shorter "punjabi" entry first because the fallback below is fuzzy.
    if key in RADIO_STATIONS:
        return RADIO_STATION_LABELS.get(key, key.capitalize()), RADIO_STATIONS[key]
    for name, url in RADIO_STATIONS.items():
        if name in key or key in name:
            return RADIO_STATION_LABELS.get(name, name.capitalize()), url
    return None, None


async def _reply_radio_status(m: types.Message) -> None:
    """Show the active radio source without exposing playback internals."""
    media = queue.get_current(m.chat.id)
    if not media or not getattr(media, "radio", False):
        return await m.reply_text(
            "📻 <b>No radio is playing.</b>\n\n"
            "Start one with <code>/radio punjabi</code>."
        )

    # ``media.time`` is maintained by the existing playback timer. For a live
    # stream it represents time since the last successful connection.
    connected_for = max(int(getattr(media, "time", 0) or 0), 0)
    minutes, seconds = divmod(connected_for, 60)
    source = escape(str(media.url or "direct stream"))
    return await m.reply_text(
        "📻 <b>Radio Status</b>\n\n"
        f"🎵 <b>{escape(media.title or 'Live Radio')}</b>\n"
        f"🌐 <code>{source}</code>\n"
        f"⏱ Connected: <code>{minutes:02d}:{seconds:02d}</code>\n"
        "🔁 Auto-reconnect: <b>ON</b>\n"
        "▶️ Source: <b>Direct radio stream</b> (no YouTube)"
    )


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

    if query.lower() in {"status", "now", "nowplaying", "info"}:
        return await _reply_radio_status(m)

    station_name, stream_url = _resolve_station(query)

    if not station_name:
        return await m.reply_text(
            m.lang["radio_not_found"].format(
                ", ".join(_station_keys())
            )
        )

    if not await ensure_assistant_in_chat(m.chat.id, m):
        return

    sent = await m.reply_text(m.lang["radio_connecting"].format(station_name))

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

    # A stale active-call flag with an empty in-memory queue must not leave
    # radio permanently queued without ever starting.
    if queue.get_current(m.chat.id) is not None:
        position = queue.add(m.chat.id, media)
        return await sent.edit_text(
            m.lang["radio_queued"].format(position, station_name)
        )

    queue.add(m.chat.id, media)
    await zefx.play_media(chat_id=m.chat.id, message=sent, media=media)


@app.on_message(
    filters.command(["radiostatus", "radioinfo"])
    & filters.group
    & ~app.bl_users
)
@lang.language()
async def radio_status_hndlr(_, m: types.Message) -> None:
    await _reply_radio_status(m)
