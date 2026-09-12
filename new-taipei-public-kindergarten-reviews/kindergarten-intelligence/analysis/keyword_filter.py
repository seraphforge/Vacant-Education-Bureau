from analysis.normalizer import aliases, compact

TOPIC_WORDS = ('幼兒園', '幼兒', '幼童', '教保', '教保員', '園長', '托育', '兒童', '體罰', '虐童', '不當管教', '霸凌', '食安', '超收', '違法', '裁罰', '勒令停辦')

def relevant(text):
    return any(word in text for word in TOPIC_WORDS)

def match_schools(text, schools):
    text = compact(text)
    return [s for s in schools if any(compact(a) in text for a in aliases(s['official_name']) if len(compact(a)) >= 4)]
