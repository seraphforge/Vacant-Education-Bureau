import base64
from urllib.parse import urlsplit, urljoin, parse_qs, urlencode
from bs4 import BeautifulSoup
from app import config
from collectors.base import Collector, Result, SourceError
from analysis.normalizer import clean, normalize_name

SUFFIXES = ('', '家長', '評價', '投訴', '體罰', '虐童', '不當管教', '新聞', '裁罰',
            'site:instagram.com', 'site:threads.net', 'site:x.com')

def queries(name, suffixes=SUFFIXES):
    name = normalize_name(name)
    return [f'"{name}" {suffix}'.strip() for suffix in suffixes]

def unwrap_url(url):
    p = urlsplit(url)
    args = parse_qs(p.query)
    if p.hostname and p.hostname.endswith('bing.com') and p.path.startswith('/ck/'):
        encoded = args.get('u', [''])[0]
        if encoded.startswith('a1'):
            try:
                return base64.urlsafe_b64decode(encoded[2:] + '=' * (-len(encoded[2:]) % 4)).decode()
            except (ValueError, UnicodeError):
                return ''
    if p.hostname and p.hostname.endswith('duckduckgo.com'):
        return args.get('uddg', [url])[0]
    return url

def search(client, query):
    separator = '&' if '?' in config.SEARCH_ENDPOINT else '?'
    url = config.SEARCH_ENDPOINT + separator + urlencode({config.QUERY_PARAM: query})
    response = client.get(url, query)
    if 'json' in response.headers.get('Content-Type', ''):
        payload = response.json()
        rows = payload if isinstance(payload, list) else payload.get('results', [])
        return [dict(title=clean(r.get('title')), content=clean(r.get('snippet', r.get('content', '')))[:500],
                     url=r['url'], query=query) for r in rows[:config.RESULT_LIMIT] if r.get('url')]
    soup = BeautifulSoup(response.content, 'lxml')
    output = []
    for block in soup.select('li.b_algo, .result, article'):
        anchor = block.select_one('h2 a[href], a.result__a[href], h3 a[href]')
        if not anchor:
            continue
        target = unwrap_url(urljoin(response.url, anchor['href']))
        if urlsplit(target).scheme not in ('https', 'http'):
            continue
        snippet = block.select_one('.b_caption p, .result__snippet, p')
        output.append(dict(title=clean(anchor.get_text()), content=clean(snippet.get_text())[:500] if snippet else '',
                           url=target, query=query))
    if not output:
        text = soup.get_text(' ', strip=True).lower()
        if not any(s in text for s in ('no results', '找不到', '沒有結果', '沒有符合', 'did not match')):
            raise SourceError('unavailable', 'Unrecognized search HTML / no parseable public results')
    return output[:config.RESULT_LIMIT]

class GenericWeb(Collector):
    source = 'web'
    suffixes = SUFFIXES
    domains = ()

    def collect(self, school):
        result = Result(self.source)
        query_list = queries(school['official_name'], self.suffixes)[:config.QUERY_BUDGET]
        result.query = ' | '.join(query_list)
        for query in query_list:
            try:
                rows = search(self.client, query)
            except SourceError as exc:
                result.status, result.error = exc.status, str(exc)
                break
            for row in rows:
                host = (urlsplit(row['url']).hostname or '').lower()
                if self.domains and not any(host == d or host.endswith('.' + d) for d in self.domains):
                    continue
                if any(term in (row['title'] + row['content']).lower() for term in
                       ('this account is private', 'these posts are protected', '此帳號為私人', '不公開帳號')):
                    continue
                # Query association is only a candidate; main links an institution only on textual matches.
                row.update(platform=self.source, source_type='public_search_index', verified=False,
                           publisher=host, candidate_kindergarten=school['official_name'])
                if self.domains:
                    path = urlsplit(row['url']).path.strip('/').split('/')[0]
                    row['author'] = path.lstrip('@') if path not in ('p', 'reel', 'explore', 'i', 'search') else ''
                    row['username'] = row['author']
                    row['text'] = row['content']
                    row['description'] = row['content']
                result.items.append(row)
        return result

def public_metadata(client, url):
    """Optional explicit public URL lookup; login/private pages are never parsed."""
    response = client.get(url)
    if '/login' in response.url or '/accounts/' in response.url:
        raise SourceError('blocked', 'Login page')
    soup = BeautifulSoup(response.content, 'lxml')
    text = soup.get_text(' ', strip=True).lower()
    if any(s in text for s in ('this account is private', 'these posts are protected', '此帳號為私人')):
        raise SourceError('blocked', 'Private or protected account')
    def meta(key):
        node = soup.find('meta', property=key) or soup.find('meta', attrs={'name': key})
        return node.get('content', '') if node else ''
    return {'title': meta('og:title'), 'content': meta('og:description') or meta('description'),
            'url': url, 'published_at': meta('article:published_time') or None}
