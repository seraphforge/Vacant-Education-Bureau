import json
from app import config
from collectors.base import Collector, Result, SourceError
from collectors.government.moe_kindergarten import records, school_record
from analysis.normalizer import now

def fetch_pages(client, dataset_id):
    # Stable dataset identifier + documented OpenAPI route, never a versioned file URL.
    base = f'https://data.ntpc.gov.tw/api/datasets/{dataset_id}/json'
    output, seen = [], set()
    for page in range(config.MAX_PAGES):
        url = f'{base}?page={page}&size=1000'
        batch = records(client.get_open_data(url))
        signature = json.dumps(batch, ensure_ascii=False, sort_keys=True)
        if batch and signature in seen:
            raise SourceError('unavailable', 'Pagination repeated; refusing silent truncation')
        seen.add(signature)
        output.extend(batch)
        if len(batch) < 1000:
            return output, base
    raise SourceError('unavailable', 'Pagination safety cap reached; increase MAX_PAGES')

class NtpcKindergarten(Collector):
    source = 'ntpc_kindergarten'
    def collect(self):
        rows, url = fetch_pages(self.client, config.NTPC_ID)
        if not rows or 'title' not in rows[0]:
            raise SourceError('unavailable', 'NTPC schema changed: missing title')
        (config.DATA / 'raw/ntpc_kindergarten.json').write_text(json.dumps(
            {'source_url': url, 'collected_at': now(), 'records': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
        output = [school_record(r['title'], '新北市', r.get('district', ''), r.get('address', ''),
                  r.get('tel', ''), url, type=r.get('type', ''), areacode=r.get('areacode', ''),
                  zipcode=r.get('zipcode', ''), dataset_url=f'https://data.ntpc.gov.tw/datasets/{config.NTPC_ID}') for r in rows]
        return Result(self.source, output)
