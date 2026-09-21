from src.cardmarket.browser_client import is_blocked, make_match_key, pick_cardmarket_tab_index


def test_is_blocked_detects_cloudflare_1015():
    assert is_blocked("<html><body>Error 1015</body></html>")


def test_is_blocked_detects_challenge_title():
    assert is_blocked("<html></html>", title="Attention Required! | Cloudflare")


def test_is_blocked_false_on_normal_page():
    assert not is_blocked("<html><body>Your stock: 42 articles</body></html>", title="My Stock | Cardmarket")


def test_make_match_key_is_case_and_whitespace_insensitive():
    a = make_match_key(" Lightning Bolt ", "Alpha", "NM", "EN", False)
    b = make_match_key("lightning bolt", "alpha", "nm", "en", False)
    assert a == b


def test_make_match_key_distinguishes_foil():
    a = make_match_key("Lightning Bolt", "Alpha", "NM", "EN", False)
    b = make_match_key("Lightning Bolt", "Alpha", "NM", "EN", True)
    assert a != b


def test_pick_cardmarket_tab_index_finds_it():
    urls = ["https://mail.google.com/", "https://www.cardmarket.com/en/Magic/Stock", "https://example.com/"]
    assert pick_cardmarket_tab_index(urls) == 1


def test_pick_cardmarket_tab_index_none_when_absent():
    urls = ["https://mail.google.com/", "https://example.com/"]
    assert pick_cardmarket_tab_index(urls) is None
