# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import re
import aiohttp

from pyrogram import enums, filters, types

from zefron import zefx, app, db, lang, queue
from zefron.helpers import Media


# Verified working stations (200 OK)
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
    # ── Regional Indian ────────────────────────────────────────────────────
    # These are public live streams, so /radio can play them without
    # downloading individual songs.  Aliases make both short and descriptive
    # commands convenient (for example /radio punjabi_old).
    "punjabi":     "https://bolpunjabi-ekamsoftware.radioca.st/stream",
    "punjabi_new": "https://s4.radio.co/sfefce156f/listen",
    "punjabi_old": "http://mehramedia.com:8021/;",
    "haryanvi":    "https://azuracast.radiokasoot.com/radio/8000/listen",
    "haryanvi_new": "https://azuracast.radiokasoot.com/radio/8000/listen",
    "haryanvi_old": "https://azuracast.radiokasoot.com/radio/8000/listen",
    "rajasthani":  "https://streamasiacdn.atc-labs.com/jaipurradio.aac",
    "rajasthani_new": "https://streamasiacdn.atc-labs.com/jaipurradio.aac",
    "rajasthani_old": "https://streamasiacdn.atc-labs.com/jaipurradio.aac",
    # ── English / International ────────────────────────────────────────────
    "english":     "https://media-ssl.musicradio.com/CapitalUK",
    "english_pop": "http://ice1.somafm.com/indiepop-128-mp3",
    "pop":         "http://ice1.somafm.com/indiepop-128-mp3",
    "pop_hits":    "https://media-ssl.musicradio.com/CapitalUK",
    "english_new": "https://media-ssl.musicradio.com/CapitalUK",
    "top40":       "https://media-ssl.musicradio.com/CapitalUK",
    "english_old": "http://listen.181fm.com/181-goodtime_128k.mp3",
    "oldies":      "http://listen.181fm.com/181-goodtime_128k.mp3",
    "english_80s": "https://media-ssl.musicradio.com/Heart80sMP3",
    "english_90s": "https://media-ssl.musicradio.com/Heart90sMP3",
    "english_2000s": "https://as-hls-ww.live.cf.md.bbci.co.uk/pool_11351741/live/ww/bbc_radio_one_anthems/bbc_radio_one_anthems.isml/bbc_radio_one_anthems-audio%3d96000.norewind.m3u8",
    "dance":       "https://media-ssl.musicradio.com/HeartDanceMP3",
    "edm":         "http://ice1.somafm.com/beatblender-128-mp3",
    "hiphop":      "http://stream.radiojar.com/c1912tk5rtzuv",
    "hip_hop":     "http://stream.radiojar.com/c1912tk5rtzuv",
    "rap":         "https://ice64.securenetsystems.net/LFTM",
    "rnb":         "https://ice64.securenetsystems.net/LFTM",
    "lofi":        "http://ice1.somafm.com/groovesalad-128-mp3",
    "chill":       "http://ice1.somafm.com/groovesalad-128-mp3",
    "rock":        "http://ice1.somafm.com/metal-128-mp3",
    "metal":       "http://ice1.somafm.com/metal-128-mp3",
    "classicrock": "https://cast1.torontocast.com:4610/stream",
    "beats":       "http://ice1.somafm.com/beatblender-128-mp3",
    "ambient":     "http://ice1.somafm.com/deepspaceone-128-mp3",
}

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _resolve_station(query: str) -> tuple[str, str] | tuple[None, None]:
    """Return (display_name, url) for a known station or direct URL."""
    q = query.strip()
    if _URL_RE.match(q):
        return q, q
    key = q.lower().replace("-", "_").replace(" ", "_")
    # Prefer an exact alias.  Without this, "punjabi_old" would match the
    # shorter "punjabi" entry first because the fallback below is fuzzy.
    if key in RADIO_STATIONS:
        return key.capitalize(), RADIO_STATIONS[key]
    for name, url in RADIO_STATIONS.items():
        if name in key or key in name:
            return name.capitalize(), url
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
        stations = "\n".join(f"  • <code>{k}</code>" for k in RADIO_STATIONS)
        return await m.reply_text(m.lang["radio_usage"].format(stations))

    query = " ".join(m.command[1:]).strip()
    station_name, stream_url = _resolve_station(query)

    if not station_name:
        return await m.reply_text(
            m.lang["radio_not_found"].format(", ".join(RADIO_STATIONS.keys()))
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
    )

    if queue.get_queue(m.chat.id) or await db.get_call(m.chat.id):
        position = queue.add(m.chat.id, media)
        return await sent.edit_text(
            m.lang["radio_queued"].format(position, station_name)
        )

    queue.add(m.chat.id, media)
    await zefx.play_media(chat_id=m.chat.id, message=sent, media=media)
