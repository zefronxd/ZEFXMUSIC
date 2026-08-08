# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import asyncio
import logging
import re
import urllib.parse

import aiohttp
import yt_dlp

logger = logging.getLogger(__name__)

# ── Common title suffixes that don't change what song it is ───────────────
_SUFFIX_RE = re.compile(
    r"[\(\[\|]?\s*("
    r"official\s*(music\s*)?video|official\s*audio|official\s*lyric(s)?(\s*video)?|"
    r"lyric(s)?(\s*video)?|full\s*(song|video|audio)|audio\s*song|"
    r"hd|hq|4k|8k|1080p|720p|"
    r"with\s*lyrics?|visualizer|remastered|re[\-\s]?upload|"
    r"\d{4}\s*(version|remaster)|feat\.?\s*[^)\]]+"
    r")\s*[\)\]]?",
    re.IGNORECASE,
)


def _normalize_title(t: str) -> str:
    """Strip suffixes/punctuation/casing so upload variants collapse to one key."""
    t = _SUFFIX_RE.sub("", t)
    t = re.sub(r"[\(\[\]\)|'\"""''`]", "", t)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to",
    "is", "it", "by", "ft", "feat", "with", "for", "from", "song",
    "music", "audio", "video", "full", "new",
}


def _key_words(title: str) -> set[str]:
    """Significant lower-case words from title (length > 2, not stopwords)."""
    return {
        w for w in re.findall(r"\w+", title.lower())
        if len(w) > 2 and w not in _STOPWORDS
    }


def _is_same_song(candidate: str, playing: str) -> bool:
    """True when candidate shares > 55 % of the playing song's key words."""
    pw = _key_words(playing)
    if not pw:
        return False
    overlap = pw & _key_words(candidate)
    return len(overlap) / len(pw) > 0.55


# ── Language / genre detection ─────────────────────────────────────────────

# Unicode script ranges
_RE_DEVANAGARI  = re.compile(r"[\u0900-\u097F]")   # Hindi / Marathi
_RE_GURMUKHI    = re.compile(r"[\u0A00-\u0A7F]")   # Punjabi
_RE_BENGALI     = re.compile(r"[\u0980-\u09FF]")
_RE_GUJARATI    = re.compile(r"[\u0A80-\u0AFF]")
_RE_TAMIL       = re.compile(r"[\u0B80-\u0BFF]")
_RE_TELUGU      = re.compile(r"[\u0C00-\u0C7F]")
_RE_KANNADA     = re.compile(r"[\u0C80-\u0CFF]")
_RE_MALAYALAM   = re.compile(r"[\u0D00-\u0D7F]")

# Known Indian music label keywords → genre
_LABEL_GENRE: list[tuple[str, str]] = [
    # Bollywood / Hindi
    ("t-series", "hindi"), ("t series", "hindi"),
    ("zee music", "hindi"), ("sony music india", "hindi"),
    ("saregama", "hindi"), ("tips music", "hindi"),
    ("eros now", "hindi"), ("yrf", "hindi"),
    ("dharma", "hindi"), ("rajshri", "hindi"),
    ("sony music entertainment india", "hindi"),
    # Punjabi
    ("speed records", "punjabi"), ("white hill", "punjabi"),
    ("t-series apna punjab", "punjabi"), ("punjabi hits", "punjabi"),
    ("desi crew", "punjabi"), ("saga music", "punjabi"),
    # Tamil
    ("sony music south", "tamil"), ("think music", "tamil"),
    ("sun music", "tamil"), ("ayngaran", "tamil"),
    ("lahari music", "tamil"),
    # Telugu
    ("aditya music", "telugu"), ("mango music", "telugu"),
    ("anand audio", "telugu"), ("lahari music telugu", "telugu"),
    # Kannada
    ("kannada music", "kannada"), ("anand audio kannada", "kannada"),
    # Malayalam
    ("malayalam music", "malayalam"), ("kappa tv", "malayalam"),
]

