import hashlib
import json
from analysis.normalizer import compact

def content_hash(item):
    values = [compact(item.get(k, '')) for k in ('title', 'content', 'kindergarten_name')]
    if not values[0] and not values[1]:
        values.append(item['url'])
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()
