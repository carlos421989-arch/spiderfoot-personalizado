# -*- coding: utf-8 -*-
"""Bounded TorDex/Toronex search and media URL discovery for SpiderFoot."""
import html
import re
import time
from collections import deque
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from spiderfoot import SpiderFootEvent, SpiderFootPlugin

TORDEX_URL = "http://tordexu73joywapk2txdr54jed4imqledpcvcuf75qsas2gwdgksvnyd.onion/"
TORONEX_URL = "http://tornetupfu7gcgidt33ftnungxzyfq2pygui5qdoyss34xbgx2qruzid.onion/"

MEDIA_EXTENSIONS = set("""
jpg jpeg png gif webp avif bmp tif tiff svg heic heif
mp4 m4v mov avi mkv webm wmv flv mpg mpeg 3gp 3g2 m2ts mts ts vob rm rmvb asf
mp3 wav flac aac m4a ogg oga opus wma aiff aif amr ape alac ra ram mka
m3u8 mpd vtt srt
""".split())

MEDIA_TAGS = ("img", "video", "audio", "source", "track", "embed", "object")
MEDIA_META = {"og:image", "og:video", "og:audio", "twitter:image", "twitter:player:stream"}
ONION_RE = re.compile(r"https?://[a-z2-7]{16,56}\.onion(?:/[^\s\"'<>)]*)?", re.I)
MEDIA_RE = re.compile(
    r"https?://[^\s\"'<>]+?\.(?:" +
    "|".join(sorted(MEDIA_EXTENSIONS, key=len, reverse=True)) +
    r")(?:\?[^\s\"'<>]*)?", re.I
)