# Keyword hints that appear in titles/channels
_KEYWORD_GENRE: list[tuple[str, str]] = [
    (r"\bbollywood\b", "hindi"), (r"\bhindi\b", "hindi"),
    (r"\bpunjabi\b", "punjabi"), (r"\bpanjabi\b", "punjabi"),
    (r"\bbhangra\b", "punjabi"),
    (r"\btamil\b", "tamil"), (r"\bkollywood\b", "tamil"),
    (r"\btelugu\b", "telugu"), (r"\btollywood\b", "telugu"),
    (r"\bkannada\b", "kannada"), (r"\bsandalwood\b", "kannada"),
    (r"\bmalayalam\b", "malayalam"), (r"\bmollywood\b", "malayalam"),
    (r"\bbengali\b", "bengali"), (r"\bbangla\b", "bengali"),
    (r"\bmarathi\b", "marathi"), (r"\bgujarati\b", "gujarati"),
]


def _detect_genre(title: str, channel: str = "") -> str | None:
    """
    Detect music language/genre from Unicode script, channel label, or keywords.
    Returns a genre key like 'hindi', 'punjabi', 'tamil', … or None for unknown.
    """
    # 1. Script — most reliable signal
    for pattern, genre in (
        (_RE_DEVANAGARI,  "hindi"),
        (_RE_GURMUKHI,    "punjabi"),
        (_RE_BENGALI,     "bengali"),
        (_RE_GUJARATI,    "gujarati"),
        (_RE_TAMIL,       "tamil"),
        (_RE_TELUGU,      "telugu"),
        (_RE_KANNADA,     "kannada"),
        (_RE_MALAYALAM,   "malayalam"),
    ):
        if pattern.search(title):
            return genre

    combined = f"{title} {channel}".lower()

    # 2. Known label in channel name
    for label, genre in _LABEL_GENRE:
        if label in combined:
            return genre

    # 3. Keyword hints
    for pattern, genre in _KEYWORD_GENRE:
        if re.search(pattern, combined, re.IGNORECASE):
            return genre

    return None


# Genre → 3 search query templates  ({title} filled at runtime, song-centric)
# NOTE: avoid "playlist", "mix", "compilation", "jukebox" — they attract long compilations
_GENRE_QUERY_TEMPLATES: dict[str, list[str]] = {
    "hindi": [
        "{title} bollywood similar song official audio",
        "hindi song like {title} same feel official",
        "bollywood {title} type romantic sad song",
    ],
    "punjabi": [
        "{title} punjabi similar song official audio",
        "punjabi song like {title} same vibe official",
        "karan aujla sidhu moosewala type song like {title}",
    ],
    "tamil": [
        "{title} tamil similar song official audio",
        "kollywood song like {title} same mood official",
        "tamil {title} type melody song",
    ],
    "telugu": [
        "{title} telugu similar song official audio",
        "tollywood song like {title} same feel official",
        "telugu {title} type melody song",
    ],
    "kannada": [
        "{title} kannada similar song official audio",
        "kannada song like {title} same mood official",
        "sandalwood {title} type song official",
    ],
    "malayalam": [
        "{title} malayalam similar song official audio",
        "mollywood song like {title} same feel official",
        "malayalam {title} type melody official",
    ],
    "bengali": [
        "{title} bangla similar song official audio",
        "bengali song like {title} same vibe official",
        "bangla {title} type romantic song",
    ],
    "gujarati": [
        "{title} gujarati similar song official audio",
        "gujarati song like {title} same vibe official",
        "gujarati {title} type song official",
    ],
    "marathi": [
        "{title} marathi similar song official audio",
        "marathi song like {title} same mood official",
        "marathi {title} type song official",
    ],
}


# ── Known music labels (channel = label, not artist) ──────────────────────
_KNOWN_LABELS = {
    "t-series", "t series", "zee music", "sony music india", "saregama",
    "tips music", "eros now", "yrf", "dharma", "rajshri", "speed records",
    "white hill", "desi crew", "saga music", "sony music south", "think music",
    "sun music", "aditya music", "mango music", "anand audio", "lahari music",
    "dy music factory", "wynk music", "jio saavn", "gaana", "hungama",
    "universal music india", "warner music india", "emi music india",
    "sony music entertainment india", "sony bmg", "times music",
    "best of", "top songs", "hindi songs", "punjabi hits",
    "music factory", "records official", "official music",
}

# Patterns that indicate garbage in a channel name
_CHANNEL_NOISE_RE = re.compile(
    r"[•·]\s*[\d\.]+[kmb]?\s*views?.*|"      # • 12.9m views • 2 days ago
    r"\s*\d+\s*(days?|hours?|weeks?)\s*ago.*|"  # 2 days ago
    r"\bviews?\b.*",                            # stray "views" suffix
    re.IGNORECASE,
)


