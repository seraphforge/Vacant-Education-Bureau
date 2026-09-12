from dataclasses import dataclass
from analysis.normalizer import aliases, compact

CHILDCARE_CONTEXT = (
    '幼兒園', '幼稚園', '幼兒', '幼童', '教保', '教保員', '園長', '托嬰', '托育',
)

NEW_TAIPEI_SIGNALS = (
    '新北市', '新北', '板橋', '新莊', '三重', '中和', '永和', '土城', '樹林', '汐止',
    '淡水', '蘆洲', '五股', '泰山', '林口', '鶯歌', '三峽', '瑞芳', '深坑', '石碇',
    '坪林', '三芝', '石門', '八里', '平溪', '雙溪', '貢寮', '金山', '萬里', '烏來',
)

@dataclass(frozen=True)
class RelevanceDecision:
    relevant: bool
    location_valid: bool
    matches: tuple
    reason: str

def _entity_matches(text, schools):
    normalized = compact(text)
    matches = []
    for school in schools:
        candidates = [a for a in aliases(school['official_name']) if len(compact(a)) >= 4]
        if any(compact(alias) in normalized for alias in candidates):
            matches.append(school)
    return tuple(matches)

def evaluate(text, schools, city='新北市', official_new_taipei=False):
    """Gate first, validate location second, then return entity matches.

    A known in-scope school alias supplies both relevance and location. Otherwise
    the text needs a childcare term and an explicit New Taipei signal. An NTPC
    government announcement is already location-validated by official provenance,
    but still needs childcare context.
    """
    text = str(text or '')
    has_context = any(word in text for word in CHILDCARE_CONTEXT)
    if not has_context:
        return RelevanceDecision(False, False, (), 'missing childcare context')
    matches = _entity_matches(text, schools)
    if matches:
        return RelevanceDecision(True, True, matches, 'known in-scope kindergarten alias')
    if city != '新北市':
        return RelevanceDecision(False, False, (), 'unsupported report city')
    location_valid = official_new_taipei or any(signal in text for signal in NEW_TAIPEI_SIGNALS)
    if not location_valid:
        return RelevanceDecision(False, False, (), 'missing New Taipei location/entity signal')
    return RelevanceDecision(True, True, (), 'childcare context and New Taipei signal')
