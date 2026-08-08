# ZEFXMusic

Telegram group-call music bot built with Python, Pyrogram/Kurigram, PyTgCalls,
NTgCalls, FFmpeg, and MongoDB.

## Runtime

- Python 3.11
- PyTgCalls 2.3.3 with NTgCalls 2.x
- FFmpeg
- 48 kHz stereo signed 16-bit PCM audio pipeline for voice-chat playback

## Running on Replit

The `Start application` workflow runs:

```bash
uv run --python 3.11 python3 -m zefron
```

The bot requires the Telegram, MongoDB, owner, logger, and assistant-session
environment variables documented in `sample.env`. FFmpeg is provided by the
Replit system environment.