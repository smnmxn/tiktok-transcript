"""TikTok web JSON extraction: profile → latest video → caption URL."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from curl_cffi import requests as cffi_requests

_UNIVERSAL_DATA_RE = re.compile(
    r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.tiktok.com/",
}

_IMPERSONATE = "chrome"


class TikTokError(Exception):
    """Base exception for this module."""


class UserNotFound(TikTokError):
    pass


class NoVideosFound(TikTokError):
    pass


class BlockedByTikTok(TikTokError):
    pass


class _SilentLogger:
    """Swallow yt-dlp's own log output so we can present our own errors."""

    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


def _fetch_universal_data(url: str) -> dict[str, Any]:
    resp = cffi_requests.get(
        url,
        impersonate=_IMPERSONATE,
        headers=_HEADERS,
        timeout=20,
    )
    if resp.status_code == 404:
        raise UserNotFound(f"TikTok returned 404 for {url}")
    if resp.status_code in (403, 429):
        raise BlockedByTikTok(
            f"TikTok returned {resp.status_code} — fingerprint blocked or rate limited"
        )
    if resp.status_code != 200:
        raise TikTokError(f"Unexpected status {resp.status_code} from {url}")

    match = _UNIVERSAL_DATA_RE.search(resp.text)
    if not match:
        body_lower = resp.text.lower()
        if "captcha" in body_lower or "verify you are a human" in body_lower:
            raise BlockedByTikTok("TikTok returned a captcha/verify page")
        raise TikTokError(
            "Could not find __UNIVERSAL_DATA_FOR_REHYDRATION__ in page — "
            "TikTok's page structure may have changed"
        )
    return json.loads(match.group(1))


def _scope(data: dict[str, Any]) -> dict[str, Any]:
    return data.get("__DEFAULT_SCOPE__", {})


