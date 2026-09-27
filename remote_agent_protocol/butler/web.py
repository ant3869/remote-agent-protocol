"""Quick web lookups for the Butler: search, and read one page.

Search goes to whichever provider is configured -- Tavily, Brave Search, or a
self-hosted SearXNG. ``read_page`` fetches one public http(s) page and returns
its text. It refuses any address on this machine or the local network, checking
each redirect as well, so a link can't make RAP probe the user's own network.

Everything returned here is untrusted text from the internet. The Butler loop
treats it that way: once a web tool has run in a turn, only read-only tools
remain available for the rest of that turn.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import aiohttp

_MAX_PAGE_BYTES = 2_000_000
_MAX_REDIRECTS = 3
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "head"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "blockquote", "pre", "table",
}  # fmt: skip


class WebLookupError(RuntimeError):
    """A search or page read that could not be done, in words the user can hear."""


@dataclass(frozen=True)
class SearchResult:
    """One search hit."""

    title: str
    url: str
    snippet: str


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in joined.split("\n"))
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> tuple[str, str]:
    """Return ``(title, readable text)`` for an HTML document."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.title.strip(), parser.text()


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def ensure_public_url(url: str) -> str:
    """Return ``url`` if it is http(s) to a public address; raise otherwise."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebLookupError("Only http and https links can be read.")
    host = parts.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise WebLookupError(f"{host} could not be found.") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses or not all(_is_public(address) for address in addresses):
        raise WebLookupError(f"{host} is not a public internet address, so it won't be read.")
    return url


class WebLookup:
    """Search the web and read pages over the session's HTTP client."""

    def __init__(
        self,
        http: Callable[[], aiohttp.ClientSession | None],
        *,
        provider: str,
        api_key: str = "",
        searxng_url: str = "",
        timeout_secs: float = 10.0,
        max_page_chars: int = 6000,
    ):
        """Configure the lookup.

        Args:
            http: The session's shared HTTP client (None before it starts).
            provider: "tavily", "brave", "searxng", or "" for reading pages only.
            api_key: The Tavily or Brave key.
            searxng_url: Base URL of a SearXNG instance with JSON output enabled.
            timeout_secs: Per-request timeout.
            max_page_chars: How much of a page's text read_page returns.
        """
        self._http = http
        self.provider = provider
        self._api_key = api_key
        self._searxng_url = searxng_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_secs)
        self._max_page_chars = max_page_chars

    @property
    def can_search(self) -> bool:
        """Whether a search provider is configured."""
        if self.provider == "searxng":
            return bool(self._searxng_url)
        return self.provider in ("tavily", "brave") and bool(self._api_key)

    def _client(self) -> aiohttp.ClientSession:
        client = self._http()
        if client is None:
            raise WebLookupError("The web client isn't running yet.")
        return client

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        """Top results for ``query`` from the configured provider."""
        if not self.can_search:
            raise WebLookupError("No web search provider is configured.")
        client = self._client()
        try:
            if self.provider == "tavily":
                async with client.post(
                    "https://api.tavily.com/search",
                    json={"query": query, "max_results": limit},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=self._timeout,
                ) as resp:
                    resp.raise_for_status()
                    rows = (await resp.json()).get("results", [])
                return [_result(r.get("title"), r.get("url"), r.get("content")) for r in rows]
            if self.provider == "brave":
                async with client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": str(limit)},
                    headers={"X-Subscription-Token": self._api_key, "Accept": "application/json"},
                    timeout=self._timeout,
                ) as resp:
                    resp.raise_for_status()
                    rows = (await resp.json()).get("web", {}).get("results", [])
                return [_result(r.get("title"), r.get("url"), r.get("description")) for r in rows][
                    :limit
                ]
            async with client.get(
                f"{self._searxng_url}/search",
                params={"q": query, "format": "json"},
                timeout=self._timeout,
            ) as resp:
                resp.raise_for_status()
                rows = (await resp.json()).get("results", [])
            return [_result(r.get("title"), r.get("url"), r.get("content")) for r in rows][:limit]
        except aiohttp.ClientResponseError as exc:
            raise WebLookupError(
                f"The search provider refused the request ({exc.status})."
            ) from exc
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise WebLookupError(f"The search didn't complete ({type(exc).__name__}).") from exc

    async def read_page(self, url: str) -> dict:
        """``{"url", "title", "text", "truncated"}`` for one public page."""
        client = self._client()
        current = url
        try:
            for _ in range(_MAX_REDIRECTS + 1):
                await ensure_public_url(current)
                async with client.get(
                    current,
                    allow_redirects=False,
                    timeout=self._timeout,
                    headers={"Accept": "text/html,text/plain;q=0.9"},
                ) as resp:
                    if resp.status in (301, 302, 303, 307, 308) and "Location" in resp.headers:
                        current = urljoin(current, resp.headers["Location"])
                        continue
                    resp.raise_for_status()
                    kind = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                    if kind not in ("text/html", "text/plain", "application/xhtml+xml", ""):
                        raise WebLookupError(f"That link is {kind}, not a readable page.")
                    body = await resp.content.read(_MAX_PAGE_BYTES)
                    charset = resp.charset or "utf-8"
                break
            else:
                raise WebLookupError("That link redirected too many times.")
        except aiohttp.ClientResponseError as exc:
            raise WebLookupError(f"The page returned an error ({exc.status}).") from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise WebLookupError(f"The page couldn't be fetched ({type(exc).__name__}).") from exc
        document = body.decode(charset, errors="replace")
        title, text = html_to_text(document) if kind != "text/plain" else ("", document.strip())
        return {
            "url": current,
            "title": title,
            "text": text[: self._max_page_chars],
            "truncated": len(text) > self._max_page_chars,
        }


def _result(title, url, snippet) -> SearchResult:
    return SearchResult(
        title=str(title or "").strip(),
        url=str(url or "").strip(),
        snippet=re.sub(r"\s+", " ", str(snippet or "")).strip()[:500],
    )
