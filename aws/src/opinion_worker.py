"""輿情分析 worker —— 由 opinion.py 非同步 invoke，實際去蒐集與判讀。

事件格式：{"jobId": 123}

為什麼是多來源而不是只打一個搜尋引擎
------------------------------------
實測（2026-09-13）：
  * Bedrock 內建 Web Search tool 只支援 openai.gpt-5.6-* 系列，本帳號無權限，
    且 bedrock-websearch 不在黑客松允許服務清單內。
  * www.bing.com/robots.txt 明確 Disallow: /search。
  * lite.duckduckgo.com/lite/ robots 沒有禁止，可以用，但連續請求幾次後會回
    HTTP 202 的 anomaly/challenge 頁 —— 也就是說單一搜尋引擎「不可靠」。
  * news.google.com/rss/search 是唯一能用園名精準搜到新聞的來源，但它的
    robots.txt 對 * 是 Disallow: /。專案負責人決定為了驗證概念小量使用，
    細節與限制寫在下面 GOOGLE_NEWS_ENABLED 附近。

所以這裡採用 kindergarten-intelligence/README.md 的作法：多來源、單一來源失敗
不阻擋整體結果，每個來源的 success / blocked / unavailable 都記下來回給前端。
對政府端來說「哪些來源查得到、哪些被擋」本身就是要交代的資訊。

判讀規則的來源
--------------
TOPIC_WORDS 與 RULES 直接對應 kindergarten-intelligence/analysis/
（keyword_filter.py、risk_analyzer.py）。改規則請兩邊一起改。

資料語意（與 004_opinion.sql 一致）
-----------------------------------
  * 全部項目都是「需要人工關注的線索」，不是已證實的事實。
  * verified=1 只給 .gov.tw / .edu.tw 的官方公開資料，且只代表來源可核對。
  * attribution：confirmed（文中有可比對的園所全名）/ ambiguous / unrelated。
    只有 confirmed 的負面項目才計入 opinion_score。
"""

import datetime
import hashlib
import html
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from common import get_conn, utcnow

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
USER_AGENT = os.environ.get(
    "OPINION_USER_AGENT",
    "NtpcKindergartenOpinion/1.0 (government oversight research; no login)",
)
REQUEST_DELAY = max(1.0, float(os.environ.get("OPINION_REQUEST_DELAY", "1.2")))
REQUEST_TIMEOUT = max(5, int(os.environ.get("OPINION_REQUEST_TIMEOUT", "20")))

SEARCH_ENDPOINT = os.environ.get(
    "OPINION_SEARCH_ENDPOINT", "https://lite.duckduckgo.com/lite/"
)
SEARCH_QUERY_PARAM = os.environ.get("OPINION_SEARCH_QUERY_PARAM", "q")
SEARCH_QUERIES = max(1, min(5, int(os.environ.get("OPINION_SEARCH_QUERIES", "3"))))
SEARCH_RESULTS = max(1, min(20, int(os.environ.get("OPINION_SEARCH_RESULTS", "8"))))

NTPC_ANNOUNCEMENT_DATASET = os.environ.get(
    "OPINION_NTPC_DATASET", "EAAC9944-2CCB-4DBB-B616-441128E17A4A"
)
RSS_FEEDS = {
    "中央社 社會": "https://feeds.feedburner.com/rsscna/social",
    "中央社 地方": "https://feeds.feedburner.com/rsscna/local",
}

# Google News RSS：唯一能用園名精準搜到新聞的來源，Demo 需要它才看得到內容。
#
# 這是一個「明知故犯」的例外：news.google.com 的 robots.txt 對 * 是
# Disallow: /，/rss/search 不在 Allow 白名單內。專案負責人在 2026-09-13 決定
# 為了驗證概念接受這個取捨，條件是「小量」，所以：
#   * 每次掃描最多 GOOGLE_NEWS_QUERIES 個查詢（預設 2），每查詢最多 8 筆
#   * 沿用全域的請求間隔，不併發
#   * 401/403/429 與 anti-bot 頁面照樣立刻停止，不重試、不換入口
# 要關掉就把 OPINION_GOOGLE_NEWS 設成 0；要移除例外就清空
# OPINION_ROBOTS_EXEMPT_HOSTS。其他來源的 robots 檢查完全不受影響。
GOOGLE_NEWS_ENABLED = os.environ.get("OPINION_GOOGLE_NEWS", "1") not in ("0", "", "false")
GOOGLE_NEWS_QUERIES = max(1, min(4, int(os.environ.get("OPINION_GOOGLE_NEWS_QUERIES", "2"))))
GOOGLE_NEWS_RESULTS = max(1, min(20, int(os.environ.get("OPINION_GOOGLE_NEWS_RESULTS", "8"))))
ROBOTS_EXEMPT_HOSTS = tuple(
    host.strip().lower()
    for host in os.environ.get("OPINION_ROBOTS_EXEMPT_HOSTS", "news.google.com").split(",")
    if host.strip()
)