class sfp_tordex(SpiderFootPlugin):
    meta = {
        "name": "TorDex + Toronex Search and Media Discovery",
        "summary": "Searches TorDex and Toronex and collects media URLs from bounded onion-page navigation without downloading media files.",
        "flags": ["tor", "slow"],
        "useCases": ["Footprint", "Investigate", "Passive"],
        "categories": ["Search Engines"],
        "dataSource": {
            "website": TORDEX_URL,
            "model": "FREE_NOAUTH",
            "description": "Tor search engines and public pages accessed through the configured Tor proxy."
        },
    }

    opts = {
        "tordex_url": TORDEX_URL,
        "toronex_url": TORONEX_URL,
        "request_timeout": 5,
        "max_search_pages": 2,
        "max_crawl_pages": 20,
        "crawl_depth": 1,
        "max_media_urls": 250,
        "max_links_per_page": 40,
        "max_runtime_seconds": 90,
        "request_delay": 0.15,
        "context_chars": 120,
    }

    optdescs = {
        "tordex_url": "TorDex .onion base URL.",
        "toronex_url": "Toronex .onion base URL as supplied.",
        "request_timeout": "Per-request timeout in seconds (3-10).",
        "max_search_pages": "Maximum search pages per engine (1-5).",
        "max_crawl_pages": "Maximum onion pages visited after search (1-50).",
        "crawl_depth": "Link depth after search results (0-2).",
        "max_media_urls": "Maximum unique media URLs emitted (1-1000).",
        "max_links_per_page": "Maximum onion links accepted per page (1-100).",
        "max_runtime_seconds": "Module runtime cap (15-180 seconds).",
        "request_delay": "Delay between requests (0-1 seconds).",
        "context_chars": "Text context around the searched term.",
    }

    def setup(self, sfc, userOpts=dict()):
        self.sf = sfc
        self.results = self.tempStorage()
        self._mergeOpts(userOpts)

    def watchedEvents(self):
        return ["KEYWORD_SEARCH"]

    def producedEvents(self):
        return [
            "DARKNET_MENTION_URL",
            "DARKNET_MENTION_CONTENT",
            "RAW_RIR_DATA",
            "URL",
            "INTERESTING_FILE",
        ]

    def _intopt(self, key, default, low, high):
        try:
            value = int(self.opts.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    def _floatopt(self, key, default, low, high):
        try:
            value = float(self.opts.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    def _remaining(self):
        return self.deadline - time.monotonic()

    def _fetch(self, url, label):
        if self.checkForStop() or self._remaining() <= 0:
            return None

        timeout = min(
            self._intopt("request_timeout", 5, 3, 10),
            max(1, int(self._remaining()))
        )
        ua = self.opts.get("_useragent") or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
            "Gecko/20100101 Firefox/128.0"
        )

        try:
            # Use SpiderFoot's configured Tor SOCKS proxy. Never bypass it.
            response = self.sf.fetchUrl(
                url,
                timeout=timeout,
                useragent=ua,
                headers={
                    "Accept": (
                        "text/html,application/xhtml+xml,"
                        "application/xml;q=0.9,*/*;q=0.8"
                    )
                },
            )
        except Exception as exc:
            self.info(
                "{} network failure ({}); trying the other engine.".format(
                    label, type(exc).__name__
                )
            )
            return {"code": "NETWORK_ERROR", "content": "", "url": url}

        if not isinstance(response, dict):
            self.info(
                "{} timed out after {}s; skipping this engine.".format(
                    label, timeout
                )
            )
            return {"code": "NETWORK_ERROR", "content": "", "url": url}

        content = response.get("content") or ""
        if isinstance(content, bytes):
            content = content.decode("utf-8", "replace")

        code = str(response.get("code", ""))

        if not content and (
            not code.isdigit() or int(code) < 100 or int(code) >= 600
        ):
            self.info(
                "{} connection failed (code {}); trying the other engine.".format(
                    label, code or "unknown"
                )
            )
            return {
                "code": "NETWORK_ERROR",
                "content": "",
                "url": response.get("url") or url,
            }

        return {
            "code": code,
            "content": content,
            "url": response.get("url") or url,
        }

    def _soup(self, content):
        return BeautifulSoup(content or "", "html.parser")

    def _clean_url(self, raw, base):
        raw = html.unescape(str(raw or "").strip())

        if not raw or raw.startswith((
            "#", "javascript:", "mailto:", "data:", "blob:", "tel:"
        )):
            return None

        try:
            parsed = urlsplit(urljoin(base, raw))

            if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
                return None

            return urlunsplit((
                parsed.scheme.lower(),
                parsed.netloc,
                parsed.path or "/",
                parsed.query,
                "",
            ))
        except (ValueError, TypeError):
            return None

    def _media_type(self, url, hint=""):
        try:
            leaf = urlsplit(url).path.rsplit("/", 1)[-1]

            if "." in leaf:
                extension = leaf.rsplit(".", 1)[-1].lower()
                if extension in MEDIA_EXTENSIONS:
                    return extension
        except Exception:
            pass

        hint = str(hint or "").lower()

        if hint.startswith(("image/", "video/", "audio/")):
            return hint.split("/", 1)[1].split("+", 1)[0]

        if hint in ("img", "image", "og:image", "twitter:image", "style"):
            return "image"
        if hint in ("video", "og:video", "twitter:player:stream"):
            return "video"
        if hint in ("audio", "og:audio"):
            return "audio"
        if hint == "track":
            return "subtitle"

        return None

    def _extract_media(self, content, page_url):
        soup = self._soup(content)
        items = []

        for tag in soup.find_all(list(MEDIA_TAGS)):
            hint = tag.get("type") or tag.name

            for attr in (
                "src", "data-src", "data-original", "data-lazy-src",
                "data-original-src", "poster", "data"
            ):
                if tag.get(attr):
                    attr_hint = "image" if attr == "poster" else hint
                    items.append((tag.get(attr), attr_hint))

            for part in (tag.get("srcset") or "").split(","):
                pieces = part.strip().split()
                if pieces:
                    items.append((pieces[0], hint))

        for tag in soup.find_all("meta"):
            prop = (tag.get("property") or tag.get("name") or "").lower()
            if prop in MEDIA_META and tag.get("content"):
                items.append((tag.get("content"), prop))

        # Inline CSS and stylesheet content can contain media URLs too.
        for tag in soup.find_all(style=True):
            for raw in re.findall(
                r"url\(['\"]?([^'\")]+)", tag.get("style", ""), re.I
            ):
                items.append((raw, "style"))

        for raw in re.findall(r"url\(['\"]?([^'\")]+)", content or "", re.I):
            items.append((raw.strip(), "style"))

        # Direct links to media files.
        for tag in soup.find_all("a", href=True):
            items.append((tag.get("href"), ""))

        for raw in MEDIA_RE.findall(html.unescape(content or "")):
            items.append((raw.rstrip(".,;]}"), ""))

        out, seen = [], set()

        for raw, hint in items:
            url = self._clean_url(raw, page_url)
            if not url:
                continue

            kind = self._media_type(url, hint)
            if not kind or url.casefold() in seen:
                continue

            seen.add(url.casefold())
            out.append((url, kind))

        return soup, out

    def _emit_media(self, url, kind, source_url, parent):
        key = "media:" + url.casefold()

        if key in self.results:
            return False

        if self._media_count >= self._intopt("max_media_urls", 250, 1, 1000):
            return False

        self.results[key] = True
        self._media_count += 1

        report = "Media URL [{}]: {}\nFound on: {}".format(
            kind, url, source_url
        )

        for event_type, data in (
            ("INTERESTING_FILE", url),
            ("URL", url),
            ("DARKNET_MENTION_URL", url),
            ("DARKNET_MENTION_CONTENT", report),
        ):
            self.notifyListeners(
                SpiderFootEvent(event_type, data, self.__name__, parent)
            )

        self.info("Media URL [{}]: {}".format(kind, url))
        return True

    def _collect_media(self, content, page_url, parent, exclude_host=None):
        soup, media = self._extract_media(content, page_url)
        before = self._media_count

        for url, kind in media:
            host = (urlsplit(url).hostname or "").lower().rstrip(".")

            if exclude_host and host == exclude_host:
                continue

            self._emit_media(url, kind, page_url, parent)

            if self._media_count >= self._intopt("max_media_urls", 250, 1, 1000):
                break

        return soup, self._media_count - before

    def _unwrap(self, url):
        try:
            query = parse_qs(urlsplit(url).query)

            for key in ("url", "u", "target", "link", "redirect", "to", "r"):
                for value in query.get(key, []):
                    value = html.unescape(value)
                    if value.startswith(("http://", "https://")):
                        return value
        except Exception:
            pass

        return url

    def _onion_links(self, soup, content, base_url, excluded_hosts):
        raw_links = [
            tag.get("href") for tag in soup.find_all("a", href=True)
        ]
        raw_links.extend(
            tag.get("src")
            for tag in soup.find_all(["iframe", "frame"], src=True)
        )
        raw_links.extend(
            tag.get("href")
            for tag in soup.find_all("link", href=True)
            if "stylesheet" in (tag.get("rel") or [])
        )
        raw_links.extend(ONION_RE.findall(html.unescape(content or "")))

        out, seen = [], set()
        limit = self._intopt("max_links_per_page", 40, 1, 100)

        for raw in raw_links:
            url = self._clean_url(raw, base_url)
            if not url:
                continue

            url = self._clean_url(self._unwrap(url), base_url)
            if not url:
                continue

            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower().rstrip(".")

            if not host.endswith(".onion") or host in excluded_hosts:
                continue
            if self._media_type(url):
                continue

            if url.casefold() not in seen:
                seen.add(url.casefold())
                out.append(url)

                if len(out) >= limit:
                    break

        return out

    def _candidate_urls(self, name, base, term, page):
        base = base.rstrip("/")
        q_query = urlencode({"query": term, "page": page})
        q_q = urlencode({"q": term, "page": page})

        if name == "TorDex":
            q_old = urlencode({
                "search": term, "submit": "Search", "page": page
            })

            return [
                ("search_query", base + "/search?" + q_query),
                ("search_q", base + "/search?" + q_q),
                ("search_legacy", base + "/search.php?" + q_old),
                ("root_q", base + "/?" + q_q),
            ]

        q_action = urlencode({
            "action": "search", "query": term, "page": page
        })

        return [
            ("search_query", base + "/search?" + q_query),
            ("search_action", base + "/search?" + q_action),
            ("search_q", base + "/search?" + q_q),
            ("root_q", base + "/?" + q_q),
        ]

    def _search_engine(self, name, base, term, root_event, excluded_hosts, queue):
        base = str(base or "").strip().rstrip("/")
        base_parsed = urlsplit(base)
        host = (base_parsed.hostname or "").lower()

        if base_parsed.scheme not in ("http", "https") or not host.endswith(".onion"):
            self.error("{} URL is invalid or not a .onion URL.".format(name))
            return

        excluded_hosts.add(host)
        preferred = None
        max_pages = self._intopt("max_search_pages", 2, 1, 5)
        delay = self._floatopt("request_delay", 0.15, 0, 1)

        for page in range(1, max_pages + 1):
            if self.checkForStop() or self._remaining() <= 0:
                return

            candidates = self._candidate_urls(name, base, term, page)

            if preferred:
                candidates.sort(
                    key=lambda item: 0 if item[0] == preferred else 1
                )

            accepted = False
            network_failed = False

            for template, url in candidates:
                response = self._fetch(url, name)

                # A connect timeout is host/circuit-level, not a URL-path error.
                # Do not waste another timeout trying paths on the same host.
                if response and response.get("code") == "NETWORK_ERROR":
                    network_failed = True
                    break

                if (
                    not response
                    or response["code"] != "200"
                    or not response["content"].strip()
                ):
                    self.info(
                        "{} route returned HTTP {}; trying alternate route.".format(
                            name,
                            response.get("code", "no response") if response else "no response"
                        )
                    )
                    continue

                content = response["content"]
                soup = self._soup(content)
                links = self._onion_links(
                    soup, content, response["url"], excluded_hosts
                )

                media_before = self._media_count
                self._collect_media(
                    content, response["url"], root_event, exclude_host=host
                )

                text = soup.get_text(" ", strip=True).casefold()
                echoed = term.casefold() in text

                if (
                    not links
                    and self._media_count == media_before
                    and not echoed
                ):
                    self.info(
                        "{} route returned no recognizable results; trying fallback.".format(name)
                    )
                    continue

                preferred = template
                accepted = True

                for link in links:
                    queue.append((link, 0, name))

                self.info(
                    "{} page {}: {} onion links; {} media URLs found on search page.".format(
                        name, page, len(links), self._media_count - media_before
                    )
                )
                break

            if network_failed:
                self.info(
                    "{} host is unreachable through the current Tor circuit; continuing with the other engine.".format(name)
                )
                return

            if not accepted:
                self.info(
                    "{}: no usable search endpoint responded on page {}.".format(
                        name, page
                    )
                )
                return

            if delay and page < max_pages:
                time.sleep(min(delay, max(0, self._remaining())))

    def _term_snippets(self, content, term):
        soup = self._soup(content)

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        text = re.sub(
            r"\s+", " ",
            html.unescape(soup.get_text(" ", strip=True))
        ).strip()

        context = self._intopt("context_chars", 120, 40, 500)
        snippets = []

        for match in re.finditer(re.escape(term), text, re.I):
            snippets.append(
                text[
                    max(0, match.start() - context):
                    min(len(text), match.end() + context)
                ]
            )

            if len(snippets) >= 5:
                break

        return soup, snippets

    def handleEvent(self, event):
        if self.checkForStop():
            return

        raw = str(event.data or "").strip()
        term = raw[1:].strip() if raw.startswith("#") else raw

        if not term:
            self.error("Use #term in the Target field.")
            return

        if len(term) > 300:
            self.error("Search term is limited to 300 characters.")
            return

        query_key = "query:" + term.casefold()
        if query_key in self.results:
            return
        self.results[query_key] = True

        if not getattr(self.sf, "socksProxy", None):
            self.error(
                "Tor SOCKS proxy is not configured. No direct connection was attempted."
            )
            return

        self.deadline = time.monotonic() + self._intopt(
            "max_runtime_seconds", 90, 15, 180
        )
        self._media_count = 0
        excluded_hosts = set()

        for key in ("tordex_url", "toronex_url"):
            host = (
                urlsplit(str(self.opts.get(key, ""))).hostname or ""
            ).lower()
            if host:
                excluded_hosts.add(host)

        queue = deque()

        for name, key, default in (
            ("TorDex", "tordex_url", TORDEX_URL),
            ("Toronex", "toronex_url", TORONEX_URL),
        ):
            if self.checkForStop() or self._remaining() <= 0:
                break

            self.info("Searching {} for #{}".format(name, term))
            self._search_engine(
                name, self.opts.get(key, default), term,
                event, excluded_hosts, queue
            )

        max_pages = self._intopt("max_crawl_pages", 20, 1, 50)
        max_depth = self._intopt("crawl_depth", 1, 0, 2)
        visited = set()
        visited_count = 0

        while (
            queue
            and visited_count < max_pages
            and self._remaining() > 0
            and not self.checkForStop()
        ):
            url, depth, source = queue.popleft()

            if url.casefold() in visited or "page:" + url.casefold() in self.results:
                continue

            visited.add(url.casefold())
            self.results["page:" + url.casefold()] = True

            response = self._fetch(url, source + " page")

            if not response or response["code"] != "200" or not response["content"]:
                continue

            visited_count += 1
            page_event = SpiderFootEvent(
                "DARKNET_MENTION_URL", url, self.__name__, event
            )
            self.notifyListeners(page_event)

            content = response["content"]
            soup, media_count = self._collect_media(
                content, response["url"], page_event
            )
            soup, snippets = self._term_snippets(content, term)

            if snippets:
                title = (
                    soup.title.get_text(" ", strip=True)
                    if soup.title else "Untitled"
                )
                report = (
                    "[{} Deep Search]\nTerm: #{}\nURL: {}\nTitle: {}\nMatches: {}"
                ).format(
                    source, term, url, title, "\n---\n".join(snippets)
                )
                self.notifyListeners(
                    SpiderFootEvent(
                        "DARKNET_MENTION_CONTENT",
                        report,
                        self.__name__,
                        page_event,
                    )
                )

            self.info(
                "Visited {} page depth {}: {}; media URLs found: {}".format(
                    source, depth, url, media_count
                )
            )

            if depth < max_depth and self._remaining() > 0:
                for link in self._onion_links(
                    soup, content, response["url"], excluded_hosts
                ):
                    if (
                        link.casefold() not in visited
                        and len(queue) < max_pages * 3
                    ):
                        queue.append((link, depth + 1, source))

        self.info(
            "Search complete: {} pages visited; {} unique media URLs emitted; {:.1f}s left.".format(
                visited_count, self._media_count, max(0, self._remaining())
            )
        )