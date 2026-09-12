import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
DATA = ROOT / 'data'
DB_PATH = DATA / 'kindergarten_intelligence.db'
for folder in (DATA / 'raw', DATA / 'normalized', DATA / 'reports', ROOT / 'logs'):
    folder.mkdir(parents=True, exist_ok=True)
DELAY = max(1.0, float(os.getenv('REQUEST_DELAY', '1.2')))
TIMEOUT = max(1, float(os.getenv('REQUEST_TIMEOUT', '25')))
USER_AGENT = os.getenv('USER_AGENT', 'KindergartenIntelligence/1.0 (public-data research; no login)')
SEARCH_ENDPOINT = os.getenv('SEARCH_ENDPOINT', 'https://www.bing.com/search')
QUERY_PARAM = os.getenv('SEARCH_QUERY_PARAM', 'q')
QUERY_BUDGET = max(1, int(os.getenv('SEARCH_QUERIES_PER_SCHOOL', '2')))
RESULT_LIMIT = max(1, min(20, int(os.getenv('SEARCH_RESULTS_LIMIT', '5'))))
MAX_PAGES = max(1, int(os.getenv('MAX_PAGES', '100')))
MOE_DATASET = 'https://data.gov.tw/dataset/6086'
NTPC_ID = 'f563b4cd-b850-41f5-9709-b910f2d147e9'
ANNOUNCEMENTS_ID = 'EAAC9944-2CCB-4DBB-B616-441128E17A4A'
RSS_FEEDS = {
    'CNA 社會': 'https://feeds.feedburner.com/rsscna/social',
    'CNA 地方': 'https://feeds.feedburner.com/rsscna/local',
    'CNA 生活': 'https://feeds.feedburner.com/rsscna/lifehealth',
}
