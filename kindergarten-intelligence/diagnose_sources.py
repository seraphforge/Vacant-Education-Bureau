"""Read-only transport diagnostics; no cookies, auth, or request bypass."""
import time
import truststore
truststore.inject_into_ssl()
import requests
from app.config import USER_AGENT

if __name__ == '__main__':
    for url in ('https://data.gov.tw/robots.txt', 'https://data.ntpc.gov.tw/robots.txt',
                'https://www.bing.com/robots.txt', 'https://feeds.feedburner.com/robots.txt'):
        try:
            r = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=25, allow_redirects=False)
            print(url, r.status_code, r.headers.get('Content-Type'), repr(r.text[:1800]), flush=True)
        except requests.RequestException as e:
            print(url, type(e).__name__, flush=True)
        time.sleep(1.2)
