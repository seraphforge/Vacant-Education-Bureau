from datetime import datetime, timezone
import calendar
import feedparser
import json
import re
from app import config
from collectors.base import Collector, Result, SourceError
from analysis.normalizer import clean
from analysis.normalizer import now

class RSS(Collector):
    def __init__(self, client, publisher, url):
        super().__init__(client)
        self.publisher, self.url = publisher, url
        self.source = 'rss:' + publisher

    def collect(self):
        response = self.client.get(self.url)
        feed = feedparser.parse(response.content)
        if feed.bozo and not feed.entries:
            raise SourceError('unavailable', 'Invalid RSS / Atom response')
        output = []
        for entry in feed.entries:
            title, summary = clean(entry.get('title')), clean(entry.get('summary'))[:300]
            if not entry.get('link'):
                continue
            parsed = entry.get('published_parsed') or entry.get('updated_parsed')
            date = datetime.fromtimestamp(calendar.timegm(parsed), timezone.utc).isoformat() if parsed else None
            output.append(dict(platform='news', source_type='rss', publisher=self.publisher,
                title=title, content=summary, summary=summary, url=entry.link, published_at=date,
                query='幼兒園 / 幼童 / 教保 / 托育等主題', verified=False, feed_url=self.url))
        safe_name = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff_-]+', '_', self.publisher)
        (config.DATA / 'raw' / f'rss_{safe_name}.json').write_text(json.dumps(
            {'feed_url': self.url, 'collected_at': now(), 'entries': output},
            ensure_ascii=False, indent=2), encoding='utf-8')
        return Result(self.source, output)