MODEL_ID = os.environ.get(
    "OPINION_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)
MAX_ITEMS = max(5, min(60, int(os.environ.get("OPINION_MAX_ITEMS", "36"))))

# 話題相關字（對應 analysis/keyword_filter.py 的 TOPIC_WORDS）
TOPIC_WORDS = (
    "幼兒園", "幼兒", "幼童", "教保", "教保員", "園長", "托育", "兒童",
    "體罰", "虐童", "不當管教", "霸凌", "食安", "超收", "違法", "裁罰",
    "勒令停辦",
)

# 風險標籤與權重（對應 analysis/risk_analyzer.py 的 RULES）
RULES = {
    "疑似體罰": (25, ["打小孩", "打學生", "體罰", "巴掌", "打人", "拉扯", "推倒"]),
    "疑似不當管教": (20, ["辱罵", "吼小孩", "恐嚇", "罰站", "關廁所", "不當管教"]),
    "疑似兒少傷害": (25, ["虐童", "受傷", "瘀青", "傷口"]),
    "食安問題": (20, ["食物中毒", "過期", "發霉", "吃壞肚子"]),
    "衛生問題": (10, ["蟑螂", "很髒", "環境髒亂"]),
    "收退費爭議": (10, ["亂收費", "退費", "學費爭議"]),
    "人力問題": (10, ["人力不足", "師生比", "無照教保", "缺老師"]),
    "超收": (15, ["超收"]),
    "公安問題": (20, ["消防不合格", "公安", "逃生出口", "違建"]),
    "行政裁罰": (20, ["裁罰", "罰鍰", "勒令停辦", "停招", "違反幼兒教育及照顧法"]),
    "家長投訴": (10, ["投訴", "申訴", "家長抗議"]),
}


# ---------------------------------------------------------------------------
# 文字處理（對應 analysis/normalizer.py）
# ---------------------------------------------------------------------------
def strip_tags(value):
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def compact(value):
    """比對用：去空白、NFKC、casefold，臺=台。"""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text).casefold().replace("臺", "台")


def normalize_name(name):
    """「新北市私立ABC幼兒園」-> 「ABC幼兒園」。"""
    name = unicodedata.normalize("NFKC", str(name or "")).strip()
    name = re.sub(r"\((?:委託|委由|由).*?\)$", "", name).strip()
    name = re.sub(r"^(?:臺|台)灣省", "", name)
    name = re.sub(r"^.{2,3}[縣市](?:私立|立|公立)?", "", name)
    return re.sub(r"^(?:私立|公立)", "", name).strip()


def name_aliases(official_name):
    """可用來比對的名稱變體。太短的不要用，否則「大同幼兒園」會亂中。"""
    short = normalize_name(official_name)
    stem = re.sub(r"幼兒園$", "", short)
    candidates = [official_name, short, stem + "幼兒園", stem]
    out = []
    for alias in candidates:
        key = compact(alias)
        if len(key) >= 4 and key not in out:
            out.append(key)
    return out


def canonical_url(url):
    parts = urllib.parse.urlsplit(str(url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("not a public http url")
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")
    ]
    return urllib.parse.urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            urllib.parse.urlencode(query),
            "",
        )
    )


def topic_relevant(text):
    return any(word in text for word in TOPIC_WORDS)


def rule_tags(text):
    return [tag for tag, (_, words) in RULES.items() if any(w in text for w in words)]


# ---------------------------------------------------------------------------
# HTTP：序列化請求、尊重 robots、遇到擋就斷路
#
# 只用標準庫（Lambda 內建沒有 requests / protego），但規則與
# kindergarten-intelligence/collectors/base.py 一致。
# ---------------------------------------------------------------------------
class Blocked(Exception):
    """來源明確拒絕（401/403/429/CAPTCHA/robots）。"""


class Unavailable(Exception):
    """網路或解析失敗。"""


CHALLENGE_MARKERS = (
    "verify you are human",
    "unusual traffic",
    "cf-chl-",
    'id="captcha"',
    "g-recaptcha",
    "challenge-form",
    "anomaly-modal",
    "<title>request rejected</title>",
)


