import ipaddress
import logging
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urljoin
from protego import Protego
import truststore
truststore.inject_into_ssl()
import requests
from app import config

logging.basicConfig(filename=config.ROOT / 'logs/crawler.log', encoding='utf-8', level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('crawler')

class SourceError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)

@dataclass
class Result:
    source: str
    items: list = field(default_factory=list)
    status: str = 'success'
    error: str = ''
    query: str = ''
    started_at: str = ''

class HttpClient:
    """Serializes requests, checks robots, and never replays server cookies."""
    def __init__(self):
        self.lock = threading.RLock()
        self.last = 0.0
        self.robots = {}
        self.denied = {}
        self.cache = {}

    def get_open_data(self, url):
        """Explicitly published machine APIs, governed by their Open Data contract.

        This is not a fallback for blocked HTML. Only the two documented API
        route prefixes are accepted, and any API rejection stops the source.
        """
        allowed = ('https://data.ntpc.gov.tw/api/datasets/', 'https://data.gov.tw/api/v2/rest/dataset/')
        if not url.startswith(allowed):
            raise SourceError('unavailable', 'Not an allowlisted official Open Data API')
        with self.lock:
            host = urlsplit(url).hostname
            if host in self.denied:
                raise SourceError('blocked', self.denied[host])
            response = self._request(url)
            if 300 <= response.status_code < 400:
                raise SourceError('unavailable', 'Open Data API unexpectedly redirected')
            return response

    def _request(self, url, query='', delay=None):
        host = urlsplit(url).hostname or ''
        if urlsplit(url).scheme not in ('https', 'http') or host in ('localhost', ''):
            raise SourceError('unavailable', 'Not a public HTTP URL')
        try:
            if not ipaddress.ip_address(host).is_global:
                raise SourceError('unavailable', 'Non-public IP rejected')
        except ValueError:
            pass
        for attempt in range(3):
            time.sleep(max(0, (delay or config.DELAY) - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                # A fresh request avoids cookie persistence and disables automatic redirects.
                r = requests.get(url, headers={'User-Agent': config.USER_AGENT}, timeout=config.TIMEOUT,
                                 allow_redirects=False)
            except requests.RequestException as exc:
                log.warning('source=%s query=%r HTTP=- result_count=0 error=%s', host, query, type(exc).__name__)
                if attempt == 2:
                    raise SourceError('unavailable', type(exc).__name__) from exc
                time.sleep(2 ** attempt)
                continue
            log.info('source=%s query=%r HTTP=%s result_count=0 error=-', host, query, r.status_code)
            if r.status_code in (401, 403, 429):
                self.denied[host] = f'HTTP {r.status_code}'
                raise SourceError('blocked', self.denied[host])
            if r.status_code >= 500 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise SourceError('unavailable', f'HTTP {r.status_code}')
            if len(r.content) > 40_000_000:
                raise SourceError('unavailable', 'Response exceeds 40 MB')
            if 'text/html' in r.headers.get('Content-Type', ''):
                lower = r.text.lower()
                if any(x in lower for x in ('verify you are human', 'unusual traffic', 'cf-chl-', 'id="captcha"', 'g-recaptcha', 'challenge-form', 'anomaly-modal', '<title>request rejected</title>')):
                    self.denied[host] = 'CAPTCHA / anti-bot challenge'
                    raise SourceError('blocked', self.denied[host])
            return r
        raise SourceError('unavailable', 'Retry exhausted')

    def get(self, url, query='', _redirects=0):
        with self.lock:
            host = urlsplit(url).hostname
            if host in self.denied:
                raise SourceError('blocked', self.denied[host] + ' (circuit open)')
            origin = f'{urlsplit(url).scheme}://{urlsplit(url).netloc}'
            if origin not in self.robots:
                robot_url = origin + '/robots.txt'
                try:
                    response = self._request(robot_url)
                    if 300 <= response.status_code < 400:
                        raise SourceError('unavailable', 'Redirected robots.txt; conservative skip')
                    # RFC 9309: ignore non-rule lines in a successfully fetched file.
                    # Explicit rejection/challenge pages are already blocked in _request.
                    if '<html' in response.text[:500].lower():
                        log.warning('source=%s query=- HTTP=200 result_count=0 error=robots_non_rule_HTML', host)
                    parser = Protego.parse(response.text)
                    self.robots[origin] = parser
                except SourceError as exc:
                    if str(exc) in ('HTTP 404', 'HTTP 410'):
                        parser = Protego.parse('')
                        self.robots[origin] = parser
                    else:
                        self.robots[origin] = exc
                        raise
            parser = self.robots[origin]
            if isinstance(parser, SourceError):
                raise parser
            if not parser.can_fetch(url, config.USER_AGENT):
                raise SourceError('blocked', 'robots.txt disallows URL')
            if url in self.cache:
                return self.cache[url]
            rate = parser.request_rate(config.USER_AGENT)
            rate_delay = rate.seconds / rate.requests if rate and rate.requests else 0
            response = self._request(url, query, max(config.DELAY, parser.crawl_delay(config.USER_AGENT) or 0, rate_delay))
            if 300 <= response.status_code < 400:
                if _redirects >= 5 or not response.headers.get('Location'):
                    raise SourceError('unavailable', 'Invalid or excessive redirect')
                return self.get(urljoin(url, response.headers['Location']), query, _redirects + 1)
            self.cache[url] = response
            return response

class Collector:
    source = 'unknown'
    def __init__(self, client):
        self.client = client

    def run(self, *args, **kwargs):
        from analysis.normalizer import now
        started = now()
        try:
            result = self.collect(*args, **kwargs)
        except SourceError as exc:
            result = Result(self.source, status=exc.status, error=str(exc))
        except Exception as exc:
            # Avoid including request URLs or credentials from arbitrary exceptions.
            log.error('source=%s collector failure error=%s', self.source, type(exc).__name__)
            result = Result(self.source, status='unavailable', error=type(exc).__name__)
        result.started_at = started
        log.info('source=%s query=%r HTTP=- result_count=%d error=%s status=%s',
                 result.source, result.query, len(result.items), result.error, result.status)
        return result
