from __future__ import annotations

import re
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from itertools import islice
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlparse

import instaloader


USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
HASHTAG_RE = re.compile(r"(?<!\w)#([\w.]+)", re.UNICODE)
logger = logging.getLogger("instatrack.instagram")


class CollectionUnavailable(RuntimeError):
    pass


class CollectionThrottled(RuntimeError):
    pass


def _browser_error_detail(exc: Exception) -> str:
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    diagnostic_lines = [
        line
        for line in lines
        if "[err]" in line
        or "process did exit" in line.lower()
        or "executable doesn't exist" in line.lower()
        or "permission denied" in line.lower()
        or "no usable sandbox" in line.lower()
    ]
    detail = " | ".join(diagnostic_lines[-4:]) if diagnostic_lines else (lines[0] if lines else "")
    return detail[:800]


def normalize_instagram_profile_url(value: str) -> tuple[str, str]:
    raw = value.strip()
    if not raw:
        raise ValueError("Instagram profile link is required")
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = parsed.netloc.lower().split(":")[0]
    if host not in {"instagram.com", "www.instagram.com"}:
        raise ValueError("Use an instagram.com profile link")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1 or parts[0].lower() in {"p", "reel", "reels", "stories", "explore"}:
        raise ValueError("Use a profile link such as https://instagram.com/username")
    username = parts[0].lstrip("@").lower()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("The Instagram username is invalid")
    return username, f"https://www.instagram.com/{username}/"


@dataclass
class PublicReel:
    shortcode: str
    permalink: str
    caption: str | None
    hashtags: list[str]
    thumbnail_url: str | None
    published_at: datetime | None
    views_count: int | None
    likes_count: int | None
    comments_count: int | None
    views_source: str | None


@dataclass
class PublicProfile:
    username: str
    display_name: str | None
    biography: str | None
    profile_picture_url: str | None
    followers_count: int | None
    reels: list[PublicReel]


class AnonymousInstaloaderAdapter:
    """Best-effort adapter for information Instagram exposes without login."""

    def __init__(self) -> None:
        self.loader = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            quiet=True,
        )

    def fetch_profile(self, username: str, reel_limit: int = 30) -> PublicProfile:
        try:
            profile = instaloader.Profile.from_username(self.loader.context, username)
            reel_iter = profile.get_reels()
            posts = list(islice(reel_iter, reel_limit))
        except instaloader.exceptions.TooManyRequestsException as exc:
            raise CollectionThrottled("Instagram rate-limited anonymous collection") from exc
        except instaloader.exceptions.LoginRequiredException as exc:
            raise CollectionUnavailable("Instagram requires login for this profile") from exc
        except instaloader.exceptions.ProfileNotExistsException as exc:
            raise CollectionUnavailable("Profile is private, deleted, or unavailable") from exc
        except instaloader.exceptions.InstaloaderException as exc:
            raise CollectionUnavailable(f"Instagram public data unavailable: {type(exc).__name__}") from exc

        reels: list[PublicReel] = []
        for post in posts:
            caption = post.caption or None
            view_count = getattr(post, "video_view_count", None)
            reels.append(PublicReel(
                shortcode=post.shortcode,
                permalink=f"https://www.instagram.com/reel/{post.shortcode}/",
                caption=caption,
                hashtags=sorted(set(HASHTAG_RE.findall(caption or ""))),
                thumbnail_url=getattr(post, "url", None),
                published_at=getattr(post, "date_utc", None),
                views_count=view_count,
                likes_count=getattr(post, "likes", None),
                comments_count=getattr(post, "comments", None),
                views_source="instagram_video_view_count" if view_count is not None else None,
            ))
        return PublicProfile(
            username=username,
            display_name=getattr(profile, "full_name", None),
            biography=getattr(profile, "biography", None),
            profile_picture_url=str(getattr(profile, "profile_pic_url", "")) or None,
            followers_count=getattr(profile, "followers", None),
            reels=reels,
        )

    def close(self) -> None:
        return None


class _InstagramHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.links: list[str] = []
        self.scripts: list[str] = []
        self._script_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = values.get("property") or values.get("name")
            if key and values.get("content"):
                self.meta[key.lower()] = unescape(values["content"])
        elif tag.lower() == "a" and values.get("href"):
            self.links.append(values["href"])
        elif tag.lower() == "script":
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._script_parts is not None:
            value = "".join(self._script_parts).strip()
            if value:
                self.scripts.append(value)
            self._script_parts = None


