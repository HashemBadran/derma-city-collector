"""Matches a list of customer names against the synced Odoo customers, flagging
them as this collector's assigned book.

Same matching approach as the receivables tracker's agency-list import: names
arrive as plain text (typed, pasted, or read off a screenshot), spelled
inconsistently against Odoo's exact records (ا/أ, ي/ى, ة/ه, extra "شركة"/"مجمع"
prefixes), so a literal comparison finds almost nothing. Names are normalised
on both sides and tried exact -> prefix -> contains, same order of confidence.
"""

import re
import unicodedata
from datetime import datetime, timezone

SOURCE_DEFAULT = 'assigned list'


def normalise(value):
    text = unicodedata.normalize('NFKC', str(value or ''))
    text = re.sub(r'[ً-ٟـ]', '', text)   # diacritics and tatweel
    for a, b in (('أ', 'ا'), ('إ', 'ا'), ('آ', 'ا'), ('ى', 'ي'),
                 ('ة', 'ه'), ('ؤ', 'و'), ('ئ', 'ي')):
        text = text.replace(a, b)
    text = re.sub(r'[^\w\s]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip().lower()


def match(conn, names):
    """Resolve a list of customer names to partner ids. Returns (hits, misses).

    hits: list of {name, partner_id, odoo_name, how}
    misses: names that matched nothing in the synced customer list.
    """
    partners = [dict(r) for r in conn.execute(
        'SELECT partner_id, name FROM customers', []).fetchall()]
    for p in partners:
        p['norm'] = normalise(p['name'])

    hits, misses = [], []
    for name in names:
        needle = normalise(name)
        if not needle:
            continue
        exact = [p for p in partners if p['norm'] == needle]
        prefix = [p for p in partners
                  if p['norm'].startswith(needle) or needle.startswith(p['norm'])]
        contains = [p for p in partners if needle in p['norm'] or p['norm'] in needle]
        found = exact or prefix or contains
        how = 'exact' if exact else ('prefix' if prefix else ('contains' if contains else ''))
        if not found:
            misses.append(name)
            continue
        for p in found:
            hits.append({'name': name, 'partner_id': p['partner_id'],
                         'odoo_name': p['name'], 'how': how})
    return hits, misses


def apply(conn, names, source=SOURCE_DEFAULT):
    """Match and flag customer_meta.assigned = 1 for every hit."""
    hits, misses = match(conn, names)
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for h in hits:
        conn.execute(
            'INSERT INTO customer_meta (partner_id, assigned, assigned_source, updated_at)'
            ' VALUES (?,1,?,?)'
            ' ON CONFLICT(partner_id) DO UPDATE SET assigned = 1,'
            '   assigned_source = excluded.assigned_source,'
            '   updated_at = excluded.updated_at',
            [h['partner_id'], source, now],
        )
    return {
        'listed': len(names),
        'matched': len(hits),
        'hits': hits,
        'unmatched': misses,
    }
