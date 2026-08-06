---
name: Radio stream validation
description: How to validate live radio endpoints reliably in this bot.
---

Live audio endpoints may reject `HEAD` requests while serving audio correctly. Validate them with a short ranged `GET` and inspect the response content type or audio bytes.

**Why:** Several working radio providers returned `405`, `400`, or redirect responses to `HEAD` even though streaming `GET` returned valid MPEG/AAC data.

**How to apply:** When adding or replacing a station URL, use a bounded `GET` probe and keep the station only if it returns audio data.