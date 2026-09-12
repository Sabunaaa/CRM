import pytest

from app.instagram import create_instagram_adapter, normalize_instagram_profile_url, parse_instagram_document


@pytest.mark.parametrize("value", ["instagram.com/Creator.Name", "https://www.instagram.com/creator.name/", "https://instagram.com/creator.name?hl=en"])
def test_normalizes_profile_links(value):
    username, url = normalize_instagram_profile_url(value)
    assert username == "creator.name"
    assert url == "https://www.instagram.com/creator.name/"


@pytest.mark.parametrize("value", ["", "https://example.com/name", "https://instagram.com/reel/abc", "https://instagram.com/a/b"])
def test_rejects_non_profile_links(value):
    with pytest.raises(ValueError):
        normalize_instagram_profile_url(value)


def test_scrapling_document_parser_reads_public_profile_and_reel_metrics():
    html = """
    <html><head>
      <meta property="og:title" content="Example Creator (@creator) • Instagram photos and videos">
      <meta property="og:description" content="1.2M Followers, 80 Following, 42 Posts">
      <meta property="og:image" content="https://cdn.example/avatar.jpg">
    </head><body>
      <a href="/creator/reel/ABC_def123/">Latest reel</a>
      <script type="application/json">{
        "user": {"username": "creator", "full_name": "Example Creator", "biography": "A public bio", "follower_count": 1200123},
        "items": [{"code": "ABC_def123", "product_type": "clips", "play_count": 45000, "like_count": 2100, "comment_count": 98, "caption": {"text": "Hello #Tbilisi"}, "taken_at": 1789257600}]
      }</script>
    </body></html>
    """

    profile, codes = parse_instagram_document("creator", html)

    assert profile.followers_count == 1_200_123
    assert profile.display_name == "Example Creator"
    assert codes == ["ABC_def123"]
    assert profile.reels[0].views_count == 45_000
    assert profile.reels[0].likes_count == 2_100
    assert profile.reels[0].comments_count == 98
    assert profile.reels[0].hashtags == ["Tbilisi"]
    assert profile.reels[0].views_source == "instagram_play_count"


def test_scrapling_document_parser_uses_public_metadata_fallbacks():
    html = """
    <html><head>
      <meta property="og:title" content="Creator (@creator) • Instagram">
      <meta property="og:description" content='12.5K Followers, 1,000 Following, 20 Posts - 4.2K likes, 87 comments - Creator on Instagram: "Caption #cars"'>
      <meta property="og:url" content="https://www.instagram.com/reel/SHORT123/">
      <meta property="og:image" content="https://cdn.example/reel.jpg">
    </head></html>
    """

    profile, codes = parse_instagram_document("creator", html)

    assert profile.followers_count == 12_500
    assert codes == ["SHORT123"]
    assert profile.reels[0].likes_count == 4_200
    assert profile.reels[0].comments_count == 87
    assert profile.reels[0].caption == "Caption #cars"


def test_collector_adapter_defaults_are_selectable():
    assert type(create_instagram_adapter("scrapling")).__name__ == "ScraplingInstagramAdapter"
    assert type(create_instagram_adapter("instaloader")).__name__ == "AnonymousInstaloaderAdapter"
    with pytest.raises(ValueError):
        create_instagram_adapter("unknown")
