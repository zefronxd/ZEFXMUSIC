<div align="center">

<img src="https://files.catbox.moe/zvziwk.jpg" width="720" height="auto" style="border-radius: 12px"/>

<br><br>

# 🎵 ZEFXMusic

**Telegram Group Calls Streaming Bot**

Stream high-quality audio & video directly into your Telegram group video chats.
Supports YouTube, Spotify, Apple Music, SoundCloud, Resso, and M3U8 links.

<br>

[![Stars](https://img.shields.io/github/stars/zefronxd/ZEFXMusic?color=blueviolet&logo=github&logoColor=black&style=for-the-badge)](https://github.com/zefronxd/ZEFXMusic/stargazers)
[![Forks](https://img.shields.io/github/forks/zefronxd/ZEFXMusic?color=blueviolet&logo=github&logoColor=black&style=for-the-badge)](https://github.com/zefronxd/ZEFXMusic/network/members)
[![License](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Written%20in-Python-blue?style=for-the-badge&logo=python)](https://python.org)

</div>

---

## ✨ Features

- 🎧 **Low-latency audio streaming** into Telegram group video chats
- 📺 **Video playback** support in group video chats
- 🌐 **Multi-platform** — YouTube, Spotify, Apple Music, SoundCloud, Resso, M3U8
- 📋 **Queue management** with auto-play support
- 🌍 **Multi-language** support
- 🖼️ **Thumbnail generation** for now-playing cards
- 🍪 **Cookie support** for age-restricted / premium content
- 👥 **Multiple assistant accounts** (up to 3 sessions)
- 🔒 **Admin-based auth** system with blacklist and sudo users
- ⚙️ **Easy deployment** — VPS, Heroku, Docker, or Replit

---

## ⚙️ Configuration

Copy `sample.env` to `.env` and fill in your values:

```env
# From my.telegram.org/apps
API_ID=123456
API_HASH=abcdef1234567890

# From @BotFather on Telegram
BOT_TOKEN=123456:ABC-DEF
LOGGER_ID=-1001234567890

# From cloud.mongodb.com
MONGO_URL=mongodb+srv://...

# Your Telegram user ID
OWNER_ID=123456789

# Pyrogram session string (get from @StringFatherBot)
SESSION=BQgfh...AA
```

### All Available Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_ID` | ✅ | — | Telegram API ID |
| `API_HASH` | ✅ | — | Telegram API Hash |
| `BOT_TOKEN` | ✅ | — | Bot token from @BotFather |
| `MONGO_URL` | ✅ | — | MongoDB connection URL |
| `LOGGER_ID` | ✅ | — | Log channel/group ID |
| `OWNER_ID` | ✅ | — | Your Telegram user ID |
| `SESSION` | ✅ | — | Pyrogram session string |
| `SESSION2` | ❌ | — | 2nd assistant session |
| `SESSION3` | ❌ | — | 3rd assistant session |
| `DURATION_LIMIT` | ❌ | `60` | Max track duration (minutes) |
| `QUEUE_LIMIT` | ❌ | `20` | Max tracks in queue |
| `PLAYLIST_LIMIT` | ❌ | `20` | Max tracks from a playlist |
| `AUTO_LEAVE` | ❌ | `False` | Auto-leave voice chat when empty |
| `AUTO_LEAVING_ASSISTANT` | ❌ | `False` | Enable periodic cleanup of inactive assistant chats |
| `AUTO_LEAVE_ASSISTANT_TIME` | ❌ | `3600` | Seconds between assistant cleanup cycles (minimum 300) |
| `AUTO_END` | ❌ | `False` | Auto-end stream when queue is empty |
| `THUMB_GEN` | ❌ | `True` | Generate thumbnails |
| `VIDEO_PLAY` | ❌ | `True` | Enable video playback |
| `LANG_CODE` | ❌ | `en` | Default language |
| `COOKIES_URL` | ❌ | — | Cookie URL (batbin.me) for restricted content |
| `SUPPORT_CHANNEL` | ❌ | — | Your support channel link |
| `SUPPORT_CHAT` | ❌ | — | Your support group link |
| `START_IMG` | ❌ | — | Start message image URL |
| `PING_IMG` | ❌ | — | Ping message image URL |
| `DEFAULT_THUMB` | ❌ | — | Default thumbnail image URL |
| `ONEGRAB_API_KEY` | ❌ | — | Legacy search API key for non-Spotify fallback |
| `SPOTIFY_CLIENT_ID` | ❌* | — | Spotify Developer app client ID |
| `SPOTIFY_CLIENT_SECRET` | ❌* | — | Spotify Developer app client secret |
| `ApiUrl` / `SHRUTI_API_URL` | ❌ | — | Shruti media provider base URL |
| `APIKey` / `SHRUTI_API_KEY` | ❌ | — | Shruti media provider credential |
| `SHRUTI_API_KEY2` | ❌ | — | Optional second Shruti provider credential |
| `LASTFM_API_KEY` | ❌ | — | Last.fm recommendation API key |
| `LASTFM_SHARED_SECRET` | ❌ | — | Last.fm shared secret, if required |
| `LASTFM_API_KEY_2` | ❌ | — | Optional Last.fm fallback API key |
| `LASTFM_SHARED_SECRET_2` | ❌ | — | Optional Last.fm fallback shared secret |

Provider credentials are intentionally environment-only. If an imported copy
contains old embedded credentials, rotate them at the provider and set the
replacement values in Replit Secrets.

---

## 🚀 Deployment

### Local / VPS

```bash
git clone https://github.com/zefronxd/ZEFXMusic && cd ZEFXMusic

# Install uv
curl -Ls https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# Install dependencies
uv sync --frozen

# Configure environment
cp sample.env .env
# Edit .env with your credentials

# Start the bot
bash start
```

### Replit audio baseline

The Replit workflow uses Python 3.11, PyTgCalls 2.3.3, and the NTgCalls
native media engine. Audio is normalized by FFmpeg to signed 16-bit,
48 kHz stereo PCM before it enters the Telegram voice chat. This is an
internal playback baseline and is intentionally not user-configurable.

### Heroku

[![Deploy on Heroku](https://img.shields.io/badge/Deploy%20On%20Heroku-black?style=for-the-badge&logo=heroku)](https://dashboard.heroku.com/new?template=https://github.com/zefronxd/ZEFXMusic)

### Docker

```bash
docker build -t zefxmusic .
docker run --env-file .env zefxmusic
```

---

## 🎮 Commands

| Command | Description |
|---|---|
| `/play [song/link]` | Play audio in voice chat |
| `/vplay [song/link]` | Play video in voice chat |
| `/pause` | Pause playback |
| `/resume` | Resume playback |
| `/skip` | Skip to next track |
| `/stop` | Stop playback and clear queue |
| `/seek [seconds]` | Seek to position |
| `/queue` | Show current queue |
| `/loop` | Toggle loop mode |
| `/radio punjabi` | Play Punjabi new-hits radio from a direct live stream |
| `/radio status` | Show the active radio source and reconnect status |
| `/ping` | Check bot latency |
| `/sametaste` | See your same-taste percentage across listeners |

---

## 🤝 Contributing

Contributions are welcome!

1. Fork the repository
2. Create your branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m 'Add some feature'`
4. Push: `git push origin feature/your-feature`
5. Open a Pull Request

---

## 📡 Support

- **Updates:** [@baka_updates](https://t.me/baka_updates)
- **Support Group:** [Join here](https://t.me/+urALmsBSdB9hYWE5)

---

## 📄 License

This project is licensed under the **MIT License** — see [LICENSE](LICENSE) for details.

---

<div align="center">

⭐ **Enjoying the bot? Star the repo — it keeps the rhythm going!**

</div>