def _compact_count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().upper().replace(" ", "")
    match = re.fullmatch(r"([0-9]+(?:[.,][0-9]+)*)([KMB])?", text)
    if not match:
        return None
    number, suffix = match.groups()
    if suffix:
        number = number.replace(",", ".") if "." not in number and number.count(",") == 1 else number.replace(",", "")
        multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
        try:
            return int(float(number) * multiplier)
        except ValueError:
            return None
    try:
        return int(number.replace(",", "").replace(".", ""))
    except ValueError:
        return None


def _nested(value: dict[str, Any], *path: str) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_count(value: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> tuple[int | None, str | None]:
    for path in paths:
        result = _compact_count(_nested(value, *path))
        if result is not None:
            return result, path[-1]
    return None, None


def _walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _json_documents(scripts: list[str], extra_documents: list[Any] | None = None) -> list[Any]:
    documents: list[Any] = list(extra_documents or [])
    for script in scripts:
        candidates = [script]
        if script.startswith("window.") and "=" in script:
            candidates.append(script.split("=", 1)[1].strip().rstrip(";"))
        for candidate in candidates:
            try:
                documents.append(json.loads(candidate))
                break
            except (json.JSONDecodeError, TypeError):
                continue
    return documents


def _profile_from_documents(username: str, documents: list[Any]) -> dict[str, Any]:
    best: tuple[int, dict[str, Any]] | None = None
    for document in documents:
        for item in _walk_json(document):
            item_username = str(item.get("username") or "").lower()
            if item_username != username.lower():
                continue
            score = sum(key in item for key in ("full_name", "biography", "profile_pic_url", "profile_pic_url_hd", "follower_count", "edge_followed_by"))
            if best is None or score > best[0]:
                best = (score, item)
    return best[1] if best else {}


def _caption_from_item(item: dict[str, Any]) -> str | None:
    caption = item.get("caption")
    if isinstance(caption, str):
        return caption.strip() or None
    if isinstance(caption, dict):
        text = caption.get("text")
        if isinstance(text, str):
            return text.strip() or None
    edges = _nested(item, "edge_media_to_caption", "edges")
    if isinstance(edges, list) and edges:
        text = _nested(edges[0], "node", "text")
        if isinstance(text, str):
            return text.strip() or None
    return None


def _datetime_from_item(item: dict[str, Any]) -> datetime | None:
    raw = item.get("taken_at_timestamp") or item.get("taken_at") or item.get("published_at")
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _thumbnail_from_item(item: dict[str, Any]) -> str | None:
    for key in ("display_url", "thumbnail_src", "thumbnail_url"):
        if isinstance(item.get(key), str):
            return item[key]
    candidates = _nested(item, "image_versions2", "candidates")
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        url = candidates[0].get("url")
        return url if isinstance(url, str) else None
    return None


def _reel_from_item(item: dict[str, Any]) -> PublicReel | None:
    shortcode = item.get("shortcode") or item.get("code")
    if not isinstance(shortcode, str) or not re.fullmatch(r"[A-Za-z0-9_-]{5,30}", shortcode):
        return None
    product_type = str(item.get("product_type") or item.get("__typename") or "").lower()
    reel_signals = ("clip", "reel", "graphvideo")
    has_metrics = any(key in item for key in ("play_count", "video_view_count", "video_play_count", "view_count"))
    if not has_metrics and not any(signal in product_type for signal in reel_signals):
        return None
    views, views_key = _first_count(item, (
        ("play_count",), ("video_play_count",), ("video_view_count",), ("view_count",), ("views_count",),
    ))
    likes, _ = _first_count(item, (
        ("like_count",), ("likes_count",), ("edge_media_preview_like", "count"), ("edge_liked_by", "count"),
    ))
    comments, _ = _first_count(item, (
        ("comment_count",), ("comments_count",), ("edge_media_to_comment", "count"), ("edge_media_to_parent_comment", "count"),
    ))
    caption = _caption_from_item(item)
    return PublicReel(
        shortcode=shortcode,
        permalink=f"https://www.instagram.com/reel/{shortcode}/",
        caption=caption,
        hashtags=sorted(set(HASHTAG_RE.findall(caption or ""))),
        thumbnail_url=_thumbnail_from_item(item),
        published_at=_datetime_from_item(item),
        views_count=views,
        likes_count=likes,
        comments_count=comments,
        views_source=f"instagram_{views_key}" if views_key else None,
    )


def _merge_reels(base: PublicReel, update: PublicReel) -> PublicReel:
    caption = update.caption or base.caption
    return PublicReel(
        shortcode=base.shortcode,
        permalink=base.permalink,
        caption=caption,
        hashtags=sorted(set(update.hashtags or base.hashtags)),
        thumbnail_url=update.thumbnail_url or base.thumbnail_url,
        published_at=update.published_at or base.published_at,
        views_count=update.views_count if update.views_count is not None else base.views_count,
        likes_count=update.likes_count if update.likes_count is not None else base.likes_count,
        comments_count=update.comments_count if update.comments_count is not None else base.comments_count,
        views_source=update.views_source or base.views_source,
    )


def parse_instagram_document(username: str, html: str, extra_documents: list[Any] | None = None) -> tuple[PublicProfile, list[str]]:
    parser = _InstagramHTMLParser()
    parser.feed(html)
    documents = _json_documents(parser.scripts, extra_documents)
    profile_item = _profile_from_documents(username, documents)
    followers, _ = _first_count(profile_item, (
        ("follower_count",), ("followers_count",), ("edge_followed_by", "count"), ("followers", "count"),
    ))
    description = parser.meta.get("description") or parser.meta.get("og:description") or ""
    if followers is None:
        match = re.search(r"([0-9][0-9.,]*\s*[KMB]?)\s+Followers", description, re.IGNORECASE)
        followers = _compact_count(match.group(1)) if match else None

    display_name = profile_item.get("full_name") if isinstance(profile_item.get("full_name"), str) else None
    if not display_name:
        title = parser.meta.get("og:title", "")
        match = re.match(r"(.+?)\s*\(@[^)]+\)", title)
        display_name = match.group(1).strip() if match else None
    biography = profile_item.get("biography") if isinstance(profile_item.get("biography"), str) else None
    picture = None
    for key in ("profile_pic_url_hd", "profile_pic_url"):
        if isinstance(profile_item.get(key), str):
            picture = profile_item[key]
            break
    picture = picture or parser.meta.get("og:image")

    reels: dict[str, PublicReel] = {}
    for document in documents:
        for item in _walk_json(document):
            reel = _reel_from_item(item)
            if reel:
                reels[reel.shortcode] = _merge_reels(reels[reel.shortcode], reel) if reel.shortcode in reels else reel
    ordered_codes: list[str] = []
    candidate_links = [*parser.links]
    if parser.meta.get("og:url"):
        candidate_links.append(parser.meta["og:url"])
    for link in candidate_links:
        match = re.search(r"/(?:[^/]+/)?reel/([A-Za-z0-9_-]{5,30})", link)
        if match and match.group(1) not in ordered_codes:
            ordered_codes.append(match.group(1))
            if match.group(1) not in reels:
                metric_match = re.search(
                    r"([0-9][0-9.,]*\s*[KMB]?)\s+likes?.*?([0-9][0-9.,]*\s*[KMB]?)\s+comments?",
                    description,
                    re.IGNORECASE,
                )
                view_match = re.search(r"([0-9][0-9.,]*\s*[KMB]?)\s+(?:views?|plays?)", description, re.IGNORECASE)
                caption_match = re.search(r"(?:Instagram:\s*)?[\"“](.+?)[\"”]\s*$", description)
                caption = caption_match.group(1).strip() if caption_match else None
                reels[match.group(1)] = PublicReel(
                    shortcode=match.group(1),
                    permalink=f"https://www.instagram.com/reel/{match.group(1)}/",
                    caption=caption,
                    hashtags=sorted(set(HASHTAG_RE.findall(caption or ""))),
                    thumbnail_url=parser.meta.get("og:image"),
                    published_at=None,
                    views_count=_compact_count(view_match.group(1)) if view_match else None,
                    likes_count=_compact_count(metric_match.group(1)) if metric_match else None,
                    comments_count=_compact_count(metric_match.group(2)) if metric_match else None,
                    views_source="instagram_public_description" if view_match else None,
                )
    for code in reels:
        if code not in ordered_codes:
            ordered_codes.append(code)
    ordered_reels = [reels.get(code) or PublicReel(code, f"https://www.instagram.com/reel/{code}/", None, [], None, None, None, None, None, None) for code in ordered_codes]
    return PublicProfile(
        username=username,
        display_name=display_name,
        biography=biography,
        profile_picture_url=picture,
        followers_count=followers,
        reels=ordered_reels,
    ), ordered_codes


class ScraplingInstagramAdapter:
    """Browser-backed anonymous collector for data Instagram makes public."""

    def __init__(self, timeout_ms: int = 45_000, reel_delay_seconds: float = 1.0) -> None:
        self.timeout_ms = timeout_ms
        self.reel_delay_seconds = reel_delay_seconds
        self._manager = None
        self._session = None
        self._browser_profile = None

    def _ensure_session(self):
        if self._session is not None:
            return self._session
        try:
            from scrapling.fetchers import StealthySession
        except ImportError as exc:
            raise CollectionUnavailable("Scrapling is not installed; install backend/requirements-collector.txt") from exc
        self._browser_profile = TemporaryDirectory(prefix="instatrack-scrapling-")
        try:
            self._manager = StealthySession(
                headless=True,
                block_webrtc=True,
                hide_canvas=True,
                block_ads=True,
                locale="en-US",
                timezone_id="Asia/Tbilisi",
                user_data_dir=self._browser_profile.name,
            )
            self._session = self._manager.__enter__()
        except Exception as exc:
            logger.exception("Scrapling browser startup failed")
            self._manager = None
            self._browser_profile.cleanup()
            self._browser_profile = None
            detail = _browser_error_detail(exc)
            suffix = f": {detail}" if detail else ""
            raise CollectionUnavailable(f"Scrapling browser could not start ({type(exc).__name__}){suffix}") from exc
        return self._session

    @staticmethod
    def _scroll_profile(page) -> None:
        for _ in range(7):
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(650)

    def _fetch(self, url: str, scroll: bool = False) -> tuple[str, list[Any]]:
        try:
            page = self._ensure_session().fetch(
                url,
                timeout=self.timeout_ms,
                network_idle=False,
                wait=1_500,
                disable_resources=False,
                page_action=self._scroll_profile if scroll else None,
                capture_xhr=r"(graphql|api/v1|clips|reels)",
                retries=1,
                retry_delay=2,
            )
        except CollectionUnavailable:
            raise
        except Exception as exc:
            message = str(exc).lower()
            if "429" in message or "too many requests" in message or "rate limit" in message:
                raise CollectionThrottled("Instagram rate-limited Scrapling collection") from exc
            raise CollectionUnavailable(f"Instagram browser collection failed: {type(exc).__name__}") from exc
        if page.status == 429:
            raise CollectionThrottled("Instagram rate-limited Scrapling collection")
        if page.status in {401, 403}:
            raise CollectionUnavailable(f"Instagram denied anonymous browser access (HTTP {page.status})")
        if page.status >= 400:
            raise CollectionUnavailable(f"Instagram public page returned HTTP {page.status}")
        html = page.body.decode(page.encoding or "utf-8", errors="replace")
        extra_documents: list[Any] = []
        for response in getattr(page, "captured_xhr", []):
            try:
                extra_documents.append(json.loads(response.body.decode(response.encoding or "utf-8", errors="replace")))
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                continue
        return html, extra_documents

    def fetch_profile(self, username: str, reel_limit: int = 30) -> PublicProfile:
        html, documents = self._fetch(f"https://www.instagram.com/{username}/reels/", scroll=True)
        profile, codes = parse_instagram_document(username, html, documents)
        lower_html = html.lower()
        if not codes and profile.followers_count is None:
            if "please wait a few minutes" in lower_html or "rate limit" in lower_html:
                raise CollectionThrottled("Instagram rate-limited Scrapling collection")
            if "accounts/login" in lower_html or "login • instagram" in lower_html or "challenge" in lower_html:
                raise CollectionUnavailable("Instagram requires login or presented a challenge")
            if "page isn't available" in lower_html or "page may have been removed" in lower_html:
                raise CollectionUnavailable("Profile is private, deleted, or unavailable")

        reels_by_code = {reel.shortcode: reel for reel in profile.reels}
        selected_codes = codes[:reel_limit]
        for index, code in enumerate(selected_codes):
            existing = reels_by_code[code]
            has_all_metrics = all(value is not None for value in (existing.views_count, existing.likes_count, existing.comments_count, existing.caption))
            if has_all_metrics:
                continue
            if index:
                time.sleep(self.reel_delay_seconds)
            reel_html, reel_documents = self._fetch(existing.permalink)
            reel_profile, _ = parse_instagram_document(username, reel_html, reel_documents)
            match = next((item for item in reel_profile.reels if item.shortcode == code), None)
            if match:
                reels_by_code[code] = _merge_reels(existing, match)
        profile.reels = [reels_by_code[code] for code in selected_codes]
        return profile

    def close(self) -> None:
        try:
            if self._manager is not None:
                self._manager.__exit__(None, None, None)
        finally:
            self._manager = None
            self._session = None
            if self._browser_profile is not None:
                self._browser_profile.cleanup()
                self._browser_profile = None


def create_instagram_adapter(name: str, timeout_ms: int = 45_000, reel_delay_seconds: float = 1.0):
    normalized = name.strip().lower()
    if normalized == "scrapling":
        return ScraplingInstagramAdapter(timeout_ms=timeout_ms, reel_delay_seconds=reel_delay_seconds)
    if normalized == "instaloader":
        return AnonymousInstaloaderAdapter()
    raise ValueError(f"Unknown collector adapter: {name}")
