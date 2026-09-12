# -*- coding: utf-8 -*-
"""本機驗證 opinion_worker 的蒐集 / 判讀流程（不需要 RDS，DB 部分用假 cursor）。

用法（在專案根目錄）：
    python aws/local_test_opinion.py                # 只跑純函式，不連外網
    python aws/local_test_opinion.py --live         # 連真的來源 + Bedrock + Comprehend

--live 會真的對外發請求（公布欄 OpenAPI / RSS / 搜尋端點）並呼叫 Bedrock、
Comprehend，需要有效的 AWS 憑證。單一來源被擋是預期行為，會顯示 blocked。
"""
import argparse
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "aws", "src"))

# common.py 在 import 時就要讀 DB_* 環境變數，這裡只是為了 import 成功，不會真的連線
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

import opinion_worker as w  # noqa: E402

PASS, FAIL = "  [OK]  ", "  [FAIL]"
failures = []


def check(label, condition, detail=""):
    print((PASS if condition else FAIL) + f" {label}" + (f" -> {detail}" if detail else ""))
    if not condition:
        failures.append(label)


class FakeCursor:
    """只回空結果：本機沒有 RDS，DB 來源就當作沒資料。"""

    def execute(self, *_args, **_kwargs):
        return 0

    def fetchall(self):
        return []

    def fetchone(self):
        return None


SCHOOL = {
    "id": 1,
    "school_name": "新北市私立快樂幼兒園",
    "county": "新北市",
    "district": "板橋區",
}


def test_text_helpers():
    print("\n== 文字處理 ==")
    check("normalize_name 去掉縣市與私立",
          w.normalize_name("新北市私立快樂幼兒園") == "快樂幼兒園",
          w.normalize_name("新北市私立快樂幼兒園"))
    check("compact 統一臺/台與空白",
          w.compact(" 臺北 市 ") == "台北市", w.compact(" 臺北 市 "))
    aliases = w.name_aliases("新北市私立快樂幼兒園")
    check("name_aliases 產生可比對別名且長度>=4", all(len(a) >= 4 for a in aliases), str(aliases))
    check("strip_tags 去 HTML", w.strip_tags("<p>你好 <b>世界</b></p>") == "你好 世界")
    check("topic_relevant 命中幼教字", w.topic_relevant("某幼兒園疑似不當管教"))
    check("topic_relevant 不誤判無關文字", not w.topic_relevant("今日股市收盤上漲"))
    tags = w.rule_tags("老師體罰學生，家長投訴")
    check("rule_tags 抓到體罰與家長投訴",
          "疑似體罰" in tags and "家長投訴" in tags, str(tags))
    check("canonical_url 去掉 utm 參數",
          w.canonical_url("https://A.example.com/x?utm_source=fb&id=3")
          == "https://a.example.com/x?id=3",
          w.canonical_url("https://A.example.com/x?utm_source=fb&id=3"))


def test_robots():
    print("\n== robots 規則 ==")
    rules = w._parse_robots("User-agent: *\nDisallow: /\nAllow: /rss/\n\nUser-agent: Bad\nDisallow: /x")
    check("只取 * 群組", rules == [("/", False), ("/rss/", True)], str(rules))
    check("$ 結尾錨定", w._robots_match("/$", "/") and not w._robots_match("/$", "/a"))
    check("* 萬用字元", w._robots_match("/a*c", "/abbbc"))
    check("Disallow: / 會擋住未列在 Allow 的路徑",
          not _allowed([("/", False), ("/topics/", True)], "/rss/search"))
    # robots 例外是明列的，不是把檢查拔掉
    check("news.google.com 在 robots 例外清單內",
          "news.google.com" in w.ROBOTS_EXEMPT_HOSTS, str(w.ROBOTS_EXEMPT_HOSTS))
    check("例外清單沒有把其他站也放進來",
          len(w.ROBOTS_EXEMPT_HOSTS) == 1, str(w.ROBOTS_EXEMPT_HOSTS))
    check("Google News 查詢量維持小量（<=4 查詢 x <=20 筆）",
          w.GOOGLE_NEWS_QUERIES <= 4 and w.GOOGLE_NEWS_RESULTS <= 20,
          f"{w.GOOGLE_NEWS_QUERIES} x {w.GOOGLE_NEWS_RESULTS}")