class Http:
    def __init__(self):
        self._last = 0.0
        self._robots = {}
        self._denied = {}

    def _sleep(self):
        wait = REQUEST_DELAY - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _raw(self, url):
        self._sleep()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
                body = resp.read(8_000_000).decode("utf-8", "replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 429):
                raise Blocked(f"HTTP {exc.code}") from exc
            raise Unavailable(f"HTTP {exc.code}") from exc
        except Exception as exc:  # noqa: BLE001
            raise Unavailable(type(exc).__name__) from exc
        lower = body[:20000].lower()
        if any(marker in lower for marker in CHALLENGE_MARKERS):
            raise Blocked("anti-bot challenge")
        return status, body

    def _robots_allows(self, url):
        """最小可用的 robots.txt 判斷：取 User-agent: * 群組，最長規則優先。"""
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            try:
                _, text = self._raw(origin + "/robots.txt")
                self._robots[origin] = _parse_robots(text)
            except Blocked:
                self._robots[origin] = [("/", False)]  # 保守：擋住整站
            except Unavailable:
                self._robots[origin] = []  # 404/網路問題視為沒有規則
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        best, allowed = -1, True
        for pattern, allow in self._robots[origin]:
            if _robots_match(pattern, path) and len(pattern) > best:
                best, allowed = len(pattern), allow
        return allowed

    def get(self, url, check_robots=True):
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if host in self._denied:
            raise Blocked(self._denied[host] + "（斷路器已開）")
        # 明列在 ROBOTS_EXEMPT_HOSTS 的 host 跳過 robots 檢查（見檔頭說明）。
        # 其餘保護（請求間隔、401/403/429、anti-bot 偵測）一律照常。
        if check_robots and host not in ROBOTS_EXEMPT_HOSTS:
            if not self._robots_allows(url):
                raise Blocked("robots.txt 不允許此路徑")
        try:
            return self._raw(url)
        except Blocked as exc:
            self._denied[host] = str(exc)
            raise


def _parse_robots(text):
    """回 [(path_pattern, allowed)]，只取 User-agent: * 的群組。"""
    rules, in_star = [], False
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            in_star = value == "*"
        elif in_star and field in ("allow", "disallow"):
            if value:
                rules.append((value, field == "allow"))
            elif field == "disallow":
                rules.append(("", True))  # 空的 Disallow 等於全部允許
    return rules


def _robots_match(pattern, path):
    """支援 * 與結尾 $ 的 robots path 比對。"""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.match("^" + regex + ("$" if anchored else ""), path) is not None


# ---------------------------------------------------------------------------
# 來源
#
# 每個 collector 回 (items, note)；抓不到就丟 Blocked / Unavailable，
# 由 collect_all 記錄狀態，不中斷其他來源。
# ---------------------------------------------------------------------------
def source_ntpc_announcements(http, school, cur):
    """新北市政府電子公布欄（官方 OpenAPI，可核對）。"""
    dataset = NTPC_ANNOUNCEMENT_DATASET
    base = f"https://data.ntpc.gov.tw/api/datasets/{dataset}/json"
    items = []
    for page in range(3):  # 每頁 1000 筆，3 頁足夠涵蓋目前公開清單
        _, body = http.get(f"{base}?page={page}&size=1000", check_robots=False)
        try:
            rows = json.loads(body)
        except ValueError as exc:
            raise Unavailable("公布欄 API 回傳非 JSON") from exc
        if not isinstance(rows, list):
            raise Unavailable("公布欄 API 結構改變")
        for row in rows:
            record = {str(k).lower(): v for k, v in row.items()}
            subject = strip_tags(record.get("bbssubject"))
            if not subject or not topic_relevant(subject):
                continue
            attachment = str(record.get("bbsfileurl") or "")
            url = (
                attachment
                if attachment.startswith(("http://", "https://"))
                else f"https://data.ntpc.gov.tw/datasets/{dataset}#{record.get('bbsid')}"
            )
            items.append(
                {
                    "title": subject,
                    "url": url,
                    "source": strip_tags(record.get("bbspublishdept")) or "新北市政府",
                    "source_type": "gov",
                    "published_at": _parse_date(record.get("bbssenddate")),
                    "snippet": subject,
                    "verified": True,
                }
            )
        if len(rows) < 1000:
            break
    return items, f"公告 {len(items)} 則命中幼教關鍵字"


def source_rss(http, school, cur):
    """中央社 RSS：全台近期議題，之後靠園名比對才會歸屬到本園。"""
    items, notes = [], []
    for publisher, feed_url in RSS_FEEDS.items():
        try:
            _, body = http.get(feed_url)
        except (Blocked, Unavailable) as exc:
            notes.append(f"{publisher}: {exc}")
            continue
        for chunk in re.findall(r"<item\b.*?</item>", body, re.S)[:80]:
            title = strip_tags(_tag(chunk, "title"))
            link = _tag(chunk, "link").strip()
            if not title or not link or not topic_relevant(title):
                continue
            items.append(
                {
                    "title": title,
                    "url": link,
                    "source": publisher,
                    "source_type": "news",
                    "published_at": _parse_date(_tag(chunk, "pubDate")),
                    "snippet": strip_tags(_tag(chunk, "description"))[:400],
                    "verified": False,
                }
            )
    return items, "；".join(notes) or f"RSS {len(items)} 則命中幼教關鍵字"


def source_web_search(http, school, cur):
    """公開搜尋端點。這是最不穩定的一環：被擋就記 blocked，不換入口規避。"""
    short = normalize_name(school["school_name"])
    queries = [
        f'"{short}" 幼兒園 家長 投訴',
        f'"{short}" 幼兒園 裁罰 違規',
        f'"{short}" 幼兒園 評價 抱怨',
        f'"{short}" 不當管教',
        f'"{short}" 幼兒園 新聞',
    ][:SEARCH_QUERIES]

    items, used = [], 0
    for query in queries:
        url = (
            SEARCH_ENDPOINT
            + ("&" if "?" in SEARCH_ENDPOINT else "?")
            + urllib.parse.urlencode({SEARCH_QUERY_PARAM: query})
        )
        _, body = http.get(url)
        used += 1
        items.extend(_parse_search_results(body)[:SEARCH_RESULTS])
    return items, f"發出 {used} 次搜尋"


def source_own_punishments(http, school, cur):
    """自家 DB 的裁罰紀錄。不是網路輿情，但是判讀輿情真偽的事實基準。

    欄位比照 app.py 的 handle_kindergarten_punishments，同一張表同一組欄位。
    """
    cur.execute(
        """SELECT punish_date, doc_no, legal_basis, violated_rule, content, fine_amount,
                  source_url
           FROM kindergarten_punishment WHERE kindergarten_id = %s
           ORDER BY punish_date DESC, id ASC LIMIT 10""",
        (school["id"],),
    )
    items = []
    for row in cur.fetchall():
        reason = strip_tags(row.get("violated_rule")) or strip_tags(row.get("content"))
        if not reason:
            continue
        fine = row.get("fine_amount")
        doc_no = strip_tags(row.get("doc_no")) or "未載明文號"
        items.append(
            {
                "title": f"行政裁罰（{doc_no}）：{reason[:100]}",
                "url": row.get("source_url")
                or f"internal://punishment/{school['id']}/{doc_no}",
                "source": "教保服務機構裁罰紀錄",
                "source_type": "gov",
                "published_at": row.get("punish_date"),
                "snippet": (
                    f"處分依據 {strip_tags(row.get('legal_basis')) or '未載明'}；"
                    f"罰鍰 {f'{fine:,} 元' if fine else '未載明'}。"
                    f"{strip_tags(row.get('content'))}"
                )[:400],
                "verified": True,
                "force_confirmed": True,  # 這是本園的紀錄，不需要 LLM 判歸屬
                "internal": not row.get("source_url"),
            }
        )
    return items, f"本府裁罰紀錄 {len(items)} 筆"


def source_parent_reports(http, school, cur):
    """家長回報（已完成 Email 驗證的案件）。第一手，但仍是指控，不是事實。

    案號與 reports.case_no() 同一套推導方式（建立時間 + id），不另存欄位。
    """
    cur.execute(
        """SELECT id, created_at, content FROM parent_report
           WHERE kindergarten_id = %s AND verified_at IS NOT NULL
             AND status <> 'pending_verification'
           ORDER BY created_at DESC LIMIT 10""",
        (school["id"],),
    )
    items = []
    for row in cur.fetchall():
        content = strip_tags(row.get("content"))
        if not content:
            continue
        created = row.get("created_at")
        case_no = f"R{created.strftime('%y%m') if created else '0000'}-{row['id']:06d}"
        items.append(
            {
                "title": f"家長回報 {case_no}",
                "url": f"internal://parent-report/{row['id']}",
                "source": "家長回報系統",
                "source_type": "report",
                "published_at": created,
                "snippet": content[:400],
                "verified": False,
                "force_confirmed": True,
                "internal": True,
            }
        )
    return items, f"已驗證家長回報 {len(items)} 件"


def source_google_news(http, school, cur):
    """Google News RSS：目前唯一能用園名精準搜到新聞的來源。

    刻意小量（見檔頭關於 robots 例外的說明）。標題長相是「標題 - 媒體名」，
    真正的媒體與網域在 <source url="…"> 裡，所以用它當來源名稱，
    標題把尾巴的媒體名去掉。
    """
    if not GOOGLE_NEWS_ENABLED:
        raise Unavailable("已由 OPINION_GOOGLE_NEWS=0 關閉")

    short = normalize_name(school["school_name"])
    queries = [
        f'"{short}"',
        f'"{short}" {school["county"]}',
        f'"{short}" 幼兒園 裁罰',
        f'"{short}" 家長 投訴',
    ][:GOOGLE_NEWS_QUERIES]

    items = []
    for query in queries:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": query, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}
        )
        _, body = http.get(url)
        for chunk in re.findall(r"<item\b.*?</item>", body, re.S)[:GOOGLE_NEWS_RESULTS]:
            title = strip_tags(_tag(chunk, "title"))
            link = strip_tags(_tag(chunk, "link"))
            if not title or not link:
                continue
            publisher_match = re.search(r"<source\b[^>]*>(.*?)</source>", chunk, re.S)
            publisher = strip_tags(publisher_match.group(1)) if publisher_match else ""
            domain_match = re.search(r'<source\b[^>]*url="([^"]+)"', chunk)
            domain = (
                urllib.parse.urlsplit(domain_match.group(1)).hostname
                if domain_match
                else ""
            )
            # 「新北三重學仕幼兒園… | 地方 - 中央社 CNA」-> 去掉尾巴的媒體名
            if publisher and title.endswith(f"- {publisher}"):
                title = title[: -len(publisher) - 2].strip(" |-")
            items.append(
                {
                    "title": title,
                    "url": link,
                    "source": publisher or domain or "Google News",
                    "source_type": "news",
                    "published_at": _parse_date(_tag(chunk, "pubDate")),
                    # RSS 的 description 只是連結 + 媒體名，沒有正文，所以留空；
                    # 不去抓原文，避免變成內容重製。
                    "snippet": "",
                    "verified": False,
                }
            )
    return items, f"Google News {len(queries)} 個查詢，{len(items)} 則"


