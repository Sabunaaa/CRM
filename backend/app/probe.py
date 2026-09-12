import argparse
import json

from .config import get_settings
from .instagram import create_instagram_adapter


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate anonymous Instagram metric availability without storing data.")
    parser.add_argument("usernames", nargs="+", help="Public Instagram usernames")
    parser.add_argument("--reels", type=int, default=3, help="Maximum reels to inspect per profile")
    parser.add_argument("--adapter", choices=["scrapling", "instaloader"], default=get_settings().collector_adapter)
    args = parser.parse_args()
    settings = get_settings()
    adapter = create_instagram_adapter(args.adapter, settings.scrapling_timeout_ms, settings.scrapling_reel_delay_seconds)
    results = []
    for username in args.usernames:
        try:
            profile = adapter.fetch_profile(username.lstrip("@"), args.reels)
            results.append({
                "username": profile.username,
                "status": "available",
                "followers_available": profile.followers_count is not None,
                "reels_discovered": len(profile.reels),
                "reels": [{
                    "shortcode": reel.shortcode,
                    "views_available": reel.views_count is not None,
                    "likes_available": reel.likes_count is not None,
                    "comments_available": reel.comments_count is not None,
                    "caption_available": reel.caption is not None,
                } for reel in profile.reels],
            })
        except Exception as exc:
            results.append({"username": username, "status": "failed", "reason": str(exc)})
    adapter.close()
    print(json.dumps(results, indent=2))
    core_available = all(
        result.get("status") == "available"
        and result.get("followers_available") is True
        and result.get("reels_discovered", 0) > 0
        and all(reel.get("views_available") is True for reel in result.get("reels", []))
        for result in results
    )
    raise SystemExit(0 if core_available else 2)


if __name__ == "__main__":
    main()