def _allowed(rules, path):
    best, allowed = -1, True
    for pattern, allow in rules:
        if w._robots_match(pattern, path) and len(pattern) > best:
            best, allowed = len(pattern), allow
    return allowed


def test_scoring():
    print("\n== 分數與摘要 ==")
    items = [
        {"attribution": "confirmed", "rule_tags": ["疑似體罰"], "negative_score": 0.9,
         "title": "t", "snippet": "s"},
        {"attribution": "ambiguous", "rule_tags": ["疑似體罰"], "negative_score": 0.9,
         "title": "t", "snippet": "s"},
    ]
    score = w.opinion_score_of(items)
    check("只計 confirmed 的項目", 0 < score <= 25, f"score={score}")
    check("沒有 confirmed 就是 0（查過且沒查到）",
          w.opinion_score_of([dict(items[1])]) == 0.0)

    # 裁罰處分書是公文腔，Comprehend 會判 NEUTRAL；官方紀錄不該因此被打折
    official = [{"attribution": "confirmed", "rule_tags": ["行政裁罰"],
                 "negative_score": 0.0007, "verified": True, "title": "t", "snippet": "s"}]
    rumour = [{"attribution": "confirmed", "rule_tags": ["行政裁罰"],
               "negative_score": 0.0007, "verified": False, "title": "t", "snippet": "s"}]
    check("官方紀錄用完整權重（不被中性語氣打折）",
          w.opinion_score_of(official) == 20.0, str(w.opinion_score_of(official)))
    check("傳聞類仍受情緒調整",
          w.opinion_score_of(rumour) < w.opinion_score_of(official),
          f"rumour={w.opinion_score_of(rumour)} official={w.opinion_score_of(official)}")
    check("官方紀錄有風險標籤即算負面訊號", w.is_negative_signal(official[0]))
    check("官方紀錄沒有風險標籤就不算",
          not w.is_negative_signal({"negative_score": 0.1, "verified": True, "rule_tags": []}))

    # 同一事件被多家媒體報導，是 1 個事件不是 N 個問題 —— 分數不該因此暴衝
    def article(negative):
        return {"attribution": "confirmed", "rule_tags": ["疑似兒少傷害"],
                "negative_score": negative, "verified": False, "title": "t", "snippet": "s"}

    one = w.opinion_score_of([article(0.8)])
    ten = w.opinion_score_of([article(0.8)] * 10)
    check("10 篇報導同一類問題 = 1 篇的分數（不按篇數累加）",
          one == ten, f"1篇={one} 10篇={ten}")
    check("不同類型的問題會累加",
          w.opinion_score_of([
              article(0.8),
              {"attribution": "confirmed", "rule_tags": ["食安問題"], "negative_score": 0.8,
               "verified": False, "title": "t", "snippet": "s"},
          ]) > one)
    check("分數上限 100",
          w.opinion_score_of([
              {"attribution": "confirmed", "rule_tags": list(w.RULES), "negative_score": 1.0,
               "verified": True, "title": "t", "snippet": "s"}
          ]) == 100.0)
    check("LLM 自加的標籤不影響分數",
          w.opinion_score_of([
              {"attribution": "confirmed", "rule_tags": ["疑似兒少傷害", "密錄", "監管"],
               "negative_score": 0.8, "verified": False, "title": "t", "snippet": "s"}
          ]) == one)
    report = [{"key": "web_search", "label": "公開網路搜尋", "status": "blocked",
               "note": "HTTP 403", "count": 0}]
    summary = w.build_summary(SCHOOL, items, report, "", score)
    check("摘要含免責語意", "非違法機率" in summary)
    check("摘要列出計分依據的問題類型", "計分依據的問題類型" in summary)
    check("摘要說明分數不按篇數計", "只按問題類型計算一次" in summary)
    check("摘要列出被擋的來源", "公開網路搜尋" in summary and "blocked" in summary)
    check("摘要說明未取得不代表沒有討論", "不代表沒有相關討論" in summary)


