import json
from analysis.normalizer import aliases, compact, now
from analysis.deduplicator import content_hash

class Repository:
    def __init__(self, connection):
        self.conn = connection

    def save_schools(self, rows):
        count = 0
        with self.conn:
            for s in rows:
                if not s['official_name'] or not s['city']:
                    continue
                # Exact city + official name merges MOE and municipal records, never a short alias.
                matches = self.conn.execute('SELECT * FROM kindergartens WHERE city=? AND official_name=?',
                                            (s['city'], s['official_name'])).fetchall()
                old = matches[0] if len(matches) == 1 else None
                if old:
                    s['id'] = old['id']
                    metadata = json.loads(old['metadata'])
                    provenance = metadata.get('sources', [old['source']])
                    metadata.update(s)
                    metadata['sources'] = list(dict.fromkeys(provenance + [s['source']]))
                    for key in ('school_year', 'code', 'public_private', 'district'):
                        s[key] = s.get(key) or old[key]
                else:
                    metadata = dict(s, sources=[s['source']])
                keys = ('id', 'official_name', 'normalized_name', 'city', 'district', 'address', 'phone', 'source',
                        'updated_at', 'school_year', 'code', 'public_private')
                values = [s.get(k, '') for k in keys] + [json.dumps(metadata, ensure_ascii=False)]
                self.conn.execute(f"INSERT INTO kindergartens ({','.join(keys)},metadata) VALUES ({','.join('?' for _ in values)}) "
                    + 'ON CONFLICT(id) DO UPDATE SET ' + ','.join(f'{k}=excluded.{k}' for k in keys[1:]) + ',metadata=excluded.metadata', values)
                count += 1
        return count

    def schools(self, city=None, district=None, name=None, limit=None):
        sql, params = 'SELECT * FROM kindergartens WHERE 1=1', []
        for key, value in (('city', city), ('district', district)):
            if value:
                sql += f' AND {key}=?'
                params.append(value)
        if name:
            sql += ' AND (official_name LIKE ? OR normalized_name LIKE ?)'
            params.extend(['%' + name + '%'] * 2)
        sql += ' ORDER BY city, district, official_name'
        if limit is not None:
            sql += ' LIMIT ?'
            params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params)]

    def save_item(self, item):
        keys = ('id', 'kindergarten_id', 'kindergarten_name', 'platform', 'source_type', 'publisher', 'author',
                'title', 'content', 'url', 'published_at', 'collected_at', 'query', 'risk_score', 'verified')
        values = [item.get(k) for k in keys]
        extra = {k: v for k, v in item.items() if k not in keys}
        with self.conn:
            cursor = self.conn.execute(f"INSERT OR IGNORE INTO items ({','.join(keys)},normalized_hash,metadata) "
                + f"VALUES ({','.join('?' for _ in range(len(keys) + 2))})",
                values + [content_hash(item), json.dumps(extra, ensure_ascii=False)])
            if cursor.rowcount:
                self.conn.executemany('INSERT INTO risk_tags VALUES (?, ?)', [(item['id'], t) for t in item['risk_tags']])
                return 1
        return 0

    def prune_items_without_relevance_gate(self):
        """Remove records produced by the pre-gate pipeline migration."""
        rows = self.conn.execute('SELECT id,metadata FROM items').fetchall()
        invalid = []
        for row in rows:
            metadata = json.loads(row['metadata'] or '{}')
            if metadata.get('relevant') is not True or metadata.get('location_valid') is not True:
                invalid.append((row['id'],))
        with self.conn:
            self.conn.executemany('DELETE FROM items WHERE id=?', invalid)
        return len(invalid)

    def record_run(self, result, inserted=0, scope=''):
        with self.conn:
            self.conn.execute('INSERT INTO crawl_runs (started_at,finished_at,source,status,items_found,error,query,items_inserted,scope) VALUES (?,?,?,?,?,?,?,?,?)',
                (result.started_at or now(), now(), result.source, result.status, len(result.items), result.error, result.query, inserted, scope))

    def save_report_scope(self, city, district, kindergarten, requested_limit, selected):
        payload = json.dumps([s['id'] for s in selected])
        with self.conn:
            self.conn.execute('''INSERT INTO report_scope
                (id,city,district,kindergarten,requested_limit,selected_ids,selected_count,updated_at)
                VALUES (1,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                city=excluded.city,district=excluded.district,kindergarten=excluded.kindergarten,
                requested_limit=excluded.requested_limit,selected_ids=excluded.selected_ids,
                selected_count=excluded.selected_count,updated_at=excluded.updated_at''',
                (city, district, kindergarten, requested_limit, payload, len(selected), now()))

    def report_scope(self):
        row = self.conn.execute('SELECT * FROM report_scope WHERE id=1').fetchone()
        if not row:
            return None
        result = dict(row)
        result['selected_ids'] = json.loads(result['selected_ids'])
        return result

    def export_keywords(self, path):
        rows = [dict(kindergarten_id=s['id'], name=s['official_name'], aliases=aliases(s['official_name']),
                     city=s['city'], district=s['district']) for s in self.schools()]
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')

    def items(self):
        rows = [dict(r) for r in self.conn.execute('SELECT * FROM items ORDER BY risk_score DESC, collected_at DESC')]
        for row in rows:
            metadata = json.loads(row.pop('metadata') or '{}')
            for key, value in metadata.items():
                row.setdefault(key, value)
            row['risk_tags'] = [r[0] for r in self.conn.execute('SELECT tag FROM risk_tags WHERE item_id=? ORDER BY tag', (row['id'],))]
        return rows