SOURCES = (
    ("ntpc_announcements", "新北市電子公布欄", source_ntpc_announcements),
    ("own_punishments", "本府裁罰紀錄", source_own_punishments),
    ("parent_reports", "家長回報", source_parent_reports),
    ("google_news", "Google News", source_google_news),
    ("rss", "新聞 RSS", source_rss),
    ("web_search", "公開網路搜尋", source_web_search),
)


def _tag(chunk, name):
    match = re.search(rf"<{name}\b[^>]*>(.*?)</{name}>", chunk, re.S)
    return match.group(1) if match else ""


def _parse_date(value):
    """回 datetime.date 或 None。來源沒寫就是 None，不要猜。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value).strip()
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        try:
            return datetime.date(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            )
        except ValueError:
            return None
    try:  # RFC 822（RSS pubDate）
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(text).date()
    except Exception:  # noqa: BLE001
        return None


def _parse_search_results(body):
    """解析搜尋結果頁。DuckDuckGo 的 lite/html 版都是 uddg= 轉址參數。"""
    results, seen = [], set()
    for match in re.finditer(
        r'<a\b[^>]*href="([^"]*uddg=[^"]*)"[^>]*>(.*?)</a>', body, re.S
    ):
        href, label = match.group(1), strip_tags(match.group(2))
        target = re.search(r"uddg=([^&\"]+)", href)
        if not target or not label:
            continue
        url = urllib.parse.unquote(target.group(1))
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        results.append(
            {
                "title": label,
                "url": url,
                "source": urllib.parse.urlsplit(url).hostname or "",
                "source_type": "web",
                "published_at": None,
                "snippet": "",
                "verified": False,
            }
        )
    # 摘要與連結在版面上是配對出現的，按順序補上
    snippets = [
        strip_tags(m.group(1))
        for m in re.finditer(r'class="result-snippet"[^>]*>(.*?)</td>', body, re.S)
    ]
    for index, snippet in enumerate(snippets):
        if index < len(results):
            results[index]["snippet"] = snippet[:400]
    return results


def collect_all(http, school, cur):
    """跑完所有來源。回 (items, source_report)。單一來源失敗不影響其他來源。"""
    items, report = [], []
    for key, label, func in SOURCES:
        entry = {"key": key, "label": label, "status": "success", "note": "", "count": 0}
        try:
            found, note = func(http, school, cur)
            entry["count"] = len(found)
            entry["note"] = note
            items.extend(found)
        except Blocked as exc:
            entry["status"] = "blocked"
            entry["note"] = str(exc)
        except Unavailable as exc:
            entry["status"] = "unavailable"
            entry["note"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            entry["status"] = "unavailable"
            entry["note"] = type(exc).__name__
            print(f"ERROR source={key}: {type(exc).__name__}: {exc}")
        print(f"source={key} status={entry['status']} count={entry['count']} {entry['note']}")
        report.append(entry)
    return items, report


# ---------------------------------------------------------------------------
# 正規化 + 去重 + 規則初判
# ---------------------------------------------------------------------------
def prepare(items, school):
    aliases = name_aliases(school["school_name"])
    out, seen_urls, seen_titles = [], set(), set()
    for raw in items:
        url = raw.get("url") or ""
        if raw.get("internal"):
            normalized = url  # internal:// 不是公開 URL，直接用
        else:
            try:
                normalized = canonical_url(url)
            except ValueError:
                continue
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if digest in seen_urls:
            continue

        title = strip_tags(raw.get("title"))[:300]
        snippet = strip_tags(raw.get("snippet"))[:1000]
        text = f"{title} {snippet}"
        if not title or not topic_relevant(text):
            continue

        # 同一篇報導會從不同來源進來（例如中央社 RSS 與 Google News 的
        # 轉址連結），URL 不同但標題相同，所以標題也要去重。
        title_key = hashlib.sha256(compact(title).encode("utf-8")).hexdigest()
        if title_key in seen_titles:
            continue
        seen_urls.add(digest)
        seen_titles.add(title_key)

        name_hit = any(alias in compact(text) for alias in aliases)
        out.append(
            {
                "title": title,
                "url": normalized[:1000],
                "url_hash": digest,
                "source": (raw.get("source") or "")[:80],
                "source_type": raw.get("source_type") or "web",
                "published_at": raw.get("published_at"),
                "snippet": snippet[:1000],
                # verified 只代表「來源可核對」：官方網域，或本府自己的資料庫紀錄。
                "verified": bool(raw.get("verified"))
                and (bool(raw.get("internal")) or _official_host(normalized)),
                "rule_tags": rule_tags(text),
                "name_hit": name_hit,
                "attribution": (
                    "confirmed" if (raw.get("force_confirmed") or name_hit) else "unrelated"
                ),
                "confidence": 1.0 if raw.get("force_confirmed") else None,
                "sentiment": None,
                "negative_score": None,
            }
        )

    # 排序：能歸屬到本園的優先，其次有風險標籤的，再按日期
    out.sort(
        key=lambda i: (
            i["attribution"] != "confirmed",
            not i["rule_tags"],
            -(i["published_at"].toordinal() if i["published_at"] else 0),
        )
    )
    return out[:MAX_ITEMS]


def _official_host(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    return host.endswith(".gov.tw") or host.endswith(".edu.tw")


# ---------------------------------------------------------------------------
# AI：Bedrock 判歸屬 + 產摘要；Comprehend 判情緒
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是台灣地方政府教育局的輿情分析助理，協助承辦人快速篩選需要人工關注的線索。

嚴格遵守：
1. 你只做「分類與摘要」，不得推論或斷定違法事實。
2. attribution 只能是 confirmed / ambiguous / unrelated：
   - confirmed：文字中明確指向這一間幼兒園（園名可對應）。
   - ambiguous：可能是同名園所、附設或分班，無法確定。
   - unrelated：只是一般幼教政策或其他園所的新聞。
   同名園所很常見，不確定就給 ambiguous，不要硬歸屬。
3. 否認、澄清、政策宣導、招生資訊都不是負面事件。
4. summary 用繁體中文，最多 200 字，只描述「查到什麼、需不需要人工複核」，
   不要下判斷句，不要建議處分。若沒有可歸屬的線索就直接說沒有。
5. summary 裡**不要寫任何筆數或統計數字**（例如「共 9 筆」），也**不要引用
   項目編號或索引**（例如「第 0 項」、「1-9 項」）。筆數由程式另外附上，
   你自己算會跟程式的數字互相矛盾。
6. 多家媒體報導同一個事件時，summary 要描述成**一個事件**，不要寫成多起事件。
只輸出 JSON，不要加說明文字或程式碼區塊。"""