def _clean_channel(channel: str) -> str:
    """Remove YouTube noise (view counts, timestamps) from a channel name."""
    if not channel:
        return ""
    cleaned = _CHANNEL_NOISE_RE.sub("", channel)
    # Strip non-word chars except basic punctuation kept in artist names
    cleaned = re.sub(r"[^\w\s\-&',$\.]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _is_label_channel(channel: str) -> bool:
    """Return True when the channel is a music label, not the actual artist."""
    cl = channel.lower()
    for label in _KNOWN_LABELS:
        if label in cl:
            return True
    # Patterns like "Artist and Universal Music India" — ends with label keywords
    if re.search(r"\band\s+(universal|sony|warner|emi|tips|zee|t[\-\s]?series|"
                 r"saregama|music\s+india)\b", cl):
        return True
    return False


def _best_artist(channel: str, title: str) -> str:
    """
    Pick the best artist string for a Last.fm lookup.

    Priority:
      1. Clean channel name — if it's not a label and not empty.
      2. Artist parsed from the title (e.g. "Song - Artist" or "Song | Artist").
      3. Clean channel even if it's a label (last resort).
    """
    cleaned_ch = _clean_channel(channel)

    if cleaned_ch and not _is_label_channel(cleaned_ch):
        # Strip " and <label>" suffix if mixed (e.g. "Jasleen Royal and Universal Music India")
        stripped = re.sub(
            r"\s+and\s+(universal|sony|warner|emi|tips|zee|t[\-\s]?series|"
            r"saregama|music\s+india).*",
            "", cleaned_ch, flags=re.IGNORECASE,
        ).strip()
        if stripped:
            return stripped

    parsed = _parse_artist(title)
    if parsed:
        return parsed

    return cleaned_ch  # label fallback — better than nothing


def _parse_artist(title: str) -> str:
    """
    Extract the artist portion from common formats:
    'Artist - Song', 'Song | Artist', 'Song by Artist'.
    Returns empty string when nothing can be parsed.
    """
    for sep in (" - ", " – ", " — ", " | "):
        parts = title.split(sep, 1)
        if len(parts) == 2:
            # Heuristic: artist name tends to be shorter
            a, b = parts[0].strip(), parts[1].strip()
            return a if len(a) <= len(b) else b
    m = re.search(r"\bby\s+([A-Za-z][^\(\[\|]+)", title, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _extract_song_name(title: str) -> str:
    """
    Extract a clean, short song name suitable for Last.fm lookup.

    YouTube titles look like:
      "Kesariya - Brahmāstra | Ranbir Kapoor | Arijit Singh | Pritam"
      "Mere Naseeb Mein (Remix) - Baby H | Prem & Hardeep | Megha C"
      "Tu Hai Kahan by AUR | Official Audio"

    We want just the first token: "Kesariya", "Mere Naseeb Mein", "Tu Hai Kahan".
    """
    # Drop "by ArtistName" suffix that comes before separators
    cleaned = re.sub(r"\s+by\s+\S.*", "", title, flags=re.IGNORECASE).strip()
    # Take only the part before the first major separator
    for sep in (" - ", " – ", " — ", " | ", " ft.", " feat."):
        if sep in cleaned:
            cleaned = cleaned.split(sep, 1)[0].strip()
            break
    # Strip trailing parenthesised qualifiers like "(Remix)", "(Official Audio)"
    cleaned = re.sub(r"[\(\[].*", "", cleaned).strip()
    return cleaned or title


from pyrogram import enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# ── In-memory recommendation cache per chat ───────────────────────────────
# Structure: { chat_id: { "title": str, "songs": list[dict], "page": int } }
_cache: dict[int, dict] = {}

SONGS_PER_PAGE = 4

# Cycling button styles: blue → red → green
_STYLES = [
    enums.ButtonStyle.PRIMARY,   # blue
    enums.ButtonStyle.DANGER,    # red
    enums.ButtonStyle.SUCCESS,   # green
]



def _fmt_views(n) -> str:
    """Format a raw view count (int or str) to e.g. '12.3M views'."""
    try:
        v = int(n)
        if v >= 1_000_000:
            return f"{v/1_000_000:.1f}M views"
        if v >= 1_000:
            return f"{v/1_000:.1f}K views"
        return f"{v} views"
    except Exception:
        return ""


def _secs_to_str(seconds) -> str:
    """Convert total seconds (int/float) to 'H:MM:SS' or 'M:SS' string."""
    try:
        t = int(seconds)
        m, s = divmod(t, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"
    except Exception:
        return "0:00"


def _best_thumbnail(thumbnails: list[dict] | None) -> str:
    """Pick the highest-resolution thumbnail URL from a yt-dlp thumbnails list."""
    if not thumbnails:
        return ""
    # yt-dlp often gives a 'preference' or 'width' field; fall back to last entry
    best = max(
        (t for t in thumbnails if t.get("url")),
        key=lambda t: t.get("width") or t.get("preference") or 0,
        default=None,
    )
    return (best or {}).get("url", "").split("?")[0]


def _ytdlp_search(query: str, max_results: int = 15) -> list[dict]:
    """
    Synchronous yt-dlp search — run inside asyncio.to_thread.
    Returns flat entry dicts without downloading anything.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            return info.get("entries") or []
    except Exception as e:
        logger.warning("yt-dlp search failed for query %r: %s", query, e)
        return []


_LASTFM_BASE = "http://ws.audioscrobbler.com/2.0/"


async def _lastfm_get(session: aiohttp.ClientSession, params: dict) -> dict:
    """Single Last.fm GET call, returns parsed JSON or {}."""
    url = _LASTFM_BASE + "?" + urllib.parse.urlencode(params)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
    except Exception as e:
        logger.debug("Last.fm request failed: %s", e)
    return {}


async def _lastfm_similar(
    track: str, artist: str, api_key: str, limit: int = 16,
    fallback_api_key: str = "",
) -> list[tuple[str, str]]:
    """
    Return up to `limit` (track_name, artist_name) pairs from Last.fm.

    Strategy — always song/vibe-first, artist-last:
      1. track.getsimilar  — most accurate, song-level similarity.
      2. track.search → canonical artist → retry track.getsimilar.
      3. track.getTopTags → tag.gettoptracks  — same era/mood/vibe as the song.
      4. Last resort: artist.getsimilar + artist.gettoptracks (artist-level).
      5. Retry all steps with fallback_api_key if primary yields nothing.
    """
    if not api_key:
        return []

    async with aiohttp.ClientSession() as session:

        orig_artist_lower = artist.lower()

        def _parse_similar(data: dict) -> list[tuple[str, str]]:
            out = []
            for t in data.get("similartracks", {}).get("track", []):
                n = (t.get("name") or "").strip()
                a = (t.get("artist", {}).get("name") or "").strip()
                # Skip tracks by the original artist — we want different artists
                if n and a and a.lower() != orig_artist_lower:
                    out.append((n, a))
            return out

        async def _tag_based_recs(key: str, ref_track: str, ref_artist: str) -> list[tuple[str, str]]:
            """
            Step 3 — fetch song's mood/era tags, then pull top tracks per tag.
            Much more song-specific than artist.gettoptracks.
            """
            tag_data = await _lastfm_get(session, {
                "method": "track.getTopTags",
                "track": ref_track, "artist": ref_artist,
                "api_key": key, "format": "json", "autocorrect": 1,
            })
            tags = [
                t.get("name", "").strip().lower()
                for t in tag_data.get("toptags", {}).get("tag", [])[:4]
                if t.get("name", "").strip()
                # Skip generic spam tags
                and t.get("name", "").strip().lower() not in (
                    "seen live", "favourite", "love", "awesome", "cool",
                    "good", "best", "great", "nice", "beautiful",
                )
            ]
            if not tags:
                return []

            seen: set[str] = set()
            results: list[tuple[str, str]] = []
            # Query top 3 tags in parallel for more variety
            fetches = await asyncio.gather(*[
                _lastfm_get(session, {
                    "method": "tag.gettoptracks",
                    "tag": tag,
                    "api_key": key, "format": "json",
                    "limit": limit,
                })
                for tag in tags[:3]
            ])
            for resp in fetches:
                for t in resp.get("tracks", {}).get("track", []):
                    name = (t.get("name") or "").strip()
                    art = (t.get("artist", {}).get("name") or "").strip()
                    # Skip same artist — tag results can include the original artist's other songs
                    if name and art and name.lower() not in seen and art.lower() != orig_artist_lower:
                        seen.add(name.lower())
                        results.append((name, art))
                if len(results) >= limit:
                    break

            if results:
                logger.info("Last.fm tag-recs: %d tracks via tags %s", len(results), tags[:2])
            return results[:limit]

        async def _try_key(key: str) -> list[tuple[str, str]]:
            # ── Step 1: track.getsimilar (song-level, best) ──────────────────
            data = await _lastfm_get(session, {
                "method": "track.getsimilar",
                "track": track, "artist": artist,
                "api_key": key, "format": "json",
                "limit": limit, "autocorrect": 1,
            })
            pairs = _parse_similar(data)
            if pairs:
                return pairs

            # ── Step 2: track.search → canonical artist → retry getsimilar ───
            search_data = await _lastfm_get(session, {
                "method": "track.search",
                "track": track,
                "api_key": key, "format": "json", "limit": 3,
            })
            canonical_artist = ""
            for t in search_data.get("results", {}).get("trackmatches", {}).get("track", []):
                candidate = (t.get("artist") or "").strip()
                if candidate and candidate.lower() not in ("", "various artists"):
                    canonical_artist = candidate
                    break

            if canonical_artist and canonical_artist.lower() != artist.lower():
                data = await _lastfm_get(session, {
                    "method": "track.getsimilar",
                    "track": track, "artist": canonical_artist,
                    "api_key": key, "format": "json",
                    "limit": limit, "autocorrect": 1,
                })
                pairs = _parse_similar(data)
                if pairs:
                    logger.info("Last.fm: %d recs via canonical artist %r", len(pairs), canonical_artist)
                    return pairs

            # ── Step 3: tag-based recs (same era/mood/vibe as the song) ──────
            ref_artist = canonical_artist or artist
            tag_pairs = await _tag_based_recs(key, track, ref_artist)
            if tag_pairs:
                return tag_pairs

            # ── Step 4: last resort — SIMILAR artists only (never original) ──
            if not ref_artist:
                return []
            sim_data = await _lastfm_get(session, {
                "method": "artist.getsimilar",
                "artist": ref_artist,
                "api_key": key, "format": "json", "limit": 6,
            })
            # IMPORTANT: do NOT include ref_artist itself — that gives same-singer songs.
            # Only use genuinely similar artists.
            sim_artists = []
            for a in sim_data.get("similarartists", {}).get("artist", [])[:5]:
                name = (a.get("name") or "").strip()
                if name and name.lower() != orig_artist_lower:
                    sim_artists.append(name)

            if not sim_artists:
                return []

            async def _top_tracks(art: str) -> list[tuple[str, str]]:
                tt = await _lastfm_get(session, {
                    "method": "artist.gettoptracks",
                    "artist": art,
                    "api_key": key, "format": "json", "limit": 5,
                })
                return [
                    (t.get("name", "").strip(), art)
                    for t in tt.get("toptracks", {}).get("track", [])
                    if t.get("name", "").strip()
                ]

            all_tracks = await asyncio.gather(*[_top_tracks(a) for a in sim_artists])
            seen: set[str] = set()
            results: list[tuple[str, str]] = []
            for track_list in all_tracks:
                for t_name, t_artist in track_list:
                    k = t_name.lower()
                    if k not in seen and t_artist.lower() != orig_artist_lower:
                        seen.add(k)
                        results.append((t_name, t_artist))
            if results:
                logger.info(
                    "Last.fm: similar-artist fallback %d recs via %s for %r",
                    len(results), sim_artists, ref_artist,
                )
            return results[:limit]

        # ── Primary key ───────────────────────────────────────────────────────
        pairs = await _try_key(api_key)
        if pairs:
            return pairs

        # ── Fallback key ──────────────────────────────────────────────────────
        if fallback_api_key and fallback_api_key != api_key:
            logger.info("Last.fm: primary key returned 0 results — retrying with fallback API key")
            pairs = await _try_key(fallback_api_key)

        return pairs


def _ytdlp_search_one(query: str) -> dict | None:
    """Search YouTube for a single best-match song entry via yt-dlp."""
    entries = _ytdlp_search(query, max_results=3)
    return entries[0] if entries else None


def _build_queries(title: str, channel: str = "") -> list[str]:
    """
    Return 3 diverse yt-dlp search queries — song/vibe-first, NOT artist-first.

    Goal: find songs with similar mood, era, and feel to the playing track.
    We deliberately avoid "all songs by <artist>" queries so recommendations
    span the same era/genre rather than dumping the same artist's discography.
    """
    song_name = _extract_song_name(title)
    clean     = _normalize_title(song_name) or _normalize_title(title)
    genre     = _detect_genre(title, channel)

    if genre and genre in _GENRE_QUERY_TEMPLATES:
        # Song-centric: focus on the track itself, not the artist
        templates = _GENRE_QUERY_TEMPLATES[genre]
        return [t.format(artist=clean, title=clean) for t in templates]

    # Generic fallback — individual songs only, no compilations
    return [
        f"{clean} similar song official audio",
        f"song like {clean} same vibe official",
        f"{clean} type song official",
    ]


# Titles containing these words are compilations/mixes — not individual songs
_JUNK_TITLE_RE = re.compile(
    r"\b(playlist|mix|mashup|compilation|top\s*\d+|best\s+of|"
    r"jukebox|nonstop|non[\-\s]stop|medley|hour|hours|"
    r"songs?\s+collection|evergreen|all\s+songs?)\b",
    re.IGNORECASE,
)

_MAX_SONG_DURATION_SEC = 600   # 10 minutes — skip compilations / long mixes
_MIN_SONG_DURATION_SEC = 30    # skip very short clips / intros


def _entry_to_song(entry: dict, playing_title: str, seen_ids: set, seen_norms: set) -> dict | None:
    """Convert a yt-dlp entry dict to a song dict, or None if it should be skipped."""
    vid_id = entry.get("id") or ""
    if not vid_id or vid_id in seen_ids:
        return None

    title_raw = (entry.get("title") or "Unknown")[:60]

    # Skip compilations / mixes by title keywords
    if _JUNK_TITLE_RE.search(title_raw):
        return None

    if _is_same_song(title_raw, playing_title):
        return None

    norm = _normalize_title(title_raw)
    if norm in seen_norms:
        return None

    # Skip videos that are too long (compilations) or too short (intros/clips)
    dur_sec = int(entry.get("duration") or 0)
    if dur_sec > _MAX_SONG_DURATION_SEC or (dur_sec > 0 and dur_sec < _MIN_SONG_DURATION_SEC):
        return None

    seen_ids.add(vid_id)
    seen_norms.add(norm)
    return {
        "id": vid_id,
        "title": title_raw,
        "url": entry.get("url") or f"https://www.youtube.com/watch?v={vid_id}",
        "duration": _secs_to_str(dur_sec),
        "duration_sec": dur_sec,
        "thumbnail": _best_thumbnail(entry.get("thumbnails")),
        "channel_name": _clean_channel(entry.get("channel") or entry.get("uploader") or ""),
        "view_count": _fmt_views(entry.get("view_count") or ""),
    }


_REC_CACHE_TTL_DAYS = 7  # MongoDB documents expire after this many days


_REC_CACHE_VERSION = "v2"  # bump when filter logic changes to invalidate stale entries
_RECOMMEND_SLOTS = asyncio.Semaphore(4)
_RECOMMEND_INFLIGHT: dict[tuple[int, str], asyncio.Task] = {}


def _rec_cache_key(title: str, artist: str) -> str:
    """Stable lookup key: normalised title + artist + cache version."""
    return f"{_REC_CACHE_VERSION}::{_normalize_title(title)}::{_normalize_title(artist)}"


async def _mongo_get_rec(key: str) -> list[dict] | None:
    """Return cached song list from MongoDB, or None on miss/error."""
    try:
        from zefron import db as _db
        import datetime
        doc = await _db.rec_cache.find_one({"_id": key})
        if doc and doc.get("songs"):
            return doc["songs"]
    except Exception as e:
        logger.warning("MongoDB rec_cache read failed: %s", e)
    return None


async def _mongo_set_rec(key: str, songs: list[dict]) -> None:
    """Upsert song list into MongoDB with a TTL expiry timestamp."""
    try:
        from zefron import db as _db
        import datetime
        expires = datetime.datetime.utcnow() + datetime.timedelta(days=_REC_CACHE_TTL_DAYS)
        await _db.rec_cache.update_one(
            {"_id": key},
            {"$set": {"songs": songs, "expires_at": expires}},
            upsert=True,
        )
        # Ensure TTL index exists (no-op if already created)
        await _db.rec_cache.create_index(
            "expires_at", expireAfterSeconds=0, background=True
        )
        logger.info("Cached %d recs in MongoDB under key %r", len(songs), key)
    except Exception as e:
        logger.warning("MongoDB rec_cache write failed: %s", e)


async def fetch_related_songs(
    video_id: str, title: str, channel: str = ""
) -> list[dict]:
    """
    Fetch up to 12 distinct related YouTube songs.

    Strategy:
      0. Check MongoDB rec_cache — return immediately on hit (7-day TTL).
      1. Call Last.fm track.getSimilar with the parsed artist + title.
      2. Resolve each Last.fm suggestion to a YouTube video via yt-dlp.
      3. If Last.fm yields nothing, fall back to genre-aware yt-dlp queries.
      4. Store fresh results in MongoDB rec_cache before returning.
    """
    from zefron import config as _cfg  # late import to avoid circular

    # Pick best artist: prefer clean channel, fall back to title parsing, skip labels
    artist = _best_artist(channel, title)
    # Use a clean short song name for Last.fm — NOT the full normalized YouTube title
    song_name = _extract_song_name(title)
    cache_key = _rec_cache_key(song_name, artist)

    # ── Helper: filter out same-artist songs ─────────────────────────────────
    artist_norm = _normalize_title(artist)

    def _is_same_artist_song(song: dict) -> bool:
        """
        Return True when a song dict is likely by the original artist.
        Only applies when the channel is NOT a label (labels like T-Series upload
        many artists, so we can't use them to identify the original artist).
        """
        if not artist_norm or len(artist_norm) < 3:
            return False
        ch = song.get("channel_name", "")
        if not ch or _is_label_channel(ch):
            return False
        ch_norm = _normalize_title(ch)
        # Match if channel name fully contains artist or vice versa
        return artist_norm in ch_norm or ch_norm in artist_norm

    def _filter_same_artist(song_list: list[dict]) -> list[dict]:
        filtered = [s for s in song_list if not _is_same_artist_song(s)]
        # Only apply filter if we retain a reasonable number of songs
        return filtered if len(filtered) >= max(3, len(song_list) // 2) else song_list

    # ── 0. MongoDB cache hit ──────────────────────────────────────────────────
    cached = await _mongo_get_rec(cache_key)
    if cached:
        logger.info("rec_cache HIT for %r — skipping Last.fm/yt-dlp", cache_key)
        # Apply same-artist filter to cached results too (cache may be stale)
        return _filter_same_artist(cached)

    seen_ids: set[str] = {video_id}
    seen_norms: set[str] = {_normalize_title(title)}
    songs: list[dict] = []

    # ── 1. Last.fm → YouTube ──────────────────────────────────────────────────
    lastfm_pairs = await _lastfm_similar(
        track=song_name,
        artist=artist,
        api_key=_cfg.LASTFM_API_KEY or "",
        limit=20,                               # fetch more so filter has room
        fallback_api_key=_cfg.LASTFM_API_KEY_2 or "",
    )

    if lastfm_pairs:
        logger.info(
            "Last.fm returned %d similar tracks for %r by %r",
            len(lastfm_pairs), song_name, artist,
        )

        async def _resolve(track_name: str, artist_name: str) -> dict | None:
            query = f"{track_name} {artist_name} official audio"
            entry = await asyncio.to_thread(_ytdlp_search_one, query)
            if not entry:
                return None
            return _entry_to_song(entry, title, seen_ids, seen_norms)

        for i in range(0, len(lastfm_pairs), 6):
            batch = lastfm_pairs[i: i + 6]
            batch_results = await asyncio.gather(*[_resolve(t, a) for t, a in batch])
            for song in batch_results:
                if song and not _is_same_artist_song(song):
                    songs.append(song)
            if len(songs) >= 12:
                break

    # ── 2. Fallback: genre-aware yt-dlp queries ───────────────────────────────
    if not songs:
        logger.info("Last.fm gave no results for %r — falling back to yt-dlp", title)
        queries = _build_queries(title, channel)
        results = await asyncio.gather(
            *[asyncio.to_thread(_ytdlp_search, q, 15) for q in queries]
        )
        for entries in results:
            for entry in entries:
                if not entry:
                    continue
                song = _entry_to_song(entry, title, seen_ids, seen_norms)
                if song and not _is_same_artist_song(song):
                    songs.append(song)
                if len(songs) >= 12:
                    break
            if len(songs) >= 12:
                break

    songs = songs[:12]

    if not songs:
        logger.warning(
            "fetch_related_songs: no results for %r (video_id=%s)", title, video_id
        )
        return songs

    # ── 3. Persist to MongoDB ─────────────────────────────────────────────────
    await _mongo_set_rec(cache_key, songs)
    return songs


def cache_recommendations(chat_id: int, title: str, songs: list[dict]) -> None:
    """Store (or overwrite) recommendation cache for a chat."""
    _cache[chat_id] = {
        "title": title,
        "songs": songs,
        "page": 0,
    }


def get_cache(chat_id: int) -> dict | None:
    """Return the cached recommendation data for a chat, or None."""
    return _cache.get(chat_id)


def set_page(chat_id: int, page: int) -> None:
    """Update the current page for a chat's recommendation cache."""
    if chat_id in _cache:
        _cache[chat_id]["page"] = page


async def fetch_and_cache(
    chat_id: int, video_id: str, title: str, channel: str = ""
) -> None:
    """Fetch related songs and store them in the cache (for background tasks)."""
    key = (chat_id, video_id)
    current = _RECOMMEND_INFLIGHT.get(key)
    if current and not current.done():
        return

    task = asyncio.current_task()
    _RECOMMEND_INFLIGHT[key] = task
    try:
        # Recommendations are optional UI work.  Bound network/search
        # concurrency so a playlist or many active VCs cannot starve audio
        # handling and Telegram update processing.
        async with _RECOMMEND_SLOTS:
            songs = await fetch_related_songs(video_id, title, channel)
        cache_recommendations(chat_id, title, songs)
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.debug("Background recommendation fetch failed: %s", ex)
    finally:
        if _RECOMMEND_INFLIGHT.get(key) is task:
            _RECOMMEND_INFLIGHT.pop(key, None)


def build_recommend_keyboard(
    chat_id: int, page: int
) -> InlineKeyboardMarkup | None:
    """Build the inline keyboard for the given page of recommendations."""
    data = _cache.get(chat_id)
    if not data or not data["songs"]:
        return None

    songs = data["songs"]
    total_pages = (len(songs) + SONGS_PER_PAGE - 1) // SONGS_PER_PAGE
    page = max(0, min(page, total_pages - 1))

    start = page * SONGS_PER_PAGE
    page_songs = songs[start: start + SONGS_PER_PAGE]

    rows = []
    for i, song in enumerate(page_songs):
        idx = start + i
        btn_title = song["title"]
        if len(btn_title) > 28:
            btn_title = btn_title[:27] + "…"
        rows.append([
            InlineKeyboardButton(
                f"♪  {btn_title}",
                callback_data=f"rec_play_{idx}_{chat_id}",
                style=_STYLES[idx % len(_STYLES)],
            ),
        ])

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅  Back",
                callback_data=f"rec_prev_{chat_id}",
                style=enums.ButtonStyle.PRIMARY,
            )
        )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                "More Songs ?",
                callback_data=f"rec_next_{chat_id}",
                style=enums.ButtonStyle.SUCCESS,
            )
        )
    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(rows)


async def show_recommendations(app, chat_id: int) -> None:
    """Send the recommendation message to the chat if songs are cached."""
    data = _cache.get(chat_id)
    if not data or not data["songs"]:
        return

    data["page"] = 0
    keyboard = build_recommend_keyboard(chat_id, 0)
    if not keyboard:
        return

    text = (
        "🎵 <b>You May Like to Listen these tracks</b>\n\n"
        "Choose a song below & I'll play it in this voice chat."
    )
    # Use the first song's thumbnail as the message header image
    thumbnail = data["songs"][0].get("thumbnail") if data["songs"] else None

    try:
        if thumbnail:
            await app.send_photo(
                chat_id=chat_id,
                photo=thumbnail,
                caption=text,
                reply_markup=keyboard,
            )
        else:
            await app.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
            )
    except Exception:
        # Fallback to plain text if photo send fails
        try:
            await app.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.warning("show_recommendations failed for chat %s: %s", chat_id, e)