def test_prepare():
    print("\n== 正規化與去重 ==")
    raw = [
        {"title": "快樂幼兒園 家長投訴不當管教", "url": "https://news.example.com/a?utm_source=x",
         "source": "example", "source_type": "news", "snippet": "家長投訴", "verified": False},
        {"title": "快樂幼兒園 家長投訴不當管教", "url": "https://news.example.com/a",
         "source": "example", "source_type": "news", "snippet": "家長投訴", "verified": False},
        {"title": "全國幼兒園政策說明", "url": "https://gov.example.com/b",
         "source": "example", "source_type": "news", "snippet": "政策", "verified": False},
        {"title": "今日天氣", "url": "https://w.example.com/c",
         "source": "example", "source_type": "news", "snippet": "晴", "verified": False},
        {"title": "本園裁罰紀錄", "url": "internal://punishment/1/abc",
         "source": "裁罰", "source_type": "gov", "snippet": "違反幼兒教育及照顧法",
         "verified": True, "force_confirmed": True, "internal": True},
        # 同一篇報導從 Google News 轉址連結進來，URL 不同但標題一樣
        {"title": "快樂幼兒園 家長投訴不當管教",
         "url": "https://news.google.com/rss/articles/CBMiZEFV?oc=5",
         "source": "中央社 CNA", "source_type": "news", "snippet": "", "verified": False},
    ]
    items = w.prepare(raw, SCHOOL)
    urls = [i["url"] for i in items]
    check("utm 版與乾淨版視為同一筆", len([u for u in urls if u.startswith("https://news.")]) == 1,
          str(urls))
    check("跨來源同標題只留一筆",
          not any("news.google.com" in u for u in urls), str(urls))
    check("無關文字被濾掉", not any("w.example.com" in u for u in urls))
    check("園名命中 -> confirmed",
          any(i["attribution"] == "confirmed" and "news.example" in i["url"] for i in items))
    check("一般幼教政策 -> unrelated",
          any(i["attribution"] == "unrelated" and "gov.example" in i["url"] for i in items))
    internal = [i for i in items if i["url"].startswith("internal://")]
    check("本府紀錄保留 verified", internal and internal[0]["verified"], str(internal[:1]))
    check("confirmed 排在最前面", items[0]["attribution"] == "confirmed")


def test_live():
    print("\n== 連線實測（--live）==")
    http = w.Http()
    cur = FakeCursor()
    items, report = w.collect_all(http, SCHOOL, cur)
    for entry in report:
        print(f"     {entry['label']:<12} {entry['status']:<12} count={entry['count']:<4} {entry['note'][:60]}")
    check("至少一個來源成功", any(e["status"] == "success" for e in report))
    check("公布欄 OpenAPI 有回資料",
          any(e["key"] == "ntpc_announcements" and e["count"] > 0 for e in report))
    check("Google News 來源有回應（成功或明確被擋）",
          any(e["key"] == "google_news" and e["status"] in ("success", "blocked")
              for e in report))

    prepared = w.prepare(items, SCHOOL)
    print(f"     正規化後 {len(prepared)} 筆")
    summary, model_id, updates = w.analyze_with_bedrock(SCHOOL, prepared[:10])
    check("Bedrock 有回摘要或判定", bool(summary) or bool(updates),
          f"model={model_id} summary_len={len(summary)} updates={len(updates)}")
    if summary:
        print("     摘要：", summary[:150].replace("\n", " "))
    sample = prepared[:5]
    for item in sample:
        item["attribution"] = "confirmed"
    w.add_sentiment(sample)
    check("Comprehend 有回情緒", any(i["sentiment"] for i in sample),
          str([(i["sentiment"], i["negative_score"]) for i in sample]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="連真的來源與 AWS 服務")
    args = parser.parse_args()

    test_text_helpers()
    test_robots()
    test_scoring()
    test_prepare()
    if args.live:
        test_live()

    print()
    if failures:
        print(f"FAILED {len(failures)}: " + "; ".join(failures))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
