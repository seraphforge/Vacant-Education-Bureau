import csv
import hashlib
import io
import json
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from app import config
from collectors.base import Collector, Result, SourceError
from analysis.normalizer import normalize_name, now, compact

def resource_links(html, base):
    soup = BeautifulSoup(html, 'lxml')
    links = [urljoin(base, a['href']) for a in soup.select('a[href]')
             if re.search(r'json|csv', a.get_text() + a['href'], re.I)]
    # Nuxt/JSON-LD serialized metadata may contain links without rendered anchors.
    decoded = html.replace('\\u002F', '/').replace('\\/', '/')
    links += re.findall(r'https?://[^\s"<>\\]+(?:\.json|\.csv)(?:\?[^\s"<>\\]*)?', decoded, re.I)
    return list(dict.fromkeys(links))

def records(response):
    for encoding in ('utf-8-sig', 'cp950'):
        try:
            text = response.content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SourceError('unavailable', 'Unsupported dataset encoding')
    if text.lstrip().startswith(('[', '{')):
        value = json.loads(text)
        if isinstance(value, list):
            return value
        for key in ('data', 'result', 'records'):
            if isinstance(value.get(key), list):
                return value[key]
        raise SourceError('unavailable', 'Unrecognized JSON dataset schema')
    if '<html' in text[:500].lower() or '<!doctype' in text[:500].lower():
        raise SourceError('unavailable', 'Dataset returned HTML')
    return list(csv.DictReader(io.StringIO(text)))

def school_record(name, city, district, address, phone, source, **extra):
    name = name.strip()
    city = re.sub(r'^\[?\d+\]?', '', city).strip()
    district = re.sub(r'^\[?\d+\]?', '', district).strip()
    key = compact(city + district + name)
    return dict(id=hashlib.sha256(key.encode()).hexdigest(), official_name=name,
                normalized_name=normalize_name(name), name=name, city=city, district=district,
                address=address, phone=phone, source=source, updated_at=now(), **extra)

class MoeKindergarten(Collector):
    source = 'moe_kindergarten'
    def collect(self):
        try:
            metadata = self.client.get_open_data('https://data.gov.tw/api/v2/rest/dataset/6086')
            urls = resource_links(metadata.text, config.MOE_DATASET)
        except SourceError as exc:
            if exc.status == 'blocked':
                raise
            urls = []
        if not urls:
            page = self.client.get(config.MOE_DATASET)
            urls = resource_links(page.text, config.MOE_DATASET)
        urls.sort(key=lambda u: ('.json' not in u.lower(), u), reverse=False)
        if not urls:
            raise SourceError('unavailable', 'No current JSON/CSV resource in dataset metadata')
        errors = []
        for url in urls:
            try:
                response = self.client.get(url)
                rows = records(response)
                if not rows or not any(k in rows[0] for k in ('學校名稱', '名稱')):
                    raise SourceError('unavailable', 'MOE schema changed: missing school name')
                years = [str(r.get('學年度', '')) for r in rows]
                newest = max(years, key=lambda y: int(y) if y.isdigit() else 0)
                output = []
                for r in rows:
                    if str(r.get('學年度', '')) != newest:
                        continue
                    output.append(school_record(r.get('學校名稱', r.get('名稱', '')),
                        r.get('縣市名稱', ''), r.get('鄉鎮市區名稱', ''), r.get('地址', ''), r.get('電話', ''),
                        response.url, school_year=r.get('學年度', ''), code=r.get('代碼', ''),
                        public_private=r.get('公/私立', ''), dataset_url=config.MOE_DATASET))
                (config.DATA / 'raw/moe_kindergarten.json').write_text(json.dumps(
                    {'source_url': response.url, 'dataset_url': config.MOE_DATASET, 'collected_at': now(), 'records': rows},
                    ensure_ascii=False, indent=2), encoding='utf-8')
                return Result(self.source, output)
            except (SourceError, ValueError) as exc:
                errors.append(f'{type(exc).__name__}: {exc}')
        raise SourceError('unavailable', '; '.join(errors))
