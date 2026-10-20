# -*- coding: utf-8 -*-

import html
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from spiderfoot import SpiderFootEvent, SpiderFootPlugin


class sfp_onionlocation(SpiderFootPlugin):

    meta = {
        'name': 'Onion-Location Discovery',
        'summary': 'Finds .onion URLs publicly advertised by target websites.',
        'flags': ['tor'],
        'useCases': ['Footprint', 'Investigate'],
        'categories': ['Content Analysis'],
        'dataSource': {
            'website': 'https://community.torproject.org/onion-services/advanced/onion-location/',
            'model': 'FREE_NOAUTH_UNLIMITED',
            'references': [
                'https://community.torproject.org/onion-services/advanced/onion-location/'
            ]
        }
    }

    opts = {
        'maxdomains': 25
    }

    optdescs = {
        'maxdomains': 'Maximum number of domains to check per scan.'
    }

    def setup(self, sfc, userOpts=dict()):
        self.sf = sfc
        self.results = self.tempStorage()
        self.checked_domains = set()

        for opt in list(userOpts.keys()):
            self.opts[opt] = userOpts[opt]

    def watchedEvents(self):
        return ['DOMAIN_NAME']

    def producedEvents(self):
        return ['DARKNET_MENTION_URL']

    def handleEvent(self, event):
        domain = str(event.data).strip().lower().rstrip('.')

        if not domain or domain.endswith('.onion'):
            return

        if not re.fullmatch(r'[a-z0-9.-]+', domain):
            return

        if domain in self.checked_domains:
            return

        try:
            limit = max(1, int(self.opts.get('maxdomains', 25)))
        except (TypeError, ValueError):
            limit = 25

        if len(self.checked_domains) >= limit:
            return

        self.checked_domains.add(domain)

        response = self.sf.fetchUrl(
            f'https://{domain}/',
            timeout=15,
            useragent=self.opts['_useragent']
        )

        if not response:
            return

        candidates = []
        headers = response.get('headers') or {}

        header_value = headers.get('onion-location')
        if header_value:
            candidates.append(header_value)

        content = response.get('content')

        if isinstance(content, (str, bytes)) and content:
            soup = BeautifulSoup(content, 'html.parser')

            for tag in soup.find_all('meta'):
                if str(tag.get('http-equiv', '')).strip().lower() == 'onion-location':
                    value = tag.get('content', '')
                    if value:
                        candidates.append(value)

        for raw_url in candidates:
            value = html.unescape(str(raw_url)).strip().strip("'\"")
            parsed = urlparse(value)

            if parsed.scheme not in ('http', 'https'):
                continue

            if not parsed.hostname:
                continue

            if not parsed.hostname.lower().endswith('.onion'):
                continue

            onion_url = parsed.geturl()

            if onion_url in self.results:
                continue

            self.results[onion_url] = True

            evt = SpiderFootEvent(
                'DARKNET_MENTION_URL',
                onion_url,
                self.__name__,
                event
            )

            self.notifyListeners(evt)
            self.info(
                f'Onion-Location advertised by {domain}: {onion_url}'
            )
