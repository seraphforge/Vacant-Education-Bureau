"""Render local, escaped Markdown with provenance and collection failures."""
import html
import re
from collections import Counter
from datetime import datetime
from urllib.parse import quote
from app.config import DATA

NOTICE = '風險分數只代表「需要人工關注程度」，不是違法機率，也不是已證實事件。關鍵字可能出現在否認、澄清或一般政策報導中；需人工閱讀原始來源。verified 只表示政府公開資料來源可核對，不表示指控已證實。'

def escape(value):
    return re.sub(r'([\\`*_{}\[\]#|])', r'\\\1', html.escape(str(value or ''), quote=False).replace('\n', ' '))

def link(url):
    return '[原始來源](<' + quote(str(url), safe=':/?&=%+#@;,') + '>)' if url else '未提供'

def detail(item):
    return [f"#### {escape(item['platform'])}", '', f"標題：{escape(item['title'])}", '',
            f"作者／來源：{escape(item['author'] or item['publisher'])}", '',
            f"內容摘要：{escape(item['content']) or '來源未提供摘要'}", '',
            f"日期：{escape(item['published_at']) or '來源未提供'}", '',
            f"URL：{link(item['url'])}", '',
            f"驗證：{'政府公開資料（不推定指控成立）' if item['verified'] else '未驗證'}", '']

def generate(repo, scope=None, output_dir=None):
    master_count = len(repo.schools())
    saved_scope = repo.report_scope()
    if saved_scope:
        city = saved_scope['city']
        city_schools = repo.schools(city=city)
        selected_ids = set(saved_scope['selected_ids'])
        schools = [s for s in city_schools if s['id'] in selected_ids]
        scope_label = (f"city={city}；district={saved_scope['district'] or '全部'}；"
                       f"kindergarten={saved_scope['kindergarten'] or '全部'}；"
                       f"limit={saved_scope['requested_limit']}；本次監測={saved_scope['selected_count']}")
    else:
        city, city_schools, schools = '', [], []
        scope_label = scope or '尚無 crawl scope；請先執行 crawl'
    all_items = repo.items()
    # Formal report data must carry the relevance/location decision for this city.
    items = [i for i in all_items if i.get('relevant') is True and i.get('location_valid') is True
             and i.get('analysis_city') == city]
    runs = [dict(r) for r in repo.conn.execute('SELECT * FROM crawl_runs ORDER BY id DESC')]
    counts = Counter(i['platform'] for i in items)
    stamp = datetime.now().astimezone()
    lines = ['# 幼兒園公開資訊與輿情蒐集報告', '', f'產生時間：{stamp.isoformat(timespec="seconds")}', '',
             f'資料範圍：{escape(scope_label)}', '',
             '資料來源：教育部、新北市 Open Data、CNA RSS、公開搜尋索引（以執行紀錄為準）', '', NOTICE, '',
             '## 統計', '', f'全台母資料庫：{master_count}', '',
             f'{escape(city) or "本次城市"}幼兒園：{len(city_schools)}', '',
             f'本次監測：{len(schools)}', '', f'本 scope 正式資料：{len(items)}', '',
             f"政府公告：{counts['government']}", '']
    for label, key in [('新聞', 'news'), ('Instagram', 'instagram'), ('Threads', 'threads'), ('X', 'x'), ('一般網頁', 'web')]:
        lines += [f'{label}：{counts[key]}', '']
    lines += ['## 高關注項目', '']
    high = [i for i in items if i['risk_score'] > 0]
    for item in high[:100]:
        lines += [f"### {escape(item['kindergarten_name']) or '未確認所屬幼兒園（一般議題／名稱待人工核對）'}", '',
                  f"風險分數：{item['risk_score']}", '', '標籤：', '',
                  *['* ' + escape(t) for t in item['risk_tags']], '', '來源：', ''] + detail(item)
    if not high:
        lines += ['沒有關鍵字命中；不代表沒有相關事件或風險。', '']
    lines += ['## 政府公開資料', '']
    for s in schools:
        lines += [f"### {escape(s['official_name'])}", '', f"官方名稱：{escape(s['official_name'])}", '',
                  f"行政區：{escape(s['city'] + s['district'])}", '', f"地址：{escape(s['address'])}", '',
                  f"電話：{escape(s['phone'])}", '', f"來源：{link(s['source'])}", '']
    for heading, platforms in [('政府公告', ('government',)), ('News', ('news',)),
                               ('Social Signals', ('instagram', 'threads', 'x')), ('Trend Alerts', ('web',))]:
        lines += ['## ' + heading, '']
        subset = sorted([i for i in items if i['platform'] in platforms], key=lambda i: i['published_at'] or i['collected_at'], reverse=True)
        for i in subset[:100]:
            lines += detail(i)
        if not subset:
            lines += ['未取得符合條件的公開資料；請參考來源執行狀態。', '']
        if len(subset) > 100:
            lines += [f'列前 100 筆，完整 {len(subset)} 筆保留於 SQLite。', '']
    lines += ['## Crawl errors', '', '最近一次各來源／查詢狀態；success 且 0 筆不保證沒有相關討論。', '']
    seen = set()
    for r in runs:
        key = (r['source'], r['query'])
        if key in seen:
            continue
        seen.add(key)
        lines += [f"* {escape(r['source'])}: {escape(r['status'])}；找到 {r['items_found']}／新增 {r['items_inserted']}；{escape(r['error'])}；查詢 {escape(r['query'])}；時間 {escape(r['finished_at'])}"]
    report_dir = output_dir or (DATA / 'reports')
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / (stamp.strftime('%Y-%m-%d_%H-%M') + '.md')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path
