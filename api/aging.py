"""Read-time aging — identical model to the receivables tracker.

Documents are stored with their due date only; age is recomputed on every read
against today's date (in the collector's own timezone — see business_today()
in index.py), so the numbers keep moving between syncs instead of freezing at
whatever the last sync saw.
"""

from datetime import date

NOT_DUE = 'Not Due'

LADDER = [
    (1, 30, '1-30'),
    (31, 60, '31-60'),
    (61, 90, '61-90'),
    (91, 179, '91-179'),
    (180, 269, '180-269'),
    (270, 364, '270-364'),
    (365, 545, '365-545'),
    (546, None, '546+'),
]

BAND_TITLES = {
    NOT_DUE: 'Within terms',
    '1-30': '1–30 days',
    '31-60': '1–2 months',
    '61-90': '2–3 months',
    '91-179': '3–6 months',
    '180-269': '6–9 months',
    '270-364': '9–12 months',
    '365-545': '1–1.5 years',
    '546+': 'Over 1.5 years',
}


def parse_date(s):
    y, m, d = (int(p) for p in s.split('-'))
    return date(y, m, d)


def days_overdue(due_date, as_of):
    return (as_of - parse_date(due_date)).days


def band_for(days):
    if days <= 0:
        return NOT_DUE
    for low, high, label in LADDER:
        if days >= low and (high is None or days <= high):
            return label
    return LADDER[-1][2]


def band_label(band):
    return BAND_TITLES.get(band, band)


def build(conn, as_of):
    """Every open customer, aged as of `as_of` (a date, always passed in
    explicitly by the caller — see business_today() in index.py for why this
    is never computed with a bare date.today() here)."""
    rows = conn.execute(
        'SELECT c.partner_id, c.name, c.phone, c.mobile, c.email, c.city,'
        '       c.payment_term, c.term_days, c.credit_limit, c.area, c.company,'
        '       d.line_id, d.doc, d.ref, d.journal, d.inv_date, d.due_date,'
        '       d.original, d.residual,'
        '       m.lat, m.lng, m.location_note, m.status, m.needs_visit,'
        '       m.assigned, m.assigned_source, m.agency, m.agency_date, m.agency_note,'
        '       m.promise_date, m.promise_amount, m.next_action_date, m.updated_at'
        '  FROM customers c'
        '  JOIN documents d ON d.partner_id = c.partner_id'
        '  LEFT JOIN customer_meta m ON m.partner_id = c.partner_id',
        [],
    ).fetchall()

    customers = {}
    for r in rows:
        pid = r['partner_id']
        c = customers.get(pid)
        if c is None:
            c = customers[pid] = {
                'partner_id': pid,
                'name': r['name'],
                'phone': r['phone'] or r['mobile'] or '',
                'email': r['email'] or '',
                'city': r['city'] or '',
                'company': r['company'] or '',
                'area': r['area'] or 'unassigned',
                'payment_term': r['payment_term'] or '',
                'term_days': r['term_days'],
                'credit_limit': r['credit_limit'] or 0.0,
                'lat': r['lat'],
                'lng': r['lng'],
                'location_note': r['location_note'] or '',
                'status': r['status'] or 'new',
                'needs_visit': bool(r['needs_visit']),
                'assigned': bool(r['assigned']),
                'assigned_source': r['assigned_source'] or '',
                'agency': bool(r['agency']),
                'agency_date': r['agency_date'] or '',
                'agency_note': r['agency_note'] or '',
                'promise_date': r['promise_date'] or '',
                'promise_amount': r['promise_amount'] or 0,
                'next_action_date': r['next_action_date'] or '',
                'updated_at': r['updated_at'] or '',
                'buckets': {},
                'total_open': 0.0,
                'overdue_total': 0.0,
                'not_due_total': 0.0,
                'documents': 0,
                'oldest_days': None,
                'oldest_due': '',
            }

        days = days_overdue(r['due_date'], as_of)
        residual = r['residual']
        c['total_open'] += residual
        c['documents'] += 1
        if days > 0:
            c['overdue_total'] += residual
        else:
            c['not_due_total'] += residual
        band = band_for(days)
        c['buckets'][band] = c['buckets'].get(band, 0.0) + residual
        if c['oldest_days'] is None or days > c['oldest_days']:
            c['oldest_days'] = days
            c['oldest_due'] = r['due_date']

    out = []
    for c in customers.values():
        for key in ('total_open', 'overdue_total', 'not_due_total'):
            c[key] = round(c[key], 2)
        c['buckets'] = {k: round(v, 2) for k, v in c['buckets'].items()}
        if c['oldest_days'] is None:
            c['oldest_days'] = 0
        c['over_limit'] = bool(c['credit_limit']) and c['total_open'] > c['credit_limit']
        out.append(c)
    out.sort(key=lambda c: -c['overdue_total'])
    return out
