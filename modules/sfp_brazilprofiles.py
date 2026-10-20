# -*- coding: utf-8 -*-
"""Brazilian social profile candidate checker for SpiderFoot."""
import re
import time
import unicodedata
from urllib.parse import quote
from spiderfoot import SpiderFootEvent, SpiderFootPlugin

PLATFORMS = [
    {"name":"Instagram","url":"https://www.instagram.com/{username}/","pattern":r"^[a-z0-9._]{1,30}$","missing":["sorry, this page isn't available","page isn't available","page not found"]},
    {"name":"TikTok","url":"https://www.tiktok.com/@{username}","pattern":r"^[a-z0-9._]{2,24}$","missing":["couldn't find this account","couldn't find this user","this account doesn't exist"]},
    {"name":"Facebook","url":"https://www.facebook.com/{username}","pattern":r"^[a-z0-9.]{5,50}$","missing":["this content isn't available right now","content isn't available","page isn't available"]},
    {"name":"YouTube","url":"https://www.youtube.com/@{username}","pattern":r"^[a-z0-9._-]{3,30}$","missing":["this channel does not exist","channel not found","this page isn't available"]},
    {"name":"Kwai","url":"https://www.kwai.com/@{username}","pattern":r"^[a-z0-9._-]{1,50}$","missing":["page not found","user not found","perfil nao encontrado"]},
    {"name":"X","url":"https://x.com/{username}","pattern":r"^[a-z0-9_]{1,15}$","missing":["this account doesn't exist","account suspended","page doesn't exist"]},
    {"name":"Threads","url":"https://www.threads.net/@{username}","pattern":r"^[a-z0-9._]{1,30}$","missing":["this page isn't available","profile isn't available","page not found"]},
    {"name":"LinkedIn","url":"https://www.linkedin.com/in/{username}/","pattern":r"^[a-z0-9-]{3,100}$","missing":["this page doesn't exist","page not found","profile not found"]},
    {"name":"Telegram","url":"https://t.me/{username}","pattern":r"^[a-z][a-z0-9_]{4,31}$","missing":["username not found","sorry, this page is not available"]},
    {"name":"Pinterest","url":"https://www.pinterest.com/{username}/","pattern":r"^[a-z0-9_]{3,30}$","missing":["sorry! we couldn't find that page","page not found"]},
    {"name":"Reddit","url":"https://www.reddit.com/user/{username}/","pattern":r"^[a-z0-9_-]{3,20}$","missing":["nobody on reddit goes by that name","user not found","page not found"]},
    {"name":"Twitch","url":"https://www.twitch.tv/{username}","pattern":r"^[a-z0-9_]{4,25}$","missing":["unless you've got a time machine","page not found"]},
    {"name":"Bluesky","url":"https://bsky.app/profile/{username}.bsky.social","pattern":r"^[a-z0-9.-]{1,30}$","missing":["profile not found","account not found"]},
    {"name":"GitHub","url":"https://github.com/{username}","pattern":r"^[a-z0-9-]{1,39}$","missing":["this is not the web page you are looking for","404 not found"]},
    {"name":"Filmow","url":"https://filmow.com/@{username}","pattern":r"^[a-z0-9._-]{1,50}$","missing":["usuario nao encontrado","perfil nao encontrado","pagina nao encontrada"]},
    {"name":"Wattpad","url":"https://www.wattpad.com/user/{username}","pattern":r"^[a-z0-9_]{1,20}$","missing":["user not found","page not found"]},
    {"name":"SoundCloud","url":"https://soundcloud.com/{username}","pattern":r"^[a-z0-9_-]{1,50}$","missing":["we can't find that user","user not found","page not found"]},
    {"name":"Medium","url":"https://medium.com/@{username}","pattern":r"^[a-z0-9._-]{1,50}$","missing":["page not found","404: page not found"]},
    {"name":"Behance","url":"https://www.behance.net/{username}","pattern":r"^[a-z0-9-]{1,50}$","missing":["profile not found","page not found"]},
    {"name":"Tumblr","url":"https://{username}.tumblr.com/","pattern":r"^[a-z0-9-]{1,50}$","missing":["there's nothing here","this tumblr is missing","page not found"]},
]

