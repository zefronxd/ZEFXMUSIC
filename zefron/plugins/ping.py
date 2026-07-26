# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import asyncio
import os
import time

import psutil
from pyrogram import filters, types

from zefron import app, zefx, boot, config, lang
from zefron.helpers import buttons

# ── Cached photo file_id (skip URL upload after first /ping) ──────────────
_ping_photo_file_id: str | None = None

# ── Background process-CPU sampler ────────────────────────────────────────
# cpu_percent(interval=0) is meaningless on first call; a rolling average
# from a background task gives a stable, accurate reading of the bot process.
_proc          = psutil.Process()
_cpu_samples:  list[float] = []
_cpu_task:     asyncio.Task | None = None


async def _cpu_sampler() -> None:
    """Sample *this process* CPU every 3 s; keep a 10-reading rolling window."""
    _proc.cpu_percent(interval=None)   # prime — first call always returns 0
    await asyncio.sleep(1)
    while True:
        sample = await asyncio.to_thread(_proc.cpu_percent, 1)
        _cpu_samples.append(sample)
        if len(_cpu_samples) > 10:
            _cpu_samples.pop(0)
        await asyncio.sleep(2)


def _start_sampler() -> None:
    global _cpu_task
    if _cpu_task is None or _cpu_task.done():
        _cpu_task = asyncio.create_task(_cpu_sampler())


def _get_cpu() -> float:
    if _cpu_samples:
        return round(sum(_cpu_samples) / len(_cpu_samples), 1)
    return round(_proc.cpu_percent(interval=None), 1)


@app.on_message(filters.command(["alive", "ping"]) & ~app.bl_users)
@lang.language()
async def _ping(_, m: types.Message):
    _start_sampler()

    global _ping_photo_file_id
    photo = _ping_photo_file_id or config.PING_IMG

    # ── Real Telegram RTT: measure actual send round-trip ─────────────────
    start = time.monotonic()
    sent  = await m.reply_photo(photo=photo, caption="<code>⏳ measuring...</code>", quote=True)
    latency = round((time.monotonic() - start) * 1000, 2)

    # Cache file_id from the sent photo for all future /ping calls
    if not _ping_photo_file_id and sent.photo:
        _ping_photo_file_id = sent.photo.file_id

    # ── Collect real bot-process stats ────────────────────────────────────
    mem_info = await asyncio.to_thread(lambda: _proc.memory_info())
    ram_mb   = round(mem_info.rss / 1024 / 1024, 1)          # bot RSS in MB
    cpu      = _get_cpu()                                     # bot process CPU %
    disk     = psutil.disk_usage(os.getcwd()).percent         # cwd disk usage
    vc_ping  = await zefx.ping()

    get_time = lambda s: (lambda r: (f"{r[-1]}, " if r[-1][:-4] != "0" else "") + ":".join(reversed(r[:-1])))([f"{v}{u}" for v, u in zip([s % 60, (s // 60) % 60, (s // 3600) % 24, s // 86400], ["s", "m", "h", "days"])])
    uptime   = get_time(int(time.time() - boot))

    caption  = m.lang["ping_pong"].format(latency, uptime, cpu, f"{ram_mb} MB", disk, vc_ping)
    markup   = buttons.ping_markup(m.lang["support"])

    await sent.edit_caption(caption=caption, reply_markup=markup)
