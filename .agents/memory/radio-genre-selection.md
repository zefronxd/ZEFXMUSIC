---
name: Genre radio selection
description: Why genre radio uses fresh song search queues instead of fixed live streams.
---

Genre channels that promise latest or trending music should search several current results and queue songs; a fixed live radio URL cannot guarantee recency or genre-specific freshness.

**Why:** Live streams are controlled by third parties and may repeat old tracks or provide no useful now-playing metadata, while a small fresh-results queue gives predictable playback and lets the existing downloader handle each song.

**How to apply:** Keep Bollywood/live stations as explicit stream entries, but implement “latest/trending” regional channels as song-search queues with deduplication, duration filtering, and download failure handling.