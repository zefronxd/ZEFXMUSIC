---
name: Genre radio selection
description: Why genre radio uses fresh song search queues instead of fixed live streams.
---

Genre channels requested as “radio” should use direct live radio streams, even when the user describes them as latest or trending; do not substitute YouTube search/download playback.

**Why:** The user's expected behavior is the same as existing Bollywood radio stations: continuous stream playback from a radio provider, not individual downloaded songs.

**How to apply:** Keep one direct station URL per requested genre, set the media duration to the infinite-stream marker, and preserve the existing radio reconnect path.