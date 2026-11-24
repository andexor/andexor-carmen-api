# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Reading a company's own website, for a model that cannot search the web.

Everything here is pure except fetch_page. See specs/011-ai-adapter-workers-ai/spec.md.
A page is evidence about a company. Its text is passed to a model as data, never as instructions.
"""

import importlib
import json
import logging
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

from access import js_module

logger = logging.getLogger("carmen")

# A link is followed when its address or its label has one of these words.
WANTED = ("about", "service", "solution", "product", "contact", "team", "company", "who-we-are", "what-we-do")
NOT_PAGES = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".mp4", ".mp3", ".doc", ".docx", ".xls", ".xlsx")
SKIPPED = ("script", "style", "noscript", "svg", "template")
BLOCKS = ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header", "footer", "title", "td", "th")

BREAK = "\x1e"  # marks the end of a block; a line break in the HTML source is only a space
MAX_HTML = 1_000_000  # characters of one page that are parsed
FETCH_MS = 10_000
FETCH_HEADERS = {"user-agent": "Mozilla/5.0 (compatible; research-fetcher/1.0)", "accept": "text/html"}


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip = 0
        self._href: str | None = None
        self._label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIPPED:
            self._skip += 1
        elif tag == "a":
            self._href, self._label = dict(attrs).get("href"), []
        elif tag in BLOCKS:
            self.text.append(BREAK)

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED:
            self._skip = max(0, self._skip - 1)
        elif tag == "a":
            if self._href:
                self.links.append((self._href, " ".join(self._label)))
            self._href = None
        elif tag in BLOCKS:
            self.text.append(BREAK)

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.text.append(data)
        if self._href is not None:
            self._label.append(data)


def read(html: str) -> tuple[str, list[tuple[str, str]]]:
    """The page's visible text, one block per line, and its links as (address, label)."""
    page = _Page()
    try:
        page.feed(html[:MAX_HTML])
        page.close()
    except Exception as e:  # damaged HTML: keep what was read
        logger.warning(json.dumps({"event": "carmen_page_damaged", "why": f"{type(e).__name__}: {e}"[:200]}))
    lines = (" ".join(line.split()) for line in "".join(page.text).split(BREAK))
    return "\n".join(line for line in lines if line), page.links


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def site_url(website: object) -> str | None:
    """The address to fetch for a company's website, or None when it is not a public web address."""
    text = str(website or "").strip()
    if not text:
        return None
    url = text if "://" in text else f"https://{text}"
    parts = urlsplit(url)
    host = parts.hostname or ""
    # A name with a dot, not a bare number: no localhost, no address literal.
    if parts.scheme not in ("http", "https") or "." not in host or ":" in host or host.replace(".", "").isdigit():
        return None
    return url


def pick_links(base: str, links: list[tuple[str, str]], limit: int) -> list[str]:
    """Up to limit pages of the same site that look like about, services, or contact pages.
    Pages nearer the top of the site come first."""
    found: dict[str, int] = {}
    home = urldefrag(base).url.rstrip("/")
    for href, label in links:
        url = urldefrag(urljoin(base, href.strip())).url
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or _host(url) != _host(base) or url.rstrip("/") == home:
            continue
        path = parts.path.lower()
        if path.endswith(NOT_PAGES) or not any(word in path or word in label.lower() for word in WANTED):
            continue
        found.setdefault(url, path.strip("/").count("/"))
    return sorted(found, key=lambda url: found[url])[: max(0, limit)]


def trim(pages: list[tuple[str, str]], budget: int) -> list[tuple[str, str]]:
    """Cut the pages so that their text fits in budget characters. Each page gets an equal share,
    and what a short page leaves over goes to the pages after it."""
    cut: list[tuple[str, str]] = []
    left = max(0, budget)
    for index, (url, text) in enumerate(pages):
        kept = text[: left // (len(pages) - index)]
        left -= len(kept)
        if kept:
            cut.append((url, kept))
    return cut


def as_text(pages: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"PAGE {url}\n{text}" for url, text in pages)


def _skipped(url: str, why: str) -> None:
    """A page that is left out is named in the Worker log, so that a run with no pages can be explained."""
    logger.warning(json.dumps({"event": "carmen_page_skipped", "url": url[:300], "why": why}))


async def fetch_page(url: str) -> tuple[str, str] | None:
    """(the address after redirects, the HTML), or None when the page cannot be read."""
    # Loaded by name: the module exists only inside a Worker.
    workers: Any = importlib.import_module("workers")
    try:
        response: Any = await workers.fetch(url, headers=FETCH_HEADERS, signal=js_module().AbortSignal.timeout(FETCH_MS))
        kind = str(response.headers.get("content-type") or "").lower()
        if int(response.status) != 200 or ("html" not in kind and "text/plain" not in kind):
            _skipped(url, f"status {int(response.status)}, content type {kind[:60] or 'none'}")
            return None
        return str(response.url or url), str(await response.text())[:MAX_HTML]
    except Exception as e:
        _skipped(url, f"{type(e).__name__}: {str(e)[:200]}")
        return None


async def read_site(website: object, max_pages: int, fetch: Any = fetch_page) -> list[tuple[str, str]]:
    """The home page and the pages it links to that are worth reading, as (address, text).
    A page that cannot be fetched is left out. Empty when there is no site or nothing could be read."""
    start = site_url(website)
    if start is None:
        return []
    home = await fetch(start)
    if home is None:
        return []
    text, links = read(home[1])
    pages = [(home[0], text)] if text else []
    for url in pick_links(home[0], links, max_pages - 1):
        # A redirect can lead off the site, so the final address is checked again.
        got = await fetch(url)
        if got is not None and _host(got[0]) == _host(home[0]):
            more = read(got[1])[0]
            if more:
                pages.append((got[0], more))
    return pages