def analyze_with_bedrock(school, items):
    """回 (summary, model_id, per_item_updates)。失敗就退回規則判定，不讓整個 job 掛掉。"""
    if not items:
        return "", MODEL_ID, {}

    payload = [
        {
            "i": index,
            "title": item["title"],
            "source": item["source"],
            "snippet": item["snippet"][:400],
            "rule_tags": item["rule_tags"],
        }
        for index, item in enumerate(items)
    ]
    user = json.dumps(
        {
            "target_kindergarten": school["school_name"],
            "county": school["county"],
            "district": school["district"],
            "items": payload,
            "output_schema": {
                "summary": "string",
                "items": [
                    {
                        "i": "int",
                        "attribution": "confirmed|ambiguous|unrelated",
                        "confidence": "0..1",
                        "tags": ["string"],
                    }
                ],
            },
        },
        ensure_ascii=False,
    )

    try:
        import boto3

        client = boto3.client("bedrock-runtime")
        response = client.converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": 2000, "temperature": 0.0},
        )
        text = response["output"]["message"]["content"][0]["text"]
    except Exception as exc:  # noqa: BLE001
        print(f"WARN bedrock unavailable: {type(exc).__name__}: {exc}")
        return "", MODEL_ID, {}

    data = _loose_json(text)
    if not isinstance(data, dict):
        print("WARN bedrock 回傳不是 JSON，改用規則判定")
        return "", MODEL_ID, {}

    updates = {}
    for entry in data.get("items") or []:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("i"))
        except (TypeError, ValueError):
            continue
        attribution = str(entry.get("attribution") or "").strip()
        if attribution not in ("confirmed", "ambiguous", "unrelated"):
            continue
        try:
            confidence = max(0.0, min(1.0, float(entry.get("confidence"))))
        except (TypeError, ValueError):
            confidence = None
        tags = [str(t)[:30] for t in (entry.get("tags") or []) if str(t).strip()]
        updates[index] = {
            "attribution": attribution,
            "confidence": confidence,
            "tags": tags[:6],
        }
    return str(data.get("summary") or "")[:2000], MODEL_ID, updates


