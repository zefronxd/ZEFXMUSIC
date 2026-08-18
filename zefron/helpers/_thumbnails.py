# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


import os
import asyncio
import aiohttp
from PIL import (Image, ImageDraw, ImageEnhance,
                 ImageFilter, ImageFont, ImageOps)

from zefron import config, logger
from zefron.core.http import get_http_session
from zefron.helpers import Track


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as output:
        output.write(data)


class Thumbnail:
    def __init__(self):
        self.rect = (914, 514)
        self.fill = (255, 255, 255)
        self.font1 = ImageFont.truetype("zefron/helpers/Raleway-Bold.ttf", 30)
        self.font2 = ImageFont.truetype("zefron/helpers/Inter-Light.ttf", 30)
        self.session: aiohttp.ClientSession | None = None
        self._render_slots = asyncio.Semaphore(2)

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.youtube.com/",
    }

    async def start(self) -> None:
        self.session = await get_http_session()

    async def close(self) -> None:
        self.session = None

    async def _fetch_thumb(self, output_path: str, url: str) -> bool:
        """Download a thumbnail URL to disk. Returns True only on a real image."""
        if not url:
            return False
        try:
            async with self.session.get(
                url,
                headers=self._HEADERS,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return False
                data = await resp.read()
                # A real image is never this small — guard against HTML error pages
                if len(data) < 800:
                    return False
                # Reject HTML error pages served with 200 status
                if data[:6] in (b"<html>", b"<!DOC", b"<HTML>"):
                    return False
                await asyncio.to_thread(_write_bytes, output_path, data)
            return True
        except Exception:
            return False

    async def _download_thumb(self, song: Track, temp: str) -> bool:
        """Try the track's thumbnail URL, then fall back to standard YT resolutions."""
        # Candidate URLs in quality order — include both JPEG and WebP
        candidates = [song.thumbnail] if song.thumbnail else []
        if song.id:
            vid = song.id
            candidates += [
                f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg",
                f"https://i.ytimg.com/vi_webp/{vid}/maxresdefault.webp",
                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                f"https://i.ytimg.com/vi_webp/{vid}/hqdefault.webp",
                f"https://i.ytimg.com/vi/{vid}/sddefault.jpg",
                f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                f"https://i.ytimg.com/vi_webp/{vid}/mqdefault.webp",
                f"https://i.ytimg.com/vi/{vid}/default.jpg",
            ]

        for url in candidates:
            if await self._fetch_thumb(temp, url):
                return True
        return False

    async def generate(self, song: Track, size=(1280, 720)) -> str:
        try:
            temp = f"cache/temp_{song.id}.jpg"
            output = f"cache/{song.id}.png"
            if os.path.exists(output):
                return output

            if not await self._download_thumb(song, temp):
                logger.warning("Thumbnail: no usable image for %s (%s)", song.title, song.id)
                return config.DEFAULT_THUMB

            async with self._render_slots:
                return await asyncio.to_thread(
                    self._render, song, temp, output, size
                )
        except Exception as e:
            logger.warning("Thumbnail generation failed for %s: %s", getattr(song, "id", "?"), e)
            return config.DEFAULT_THUMB

    def _render(self, song: Track, temp: str, output: str, size) -> str:
        """Render PIL work off the event loop and return the cached image."""
        try:
            thumb = Image.open(temp).convert("RGBA").resize(
                size, Image.Resampling.LANCZOS,
            )
            blur = thumb.filter(ImageFilter.GaussianBlur(25))
            image = ImageEnhance.Brightness(blur).enhance(.40)

            # Rounded-rect overlay
            mask = Image.new("L", self.rect, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, self.rect[0], self.rect[1]),
                radius=15,
                fill=255,
            )
            _rect = ImageOps.fit(
                thumb, self.rect,
                method=Image.LANCZOS, centering=(0.5, 0.5),
            )
            _rect.putalpha(mask)
            image.paste(_rect, (183, 30), _rect)

            draw = ImageDraw.Draw(image)
            draw.text(
                xy=(50, 560),
                text=f"{song.channel_name[:25]} | {song.view_count}",
                font=self.font2, fill=self.fill,
            )
            draw.text((50, 600), song.title[:50], font=self.font1, fill=self.fill)
            draw.text((40, 650), "0:01", font=self.font1)
            draw.line([(140, 670), (1160, 670)], fill=self.fill, width=5, joint="curve")
            draw.text((1185, 650), song.duration, font=self.font1, fill=self.fill)

            image.save(output)
            try:
                os.remove(temp)
            except Exception:
                pass
            return output

        except Exception as e:
            logger.warning("Thumbnail generation failed for %s: %s", getattr(song, "id", "?"), e)
            return config.DEFAULT_THUMB
