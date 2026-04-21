"""Command-line entry point for tiktok-transcript."""

from __future__ import annotations

import argparse
import json as _json
import sys
from datetime import datetime, timezone

from .tiktok import (
    BlockedByTikTok,
    NoVideosFound,
    TikTokError,
    UserNotFound,
    extract_caption_url,
    extract_metadata,
    fetch_video_item,
    fetch_vtt,
    get_latest_video_id,
    list_user_videos,
)
from .vtt import vtt_to_text


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tiktok-transcript",
        description="Print the transcript of a TikTok user's latest video, "
        "or all videos since a given date.",
    )
    parser.add_argument(
        "username",
        help="TikTok username (with or without leading @)",
    )
    parser.add_argument(
        "--since",
        help="Only include videos posted on or after this date "
        "(YYYY-MM-DD or ISO-8601 datetime, assumed UTC if no tz). "
        "Triggers multi-video output.",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=30,
        help="With --since: safety cap on videos examined (default: 30).",
    )
    parser.add_argument(
        "--lang",
        help="Preferred caption language code (e.g. eng-US). Default: first available.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "vtt", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Write output to this file instead of stdout",
    )
    parser.add_argument(
        "--video-url",
        action="store_true",
        help="Also print source video URLs to stderr",
    )
    return parser


def _parse_since(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as e:
        raise SystemExit(
            f"error: --since value '{value}' is not a valid ISO-8601 date/datetime"
        ) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _process_video(
    username: str,
    video_id: str,
    video_url: str,
    prefer_langs: list[str] | None,
) -> dict:
    """Fetch a video page, pull metadata, and transcribe captions if available.

    The returned dict carries metadata + parsed `transcript` + raw `_vtt`
    (hidden from JSON output). `transcript` and `_vtt` are None when no
    captions are available.
    """
    item = fetch_video_item(video_url)
    caption_result = extract_caption_url(item, prefer_langs=prefer_langs)
    transcript = None
    transcript_lang = None
    raw_vtt = None
    if caption_result is not None:
        caption_url, transcript_lang = caption_result
        raw_vtt = fetch_vtt(caption_url)
        transcript = vtt_to_text(raw_vtt)
    return {
        "username": username,
        "video_id": video_id,
        "video_url": video_url,
        **extract_metadata(item),
        "transcript_lang": transcript_lang,
        "transcript": transcript,
        "_vtt": raw_vtt,
    }


def _strip_internal(result: dict) -> dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def _format_single(result: dict, fmt: str) -> str:
    if fmt == "json":
        return _json.dumps(_strip_internal(result), ensure_ascii=False, indent=2)
    description = (result.get("description") or "").strip()
    transcript = result.get("transcript") or "[no captions available]"
    return f"{description}\n\n{transcript}" if description else transcript


def _format_multi(results: list[dict], fmt: str) -> str:
    if fmt == "json":
        return _json.dumps(
            [_strip_internal(r) for r in results], ensure_ascii=False, indent=2
        )
    blocks = []
    for r in results:
        date = (r.get("create_time") or "")[:10]
        header = f"=== {date} · {r['video_url']} ==="
        description = (r.get("description") or "").strip()
        transcript = r.get("transcript") or "[no captions available]"
        body = f"{description}\n\n{transcript}" if description else transcript
        blocks.append(f"{header}\n{body}")
    return "\n\n".join(blocks)


def _write(output: str, path: str | None) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(output)
            if not output.endswith("\n"):
                f.write("\n")
    else:
        print(output)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    username = args.username.lstrip("@")
    prefer_langs = [args.lang] if args.lang else None

    if args.since:
        if args.format == "vtt":
            print(
                "error: --since is incompatible with --format vtt "
                "(multiple VTT files cannot be safely concatenated)",
                file=sys.stderr,
            )
            return 2
        since_dt = _parse_since(args.since)

        try:
            videos = list_user_videos(username, max_count=args.max)
        except (UserNotFound, NoVideosFound, BlockedByTikTok, TikTokError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

        results: list[dict] = []
        stopped_by_date = False
        for vid, url in videos:
            if args.video_url:
                print(f"video: {url}", file=sys.stderr)
            try:
                result = _process_video(username, vid, url, prefer_langs)
            except (BlockedByTikTok, TikTokError) as e:
                print(f"error processing {url}: {e}", file=sys.stderr)
                return 1
            ct_unix = result.get("create_time_unix")
            if ct_unix:
                video_dt = datetime.fromtimestamp(ct_unix, tz=timezone.utc)
                if video_dt < since_dt:
                    stopped_by_date = True
                    break
            results.append(result)

        if not stopped_by_date and len(videos) >= args.max:
            print(
                f"warning: hit --max limit of {args.max}; "
                "older videos may exist. Increase --max to examine further.",
                file=sys.stderr,
            )

        output = _format_multi(results, args.format)
        _write(output, args.output)
        return 0

    # single-video mode
    try:
        video_id, video_url = get_latest_video_id(username)
    except (UserNotFound, NoVideosFound, BlockedByTikTok, TikTokError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.video_url:
        print(f"video: {video_url}", file=sys.stderr)

    try:
        result = _process_video(username, video_id, video_url, prefer_langs)
    except (BlockedByTikTok, TikTokError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if result["transcript"] is None:
        if args.format == "vtt":
            print(
                f"error: no native captions available for {video_url}",
                file=sys.stderr,
            )
            return 2
        _write(_format_single(result, args.format), args.output)
        return 2 if args.format == "text" else 0

    if args.format == "vtt":
        _write(result["_vtt"], args.output)
        return 0

    _write(_format_single(result, args.format), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