def _loose_json(text):
    """LLM 偶爾會包 ```json 或前後加字，取第一個大括號區塊。"""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except ValueError:
            return None
    return None


def add_sentiment(items):
    """Comprehend DetectSentiment（實測支援 zh-TW）。只跑可能歸屬本園的項目。"""
    targets = [i for i in items if i["attribution"] in ("confirmed", "ambiguous")]
    if not targets:
        return
    try:
        import boto3

        client = boto3.client("comprehend")
    except Exception as exc:  # noqa: BLE001
        print(f"WARN comprehend client: {type(exc).__name__}: {exc}")
        return

    for batch_start in range(0, len(targets), 25):  # BatchDetectSentiment 上限 25
        batch = targets[batch_start : batch_start + 25]
        documents = [
            (f"{i['title']} {i['snippet']}".strip() or i["title"])[:4500] for i in batch
        ]
        try:
            response = client.batch_detect_sentiment(
                TextList=documents, LanguageCode="zh-TW"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"WARN comprehend batch: {type(exc).__name__}: {exc}")
            return
        for entry in response.get("ResultList", []):
            item = batch[entry["Index"]]
            item["sentiment"] = entry.get("Sentiment")
            item["negative_score"] = round(
                float(entry.get("SentimentScore", {}).get("Negative") or 0.0), 4
            )


def is_negative_signal(item):
    """這一筆算不算「負面訊號」。

    兩種情況：
      1. 情緒判定為負面（新聞、社群、家長回報的主要判準）。
      2. 官方可核對的紀錄本身就有風險標籤 —— 裁罰處分書的文字是公文腔，
         Comprehend 會判成 NEUTRAL，但「被罰了」這件事跟語氣無關。
    """
    if (item.get("negative_score") or 0) >= 0.5:
        return True
    return bool(item.get("verified")) and bool(item.get("rule_tags"))


