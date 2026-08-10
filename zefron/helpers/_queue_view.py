# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.

import html
import math


PAGE_SIZE = 20


def page_count(item_count: int) -> int:
    return max(1, math.ceil(item_count / PAGE_SIZE))


def playlist_page_text(lang: dict, total: int, items: list[tuple[str, str]], page: int) -> str:
    """Render one 20-track page from a playlist result."""
    start = page * PAGE_SIZE
    visible = items[start : start + PAGE_SIZE]
    text = lang["playlist_queued"].format(total)
    text += "<blockquote expandable>"
    for index, (title, duration) in enumerate(visible, start=start + 1):
        text += (
            f"<b>{index}.</b> {html.escape(title)}"
            f" — {html.escape(duration)}\n"
        )
    return text + "</blockquote>"


def queue_page_text(lang: dict, items: list, page: int) -> str:
    """Render the current track and one 20-track queue page."""
    current = items[0]
    text = lang["queue_curr"].format(
        current.url,
        html.escape((current.title or "")[:50]),
        current.duration,
        current.user,
    )
    queued = items[1:]
    start = page * PAGE_SIZE
    visible = queued[start : start + PAGE_SIZE]
    if visible:
        text += "<blockquote expandable>"
        for index, media in enumerate(visible, start=start + 2):
            text += lang["queue_item"].format(
                index, html.escape(media.title or ""), media.duration
            )
        text += "</blockquote>"
    return text