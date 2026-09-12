import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from bs4 import BeautifulSoup

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def clean(value):
    return re.sub(r'\s+', ' ', BeautifulSoup(str(value or ''), 'html.parser').get_text(' ', strip=True)).strip()

def compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).casefold().replace('臺', '台')

def normalize_name(name):
    name = unicodedata.normalize('NFKC', name).strip()
    name = re.sub(r'\((?:委託|委由|由).*?\)$', '', name).strip()
    name = re.sub(r'^.*?附設(?=.+幼兒園)', '', name) if name.startswith(('財團法人', '社團法人')) else name
    name = re.sub(r'^(?:臺|台)灣省', '', name)
    name = re.sub(r'^.{2,3}[縣市](?:私立|立|公立)?', '', name)
    return re.sub(r'^(?:私立|公立)', '', name).strip()

def aliases(name):
    short = normalize_name(name)
    stem = re.sub(r'幼兒園$', '', short)
    return list(dict.fromkeys([short, stem + ' 幼兒園', stem + ' kindergarten', name]))

def canonical_url(url):
    p = urlsplit(str(url).strip())
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Invalid public source URL')
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', urlencode(query), ''))

def normalize_item(item):
    result = {key: '' for key in ('id', 'kindergarten_name', 'platform', 'source_type', 'publisher', 'author', 'title', 'content', 'url', 'query')}
    result.update(item)
    for key in ('title', 'content', 'author', 'publisher', 'kindergarten_name'):
        result[key] = clean(result.get(key))
    result['content'] = result['content'][:500]
    result['url'] = canonical_url(result['url'])
    result['id'] = hashlib.sha256(result['url'].encode()).hexdigest()
    result['published_at'] = result.get('published_at') or None
    result['collected_at'] = result.get('collected_at') or now()
    result['risk_score'] = 0
    result['risk_tags'] = []
    # Collector provenance, never a statement in scraped text, controls verification.
    host = urlsplit(result['url']).hostname or ''
    result['verified'] = bool(result.get('verified') and result['platform'] == 'government'
                              and result['source_type'] == 'government_open_data'
                              and (host.endswith('.gov.tw') or host.endswith('.edu.tw')))
    return result
