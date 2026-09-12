"""Local public kindergarten intelligence; Python 3.11+."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import sys
from app import config
from collectors.base import HttpClient, Result
from collectors.government.moe_kindergarten import MoeKindergarten
from collectors.government.ntpc_kindergarten import NtpcKindergarten
from collectors.government.ntpc_announcements import NtpcAnnouncements
from collectors.news.rss import RSS
from collectors.news.news_search import NewsSearch
from collectors.social.instagram import Instagram
from collectors.social.threads import Threads
from collectors.social.x import X
from collectors.web.generic import GenericWeb
from analysis.normalizer import normalize_item, now
from analysis.relevance_filter import evaluate
from analysis.risk_analyzer import analyze
from database.db import connect
from database.repository import Repository
from reports.markdown import generate

def ingest(repo, result, schools, scope, city='新北市'):
    inserted, normalized, errors, rejected = 0, [], [], 0
    for raw in result.items:
        try:
            item = normalize_item(raw)
            text = item['title'] + ' ' + item['content']
            official_ntpc = item['platform'] == 'government' and item['source_type'] == 'government_open_data'
            decision = evaluate(text, schools, city=city, official_new_taipei=official_ntpc)
            if not decision.relevant or not decision.location_valid:
                rejected += 1
                continue
            matches = decision.matches
            if len(matches) == 1:
                item['kindergarten_id'] = matches[0]['id']
                item['kindergarten_name'] = matches[0]['official_name']
                item['match_basis'] = 'unique textual alias; human review required'
            else:
                item['kindergarten_id'] = None
                item['kindergarten_name'] = ''
                item['candidate_matches'] = [s['id'] for s in matches]
            item['relevant'] = True
            item['location_valid'] = True
            item['analysis_city'] = city
            item['relevance_reason'] = decision.reason
            analyze(item)
            inserted += repo.save_item(item)
            normalized.append(item)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(type(exc).__name__)
    if errors:
        result.status = 'partial'
        result.error += ' Invalid records: ' + ', '.join(errors)
    repo.record_run(result, inserted, scope)
    if normalized:
        safe = result.source.replace(':', '_').replace(' ', '_')
        path = config.DATA / 'normalized' / (safe + '_' + now().replace(':', '-') + '.json')
        # Only permitted snippets/metadata, never social HTML or full news articles.
        path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{result.source}: {result.status}; raw={len(result.items)} relevant={len(normalized)} rejected={rejected} inserted={inserted} {result.error}', flush=True)

def gov(repo, client):
    for cls in (MoeKindergarten, NtpcKindergarten):
        result = cls(client).run()
        count = repo.save_schools(result.items) if result.items else 0
        repo.record_run(result, count, '政府名錄：全台／新北')
        print(f'{result.source}: {result.status}; records={count} {result.error}', flush=True)
    repo.export_keywords(config.DATA / 'kindergarten_keywords.json')
    ingest(repo, NtpcAnnouncements(client).run(), repo.schools(city='新北市'), '政府公告：新北市', '新北市')

def bounded(minimum, maximum):
    def parse(value):
        number = int(value)
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f'must be {minimum}..{maximum}')
        return number
    return parse

def parser():
    cli = argparse.ArgumentParser(description='幼兒園公開資訊與輿情蒐集；全部資料存於本機')
    commands = cli.add_subparsers(dest='command', required=True)
    for name in ('gov', 'crawl', 'news', 'social', 'report', 'status'):
        cmd = commands.add_parser(name)
        if name in ('crawl', 'news', 'social'):
            cmd.add_argument('--city', default=None)
            cmd.add_argument('--district')
            cmd.add_argument('--kindergarten')
            cmd.add_argument('--limit', type=bounded(1, 100000), default=10)
            cmd.add_argument('--workers', type=bounded(1, 5), default=1)
    return cli

def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    args = parser().parse_args()
    conn = connect()
    repo, client = Repository(conn), HttpClient()
    try:
        if args.command == 'status':
            print(f'kindergartens: {conn.execute("SELECT COUNT(*) FROM kindergartens").fetchone()[0]}')
            print(f'ntpc kindergartens: {len(repo.schools(city="新北市"))}')
            print(f'items: {conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]}')
            for platform in ('news', 'instagram', 'threads', 'x'):
                print(f'{platform}: {conn.execute("SELECT COUNT(*) FROM items WHERE platform=?", (platform,)).fetchone()[0]}')
            last = conn.execute('SELECT finished_at,source,status FROM crawl_runs ORDER BY id DESC LIMIT 1').fetchone()
            print(f'last crawl: {dict(last) if last else "none"}')
            print(f'database size: {config.DB_PATH.stat().st_size:,} bytes')
            print(f'database: {config.DB_PATH}')
            return 0
        if args.command == 'report':
            print(generate(repo))
            return 0
        if args.command == 'gov':
            gov(repo, client)
            print(generate(repo, '政府名錄全台；公告新北'))
            return 0 if repo.schools() else 1
        if not repo.schools():
            gov(repo, client)
        removed = repo.prune_items_without_relevance_gate()
        if removed:
            print(f'pruned pre-gate formal items: {removed}', flush=True)
        city = args.city or (None if args.kindergarten else '新北市')
        selected = repo.schools(city, args.district, args.kindergarten, args.limit)
        scoped_schools = repo.schools(city, args.district)
        repo.save_report_scope(city or '', args.district, args.kindergarten, args.limit, selected)
        scope = f'{city or "全台"} {args.district or ""} {args.kindergarten or ""}；選取 {len(selected)} 間（limit={args.limit}）；RSS 為全台近期議題'
        print(scope, flush=True)
        if not selected:
            print('沒有符合條件的幼兒園，名稱搜尋略過；news/crawl 仍讀取 RSS。', flush=True)
        if args.command == 'crawl':
            ingest(repo, NtpcAnnouncements(client).run(), scoped_schools, scope, city)
        if args.command in ('crawl', 'news'):
            for publisher, url in config.RSS_FEEDS.items():
                ingest(repo, RSS(client, publisher, url).run(), scoped_schools, scope, city)
        classes = []
        if args.command in ('crawl', 'news'):
            classes += [NewsSearch]
        if args.command in ('crawl', 'social'):
            classes += [Instagram, Threads, X]
        if args.command == 'crawl':
            classes += [GenericWeb]
        jobs = [(cls, school) for school in selected for cls in classes]
        def execute(job):
            cls, school = job
            return cls(client).run(school)
        # A shared HTTP lock keeps outbound traffic serial even with multiple processing workers.
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for result in pool.map(execute, jobs):
                ingest(repo, result, scoped_schools, scope, city)
        print(generate(repo))
        return 0
    finally:
        conn.close()

if __name__ == '__main__':
    raise SystemExit(main())
