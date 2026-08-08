---
name: Telegram VC FFmpeg tuning
description: Stable low-latency input settings for the Telegram music voice-chat path.
---

Use the PyTgCalls 2.x audio contract explicitly as 48 kHz, two-channel PCM, and keep remote input tuning bounded: reconnect quickly, discard corrupt packets, repair timestamps, select only the first audio stream, and resample with async compensation.

**Why:** A real remote audio endpoint decoded cleanly with no FFmpeg warnings after these constraints were applied, while unbounded or loosely specified remote input behavior can turn short CDN jitter into audible VC gaps.

**How to apply:** Preserve these constraints when changing providers or stream handling; validate the generated FFmpeg command after PyTgCalls upgrades because its custom option markers are consumed before FFmpeg runs.