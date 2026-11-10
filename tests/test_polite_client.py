import pytest

from pyhuurwoningen._http import PoliteClient, RobotsDisallowed


class Resp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, robots="User-agent: *\nDisallow: /contact/\n", pages=None):
        self.headers, self.robots, self.pages, self.requested = {}, robots, pages or {}, []

    def get(self, url, params=None, timeout=None):
        self.requested.append(url)
        if url.endswith("/robots.txt"):
            return Resp(text=self.robots)
        queue = self.pages.get(url)
        return queue.pop(0) if queue else Resp(text="ok")


def make(session, **kw):
    sleeps = []
    client = PoliteClient("https://example.nl", "test-agent", session=session, sleep=sleeps.append, **kw)
    return client, sleeps


def test_respects_robots():
    client, _ = make(FakeSession(), respect_robots=True)
    assert client.get("/in/rotterdam/").text == "ok"
    with pytest.raises(RobotsDisallowed):
        client.get("/contact/form")


def test_no_wait_between_requests():
    client, sleeps = make(FakeSession())
    client.get("/a")
    client.get("/b")
    assert sleeps == []


def test_retries_429_with_retry_after():
    url = "https://example.nl/busy"
    session = FakeSession(pages={url: [Resp(429, headers={"Retry-After": "30"}), Resp(text="done")]})
    client, sleeps = make(session)
    assert client.get("/busy").text == "done"
    assert 30 in sleeps


def test_refuses_other_hosts():
    client, _ = make(FakeSession())
    with pytest.raises(ValueError):
        client.get("https://other.nl/x")


def test_errors_are_readable():
    from pyhuurwoningen import HuurwoningenError

    url = "https://example.nl/gone"
    client, _ = make(FakeSession(pages={url: [Resp(404)]}))
    with pytest.raises(HuurwoningenError) as err:
        client.get("/gone")
    assert err.value.status == 404


def test_bot_check_gets_a_clear_message():
    from pyhuurwoningen import HuurwoningenError

    url = "https://example.nl/blocked"
    client, _ = make(FakeSession(pages={url: [Resp(403, headers={"cf-mitigated": "challenge"})]}))
    with pytest.raises(HuurwoningenError, match="bot check"):
        client.get("/blocked")


def test_impersonation_keeps_browser_user_agent():
    # Sending our own UA next to a Chrome TLS fingerprint is a mismatch bot checks flag.
    client, _ = make(FakeSession())
    assert client.impersonate == "chrome"
    assert "User-Agent" not in client.session.headers


def test_plain_mode_sends_honest_user_agent():
    client, _ = make(FakeSession(), impersonate=None)
    assert client.session.headers["User-Agent"] == "test-agent"


def test_failed_robots_fetch_is_retried():
    from pyhuurwoningen import HuurwoningenError

    class FlakySession(FakeSession):
        fail_once = True

        def get(self, url, params=None, timeout=None):
            if url.endswith("/robots.txt") and self.fail_once:
                self.fail_once = False
                raise RequestException("timed out")
            return super().get(url, params, timeout)

    from curl_cffi.requests.exceptions import RequestException

    session = FlakySession()
    client, _ = make(session, respect_robots=True)
    with pytest.raises(HuurwoningenError):
        client.get("/in/rotterdam/")
    assert client.get("/in/rotterdam/").text == "ok"  # robots.txt re-fetched, not stuck as "disallowed"


def test_retry_after_zero_is_honoured():
    url = "https://example.nl/busy"
    session = FakeSession(pages={url: [Resp(503, headers={"Retry-After": "0"}), Resp(text="done")]})
    client, sleeps = make(session)
    assert client.get("/busy").text == "done"
    assert 0 in sleeps  # not replaced by the 10s default backoff


def test_long_retry_after_fails_fast():
    from pyhuurwoningen import HuurwoningenError

    url = "https://example.nl/busy"
    session = FakeSession(pages={url: [Resp(429, headers={"Retry-After": "3600"})]})
    client, sleeps = make(session)
    with pytest.raises(HuurwoningenError, match="wait 60 minutes") as err:
        client.get("/busy")
    assert err.value.status == 429
    assert all(s < 3600 for s in sleeps)


def test_retry_after_http_date():
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone

    when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    url = "https://example.nl/busy"
    session = FakeSession(pages={url: [Resp(429, headers={"Retry-After": when}), Resp(text="done")]})
    client, sleeps = make(session)
    assert client.get("/busy").text == "done"
    assert any(25 <= s <= 30 for s in sleeps)