def _item_weight_multiplier(item):
    """官方紀錄用完整權重；傳聞類依情緒調整。

    裁罰處分書的文字是公文腔，Comprehend 會判成 NEUTRAL，但「被罰了」這件事
    跟語氣無關，所以官方紀錄不套這個折扣。
    """
    if item.get("verified"):
        return 1.0
    negative = item.get("negative_score")
    if negative is None:
        negative = 0.5  # 情緒判不出來就給中性權重，不放大也不歸零
    return 0.4 + 0.6 * negative


def opinion_score_of(items):
    """0-100 的「需要人工關注程度」。只算 attribution='confirmed' 的項目。

    **按「問題類型」計分，不是按「報導篇數」。**

    一個事件被 10 家媒體報導，是 1 個事件而不是 10 個問題。早期版本把每篇文章
    的權重相加，結果只要有新聞群聚就直接頂到 100 分，等於在衡量媒體關注度而不是
    風險。所以改成：每種風險標籤只計一次，取帶有該標籤的項目中權重最高的那個。
    報導篇數另外在摘要裡如實呈現，不混進分數。

    沒有任何可歸屬的線索就回 0（不是 None —— 查過而且沒查到，是有意義的資訊）。
    """
    confirmed = [i for i in items if i["attribution"] == "confirmed"]
    if not confirmed:
        return 0.0
    best_per_tag = {}
    for item in confirmed:
        multiplier = _item_weight_multiplier(item)
        for tag in item["rule_tags"]:
            if tag not in RULES:
                continue  # LLM 自己加的標籤沒有權重，只用於顯示
            contribution = RULES[tag][0] * multiplier
            if contribution > best_per_tag.get(tag, 0.0):
                best_per_tag[tag] = contribution
    return round(min(100.0, sum(best_per_tag.values())), 2)


# ---------------------------------------------------------------------------
# 寫回資料庫
# ---------------------------------------------------------------------------
def set_status(conn, job_id, status, **fields):
    assignments = ["status = %s"]
    values = [status]
    for column, value in fields.items():
        assignments.append(f"{column} = %s")
        values.append(value)
    values.append(job_id)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE opinion_scan_job SET {', '.join(assignments)} WHERE id = %s",
            values,
        )


def save_items(cur, job_id, kg_id, items):
    now = utcnow()
    for item in items:
        cur.execute(
            """INSERT INTO opinion_item
                   (job_id, kindergarten_id, title, url, url_hash, source, source_type,
                    published_at, snippet, sentiment, negative_score, risk_tags,
                    attribution, confidence, verified, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                   title = VALUES(title), snippet = VALUES(snippet),
                   sentiment = VALUES(sentiment), negative_score = VALUES(negative_score),
                   risk_tags = VALUES(risk_tags), attribution = VALUES(attribution),
                   confidence = VALUES(confidence)""",
            (
                job_id,
                kg_id,
                item["title"],
                item["url"],
                item["url_hash"],
                item["source"],
                item["source_type"],
                item["published_at"],
                item["snippet"],
                item["sentiment"],
                item["negative_score"],
                ",".join(item["rule_tags"])[:200],
                item["attribution"],
                item["confidence"],
                1 if item["verified"] else 0,
                now,
            ),
        )


def update_risk_dimension(cur, kg_id, score):
    """把 opinion 分數寫回風險雷達圖的 opinion 軸，其他維度不動。

    total_score / risk_level 故意不算：其他維度還是 placeholder，
    算總分會給人「已完成評估」的錯覺。
    """
    cur.execute(
        "SELECT dimensions FROM risk_score_current WHERE kindergarten_id = %s", (kg_id,)
    )
    row = cur.fetchone()
    stored = row["dimensions"] if row else None
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except ValueError:
            stored = None
    dimensions = stored if isinstance(stored, list) else []

    found = False
    for dimension in dimensions:
        if isinstance(dimension, dict) and dimension.get("key") == "opinion":
            dimension["score"] = score
            found = True
    if not found:
        dimensions.append(
            {"key": "opinion", "label": "輿情負面", "score": score, "weight": 0.2}
        )

    cur.execute(
        """INSERT INTO risk_score_current
               (kindergarten_id, dimensions, is_placeholder, computed_at)
           VALUES (%s, %s, 1, %s)
           ON DUPLICATE KEY UPDATE
               dimensions = VALUES(dimensions), computed_at = VALUES(computed_at)""",
        (kg_id, json.dumps(dimensions, ensure_ascii=False), utcnow()),
    )


