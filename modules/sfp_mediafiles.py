# -*- coding: utf-8 -*-

import html
import posixpath
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from spiderfoot import SpiderFootEvent, SpiderFootPlugin


MEDIA_EXTENSIONS = {
    ".3g2", ".3gp", ".aac", ".aif", ".aiff", ".alac", ".amr",
    ".ape", ".asf", ".avi", ".avif", ".bmp", ".flac", ".flv",
    ".gif", ".heic", ".jpeg", ".jpg", ".m2ts", ".m3u8", ".m4a",
    ".m4v", ".mka", ".mkv", ".mov", ".mp3", ".mp4", ".mpd",
    ".mpeg", ".mpg", ".mts", ".oga", ".ogg", ".ogv", ".opus",
    ".png", ".ra", ".ram", ".rm", ".rmvb", ".svg", ".tif",
    ".tiff", ".ts", ".vob", ".wav", ".webm", ".webp", ".wma",
    ".wmv"
}

URL_RE = re.compile(
    r"""(?i)\b(?:https?|ftp)://[^\s<>"'`]+"""
)

ATTR_RE = re.compile(
    r"""(?is)\b(?:href|src|poster|data-src|data-url|data-original)\s*=\s*["']([^"']+)["']"""
)


class sfp_mediafiles(SpiderFootPlugin):

    meta = {
        "name": "Media File Finder - All Modules",
        "summary": (
            "Detects media URLs in events from enabled modules. "
            "Does not download files."
        ),
        "flags": [],
        "useCases": ["Footprint", "Investigate"],
        "categories": ["Content Analysis"]
    }

    opts = {
        "maxcontentchars": 1000000,
        "maxurlsper_event": 200
    }

    optdescs = {
        "maxcontentchars": "Maximum event text length to inspect.",
        "maxurlsper_event": "Maximum candidate URLs to inspect per event."
    }

    def setup(self, sfc, userOpts=dict()):
        self.sf = sfc
        self.results = self.tempStorage()
        self.__dataSource__ = "Existing scan events"

        for opt in list(userOpts.keys()):
            self.opts[opt] = userOpts[opt]

    def watchedEvents(self):
        return ["*"]

    def producedEvents(self):
        return ["INTERESTING_FILE", "URL"]

    def handleEvent(self, event):
        if event.module == self.__name__:
            return

        if event.eventType == "INTERESTING_FILE":
            return

        data = event.data
        if not data:
            return

        if isinstance(data, bytes):
            text = data.decode("utf-8", errors="replace")
        else:
            text = str(data)

        try:
            max_chars = max(
                1000, int(self.opts.get("maxcontentchars", 1000000))
            )
        except (TypeError, ValueError):
            max_chars = 1000000

        try:
            max_urls = max(
                1, int(self.opts.get("maxurlsper_event", 200))
            )
        except (TypeError, ValueError):
            max_urls = 200

        text = html.unescape(text[:max_chars]).replace("\\/", "/")

        candidates = [
            match.group(0)
            for match in URL_RE.finditer(text)
        ]

        base = getattr(event, "actualSource", None)

        if isinstance(base, str) and base.lower().startswith(
            ("http://", "https://", "ftp://")
        ):
            for match in ATTR_RE.finditer(text):
                raw = match.group(1).strip()

                if not raw or raw.startswith("#"):
                    continue

                if raw.lower().startswith(
                    ("javascript:", "data:", "mailto:")
                ):
                    continue

                candidates.append(urljoin(base, raw))

        seen_in_event = set()

        for index, raw in enumerate(candidates):
            if index >= max_urls:
                break

            url = raw.strip().strip("\"'<>")
            url = url.rstrip(".,;:!?)]}")

            if not url:
                continue

            try:
                parsed = urlsplit(url)
            except ValueError:
                continue

            if parsed.scheme.lower() not in ("http", "https", "ftp"):
                continue

            if not parsed.netloc:
                continue

            extension = posixpath.splitext(
                parsed.path.lower()
            )[1]

            if extension not in MEDIA_EXTENSIONS:
                continue

            normalized_url = urlunsplit((
                parsed.scheme.lower(),
                parsed.netloc,
                parsed.path,
                parsed.query,
                parsed.fragment
            ))

            if normalized_url in seen_in_event:
                continue

            seen_in_event.add(normalized_url)

            if normalized_url in self.results:
                continue

            self.results[normalized_url] = True

            evt = SpiderFootEvent(
                "INTERESTING_FILE",
                normalized_url,
                self.__name__,
                event
            )

            self.notifyListeners(evt)

            # Publish a standard URL event for the media candidate.
            url_evt = SpiderFootEvent(
                "URL",
                normalized_url,
                self.__name__,
                event
            )
            self.notifyListeners(url_evt)

            self.info(
                f"Media URL detected ({extension}); candidate download URL: "
                f"{normalized_url}"
            )