from collectors.web.generic import GenericWeb

class NewsSearch(GenericWeb):
    source = 'news'
    suffixes = ('新聞', '新北', '體罰', '家長', '幼童', '教保員', '園長', '托育', '虐童', '不當管教',
                '霸凌', '食安', '超收', '違法', '裁罰', '勒令停辦')
    domains = ('cna.com.tw', 'pts.org.tw', 'ltn.com.tw', 'udn.com', 'ettoday.net', 'tvbs.com.tw',
               'setn.com', 'chinatimes.com', 'newtalk.tw', 'nownews.com', 'yahoo.com')
