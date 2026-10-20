# -*- coding: utf-8 -*-
import json
import posixpath
import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from spiderfoot import SpiderFootEvent, SpiderFootPlugin

GROUPS = {
    "video": ("mp4", "avi", "mkv", "mov", "webm", "wmv", "flv", "m4v", "mpg", "mpeg", "3gp", "3g2", "m2ts", "mts", "ts", "vob", "rm", "rmvb", "asf"),
    "audio": ("mp3", "wav", "flac", "aac", "m4a", "ogg", "oga", "opus", "wma", "aiff", "aif", "amr", "ape", "alac", "ra", "ram", "mka"),
    "image": ("jpg", "jpeg", "png", "gif", "webp", "avif", "bmp", "tif", "tiff", "svg", "heic"),
    "playlist": ("m3u8", "mpd")
}
ALL_EXTENSIONS = set(ext for group in GROUPS.values() for ext in group)

DOMAIN = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
TARGET_RE = re.compile(
    r"^media:(?P<kind>[a-z0-9]+)(?:\s+(?P<domain>" + DOMAIN + r"))?$",
    re.IGNORECASE
)


class sfp_mediasearch(SpiderFootPlugin):
    meta = {
        "name": "Media Search - Global and Onion",
        "summary": "Searches configured search indexes for direct media URLs. Does not download files.",
        "flags": [],
        "useCases": ["Footprint", "Investigate"],
        "categories": ["Search Engines"]
    }

    opts = {"maxresults": 50}
    optdescs = {"maxresults": "Maximum direct media URLs per provider."}

    def setup(self, sfc, userOpts=dict()):
        self.sf = sfc
        self.results = self.tempStorage()
        for opt in list(userOpts.keys()):
            self.opts[opt] = userOpts[opt]

    def watchedEvents(self):
        return ["MEDIA_SEARCH"]

    def producedEvents(self):
        return ["INTERESTING_FILE", "URL", "DARKNET_MENTION_URL"]

    def _emit(self, url, event, extension, onion=False):
        key = url.casefold()
        if key in self.results:
            return False

        self.results[key] = True
        self.notifyListeners(
            SpiderFootEvent("INTERESTING_FILE", url, self.__name__, event)
        )
        self.notifyListeners(
            SpiderFootEvent("URL", url, self.__name__, event)
        )

        if onion:
            self.notifyListeners(
                SpiderFootEvent("DARKNET_MENTION_URL", url, self.__name__, event)
            )

        self.info("Media URL found ({}): {}".format(extension, url))
        return True

    def handleEvent(self, event):
        if self.checkForStop():
            return

        match = TARGET_RE.fullmatch(str(event.data).strip())
        if not match:
            self.error("Use media:mp4, media:avi, media:mp3 or media:all. Domain is optional.")
            return

        kind = match.group("kind").lower()
        domain = match.group("domain")
        if domain:
            domain = domain.lower().rstrip(".")

        if kind != "all" and kind not in ALL_EXTENSIONS:
            self.error("Unsupported media extension: " + kind)
            return

        cfg = getattr(self.sf, "opts", {})
        if not isinstance(cfg, dict):
            cfg = {}

        module_config = cfg.get("__modules__", {})
        if not isinstance(module_config, dict):
            module_config = {}

        def module_opts(name):
            entry = module_config.get(name, {})
            if not isinstance(entry, dict):
                return {}
            options = entry.get("opts", {})
            return options if isinstance(options, dict) else {}

        google = module_opts("sfp_googlesearch")
        bing = module_opts("sfp_bingsearch")
        onion_cfg = module_opts("sfp_onionsearchengine_api")

        google_key = str(google.get("api_key", "")).strip()
        google_cse = str(google.get("cse_id", "")).strip()
        bing_key = str(bing.get("api_key", "")).strip()
        onion_key = str(onion_cfg.get("api_key", "")).strip()

        providers = []
        if google_key and google_cse:
            providers.append(("google", google_key, google_cse))
        if bing_key:
            providers.append(("bing", bing_key, ""))

        if kind == "all":
            query_groups = list(GROUPS.values())
            wanted = ALL_EXTENSIONS
        else:
            query_groups = [(kind,)]
            wanted = {kind}

        try:
            limit = max(1, min(int(self.opts.get("maxresults", 50)), 200))
        except (TypeError, ValueError):
            limit = 50

        timeout = cfg.get("_fetchtimeout", 30)
        useragent = cfg.get("_useragent", "SpiderFoot")

        # Public search providers. With no domain, search globally.
        for provider, api_key, cse_id in providers:
            found = 0

            for group in query_groups:
                if self.checkForStop() or found >= limit:
                    break

                if len(group) == 1:
                    query = "filetype:" + group[0]
                else:
                    query = "(" + " OR ".join(
                        "filetype:" + ext for ext in group
                    ) + ")"

                if domain:
                    query = "site:" + domain + " " + query

                try:
                    if provider == "google":
                        response = self.sf.googleIterate(
                            searchString=query,
                            opts={
                                "timeout": timeout,
                                "useragent": useragent,
                                "api_key": google_key,
                                "cse_id": google_cse
                            }
                        )
                    else:
                        response = self.sf.bingIterate(
                            searchString=query,
                            opts={
                                "timeout": timeout,
                                "useragent": useragent,
                                "count": 10,
                                "api_key": bing_key
                            }
                        )
                except Exception as exc:
                    self.error("{} search failed: {}".format(
                        provider, type(exc).__name__
                    ))
                    continue

                if not isinstance(response, dict):
                    continue

                urls = response.get("urls", [])
                if not isinstance(urls, (list, tuple)):
                    continue

                for raw_url in urls:
                    if self.checkForStop() or found >= limit:
                        break

                    try:
                        parsed = urlsplit(str(raw_url).strip())
                    except ValueError:
                        continue

                    if parsed.scheme.lower() not in ("http", "https"):
                        continue

                    host = (parsed.hostname or "").lower().rstrip(".")
                    if domain and host != domain and not host.endswith("." + domain):
                        continue

                    extension = posixpath.splitext(parsed.path.lower())[1].lstrip(".")
                    if extension not in wanted:
                        continue

                    url = urlunsplit((
                        parsed.scheme.lower(), parsed.netloc,
                        parsed.path, parsed.query, ""
                    ))

                    if self._emit(url, event, extension):
                        found += 1

        # Query the configured Onion Search Engine API separately.
        if onion_key:
            found = 0

            for group in query_groups:
                if self.checkForStop() or found >= limit:
                    break

                query = " OR ".join(group)
                if domain:
                    query = '"' + domain + '" ' + query

                api_url = "https://onionsearchengine.com/api.php?" + urlencode({
                    "q": query,
                    "page": 1
                })

                try:
                    response = self.sf.fetchUrl(
                        api_url,
                        timeout=timeout,
                        useragent=useragent,
                        headers={
                            "X-API-Key": onion_key,
                            "Accept": "application/json"
                        }
                    )
                except Exception as exc:
                    self.error("Onion search failed: " + type(exc).__name__)
                    continue

                if not isinstance(response, dict):
                    continue
                if str(response.get("code", "")) != "200":
                    self.error("Onion Search Engine returned HTTP " + str(response.get("code", "")))
                    continue

                content = response.get("content") or ""
                if isinstance(content, bytes):
                    content = content.decode("utf-8", "replace")

                try:
                    data = json.loads(content)
                except (ValueError, TypeError):
                    continue

                if not isinstance(data, dict) or data.get("status") != "success":
                    continue

                records = data.get("results", [])
                if not isinstance(records, list):
                    continue

                for record in records:
                    if self.checkForStop() or found >= limit:
                        break
                    if not isinstance(record, dict):
                        continue

                    raw_url = (
                        record.get("url") or record.get("link") or
                        record.get("href") or record.get("result_url") or ""
                    )
                    if not isinstance(raw_url, str) or not raw_url:
                        continue

                    try:
                        parsed = urlsplit(raw_url.strip())
                    except ValueError:
                        continue

                    if parsed.scheme.lower() not in ("http", "https"):
                        continue

                    host = (parsed.hostname or "").lower().rstrip(".")
                    if not host.endswith(".onion"):
                        continue

                    extension = posixpath.splitext(parsed.path.lower())[1].lstrip(".")
                    if extension not in wanted:
                        continue

                    url = urlunsplit((
                        parsed.scheme.lower(), parsed.netloc,
                        parsed.path, parsed.query, ""
                    ))

                    if self._emit(url, event, extension, onion=True):
                        found += 1

        if not providers and not onion_key:
            self.error(
                "Configure Google API key + CSE ID, Bing API key, "
                "or the existing Onion Search Engine API key."
            )
        else:
            self.info("Media search completed for {}.".format(
                domain or "all indexed domains"
            ))