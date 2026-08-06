"""Pulls open receivables for Derma City out of Odoo and into the local store.

Aging is deliberately left uncomputed here: due date and residual are stored
as-is, and aging.py ages them at read time so the numbers keep moving between
syncs. This is a trimmed copy of the combined receivables tracker's sync,
scoped to one company and without the cash-collected side — this app is about
field collection work, not reconciling bank receipts.
"""

import re
import xmlrpc.client
from datetime import datetime, timezone

import db

LINE_FIELDS = [
    'id', 'date', 'date_maturity', 'move_name', 'ref', 'name', 'journal_id',
    'balance', 'amount_residual', 'partner_id', 'company_id',
]
PARTNER_FIELDS = ['id', 'name', 'phone', 'mobile', 'email', 'vat', 'city',
                  'property_payment_term_id', 'credit_limit', 'company_id',
                  'region_id']


def term_days(label):
    if not label:
        return None
    if 'immediate' in label.lower():
        return 0
    match = re.search(r'(\d+)\s*days?', label, re.IGNORECASE)
    return int(match.group(1)) if match else None


class OdooError(RuntimeError):
    pass


class Odoo:
    def __init__(self, url, database, username, password):
        self.url = url.rstrip('/')
        self.db = database
        self.username = username
        self.password = password
        self.uid = None
        self._models = None

    def connect(self):
        try:
            common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common')
            version = common.version()
            self.uid = common.authenticate(self.db, self.username, self.password, {})
        except Exception as exc:
            raise OdooError(f'Could not reach {self.url}: {exc}') from exc
        if not self.uid:
            raise OdooError('Odoo rejected the credentials in config.json.')
        self._models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object')
        return version

    def call(self, model, method, args, kwargs=None, context=None):
        payload = dict(kwargs or {})
        if context:
            payload['context'] = context
        return self._models.execute_kw(
            self.db, self.uid, self.password, model, method, args, payload
        )


def _fetch_all(odoo, model, domain, fields, context, page=500):
    total = odoo.call(model, 'search_count', [domain], context=context)
    rows, offset = [], 0
    while offset < total:
        batch = odoo.call(
            model, 'search_read', [domain],
            {'fields': fields, 'limit': page, 'offset': offset, 'order': 'id'},
            context=context,
        )
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
    return rows


def sync(config, progress=None):
    """Replace the synced tables with a fresh pull. Collector work is untouched."""
    def say(msg):
        if progress:
            progress(msg)

    cfg = config['odoo']
    company_ids = config['company_ids']
    labels = config.get('company_labels', {})
    context = {'allowed_company_ids': company_ids}

    odoo = Odoo(cfg['url'], cfg['db'], cfg['username'], cfg['password'])
    say('Authenticating…')
    version = odoo.connect()

    say('Fetching open receivable lines…')
    domain = [
        ('parent_state', '=', 'posted'),
        ('account_id.account_type', '=', 'asset_receivable'),
        ('amount_residual', '!=', 0),
        ('company_id', 'in', company_ids),
    ]
    lines = _fetch_all(odoo, 'account.move.line', domain, LINE_FIELDS, context)
    say(f'Got {len(lines)} open lines. Fetching customer details…')

    partner_ids = sorted({l['partner_id'][0] for l in lines if l['partner_id']})
    partners = []
    for i in range(0, len(partner_ids), 300):
        partners.extend(odoo.call(
            'res.partner', 'read', [partner_ids[i:i + 300]],
            {'fields': PARTNER_FIELDS}, context=context,
        ))

    say('Writing to database…')
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    conn = db.connect()
    try:
        conn.execute('DELETE FROM documents')
        conn.execute('DELETE FROM customers')

        customer_rows = []
        for p in partners:
            term = p.get('property_payment_term_id')
            term_label = term[1] if term else ''
            customer_rows.append((
                p['id'], p.get('name') or '', p.get('phone') or '',
                p.get('mobile') or '', p.get('email') or '', p.get('vat') or '',
                p.get('city') or '',
                labels.get(str(p['company_id'][0])) if p.get('company_id') else '',
                (p['region_id'][1] if p.get('region_id') else 'unassigned'),
                term_label, term_days(term_label),
                p.get('credit_limit') or 0.0,
            ))
        conn.executemany(
            'INSERT INTO customers (partner_id, name, phone, mobile, email, vat,'
            ' city, company, area, payment_term, term_days, credit_limit)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            customer_rows,
        )

        conn.executemany(
            'INSERT INTO documents (line_id, partner_id, company_id, company,'
            ' doc, ref, journal, inv_date, due_date, original, residual)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            [(l['id'],
              l['partner_id'][0] if l['partner_id'] else 0,
              l['company_id'][0] if l.get('company_id') else 0,
              labels.get(str(l['company_id'][0]), '') if l.get('company_id') else '',
              l.get('move_name') or '',
              (l.get('ref') or l.get('name') or ''),
              l['journal_id'][1] if l.get('journal_id') else '',
              l['date'],
              l.get('date_maturity') or l['date'],
              round(l.get('balance') or 0.0, 2),
              round(l.get('amount_residual') or 0.0, 2)) for l in lines],
        )

        residual = round(sum(l.get('amount_residual') or 0.0 for l in lines), 2)
        conn.execute(
            'INSERT INTO sync_log (synced_at, lines, customers, total_open)'
            ' VALUES (?,?,?,?)',
            [now, len(lines), len(partners), residual],
        )
        db.set_setting(conn, 'last_sync', now)
    finally:
        conn.close()

    say('Done.')
    return {
        'synced_at': now,
        'lines': len(lines),
        'customers': len(partners),
        'total_open': residual,
        'server_version': version.get('server_version'),
    }