class sfp_brazilprofiles(SpiderFootPlugin):
    meta = {
        "name": "Brazilian Social Profile Candidates",
        "summary": "Checks public social profile candidates; manual verification required.",
        "flags": ["slow"],
        "useCases": ["Footprint", "Passive"],
        "categories": ["Social Media"],
    }
    opts = {"max_variants": 8, "max_checks": 160, "request_delay": 0.2}
    optdescs = {
        "max_variants": "Username variants to test (1-12).",
        "max_checks": "Maximum public URL requests per username (1-240).",
        "request_delay": "Seconds between requests (0-2).",
    }

    def setup(self, sfc, userOpts=dict()):
        self.sf = sfc
        self.results = self.tempStorage()
        self.__dataSource__ = "Public social profile URL checks"
        for key, value in userOpts.items():
            self.opts[key] = value

    def watchedEvents(self):
        return ["USERNAME"]

    def producedEvents(self):
        return ["SOCIAL_MEDIA"]

    @staticmethod
    def _fold(value):
        value = unicodedata.normalize("NFKD", str(value or ""))
        return "".join(c for c in value if not unicodedata.combining(c)).casefold()

    @staticmethod
    def _limit(value, default, minimum, maximum):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _variants(self, value):
        raw = str(value or "").strip().lstrip("@")
        if not raw or len(raw) > 80:
            return []
        folded = self._fold(raw).strip()
        parts = [p for p in re.split(r"[\s._-]+", folded) if p]
        compact = "".join(parts)
        candidates = [folded, compact]
        if len(parts) > 1:
            candidates.extend([".".join(parts), "_".join(parts), "-".join(parts)])
        if compact:
            candidates.extend([
                compact + "br", compact + "oficial", compact + "ofc",
                compact + "real", "real" + compact, "oficial" + compact
            ])
        result = []
        seen = set()
        for item in candidates:
            item = item.strip("._-")
            if item and item not in seen and re.fullmatch(r"[a-z0-9._-]+", item):
                seen.add(item)
                result.append(item)
        return result[:self._limit(self.opts.get("max_variants", 8), 8, 1, 12)]

    def _check(self, platform, username):
        if not re.fullmatch(platform["pattern"], username):
            return None
        url = platform["url"].format(username=quote(username, safe="._-"))
        timeout = self._limit(self.opts.get("_fetchtimeout", 5), 5, 3, 8)
        try:
            response = self.sf.fetchUrl(
                url, timeout=timeout,
                useragent=self.opts.get("_useragent") or "SpiderFoot",
                noLog=True
            )
        except Exception as exc:
            self.debug("Request failed for {}: {}".format(platform["name"], exc))
            return None
        if not response:
            return None
        status = str(response.get("code", ""))
        content = str(response.get("content") or "")
        if status == "429":
            self.info("HTTP 429 from {}; stopping this scan.".format(platform["name"]))
            return "RATE_LIMIT"
        if status != "200" or not content:
            return None
        text = self._fold(content)
        if any(self._fold(marker) in text for marker in platform["missing"]):
            return None
        if self._fold(username) not in text:
            return None
        return url, status

    def handleEvent(self, event):
        if event.eventType != "USERNAME":
            return
        seed = str(event.data or "").strip().lstrip("@")
        if not seed or len(seed) > 80:
            return
        seed_key = "seed:" + self._fold(seed)
        if seed_key in self.results:
            return
        self.results[seed_key] = True

        variants = self._variants(seed)
        if not variants:
            return
        max_checks = self._limit(self.opts.get("max_checks", 160), 160, 1, 240)
        try:
            delay = float(self.opts.get("request_delay", 0.2))
        except (TypeError, ValueError):
            delay = 0.2
        delay = max(0.0, min(2.0, delay))
        checks = 0

        for variant in variants:
            for platform in PLATFORMS:
                if checks >= max_checks:
                    self.info("Reached max_checks={}; stopping.".format(max_checks))
                    return
                checks += 1
                result = self._check(platform, variant)
                if delay:
                    time.sleep(delay)
                if result == "RATE_LIMIT":
                    return
                if not result:
                    continue
                url, status = result
                key = "url:" + url.casefold()
                if key in self.results:
                    continue
                self.results[key] = True
                description = (
                    "Possible public profile candidate: {} / @{} "
                    "(HTTP {}; manual verification required)\n<SFURL>{}</SFURL>"
                ).format(platform["name"], variant, status, url)
                self.info("Possible candidate on {} for @{}.".format(platform["name"], variant))
                self.notifyListeners(
                    SpiderFootEvent("SOCIAL_MEDIA", description, self.__name__, event)
                )