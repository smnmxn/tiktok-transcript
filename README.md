# tiktok-extractor

Command-line tool that prints the transcript (and optionally metadata) of a
TikTok user's latest video — or all videos since a given date.

Pulls TikTok's native auto-generated captions directly from the public web
page (`__UNIVERSAL_DATA_FOR_REHYDRATION__` → `cla_info.caption_infos[].url`).
No authentication, no API key, no video download, no local speech-to-text.

## Requirements

- Python 3.10 or newer
- macOS, Linux, or Windows (tested on macOS)

## Install

Using [`uv`](https://docs.astral.sh/uv/) (recommended — gives you an isolated,
global `tiktok-transcript` binary):

```sh
uv tool install .
```

Or with pip (prefer a virtualenv):

```sh
pip install .
```

After installation, the `tiktok-transcript` command is on your `PATH`.

## Quick start

```sh
tiktok-transcript tiktok
```

Prints the description and auto-generated transcript of `@tiktok`'s latest
video to stdout.

## Usage

```
tiktok-transcript USERNAME [options]
```

The leading `@` is optional — `tiktok-transcript @tiktok` and
`tiktok-transcript tiktok` behave identically.

### Options

| Flag | Default | Description |
|---|---|---|
| `--since DATE` | — | Fetch all videos posted on or after `DATE`. Accepts `YYYY-MM-DD` or ISO-8601 datetime. Assumed UTC if no timezone. Triggers multi-video output. |
| `--max N` | `30` | With `--since`: cap on how many videos to examine before giving up. A warning is printed to stderr if the cap is hit. |
| `--lang CODE` | first available | Preferred caption language code (e.g. `eng-US`). |
| `--format {text,vtt,json}` | `text` | Output format. `vtt` emits raw WebVTT and is not compatible with `--since`. |
| `--output PATH` / `-o` | stdout | Write output to a file instead of stdout. |
| `--video-url` | off | Also print the source video URL(s) to stderr. |

### Examples

```sh
# Plain-text transcript of the latest video (description, then transcript)
tiktok-transcript tiktok

# Structured JSON with full metadata (stats, author, music, video specs, …)
tiktok-transcript tiktok --format json

# Raw WebVTT caption file
tiktok-transcript tiktok --format vtt

# All videos from the last 30 days as a JSON array
tiktok-transcript tiktok --since 2026-03-22 --format json

# All videos since a date, as concatenated text blocks with date/URL headers
tiktok-transcript tiktok --since 2026-03-22

# Go back further, raising the safety cap
tiktok-transcript tiktok --since 2025-01-01 --max 500

# Pick a specific caption language if available
tiktok-transcript tiktok --lang eng-US

# Save to a file
tiktok-transcript tiktok --format json -o tiktok-latest.json
```

### Composing with other tools

stdout contains only the transcript / JSON / VTT. Diagnostics go to stderr,
so piping is clean:

```sh
tiktok-transcript tiktok | wc -w
tiktok-transcript tiktok --format json | jq -r .transcript | pbcopy
tiktok-transcript tiktok --since 2026-04-01 --format json \
    | jq -r '.[] | "\(.create_time[:10])  \(.stats.plays) plays  \(.description)"'
```

## JSON schema

Single-video mode (`--format json` without `--since`) returns one object.
`--since` returns an array of objects with the same shape.

```jsonc
{
  "username": "tiktok",
  "video_id": "7628619815690833183",
  "video_url": "https://www.tiktok.com/@tiktok/video/...",
  "description": "a reminder to romanticize the little moments ❤️",
  "create_time": "2026-04-14T14:21:33+00:00",
  "create_time_unix": 1776176493,
  "language": "en",
  "location": "US",
  "hashtags": [],
  "mentions": [],
  "is_ai_generated": false,
  "author": {
    "unique_id": "tiktok",
    "nickname": "TikTok",
    "id": "107955",
    "sec_uid": "MS4wLj...",
    "verified": true,
    "signature": "One TikTok can make a big impact"
  },
  "author_stats": {
    "followers": 93800000,
    "following": 3,
    "likes": 457300000,
    "videos": 1422
  },
  "stats": {
    "plays": 289500, "likes": 6674, "comments": 2188,
    "shares": 714, "bookmarks": 699, "reposts": 0
  },
  "music": {
    "title": "original sound", "author": "TikTok",
    "original": true, "duration_seconds": 48, "id": "7628..."
  },
  "video": {
    "duration_seconds": 48, "width": 720, "height": 1280,
    "format": "mp4", "codec": "h264",
    "cover_url": "https://...", "download_url": "https://..."
  },
  "transcript_lang": "eng-US",
  "transcript": "I would love to hear all of the like\nlittle ways that..."
}
```

When a video has no captions, `transcript` and `transcript_lang` are `null`;
all other fields are still populated.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Fetch or parse error (unknown user, TikTok blocked us, schema changed) |
| `2` | Latest video has no native captions (single-video mode), or invalid flag combination (e.g. `--since --format vtt`) |

## How it works

1. `yt-dlp` enumerates the user's most-recent post(s) via TikTok's internal
   signed feed API. We only use it to discover video IDs and URLs.
2. For each video, we fetch the public video page with
   [`curl_cffi`](https://github.com/lexiforest/curl_cffi), which impersonates
   a real Chrome's TLS/HTTP2 fingerprint — TikTok's WAF rejects bare
   `requests` with a "please wait…" stub.
3. The page embeds a JSON blob in a `<script
   id="__UNIVERSAL_DATA_FOR_REHYDRATION__">` tag; we parse out the
   `cla_info.caption_infos[].url` field, which points to a direct WebVTT file.
4. The VTT is downloaded and converted to plain text (or kept raw with
   `--format vtt`).

## Limitations

- **Schema drift:** depends on TikTok's web JSON structure. If TikTok renames
  the caption fields or the WAF starts challenging `curl_cffi`'s latest
  Chrome profile, the tool will break until updated.
- **Caption quality:** transcripts are TikTok's machine-generated captions
  — generally good but not perfect, especially on music-heavy or multi-speaker
  content.
- **Missing captions:** a small fraction of videos (~5–10%) have no caption
  track (music-only, very old, creator opted out). Single-video mode exits
  `2`; multi-video mode includes them with `transcript: null`. An
  audio-transcription fallback via Whisper is planned
  (`pip install tiktok-extractor[whisper]`) but not yet implemented.
- **Rate limiting:** TikTok's WAF will throttle rapid bursts of video-page
  requests. Single-video mode makes 2–3 HTTP calls and is fine for casual
  use. `--since` over a long window makes one request per video — be nice.

## Troubleshooting

**`error: Could not find __UNIVERSAL_DATA_FOR_REHYDRATION__ in page`**  
TikTok's WAF challenged the request, usually because your `curl_cffi`
Chrome-fingerprint profile has aged. Upgrade: `uv tool upgrade
tiktok-extractor`, or `pip install -U curl_cffi` inside the venv.

**`error: User @X not found`**  
The handle doesn't exist, is suspended, or was misspelled. Double-check on
tiktok.com.

**Hit the `--max` limit without finding a cutoff video**  
Re-run with a larger `--max` (e.g. `--max 500`). The warning is advisory;
the results already collected are still emitted.

## License

MIT. See `LICENSE` if present; otherwise assume MIT unless you hear otherwise.
