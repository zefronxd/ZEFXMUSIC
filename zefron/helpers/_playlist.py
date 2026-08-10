# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.

from collections import OrderedDict


# Playlist result messages are short-lived UI state. Keep a bounded in-memory
# snapshot so pagination does not depend on mutable queue positions.
_pages: OrderedDict[tuple[int, int], list[tuple[str, str]]] = OrderedDict()
_MAX_SNAPSHOTS = 500


def remember(chat_id: int, message_id: int, tracks: list) -> None:
    key = (chat_id, message_id)
    _pages[key] = [
        (track.title or "", track.duration or "00:00")
        for track in tracks
    ]
    _pages.move_to_end(key)
    while len(_pages) > _MAX_SNAPSHOTS:
        _pages.popitem(last=False)


def get(chat_id: int, message_id: int) -> list[tuple[str, str]] | None:
    return _pages.get((chat_id, message_id))