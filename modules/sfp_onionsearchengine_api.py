# -*- coding: utf-8 -*-

import json
from urllib.parse import urlencode, urlparse

from spiderfoot import SpiderFootEvent, SpiderFootPlugin


class sfp_onionsearchengine_api(SpiderFootPlugin):

    meta = {
        "name": "Onion Search Engine API",
        "summary": "Searches the Onion Search Engine index using its authenticated JSON API.",
        "flags": ["apikey", "slow"],
        "useCases": ["Footprint", "Investigate"],
        "categories": ["Search Engines"],
        "dataSource": {
            "website": "https://onionsearchengine.com/",
            "model": "FREE_AUTH_LIMITED",
            "references": [
                "https://onionsearchengine.com/api_documentation.php",
                "https://onionsearchengine.com/solutions.php"
            ],
            "apiKeyInstructions": [
                "Obtain an API key from the Onion Search Engine dashboard.",
                "Configure the key in SpiderFoot Settings > API Keys."
            ],
            "description": (
                "Searches the indexed Tor network using the official "
                "JSON API. Returns discovered .onion URLs and available "
                "result context. Does not automatically visit result URLs."
            )
        }
    }

    opts = {
        "api_key": "",
        "max_results": 20
    }

    optdescs = {
        "api_key": "Onion Search Engine API key.",
        "max_results": "Maximum results processed per query (1-50)."
    }

    def setup(self, sfc, userOpts=dict()):
        self.sf = sfc
        self.results = self.tempStorage()
        self.errorState = False
        for opt in list(userOpts.keys()):
            self.opts[opt] = userOpts[opt]

    def watchedEvents(self):
        return ["DOMAIN_NAME", "EMAILADDR", "USERNAME", "HUMAN_NAME"]

    def producedEvents(self):
        return ["DARKNET_MENTION_URL", "DARKNET_MENTION_CONTENT"]

    def handleEvent(self, event):
        if self.errorState or self.checkForStop():
            return

        query = str(event.data).strip()
        if not query or query in self.results:
            return

        self.results[query] = True

        api_key = str(self.opts.get("api_key", "")).strip()
        if not api_key:
            self.error(
                "API key missing. Configure Onion Search Engine API "
                "under Settings > API Keys."
            )
            self.errorState = True
            return

        try:
            max_results = int(self.opts.get("max_results", 20))
        except (ValueError, TypeError):
            max_results = 20

        max_results = max(1, min(max_results, 50))

        url = "https://onionsearchengine.com/api.php?" + urlencode({
            "q": query,
            "page": 1
        })

        response = self.sf.fetchUrl(
            url,
            timeout=self.opts.get("_fetchtimeout", 30),
            useragent=self.opts.get("_useragent", "SpiderFoot"),
            headers={
                "X-API-Key": api_key,
                "Accept": "application/json"
            }
        )

        if not response:
            self.error("No response from Onion Search Engine API.")
            return

        status_code = str(response.get("code", ""))
        content = response.get("content")

        if status_code != "200":
            self.error(
                f"Onion Search Engine API returned HTTP {status_code}."
            )
            if status_code in ("401", "403", "429"):
                self.errorState = True
            return

        if not content:
            self.info("Onion Search Engine API returned an empty response.")
            return

        try:
            data = json.loads(content)
        except (ValueError, TypeError) as exc:
            self.error(f"Could not parse API JSON: {exc}")
            return

        if data.get("status") != "success":
            self.error(
                "Onion Search Engine API reported an error: "
                + str(data.get("message", "unknown error"))
            )
            return

        records = data.get("results", [])
        if not isinstance(records, list) or not records:
            self.info(f"No indexed results returned for query: {query}")
            return

        count = 0

        for record in records:
            if self.checkForStop() or count >= max_results:
                break

            if not isinstance(record, dict):
                continue

            result_url = str(record.get("url", "")).strip()
            if not result_url:
                continue

            if "://" not in result_url:
                result_url = "http://" + result_url

            parsed = urlparse(result_url)
            hostname = (parsed.hostname or "").lower()

            # Only emit actual onion-service URLs.
            if (
                parsed.scheme not in ("http", "https")
                or not hostname.endswith(".onion")
            ):
                continue

            if result_url in self.results:
                continue

            self.results[result_url] = True
            count += 1

            url_event = SpiderFootEvent(
                "DARKNET_MENTION_URL",
                result_url,
                self.__name__,
                event
            )
            self.notifyListeners(url_event)

            title = str(record.get("title", "")).strip()
            context = str(record.get("context", "")).strip()

            if title or context:
                description = (
                    f"Onion Search Engine API result for [{query}]\n"
                    f"Title: {title}\n"
                    f"URL: {result_url}\n"
                    f"Context: {context[:1000]}"
                )

                content_event = SpiderFootEvent(
                    "DARKNET_MENTION_CONTENT",
                    description,
                    self.__name__,
                    url_event
                )
                self.notifyListeners(content_event)

            self.info(
                f"Found indexed onion URL for query [{query}]: "
                f"{result_url}"
            )

        self.info(
            f"Processed {count} onion URLs for query [{query}]."
        )