def build_summary(school, items, source_report, llm_summary, score):
    """組出給承辦人看的摘要。LLM 有給就用它，但來源狀態一律由程式附上。"""
    confirmed = [i for i in items if i["attribution"] == "confirmed"]
    ambiguous = [i for i in items if i["attribution"] == "ambiguous"]
    negative = [i for i in confirmed if is_negative_signal(i)]

    if llm_summary:
        head = llm_summary
    elif confirmed:
        tags = sorted({t for i in confirmed for t in i["rule_tags"]})
        head = (
            f"查到 {len(confirmed)} 筆可比對到「{school['school_name']}」的公開資訊"
            + (f"，涉及：{'、'.join(tags)}。" if tags else "。")
            + "以上僅為線索，需人工複核。"
        )
    else:
        head = (
            f"本次未查到可明確歸屬「{school['school_name']}」的負面公開資訊；"
            f"另有 {len(ambiguous)} 筆同名或無法確認歸屬的資料待人工判斷。"
        )

    blocked = [s for s in source_report if s["status"] != "success"]
    tags = sorted({t for i in confirmed for t in i["rule_tags"] if t in RULES})
    lines = [
        head,
        "",
        f"關注指數 {score}／100（僅代表需要人工關注的程度，非違法機率）",
        # 分數按「問題類型」計，篇數另計 —— 同一事件被多家媒體報導不會加重分數
        f"計分依據的問題類型：{'、'.join(tags) if tags else '無'}",
        f"可歸屬本園 {len(confirmed)} 筆資料（其中負面訊號 {len(negative)} 筆）；"
        f"待人工確認 {len(ambiguous)} 筆。多家媒體報導同一事件會列為多筆，"
        f"但分數只按問題類型計算一次。",
    ]
    if blocked:
        lines.append(
            "未能取得的來源："
            + "、".join(f"{s['label']}（{s['status']}：{s['note']}）" for s in blocked)
            + "。未取得結果不代表沒有相關討論。"
        )
    return "\n".join(lines)[:60000]


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def handler(event, context):  # noqa: ARG001
    job_id = (event or {}).get("jobId")
    if not job_id:
        return {"ok": False, "error": "缺少 jobId"}

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT j.id, j.kindergarten_id, j.status, k.school_name, k.county, k.district
               FROM opinion_scan_job j
               JOIN kindergarten k ON k.id = j.kindergarten_id
               WHERE j.id = %s""",
            (job_id,),
        )
        job = cur.fetchone()
    if not job:
        print(f"ERROR job {job_id} 不存在")
        return {"ok": False, "error": "job 不存在"}
    if job["status"] not in ("queued", "searching", "analyzing"):
        print(f"job {job_id} 狀態是 {job['status']}，不重複執行")
        return {"ok": True, "skipped": True}

    school = {
        "id": job["kindergarten_id"],
        "school_name": job["school_name"],
        "county": job["county"],
        "district": job["district"],
    }

    try:
        set_status(conn, job_id, "searching", started_at=utcnow())
        http = Http()
        with conn.cursor() as cur:
            raw_items, source_report = collect_all(http, school, cur)
        items = prepare(raw_items, school)

        set_status(conn, job_id, "analyzing")
        llm_summary, model_id, updates = analyze_with_bedrock(school, items)
        for index, update in updates.items():
            if index < len(items):
                items[index]["attribution"] = update["attribution"]
                if update["confidence"] is not None:
                    items[index]["confidence"] = update["confidence"]
                if update["tags"]:
                    merged = list(
                        dict.fromkeys(items[index]["rule_tags"] + update["tags"])
                    )
                    items[index]["rule_tags"] = merged[:8]
        add_sentiment(items)

        score = opinion_score_of(items)
        summary = build_summary(school, items, source_report, llm_summary, score)
        confirmed = sum(1 for i in items if i["attribution"] == "confirmed")
        negative = sum(
            1
            for i in items
            if i["attribution"] == "confirmed" and is_negative_signal(i)
        )
        searched = next(
            (s for s in source_report if s["key"] == "web_search"), {"status": "-"}
        )

        with conn.cursor() as cur:
            save_items(cur, job_id, school["id"], items)
            update_risk_dimension(cur, school["id"], score)
            cur.execute(
                """UPDATE opinion_scan_job SET
                       status='done', finished_at=%s, item_count=%s, confirmed_count=%s,
                       negative_count=%s, opinion_score=%s, summary=%s,
                       search_provider=%s, model_id=%s, query_count=%s, error=NULL
                   WHERE id = %s""",
                (
                    utcnow(),
                    len(items),
                    confirmed,
                    negative,
                    score,
                    summary,
                    "http" if searched["status"] == "success" else "http(blocked)",
                    model_id,
                    sum(s["count"] for s in source_report),
                    job_id,
                ),
            )
            cur.execute(
                """INSERT INTO opinion_scan_audit
                       (job_id, kindergarten_id, action, detail, created_at)
                   VALUES (%s, %s, 'scan_finished', %s, %s)""",
                (
                    job_id,
                    school["id"],
                    f"items={len(items)} confirmed={confirmed} score={score}",
                    utcnow(),
                ),
            )
        print(f"job {job_id} done items={len(items)} confirmed={confirmed} score={score}")
        return {"ok": True, "jobId": job_id, "items": len(items), "score": score}

    except Exception as exc:  # noqa: BLE001
        print(f"ERROR job {job_id}: {type(exc).__name__}: {exc}")
        try:
            set_status(
                conn,
                job_id,
                "failed",
                finished_at=utcnow(),
                error=f"{type(exc).__name__}: {exc}"[:255],
            )
        except Exception as inner:  # noqa: BLE001
            print(f"ERROR 無法標記 job 失敗: {inner}")
        return {"ok": False, "jobId": job_id, "error": type(exc).__name__}
