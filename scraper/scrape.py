# -*- coding: utf-8 -*-
"""Scrape all 新北市 kindergarten punishment records from ap.ece.moe.edu.tw.

Strategy (the search grid caps at 10 rows, no pager):
  1. For each of 29 districts (city=03), run a plain search  -> baseline rows.
  2. For coverage beyond the 10-row cap, also run keyword searches
     (city=03, txtKeyNameS = each CJK char that appears in 新北市 園名),
     deduping schools by their `sch` token.
  3. For every unique school (sch token), GET dtl/punish_view.aspx?sch=...
     within the same session and parse the punishment GridView.

Output: scraper/punishments.json  (list of {school + records[]})
"""
import json
import os
import re
import sys
import time

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings()

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://ap.ece.moe.edu.tw/webecems/punishSearch.aspx"
DETAIL = "https://ap.ece.moe.edu.tw/webecems/dtl/punish_view.aspx?sch="
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": URL,
    "Origin": "https://ap.ece.moe.edu.tw",
}
CITY = "03"  # 新北市
DISTRICTS = ["207","208","220","221","222","223","224","226","227","228","231",
             "232","233","234","235","236","237","238","239","241","242","243",
             "244","247","248","249","251","252","253"]


def hidden(html):
    d = {}
    for n in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        m = re.search(r'id="' + n + r'"[^>]*value="([^"]*)"', html)
        d[n] = m.group(1) if m else ""
    return d


def parse_rows(html):
    """Return list of dicts for each school row + its sch token."""
    soup = BeautifulSoup(html, "html.parser")
    gv = soup.find("table", id="GridView1")
    if not gv:
        return []
    rows = []
    # each school block is identified by index i in the span ids
    idxs = sorted({int(m) for m in re.findall(r'id="GridView1_lblSchName_(\d+)"', html)})
    for i in idxs:
        def g(label):
            el = soup.find(id=f"GridView1_{label}_{i}")
            return el.get_text(" ", strip=True) if el else ""
        # sch token from the lbView link for this row
        link = soup.find(id=f"GridView1_lbView_{i}")
        sch = ""
        if link and link.has_attr("onclick"):
            m = re.search(r"punish_view\.aspx\?sch=([A-Za-z0-9+/=]+)", link["onclick"])
            if m:
                sch = m.group(1)
        rows.append({
            "school_name": g("lblSchName"),
            "county": g("lblCity"),
            "district": g("lblArea"),
            "ownership": g("lblPub"),
            "phone": g("lblTel"),
            "capacity": g("lblGenStd"),
            "status": g("lblBStatus"),
            "sch": sch,
        })
        # address is an <a>
        a = soup.find(id=f"GridView1_hlAddr_{i}")
        rows[-1]["address"] = a.get_text(" ", strip=True) if a else ""
    return rows


def search(sess, viewstate_html, district="", keyword=""):
    f = hidden(viewstate_html)
    form = {"__EVENTTARGET": "", "__EVENTARGUMENT": "", **f,
            "ddlKey": "school_name", "txtKeyNameS": keyword,
            "ddlCityS": CITY, "ddlAreaS": district, "btnSearch": "搜尋"}
    r = sess.post(URL, data=form, timeout=60)
    return r.text


def parse_detail(html):
    """Parse punishment records from the detail page's GridView1."""
    soup = BeautifulSoup(html, "html.parser")
    gv = soup.find("table", id="GridView1")
    if not gv:
        return []
    trs = gv.find_all("tr")
    if len(trs) < 2:
        return []
    header = [c.get_text(" ", strip=True) for c in trs[0].find_all(["th", "td"])]
    recs = []
    for tr in trs[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 7 or not any(cells):
            continue
        recs.append({
            "punish_date": cells[0],
            "school_name_at_time": cells[1],
            "doc_no": cells[2],
            "legal_basis": cells[3],
            "violated_rule": cells[4],
            "person": cells[5],
            "content": cells[6],
        })
    return recs


def main():
    chars = json.load(open(os.path.join(HERE, "name_chars.json"), encoding="utf-8"))
    sess = requests.Session()
    sess.verify = False
    sess.headers.update(HEADERS)

    # fresh page
    r = sess.get(URL, timeout=30)
    base_html = r.text

    # register districts in the viewstate via the city-change autopostback
    f = hidden(base_html)
    city_form = {"__EVENTTARGET": "ddlCityS", "__EVENTARGUMENT": "", **f,
                 "ddlKey": "school_name", "txtKeyNameS": "",
                 "ddlCityS": CITY, "ddlAreaS": ""}
    base_html = sess.post(URL, data=city_form, timeout=60).text

    schools = {}   # sch -> row dict

    def absorb(html):
        for row in parse_rows(html):
            if row["sch"]:
                schools.setdefault(row["sch"], row)

    # 1) per-district baseline
    print("phase 1: per-district", flush=True)
    for d in DISTRICTS:
        html = search(sess, base_html, district=d)
        absorb(html)
        base_html = html  # keep viewstate fresh from last response
        time.sleep(0.2)
    print("  after districts, unique schools:", len(schools), flush=True)

    # 2) keyword split (city only) to exceed the 10-cap
    print("phase 2: keyword split over", len(chars), "chars", flush=True)
    for i, ch in enumerate(chars):
        html = search(sess, base_html, district="", keyword=ch)
        absorb(html)
        base_html = html
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(chars)} chars, unique schools: {len(schools)}", flush=True)
        time.sleep(0.12)
    print("  total unique schools with sch token:", len(schools), flush=True)

    # 3) fetch punishment details per school
    print("phase 3: fetch details", flush=True)
    results = []
    for j, (sch, row) in enumerate(schools.items()):
        try:
            d = sess.get(DETAIL + sch, headers={"Referer": URL}, timeout=30)
            recs = parse_detail(d.text)
        except Exception as e:
            print("  detail error", row["school_name"], e, flush=True)
            recs = []
        row["records"] = recs
        results.append(row)
        if (j + 1) % 25 == 0:
            print(f"  {j+1}/{len(schools)} details", flush=True)
        time.sleep(0.15)

    total_recs = sum(len(r["records"]) for r in results)
    json.dump(results, open(os.path.join(HERE, "punishments.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"DONE: {len(results)} schools, {total_recs} punishment records")
    print("wrote scraper/punishments.json")


if __name__ == "__main__":
    main()
