---
name: Radio stream verification
description: Reliability rule for public internet radio sources used by the bot.
---

Public radio directories and station pages can list endpoints that are no longer reachable. A source should be checked as a real streaming response before it becomes a station default; a successful probe should expose an audio content type and, ideally, station metadata.

**Why:** A listed Punjabi station endpoint refused connections even though its public station page still advertised it, while the verified Zeno direct stream returned live MPEG audio.

**How to apply:** Prefer direct HTTP audio streams over web-player URLs or YouTube links, probe them with a bounded request, and keep playback reconnect handling enabled for long-running live stations.