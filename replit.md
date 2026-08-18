# ZEFXMusic

## Project overview

ZEFXMusic is a Python Telegram music bot with PyTgCalls voice-chat playback,
YouTube/provider media resolution, recommendations, and an optional AI chat
handler.

## Running on Replit

- Workflow: `Start application`
- Command: `uv run --python 3.11 python3 -m zefron`
- Required runtime configuration is supplied through Replit Secrets.
- Playback assistants must be Telegram administrators with permission to
  manage voice/video chats in groups where music is used.

## User preferences

- Keep the existing Python/Pyrogram/PyTgCalls structure.
- Prefer fast user-visible responses and bounded retries over waiting
  indefinitely on external media services.
- Never hardcode provider credentials; read them from environment variables.