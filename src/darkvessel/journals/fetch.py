"""HTTP helper that turns blocked hosts and bad responses into clear errors.

Some environments (for example the Claude cloud container) route all traffic
through an egress proxy that refuses most publisher hosts with HTTP 403 on the
CONNECT request. The requests library reports that as a ProxyError. This module
maps such failures to a FetchError with a short, actionable message instead of a
stack trace.

requests is imported lazily, so importing this module needs no network library.
"""

from __future__ import annotations

import html
import re
import time
import urllib.parse
from html.parser import HTMLParser
from typing import Protocol

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) darkvessel-journals/0.1"

KINDS = ("blocked", "http", "network", "parse", "dependency")


class FetchError(RuntimeError):
    """A download or parse step failed. kind is one of KINDS."""

    def __init__(self, kind: str, url: str, message: str) -> None:
        super().__init__(message)
        if kind not in KINDS:
            raise ValueError(f"unknown FetchError kind {kind!r}")
        self.kind = kind
        self.url = url
        self.message = message

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.url).hostname or ""

    def describe(self) -> str:
        """Multi-line text for the console."""
        lines = [f"{self.kind.upper()}: {self.message}", f"  url: {self.url}"]
        hint = HINTS.get(self.kind)
        if hint:
            lines.append(f"  next step: {hint}")
        return "\n".join(lines)


HINTS = {
    "blocked": (
        "this machine cannot reach the host (an egress proxy or firewall refuses it). Run the script "
        "where the host is reachable, or download the file in a browser and pass it with the matching "
        "--*-csv or --*-xlsx option."
    ),
    "http": "the server answered but refused or did not find the file. Download it in a browser and pass the local path.",
    "network": "check the network connection, the proxy settings (HTTPS_PROXY) and the CA bundle (REQUESTS_CA_BUNDLE).",
    "parse": "the download worked but the content was not what the script expects (for example a login page or a changed layout).",
    "dependency": "install the missing package into the active environment.",
}


def classify_exception(exc: BaseException, url: str) -> FetchError:
    """Map an exception raised by an HTTP client to a FetchError."""
    name = type(exc).__name__
    text = f"{name}: {exc}"
    lowered = text.lower()
    host = urllib.parse.urlsplit(url).hostname or url
    if (
        "tunnel connection failed" in lowered
        or "egress_blocked" in lowered
        or "connect tunnel failed" in lowered
        or ("proxyerror" in lowered and re.search(r"\b(403|407)\b", lowered))
    ):
        return FetchError(
            "blocked", url, f"{host} is blocked by the network proxy (HTTP 403 on the proxy CONNECT request)"
        )
    if "timeout" in lowered or "timed out" in lowered:
        return FetchError("network", url, f"timed out while contacting {host}")
    if "sslerror" in lowered or "certificate" in lowered:
        return FetchError("network", url, f"TLS verification failed for {host} ({exc})")
    return FetchError("network", url, f"could not reach {host}: {name}")


class Fetcher(Protocol):
    """Anything with get_bytes(url). Tests pass a fake; scripts use RequestsFetcher."""

    def get_bytes(self, url: str) -> bytes: ...


class RequestsFetcher:
    """Download with the requests library. Honours HTTPS_PROXY and REQUESTS_CA_BUNDLE."""

    def __init__(self, timeout: float = 60.0, attempts: int = 3, session: object | None = None) -> None:
        self.timeout = timeout
        self.attempts = max(1, attempts)
        self._session = session

    def _requests(self):
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - requests is a project dependency
            raise FetchError("dependency", "", "the requests package is not installed (pip install requests)") from exc
        return requests

    def get_bytes(self, url: str) -> bytes:
        requests = self._requests()
        session = self._session or requests.Session()
        last: FetchError | None = None
        for attempt in range(self.attempts):
            try:
                resp = session.get(url, timeout=self.timeout, headers={"User-Agent": USER_AGENT})
            except Exception as exc:  # noqa: BLE001 - classified below
                last = classify_exception(exc, url)
                if last.kind == "blocked":
                    raise last from exc  # retrying a policy block is pointless
                time.sleep(min(2.0 * (attempt + 1), 6.0))
                continue
            status = resp.status_code
            if status == 200:
                return resp.content
            if status in (500, 502, 503, 504) and attempt + 1 < self.attempts:
                time.sleep(min(2.0 * (attempt + 1), 6.0))
                continue
            if status in (401, 403):
                msg = f"HTTP {status}: the server refuses scripted downloads or needs a login"
            elif status == 404:
                msg = "HTTP 404: file not found (the link may have changed)"
            elif status == 429:
                msg = "HTTP 429: rate limited, try again later"
            else:
                msg = f"HTTP {status}"
            raise FetchError("http", url, msg)
        assert last is not None
        raise last


def decode_text(data: bytes) -> str:
    """Decode a download as UTF-8 (with or without BOM), falling back to Latin-1."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self._href = values["href"]
            self._text = []
        elif tag in ("iframe", "embed") and values.get("src"):
            self.links.append((values["src"] or "", ""))

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def find_links(page_html: str, base_url: str) -> list[tuple[str, str]]:
    """(absolute URL, anchor text) for every link, iframe and embed in an HTML page."""
    parser = _LinkParser()
    parser.feed(page_html)
    parser.close()
    out = []
    for href, text in parser.links:
        href = href.strip()
        if href and not href.startswith(("#", "javascript:", "mailto:")):
            out.append((urllib.parse.urljoin(base_url, href), text))
    return out


def find_discontinued_xlsx(page_html: str, base_url: str) -> list[str]:
    """URLs of .xlsx links whose address or anchor text mentions 'discontinued'."""
    hits = []
    for url, text in find_links(page_html, base_url):
        path = urllib.parse.urlsplit(url).path.lower()
        wanted = "discontinu" in url.lower() or "discontinu" in text.lower()
        if path.endswith((".xlsx", ".xlsm")) and wanted and url not in hits:
            hits.append(url)
    return hits


_SHEET_URL = re.compile(r"https://docs\.google\.com/spreadsheets/d/[^\s\"'<>\\]+")


def find_google_sheet_urls(page_html: str) -> list[str]:
    """Google Sheets URLs mentioned anywhere in a page (links, iframes and raw text)."""
    seen: list[str] = []
    for raw in _SHEET_URL.findall(page_html):
        url = html.unescape(raw).rstrip(".,;)")
        if url not in seen:
            seen.append(url)
    return seen


def sheet_csv_url(url: str) -> str | None:
    """CSV export address for a Google Sheets link, or None when the link is not a sheet."""
    parts = urllib.parse.urlsplit(url)
    if parts.hostname != "docs.google.com":
        return None
    query = urllib.parse.parse_qs(parts.query)
    gid = (query.get("gid") or [None])[0]
    if gid is None and parts.fragment.startswith("gid="):
        gid = parts.fragment.split("=", 1)[1]
    published = re.match(r"^/spreadsheets/d/e/([A-Za-z0-9_\-]+)/", parts.path)
    if published:
        out = f"https://docs.google.com/spreadsheets/d/e/{published.group(1)}/pub?output=csv"
        if gid:
            out += f"&gid={gid}&single=true"
        return out
    regular = re.match(r"^/spreadsheets/d/([A-Za-z0-9_\-]+)", parts.path)
    if regular:
        out = f"https://docs.google.com/spreadsheets/d/{regular.group(1)}/export?format=csv"
        if gid:
            out += f"&gid={gid}"
        return out
    return None
