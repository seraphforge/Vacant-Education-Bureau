import json
import pytest
from analysis.normalizer import normalize_name, aliases, normalize_item
from analysis.risk_analyzer import analyze
from analysis.deduplicator import content_hash
from analysis.keyword_filter import match_schools
from database.db import connect
from database.repository import Repository
from collectors.government.moe_kindergarten import school_record, resource_links
from collectors.base import HttpClient, SourceError
from collectors.social.instagram import Instagram
from main import ingest, parser
from analysis.relevance_filter import evaluate

@pytest.mark.parametrize('name,expected', [
    ('新北市私立ABC幼兒園', 'ABC幼兒園'), ('新北市立ABC幼兒園', 'ABC幼兒園'),
    ('財團法人XXX附設ABC幼兒園', 'ABC幼兒園'), ('臺北市私立ABC幼兒園', 'ABC幼兒園')])
def test_names(name, expected):
    assert normalize_name(name) == expected
    assert 'ABC kindergarten' in aliases(name)

def make_item(**kwargs):
    data = dict(url='https://example.org/a', title='幼兒園遭投訴體罰；園方否認', content='待調查',
                platform='news', source_type='rss', verified=True)
    data.update(kwargs)
    return analyze(normalize_item(data))

def test_allegations_not_verified():
    item = make_item()
    assert item['verified'] is False
    assert item['risk_score'] > 0
    assert '疑似體罰' in item['risk_tags']

def test_official_verification_needs_provenance():
    assert make_item(url='https://data.ntpc.gov.tw/api/test', platform='government',
                     source_type='government_open_data')['verified'] is True
    assert make_item(url='https://example.org/api', platform='government',
                     source_type='government_open_data')['verified'] is False

def test_dedup_and_foreign_keys(tmp_path):
    conn = connect(tmp_path / 'test.db')
    repo = Repository(conn)
    item = make_item()
    assert repo.save_item(item) == 1
    assert repo.save_item(item) == 0
    other = make_item(url='https://another.org/b')
    assert repo.save_item(other) == 0
    assert conn.execute('SELECT count(*) FROM items').fetchone()[0] == 1
    assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    conn.close()

def test_hash_includes_institution():
    assert content_hash(make_item(kindergarten_name='甲幼兒園')) != content_hash(make_item(kindergarten_name='乙幼兒園'))

def test_url_tracking_removed():
    assert make_item(url='https://example.org/a?utm_source=x&doc=12#top')['url'] == 'https://example.org/a?doc=12'

def test_school_provenance_merge(tmp_path):
    conn = connect(tmp_path / 'test.db')
    repo = Repository(conn)
    a = school_record('新北市私立ABC幼兒園', '新北市', '板橋區', '地址', '02', 'https://stats.moe.gov.tw/data.json', code='123')
    b = school_record('新北市私立ABC幼兒園', '新北市', '板橋區', '地址2', '03', 'https://data.ntpc.gov.tw/api')
    repo.save_schools([a, b])
    assert len(repo.schools()) == 1
    assert repo.schools()[0]['code'] == '123'
    assert len(json.loads(repo.schools()[0]['metadata'])['sources']) == 2
    conn.close()

def test_ambiguous_school_names():
    schools = [dict(official_name='新北市私立ABC幼兒園'), dict(official_name='臺北市私立ABC幼兒園')]
    assert len(match_schools('ABC幼兒園家長', schools)) == 2

def test_operator_annotation_removed_from_query_name():
    assert normalize_name('新北市ABC非營利幼兒園（委託財團法人XXX辦理）') == 'ABC非營利幼兒園'

def test_resource_discovery_uses_page():
    urls = resource_links('<a href="https://stats.moe.gov.tw/current.json">JSON</a>', 'https://data.gov.tw/dataset/6086')
    assert urls == ['https://stats.moe.gov.tw/current.json']

class Response:
    def __init__(self, status=200, text='', headers=None):
        self.status_code, self.text = status, text
        self.content = text.encode()
        self.headers = headers or {'Content-Type': 'text/plain'}

def test_robots_disallow(monkeypatch):
    client = HttpClient()
    calls = []
    def request(url, *args):
        calls.append(url)
        return Response(text='User-agent: *\nDisallow: /search')
    monkeypatch.setattr(client, '_request', request)
    with pytest.raises(SourceError, match='robots'):
        client.get('https://example.org/search?q=a')
    assert calls == ['https://example.org/robots.txt']

