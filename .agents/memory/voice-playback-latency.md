---
name: Voice playback latency
description: Durable performance rule for Telegram VC playback changes.
---

Remote audio playback is latency-sensitive. Start the stream before generating thumbnails, editing the now-playing card, or sending optional logs. Keep those side effects backgrounded and cancel stale card work when the current track changes.

**Why:** Network and image operations on the playback path made the assistant feel slow and allowed stale UI tasks to interfere with queue transitions.

**How to apply:** For future playback changes, keep `client.play` as the first awaited operation after media resolution; use direct remote audio URLs for songs and retain a fallback only when the stream provider rejects that track.