def list_user_videos(username: str, max_count: int = 1) -> list[tuple[str, str]]:
    """Return (video_id, video_url) pairs for a user's most recent posts.

    Reverse-chronological order, newest first. Uses yt-dlp to enumerate
    TikTok's signed feed API.
    """
    username = username.lstrip("@").strip()
    if not username:
        raise ValueError("Empty username")

    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    profile_url = f"https://www.tiktok.com/@{username}"
    ydl_opts = {
        "extract_flat": True,
        "playlistend": max_count,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": False,
        "logger": _SilentLogger(),
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(profile_url, download=False)
    except DownloadError as e:
        msg = str(e).lower()
        if (
            "unable to find user" in msg
            or "user not found" in msg
            or "secondary user id" in msg
            or "404" in msg
        ):
            raise UserNotFound(f"User @{username} not found") from e
        raise TikTokError(f"yt-dlp failed to list videos for @{username}: {e}") from e

    entries = [e for e in ((info or {}).get("entries") or []) if e]
    if not entries:
        raise NoVideosFound(f"No videos found for @{username}")

    out: list[tuple[str, str]] = []
    for entry in entries:
        vid = str(entry.get("id") or "")
        if not vid:
            continue
        url = (
            entry.get("url")
            or entry.get("webpage_url")
            or f"https://www.tiktok.com/@{username}/video/{vid}"
        )
        out.append((vid, url))
    if not out:
        raise TikTokError("yt-dlp returned entries with no usable ids")
    return out


def get_latest_video_id(username: str) -> tuple[str, str]:
    """Return (video_id, canonical_video_url) for the user's most recent post."""
    return list_user_videos(username, max_count=1)[0]


def fetch_video_item(video_url: str) -> dict[str, Any]:
    """Fetch a video page and return its `itemStruct` dict."""
    data = _fetch_universal_data(video_url)
    scope = _scope(data)
    detail = scope.get("webapp.video-detail", {})
    item = (
        detail.get("itemInfo", {}).get("itemStruct")
        or detail.get("itemStruct")
    )
    if not item:
        raise TikTokError("Could not find video details in page JSON")
    return item


def extract_caption_url(
    item: dict[str, Any],
    prefer_langs: Iterable[str] | None = None,
) -> tuple[str, str] | None:
    """Return (caption_url, lang) or None if the video has no captions."""
    video = item.get("video", {})
    cla_info = video.get("claInfo") or video.get("cla_info") or {}
    caption_infos = (
        cla_info.get("captionInfos")
        or cla_info.get("caption_infos")
        or []
    )
    if not caption_infos:
        return None

    prefer = list(prefer_langs) if prefer_langs else []
    if prefer:
        for info in caption_infos:
            lang = info.get("lang") or info.get("languageCode") or ""
            if lang in prefer:
                url = info.get("url") or info.get("subtitleUri")
                if url:
                    return url, lang

    for info in caption_infos:
        url = info.get("url") or info.get("subtitleUri")
        if url:
            lang = info.get("lang") or info.get("languageCode") or ""
            return url, lang

    return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Return a flat, opinionated subset of video metadata."""
    from datetime import datetime, timezone

    author = item.get("author") or {}
    astats = item.get("authorStatsV2") or item.get("authorStats") or {}
    stats = item.get("statsV2") or item.get("stats") or {}
    music = item.get("music") or {}
    video = item.get("video") or {}

    text_extras = item.get("textExtra") or []
    hashtags = sorted({
        e.get("hashtagName")
        for e in text_extras
        if e.get("hashtagName")
    })
    mentions = sorted({
        e.get("userUniqueId")
        for e in text_extras
        if e.get("userUniqueId")
    })

    create_time = _int(item.get("createTime"))
    create_time_iso = (
        datetime.fromtimestamp(create_time, tz=timezone.utc).isoformat()
        if create_time
        else None
    )

    return {
        "description": item.get("desc"),
        "create_time": create_time_iso,
        "create_time_unix": create_time,
        "language": item.get("textLanguage"),
        "location": item.get("locationCreated"),
        "hashtags": list(hashtags),
        "mentions": list(mentions),
        "is_ai_generated": item.get("IsAigc") in (True, "true", "True"),
        "author": {
            "unique_id": author.get("uniqueId"),
            "nickname": author.get("nickname"),
            "id": author.get("id"),
            "sec_uid": author.get("secUid"),
            "verified": bool(author.get("verified")),
            "signature": author.get("signature"),
        },
        "author_stats": {
            "followers": _int(astats.get("followerCount")),
            "following": _int(astats.get("followingCount")),
            "likes": _int(astats.get("heartCount")),
            "videos": _int(astats.get("videoCount")),
        },
        "stats": {
            "plays": _int(stats.get("playCount")),
            "likes": _int(stats.get("diggCount")),
            "comments": _int(stats.get("commentCount")),
            "shares": _int(stats.get("shareCount")),
            "bookmarks": _int(stats.get("collectCount")),
            "reposts": _int(stats.get("repostCount")),
        },
        "music": {
            "title": music.get("title"),
            "author": music.get("authorName"),
            "original": bool(music.get("original")),
            "duration_seconds": _int(music.get("duration")),
            "id": music.get("id"),
        },
        "video": {
            "duration_seconds": _int(video.get("duration")),
            "width": _int(video.get("width")),
            "height": _int(video.get("height")),
            "format": video.get("format"),
            "codec": video.get("codecType"),
            "cover_url": video.get("cover"),
            "download_url": video.get("downloadAddr"),
        },
    }


def fetch_vtt(caption_url: str) -> str:
    resp = cffi_requests.get(
        caption_url,
        impersonate=_IMPERSONATE,
        headers={"Accept-Language": "en-US,en;q=0.9"},
        timeout=20,
    )
    if resp.status_code != 200:
        raise TikTokError(
            f"Caption fetch returned {resp.status_code} (URL may have expired)"
        )
    return resp.text
