# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import asyncio

from pyrogram import filters, types

from zefron import app, db


_COMMANDS = ["sametaste", "taste", "musicmatch"]


def _song_ids(songs: list[dict]) -> set[str]:
    return {str(song.get("id")) for song in songs if song.get("id")}


@app.on_message(
    filters.command(_COMMANDS)
    & filters.group
    & ~app.bl_users
)
async def same_taste(_, m: types.Message):
    """Show only the user's same-taste percentage across saved listeners."""
    mine, other_lists = await asyncio.gather(
        db.get_favourites(m.from_user.id),
        db.get_all_favourite_lists(exclude_id=m.from_user.id),
    )

    if not mine:
        return await m.reply_text(
            "🎧 You have not saved any favourite songs yet.\n"
            "Save songs with the ❤️ button, then try <code>/sametaste</code>.",
            quote=True,
        )

    mine_ids = _song_ids(mine)
    matching_users = sum(
        bool(mine_ids & _song_ids(song_list))
        for song_list in other_lists
    )
    total_listeners = len(other_lists)
    score = round(matching_users / total_listeners * 100) if total_listeners else 0

    await m.reply_text(
        f"🎧 <b>Same Taste Finder</b>\n\n"
        f"💯 <b>Your same-taste score:</b> {score}%",
        quote=True,
    )