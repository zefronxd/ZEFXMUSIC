# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import re

from pyrogram import filters, types

from zefron import zefx, app, db, lang, queue
from zefron.helpers import Media


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
    "punjabi":     "https://bolpunjabi-ekamsoftware.radioca.st/stream",
    "haryanvi":    "https://stream.zeno.fm/ryektectsf9uv",
    "topharyanvi": "http://weareharyanvi.com:8000/listen",
    "english":     "https://media-ssl.musicradio.com/CapitalUK",
    "rajasthani":  "https://streamasiacdn.atc-labs.com/jaipurradio.aac",
}

RADIO_STATION_ALIASES = {
    "haryani": "haryanvi",
    "top_haryanvi": "topharyanvi",
    "rajashtani": "rajasthani",
}
RADIO_STATION_LABELS = {
    "punjabi": "Punjabi",
    "haryanvi": "Haryanvi Hits (Zeno FM)",
    "topharyanvi": "Radio Haryanvi",
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
