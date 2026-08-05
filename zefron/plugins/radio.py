# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import re
import aiohttp

from pyrogram import filters, types

from zefron import zefx, app, db, lang, queue
from zefron.helpers import Media
from zefron.helpers._play import checkUB


# Known internet radio stations (name → stream URL)
RADIO_STATIONS: dict[str, str] = {
    "lofi":        "https://streams.ilovemusic.de/iloveradio17.mp3",
    "jazz":        "https://streaming.radio.co/s3b4e64b20/listen",
    "classical":   "http://ms1.radionomy.com/ClassicalMusicOnly",
    "pop":         "https://streams.ilovemusic.de/iloveradio2.mp3",
    "edm":         "https://streams.ilovemusic.de/iloveradio9.mp3",
    "hits":        "https://streams.ilovemusic.de/iloveradio1.mp3",
    "chill":       "https://streams.ilovemusic.de/iloveradio17.mp3",
    "rock":        "https://streams.ilovemusic.de/iloveradio6.mp3",
    "hip-hop":     "https://streams.ilovemusic.de/iloveradio3.mp3",
    "bollywood":   "https://airhlsstreamingws01.akamaized.net/airhlsstream/airfm5/playlist.m3u8",
}

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _resolve_station(query: str) -> tuple[str, str] | tuple[None, None]:
    """Return (name, url) for a known station or a raw URL."""
    if _URL_RE.match(query):
        return query, query
    key = query.strip().lower()
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
@checkUB
async def radio_hndlr(
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

    video = "v" in m.command[0].lower()

    # ── Get query ─────────────────────────────────────────────────────────
    if len(m.command) < 2:
        stations = "\n".join(
            f"  • <code>{k}</code>" for k in RADIO_STATIONS
        )
        return await m.reply_text(
            m.lang["radio_usage"].format(stations)
        )

    query = " ".join(m.command[1:]).strip()
    station_name, stream_url = _resolve_station(query)

    if not station_name:
        return await m.reply_text(
            m.lang["radio_not_found"].format(
                ", ".join(RADIO_STATIONS.keys())
            )
        )

    sent = await m.reply_text(m.lang["radio_connecting"].format(station_name))

    # ── Quick HEAD check ──────────────────────────────────────────────────
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(
                stream_url,
                timeout=aiohttp.ClientTimeout(total=8),
                allow_redirects=True,
            ) as resp:
                if resp.status not in (200, 206):
                    return await sent.edit_text(
                        m.lang["radio_unreachable"].format(station_name)
                    )
    except Exception:
        # Some radio streams don't respond to HEAD — allow through
        pass

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

    if force:
        queue.force_add(m.chat.id, media)
    else:
        position = queue.add(m.chat.id, media)
        if position != 0 or await db.get_call(m.chat.id):
            return await sent.edit_text(
                m.lang["radio_queued"].format(position, station_name)
            )

    await zefx.play_media(chat_id=m.chat.id, message=sent, media=media)