def test_block_circuit_no_retry(monkeypatch):
    import collectors.base as base
    client, calls = HttpClient(), []
    def request(*args, **kwargs):
        calls.append(args[0])
        return Response(status=429)
    monkeypatch.setattr(base.requests, 'get', request)
    with pytest.raises(SourceError):
        client.get('https://example.org/search')
    with pytest.raises(SourceError):
        client.get('https://example.org/search?q=b')
    assert len(calls) == 1

def test_collector_survives_block():
    class Blocked:
        def get(self, *args):
            raise SourceError('blocked', 'robots.txt disallows URL')
    result = Instagram(Blocked()).run({'official_name': '新北市私立ABC幼兒園'})
    assert result.status == 'blocked'
    assert not result.items
    assert result.query

def test_cli_limits():
    assert parser().parse_args(['crawl']).limit == 10
    with pytest.raises(SystemExit):
        parser().parse_args(['crawl', '--workers', '6'])
    with pytest.raises(SystemExit):
        parser().parse_args(['crawl', '--limit', '0'])

def test_robots_wildcards_and_precedence(monkeypatch):
    client = HttpClient()
    def request(url, *args):
        return Response(text='User-agent: *\nDisallow: /*/private$\nDisallow: /root\nAllow: /root/public')
    monkeypatch.setattr(client, '_request', request)
    with pytest.raises(SourceError):
        client.get('https://example.org/a/private')
    assert client.get('https://example.org/root/public').status_code == 200

def test_open_data_allowlist():
    with pytest.raises(SourceError, match='allowlisted'):
        HttpClient().get_open_data('https://example.org/api/private')

def test_markdown_escapes_untrusted_text():
    from reports.markdown import escape
    assert '<script>' not in escape('<script>alert(1)</script>')
    assert escape('[fake](x)').startswith('\\[')

def test_relevance_gate_rejects_unrelated_penalty():
    schools = [dict(official_name='新北市私立ABC幼兒園')]
    decision = evaluate('金門2養生館涉妨害風化，依社維法裁罰', schools)
    assert decision.relevant is False
    assert decision.location_valid is False

def test_relevance_gate_requires_new_taipei_location():
    schools = [dict(official_name='新北市私立ABC幼兒園')]
    assert evaluate('南投縣幼兒園舉辦活動', schools).relevant is False
    decision = evaluate('板橋幼兒園教保員研習', schools)
    assert decision.relevant is True
    assert decision.location_valid is True

def test_known_scoped_alias_passes_gate():
    school = dict(official_name='新北市私立ABC幼兒園')
    decision = evaluate('ABC幼兒園家長反映問題', [school])
    assert decision.relevant is True
    assert decision.matches == (school,)

def test_ingest_does_not_analyze_or_save_irrelevant(tmp_path):
    from collectors.base import Result
    conn = connect(tmp_path / 'test.db')
    repo = Repository(conn)
    result = Result('rss:test', [dict(platform='news', source_type='rss', publisher='test',
        title='金門2養生館涉妨害風化', content='社維法裁罰', url='https://example.org/unrelated',
        verified=False)])
    ingest(repo, result, [dict(id='s1', official_name='新北市私立ABC幼兒園')], 'test', '新北市')
    assert conn.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 0
    conn.close()

def test_report_scope_lists_only_selected_schools(tmp_path):
    from reports.markdown import generate
    conn = connect(tmp_path / 'test.db')
    repo = Repository(conn)
    ntpc = [school_record(f'新北市私立第{i}幼兒園', '新北市', '板橋區', '新北市板橋區', '02', 'https://data.ntpc.gov.tw/api') for i in range(3)]
    nantou = school_record('南投縣私立錯誤幼兒園', '南投縣', '南投市', '南投縣', '049', 'https://stats.moe.gov.tw/data.json')
    repo.save_schools(ntpc + [nantou])
    repo.save_report_scope('新北市', None, None, 2, ntpc[:2])
    text = generate(repo, output_dir=tmp_path / 'reports').read_text(encoding='utf-8')
    assert '南投縣' not in text
    assert '錯誤幼兒園' not in text
    assert text.count('官方名稱：') == 2
    assert '全台母資料庫：4' in text
    assert '新北市幼兒園：3' in text
    assert '本次監測：2' in text
    conn.close()
