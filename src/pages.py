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
import re
import time
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
MAX_BYTES = 5_000_000  # a page that says it is longer is not read: its whole body would be held in memory
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
    try:
        parts = urlsplit(url)
    except ValueError:  # an unbalanced bracket, as in "acme.com]"
        return None
    host = parts.hostname or ""
    # A name with a dot whose last label starts with a letter: no localhost, and no address literal
    # in any spelling (decimal, octal, hexadecimal, or IPv6), since those end in a number.
    if parts.scheme not in ("http", "https") or "." not in host or not re.fullmatch(r"[a-z][a-z0-9-]*", host.rsplit(".", 1)[-1]):
        return None
    return url


def pick_links(base: str, links: list[tuple[str, str]], limit: int) -> list[str]:
    """Up to limit pages of the same site that look like about, services, or contact pages.
    Pages nearer the top of the site come first."""
    found: dict[str, int] = {}
    home = urldefrag(base).url.rstrip("/")
    for href, label in links:
        try:
            url = urldefrag(urljoin(base, href.strip())).url
            parts = urlsplit(url)
            other = _host(url) != _host(base)
        except ValueError:  # a link that is not an address, as in "http://[YOUR_DOMAIN]/about"
            continue
        if parts.scheme not in ("http", "https") or other or url.rstrip("/") == home:
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


def _skipped(url: str, shown: str, detail: str = "") -> str:
    """Record a page that is left out. The Worker log gets the detail; the caller gets a short line
    that is safe to show to a user, so that a run with missing pages can be explained."""
    logger.warning(json.dumps({"event": "carmen_page_skipped", "url": url[:300], "why": detail or shown}))
    return f"{url[:200]}: {shown}"


def too_large(length: object) -> bool:
    """Whether a Content-Length header says the page is over MAX_BYTES. No header, or one that is not
    a whole number, says nothing, and the page is read (specs/044-page-size-check/spec.md)."""
    text = str(length or "").strip()
    return text.isascii() and text.isdigit() and int(text) > MAX_BYTES


async def fetch_page(url: str) -> tuple[str, str] | str:
    """(the address after redirects, the HTML), or a short reason why the page cannot be read."""
    # Loaded by name: the module exists only inside a Worker.
    workers: Any = importlib.import_module("workers")
    try:
        response: Any = await workers.fetch(url, headers=FETCH_HEADERS, signal=js_module().AbortSignal.timeout(FETCH_MS))
        status = int(response.status)
        kind = str(response.headers.get("content-type") or "").lower()
        if status != 200:
            return _skipped(url, f"the site answered {status}", f"status {status}, content type {kind[:60] or 'none'}")
        if "html" not in kind and "text/plain" not in kind:
            return _skipped(url, "not a web page", f"status 200, content type {kind[:60] or 'none'}")
        length = response.headers.get("content-length")
        if too_large(length):
            # shortcut: only a page that states its length is stopped; one that sends no length, or a false
            # one, is still read whole. Read the body as a stream when that happens.
            return _skipped(url, "the page is too large", f"content length {str(length)[:20]}")
        return str(response.url or url), str(await response.text())[:MAX_HTML]
    except Exception as e:
        name = type(e).__name__
        shown = f"no answer in {FETCH_MS // 1000} seconds" if "Timeout" in name or "Abort" in name else f"could not be fetched ({name})"
        return _skipped(url, shown, f"{name}: {str(e)[:200]}")


def is_up(status: int | None) -> bool:
    """A site is up when it answered with a status below 500. A refusal (403) or a missing page (404)
    still shows a server that works; no answer, and 500 and above, do not."""
    return status is not None and status < 500


def percent_up(ups: int, checks: int) -> float:
    """The share of successful uptime checks, 0 to 100, with one decimal."""
    return round(100 * ups / checks, 1)


async def check_site(url: str) -> dict[str, Any]:
    """One uptime check of a site, as the columns of an uptime_check row. A site that fails is tried
    a second time before it counts as down. Never raises."""
    # Loaded by name: the module exists only inside a Worker.
    workers: Any = importlib.import_module("workers")
    check: dict[str, Any] = {}
    for _ in range(2):
        started = time.monotonic()
        status: int | None = None
        detail: str | None = None
        try:
            response: Any = await workers.fetch(url, headers=FETCH_HEADERS, signal=js_module().AbortSignal.timeout(FETCH_MS))
            status = int(response.status)
            if not is_up(status):
                # 530 is not the site speaking: the Workers runtime answers it when the name does not resolve.
                detail = "the site could not be reached (530)" if status == 530 else f"the site answered {status}"
        except Exception as e:
            name = type(e).__name__
            detail = f"no answer in {FETCH_MS // 1000} seconds" if "Timeout" in name or "Abort" in name else f"could not be reached ({name})"
            logger.warning(json.dumps({"event": "carmen_uptime_down", "url": url[:300], "why": f"{name}: {str(e)[:200]}"}))
        check = {"up": int(is_up(status)), "status": status, "duration_ms": int((time.monotonic() - started) * 1000), "detail": detail}
        if check["up"]:
            break
    return check


async def read_site(website: object, max_pages: int, fetch: Any = fetch_page) -> tuple[list[tuple[str, str]], list[str]]:
    """The home page and the pages it links to that are worth reading, as (address, text), and a
    line for each page that was left out, saying why. A page that cannot be read never fails the
    site: the caller decides what to do with none."""
    start = site_url(website)
    if start is None:
        return [], ["the company has no website address that can be fetched"]
    home = await fetch(start)
    if isinstance(home, str):
        return [], [home]
    if site_url(home[0]) is None:
        # A redirect led to something that is not a public web address. Nothing of it is used.
        return [], [_skipped(start, "redirected to an address that is not allowed")]
    text, links = read(home[1])
    pages = [(home[0], text)] if text else []
    skipped = [] if text else [_skipped(home[0], "no text could be read from the page")]
    for url in pick_links(home[0], links, max_pages - 1):
        got = await fetch(url)
        if isinstance(got, str):
            skipped.append(got)
        elif _host(got[0]) != _host(home[0]):
            # A redirect can lead off the site, so the final address is checked again.
            skipped.append(_skipped(url, "redirected to another site"))
        elif more := read(got[1])[0]:
            pages.append((got[0], more))
        else:
            skipped.append(_skipped(got[0], "no text could be read from the page"))
    return pages, skipped
