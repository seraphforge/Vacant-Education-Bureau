import json
from urllib.parse import urlencode
from app import config
from collectors.base import Collector, Result
from collectors.government.ntpc_kindergarten import fetch_pages
from analysis.normalizer import now

WORDS = ('幼兒園', '幼兒', '教保', '教保員', '托育', '兒童', '體罰', '不當管教', '裁罰')

class NtpcAnnouncements(Collector):
    source = 'ntpc_announcements'
    def collect(self):
        rows, base = fetch_pages(self.client, config.ANNOUNCEMENTS_ID)
        output = []
        for row in rows:
            r = {k.lower(): v for k, v in row.items()}
            title = str(r.get('bbssubject', ''))
            if not any(w in title for w in WORDS):
                continue
            document = str(r.get('bbsid', ''))
            source_url = base
            attachment = str(r.get('bbsfileurl', '') or '')
            item_url = attachment if attachment.startswith(('https://', 'http://')) else source_url
            output.append(dict(platform='government', source_type='government_open_data',
                publisher=r.get('bbspublishdept', ''), department=r.get('bbspublishdept', ''),
                title=title, subject=title, content='', url=item_url, source_url=source_url,
                document_id=document, attachment_url=r.get('bbsfileurl', ''),
                published_at=r.get('bbssenddate'), date=r.get('bbssenddate'), verified=True,
                query=' OR '.join(WORDS), collected_at=now()))
        (config.DATA / 'raw/ntpc_announcements.json').write_text(json.dumps(
            {'source_url': base, 'collected_at': now(), 'records': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
        return Result(self.source, output)
