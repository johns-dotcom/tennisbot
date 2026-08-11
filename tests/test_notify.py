import bot.notify as N


def test_notify_disabled_without_webhook(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert N.discord_enabled() is False
    # no webhook -> no-op, returns False, never raises
    assert N.notify("pushed", "title", "msg") is False
    assert N.notify_signal(match="A vs B", pick="A", confidence="70%",
                           analysis="x") is False


def _capture(monkeypatch):
    """Capture the payload list dispatched on the (single) worker thread."""
    captured = {}

    class _FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._target = target

        def start(self):
            # run the dispatch body synchronously so we see every _post payload
            self._target()

    posts = []
    monkeypatch.setattr(N.threading, "Thread", _FakeThread)
    monkeypatch.setattr(N, "_post",
                        lambda hook, payload, kind: posts.append((payload, kind)))
    captured["posts"] = posts
    return captured


def test_signal_sends_ping_then_analysis(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    cap = _capture(monkeypatch)
    ok = N.notify_signal(
        match="Price vs Malkin", pick="Misa Malkin", confidence="68%",
        analysis="Trigger fired: takes set 1 (1-0), priced 52¢. Malkin is the play.",
        fields=[("State", "1-0"), ("Price", "52¢")],
        link="https://example/match")
    assert ok is True
    posts = cap["posts"]
    assert len(posts) == 2, "one ping message + one analysis message"

    ping, _ = posts[0]
    detail, _ = posts[1]

    # MESSAGE 1: pings @everyone, carries ONLY pick + confidence, no analysis
    assert ping["content"].startswith("@everyone")
    assert "Model's pick: **Misa Malkin**" in ping["content"]
    assert "Model's confidence: **68%**" in ping["content"]
    assert ping["allowed_mentions"] == {"parse": ["everyone"]}
    assert "Trigger fired" not in ping["content"]  # analysis stays out of the ping
    assert "embeds" not in ping                     # ping is bare, phone-friendly

    # MESSAGE 2: the analysis, and it must NOT ping anyone
    assert detail["allowed_mentions"] == {"parse": []}
    assert "@everyone" not in detail["content"]
    assert "Trigger fired" in detail["content"] and "the play" in detail["content"]
    embed = detail["embeds"][0]
    assert embed["title"] == "Analysis · Price vs Malkin"
    assert {f["name"] for f in embed["fields"]} == {"State", "Price"}
    assert embed["url"] == "https://example/match"


def test_signal_order_ping_before_analysis(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    cap = _capture(monkeypatch)
    N.notify_signal(match="A vs B", pick="A", confidence="70%", analysis="because")
    # the @everyone ping is dispatched first, the analysis second
    assert cap["posts"][0][0]["content"].startswith("@everyone")
    assert cap["posts"][1][0]["allowed_mentions"] == {"parse": []}


def test_plain_notify_no_everyone(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x")
    cap = _capture(monkeypatch)
    N.notify("armed", "◉ title", "body", fields=[("State", "1-1")])
    payload, kind = cap["posts"][0]
    assert kind == "armed"
    assert "@everyone" not in payload["content"]
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["embeds"][0]["title"] == "◉ title"
