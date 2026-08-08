#!/usr/bin/env python3
"""Derma City Collector — API.

A personal field-collection tool: receivables aging (synced from Odoo, same
model as the receivables tracker) plus everything a collector actually needs
in the field — contact people and their positions, a call/visit log, signed
reconciliation proof, map locations, a proximity-based weekly visit plan, a
calendar, and an Arabic script bank for common customer responses.

Deployed on Vercel as a single Python Function (this file, `handler`) with
vercel.json rewriting every /api/* request here. The real requested path
travels through as a __path query param (see _path()) because Vercel's
Python runtime hands the function the *rewritten* destination path, not the
one the browser actually asked for — confirmed the hard way deploying the
receivables tracker.
"""

import base64
import binascii
import io
import json
import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aging
import assign
import db
import export
import odoo_sync
import routing

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), encoding='utf-8') as fh:
    CONFIG = json.load(fh)

for env_key, cfg_key in (('ODOO_URL', 'url'), ('ODOO_DB', 'db'),
                         ('ODOO_USER', 'username'), ('ODOO_PASSWORD', 'password')):
    if os.environ.get(env_key):
        CONFIG['odoo'][cfg_key] = os.environ[env_key]

CRON_SECRET = os.environ.get('CRON_SECRET', '')
MIN_SYNC_GAP_SECONDS = 120

# Both companies in this Odoo instance are Saudi Arabia-registered and the
# large majority of its users are set to Asia/Riyadh — confirmed directly
# against Odoo while fixing the identical bug in the receivables tracker,
# where the server's UTC "today" quietly disagreed with Riyadh's for part of
# every day. Every calendar-day computation in this file goes through
# business_today()/business_now() instead of a bare datetime.now().
BUSINESS_TZ = ZoneInfo(CONFIG.get('timezone', 'Asia/Riyadh'))

# Vercel Functions cap request bodies at ~4.5MB regardless of what this app
# says — base64 inflates a photo by ~33%, so 3MB of decoded image plus JSON
# overhead stays safely under that rather than risking a raw 413 the client-
# side error message below couldn't explain.
MAX_IMAGE_BYTES = 3 * 1024 * 1024

# Run at import time so most cold starts pay this cost once, up front — but
# never let it take the whole function down. A Turso hiccup (bad token,
# paused database, wrong URL) used to raise here, which crashes the import
# itself and turns every route into an opaque FUNCTION_INVOCATION_FAILED
# with no diagnostic. Deferring the failure to request time means each
# request instead gets a real 503 explaining what's wrong, and the next
# cold start simply retries db.init() on its own.
DB_INIT_ERROR = None
try:
    db.init()
except Exception as exc:
    DB_INIT_ERROR = str(exc)
    traceback.print_exc()


def business_now():
    return datetime.now(BUSINESS_TZ)


def business_today():
    return business_now().date()


# --------------------------------------------------------------------------- helpers

def run_sync():
    return odoo_sync.sync(CONFIG, progress=None)


def seconds_since_last_sync(conn):
    row = conn.execute('SELECT synced_at FROM sync_log ORDER BY id DESC LIMIT 1', []).fetchone()
    if not row or not row['synced_at']:
        return None
    try:
        last = datetime.fromisoformat(row['synced_at'])
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds()


MAPS_PATTERNS = (
    # A full "place" URL (.../place/Name/@lat,lng,zoom/data=...!3dLAT!4dLNG...)
    # carries two different coordinate pairs: @lat,lng is just wherever the map
    # viewport happened to be centred (panning or zooming changes it, and nothing
    # says it matches the pin), while !3d...!4d... is the actual marked place —
    # Google's own convention for the pin's real location. Checked first, since
    # trusting @lat,lng instead silently placed a real customer here ~30km off
    # in testing (same latitude, very different longitude) before this was caught.
    re.compile(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)'),
    re.compile(r'@(-?\d+\.\d+),(-?\d+\.\d+)'),   # .../@24.7136,46.6753,15z/... (viewport only)
    re.compile(r'[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)'),  # ?q=24.7136,46.6753
    re.compile(r'^\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*$'),  # bare "24.7136, 46.6753"
)


def parse_location(text):
    """Pull lat/lng out of a pasted Google Maps URL or a bare "lat,lng" string."""
    text = (text or '').strip()
    for pattern in MAPS_PATTERNS:
        m = pattern.search(text)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None, None


def promise_broken(c, today_iso):
    return bool(c['promise_date']) and c['promise_date'] < today_iso and c['status'] == 'promised'


def action_due(c, today_iso):
    return bool(c['next_action_date']) and c['next_action_date'] <= today_iso


def filter_customers(customers, params, today_iso):
    assigned_only = params.get('assigned', '1') != '0'
    # Handed to an agency means the collector is done working the account —
    # hidden by default the same way the receivables tracker's own agency
    # flag works, but never actually removed: "only" surfaces exactly who's
    # been handed off, for whoever needs to check on that.
    agency_mode = params.get('agency') or 'hide'
    q = (params.get('q') or '').strip().lower()
    status = params.get('status') or ''
    area = params.get('area') or ''
    needs_visit = params.get('needs_visit') == '1'
    has_overdue = params.get('has_overdue') == '1'
    due_only = params.get('due_only') == '1'
    try:
        minimum = float(params.get('min') or 0)
    except ValueError:
        minimum = 0.0

    out = []
    for c in customers:
        if assigned_only and not c['assigned']:
            continue
        if agency_mode == 'hide' and c['agency']:
            continue
        if agency_mode == 'only' and not c['agency']:
            continue
        if q and q not in c['name'].lower() and q not in (c['phone'] or '').lower():
            continue
        if status and c['status'] != status:
            continue
        if area and c['area'] != area:
            continue
        if needs_visit and not c['needs_visit']:
            continue
        if has_overdue and c['overdue_total'] <= 0:
            continue
        if minimum and c['total_open'] < minimum:
            continue
        if due_only and not (promise_broken(c, today_iso) or action_due(c, today_iso)):
            continue
        out.append(c)
    return out


def attention_items(customers, today_iso):
    broken, due = [], []
    for c in customers:
        if not c['assigned'] or c['agency']:
            continue
        if promise_broken(c, today_iso):
            broken.append({'partner_id': c['partner_id'], 'name': c['name'],
                           'date': c['promise_date'], 'amount': c['promise_amount']})
        if action_due(c, today_iso):
            due.append({'partner_id': c['partner_id'], 'name': c['name'],
                        'date': c['next_action_date'], 'amount': c['overdue_total']})
    broken.sort(key=lambda x: x['date'])
    due.sort(key=lambda x: x['date'])
    return {'broken_promises': broken, 'due_actions': due}


def week_start(d):
    """Monday of the week containing d — the Gregorian week is used purely as a
    stable 7-day bucket; the app's own day labels handle the Sun-Thu work week."""
    return d - timedelta(days=d.weekday())


# --------------------------------------------------------------------------- handler

class handler(BaseHTTPRequestHandler):
    server_version = 'DermaCollector/1.0'

    def log_message(self, fmt, *args):
        if os.environ.get('TRACKER_VERBOSE'):
            super().log_message(fmt, *args)

    def _send(self, code, body=b'', content_type='application/json; charset=utf-8', extra_headers=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode('utf-8'))

    def _error(self, code, message):
        self._json({'error': message}, code)

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError:
            return {}

    def _path(self):
        qs = parse_qs(urlparse(self.path).query)
        smuggled = qs.get('__path')
        if smuggled:
            return urlparse(smuggled[0]).path
        return urlparse(self.path).path

    # -- routing ------------------------------------------------------------
    def do_GET(self):
        path = self._path()
        params = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        if DB_INIT_ERROR:
            return self._error(503, f'Database unavailable: {DB_INIT_ERROR}')
        try:
            if path == '/api/bootstrap':
                return self.api_bootstrap()
            if path == '/api/customers':
                return self.api_customers(params)
            if path == '/api/sync':
                return self.api_sync_status()
            if path == '/api/scripts':
                return self.api_scripts_list(params)
            if path == '/api/plan':
                return self.api_plan_get(params)
            if path == '/api/calendar':
                return self.api_calendar(params)
            if path == '/api/export.xlsx':
                return self.api_export()
            match = re.fullmatch(r'/api/customers/(\d+)', path)
            if match:
                return self.api_customer_detail(int(match.group(1)))
            match = re.fullmatch(r'/api/reconciliations/(\d+)/image', path)
            if match:
                return self.api_reconciliation_image(int(match.group(1)))
            return self._error(404, 'Not found')
        except BrokenPipeError:
            pass
        except Exception as exc:
            traceback.print_exc()
            self._error(500, str(exc))

    def do_POST(self):
        path = self._path()
        if DB_INIT_ERROR:
            return self._error(503, f'Database unavailable: {DB_INIT_ERROR}')
        try:
            if path == '/api/sync':
                return self.api_sync_trigger()
            if path == '/api/assign':
                return self.api_assign()
            if path == '/api/scripts':
                return self.api_scripts_add()
            if path == '/api/plan':
                return self.api_plan_set()
            if path == '/api/plan/generate':
                return self.api_plan_generate()
            match = re.fullmatch(r'/api/customers/(\d+)/location', path)
            if match:
                return self.api_set_location(int(match.group(1)))
            match = re.fullmatch(r'/api/customers/(\d+)/meta', path)
            if match:
                return self.api_set_meta(int(match.group(1)))
            match = re.fullmatch(r'/api/customers/(\d+)/contacts', path)
            if match:
                return self.api_add_contact(int(match.group(1)))
            match = re.fullmatch(r'/api/customers/(\d+)/visits', path)
            if match:
                return self.api_add_visit(int(match.group(1)))
            match = re.fullmatch(r'/api/customers/(\d+)/reconciliation', path)
            if match:
                return self.api_add_reconciliation(int(match.group(1)))
            match = re.fullmatch(r'/api/contacts/(\d+)/delete', path)
            if match:
                return self.api_delete_contact(int(match.group(1)))
            match = re.fullmatch(r'/api/scripts/(\d+)/delete', path)
            if match:
                return self.api_delete_script(int(match.group(1)))
            match = re.fullmatch(r'/api/plan/(\d+)/delete', path)
            if match:
                return self.api_delete_plan(int(match.group(1)))
            return self._error(404, 'Not found')
        except BrokenPipeError:
            pass
        except Exception as exc:
            traceback.print_exc()
            self._error(500, str(exc))

    do_HEAD = do_GET

    # -- bootstrap / list -----------------------------------------------------
    def api_bootstrap(self):
        conn = db.connect()
        try:
            last_res, assigned_res = conn.select_many([
                ('SELECT synced_at, lines, customers, total_open FROM sync_log'
                 ' ORDER BY id DESC LIMIT 1', []),
                ('SELECT COUNT(*) AS n FROM customer_meta WHERE assigned = 1', []),
            ])
            last = last_res.fetchone()
            payload = {
                'app_label': CONFIG.get('app_label', 'Derma City Collector'),
                'currency': CONFIG.get('currency', 'SAR'),
                'odoo_url': (CONFIG.get('odoo') or {}).get('url', '').rstrip('/'),
                'has_data': db.has_data(conn),
                'last_sync': dict(last) if last else None,
                'today': business_today().isoformat(),
                'statuses': [{'key': k, 'label': v} for k, v in db.STATUSES],
                'channels': [{'key': k, 'label': v} for k, v in db.CHANNELS],
                'assigned_count': assigned_res.fetchone()['n'],
            }
        finally:
            conn.close()
        self._json(payload)

    def api_customers(self, params):
        conn = db.connect()
        try:
            everything = aging.build(conn, business_today())
        finally:
            conn.close()
        today_iso = business_today().isoformat()
        for c in everything:
            c['promise_broken'] = promise_broken(c, today_iso)
            c['action_due'] = action_due(c, today_iso)
        filtered = filter_customers(everything, params, today_iso)
        assigned_all = [c for c in everything if c['assigned']]
        active = [c for c in assigned_all if not c['agency']]
        handed_off = [c for c in assigned_all if c['agency']]
        self._json({
            'customers': filtered,
            'totals': {
                'count': len(filtered),
                'total_open': round(sum(c['total_open'] for c in filtered), 2),
                'overdue_total': round(sum(c['overdue_total'] for c in filtered), 2),
            },
            # The collector's active book — assigned, still theirs to chase.
            'grand_totals': {
                'count': len(active),
                'total_open': round(sum(c['total_open'] for c in active), 2),
                'overdue_total': round(sum(c['overdue_total'] for c in active), 2),
            },
            # Still a real receivable, still counted in Odoo — just no longer
            # this collector's to work day to day.
            'agency': {
                'count': len(handed_off),
                'total_open': round(sum(c['total_open'] for c in handed_off), 2),
                'overdue_total': round(sum(c['overdue_total'] for c in handed_off), 2),
            },
            'attention': attention_items(everything, today_iso),
            'areas': sorted({c['area'] for c in assigned_all if c['area']}),
        })

    def api_customer_detail(self, partner_id):
        conn = db.connect()
        try:
            everything = aging.build(conn, business_today())
            customer = next((c for c in everything if c['partner_id'] == partner_id), None)
            if customer is None:
                return self._error(404, 'No open receivable for this customer')
            docs_res, contacts_res, visits_res, recon_res = conn.select_many([
                ('SELECT line_id, doc, ref, journal, inv_date, due_date, original, residual'
                 '  FROM documents WHERE partner_id = ? ORDER BY due_date', [partner_id]),
                ('SELECT id, name, position, phone, notes, created_at FROM contacts'
                 ' WHERE partner_id = ? ORDER BY created_at DESC', [partner_id]),
                ('SELECT v.id, v.contact_id, v.channel, v.status, v.outcome, v.promise_date,'
                 '       v.promise_amount, v.next_action_date, v.notes, v.created_at,'
                 '       c.name AS contact_name, c.position AS contact_position'
                 '  FROM visits v LEFT JOIN contacts c ON c.id = v.contact_id'
                 ' WHERE v.partner_id = ? ORDER BY v.created_at DESC', [partner_id]),
                ('SELECT id, amount, reconciled_date, signed_by, signed_position, notes, created_at,'
                 "       (image_base64 != '') AS has_image"
                 '  FROM reconciliations WHERE partner_id = ? ORDER BY reconciled_date DESC',
                 [partner_id]),
            ])
            today = business_today()
            documents = []
            for d in docs_res.fetchall():
                days = aging.days_overdue(d['due_date'], today)
                documents.append({**dict(d), 'days': days, 'band': aging.band_for(days)})
            documents.sort(key=lambda d: -d['days'])
        finally:
            conn.close()
        self._json({
            'customer': customer,
            'documents': documents,
            'contacts': [dict(r) for r in contacts_res.fetchall()],
            'visits': [dict(r) for r in visits_res.fetchall()],
            'reconciliations': [dict(r) for r in recon_res.fetchall()],
            'odoo_url': f"{CONFIG['odoo']['url']}/odoo/res.partner/{partner_id}",
        })

    # -- location / meta -------------------------------------------------------
    def api_set_location(self, partner_id):
        payload = self._body()
        lat, lng = payload.get('lat'), payload.get('lng')
        if lat is None or lng is None:
            lat, lng = parse_location(payload.get('text', ''))
        if lat is None or lng is None:
            return self._error(400, 'Could not read a location from that — paste a Google Maps '
                                     'link, or "lat, lng", or use "Use my location".')
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return self._error(400, 'That location is out of range.')
        conn = db.connect()
        try:
            db.ensure_meta(conn, partner_id)
            conn.execute(
                'UPDATE customer_meta SET lat = ?, lng = ?, location_note = ?, updated_at = ?'
                ' WHERE partner_id = ?',
                [lat, lng, (payload.get('location_note') or '').strip(),
                 datetime.now(timezone.utc).isoformat(timespec='seconds'), partner_id],
            )
        finally:
            conn.close()
        self._json({'lat': lat, 'lng': lng})

    def api_set_meta(self, partner_id):
        payload = self._body()
        status = payload.get('status')
        if status is not None and status not in db.STATUS_KEYS:
            return self._error(400, f'Unknown status: {status}')
        conn = db.connect()
        try:
            db.ensure_meta(conn, partner_id)
            sets, params = [], []
            for field, key in (('status', 'status'), ('promise_date', 'promise_date'),
                               ('next_action_date', 'next_action_date')):
                if key in payload:
                    sets.append(f'{field} = ?')
                    params.append((payload[key] or '').strip())
            if 'promise_amount' in payload:
                try:
                    sets.append('promise_amount = ?')
                    params.append(float(payload['promise_amount'] or 0))
                except (TypeError, ValueError):
                    pass
            if 'needs_visit' in payload:
                sets.append('needs_visit = ?')
                params.append(1 if payload['needs_visit'] else 0)
            if 'agency' in payload:
                is_agency = bool(payload['agency'])
                sets.append('agency = ?')
                params.append(1 if is_agency else 0)
                sets.append('agency_date = ?')
                params.append(business_today().isoformat() if is_agency else '')
            if 'agency_note' in payload:
                sets.append('agency_note = ?')
                params.append((payload['agency_note'] or '').strip())
            sets.append('updated_at = ?')
            params.append(datetime.now(timezone.utc).isoformat(timespec='seconds'))
            params.append(partner_id)
            conn.execute(f'UPDATE customer_meta SET {", ".join(sets)} WHERE partner_id = ?', params)
            row = conn.execute('SELECT * FROM customer_meta WHERE partner_id = ?',
                               [partner_id]).fetchone()
        finally:
            conn.close()
        self._json({'meta': dict(row)})

    # -- contacts ---------------------------------------------------------------
    def api_add_contact(self, partner_id):
        payload = self._body()
        name = (payload.get('name') or '').strip()
        if not name:
            return self._error(400, 'Contact name is required')
        conn = db.connect()
        try:
            cur = conn.execute(
                'INSERT INTO contacts (partner_id, name, position, phone, notes, created_at)'
                ' VALUES (?,?,?,?,?,?)',
                [partner_id, name, (payload.get('position') or '').strip(),
                 (payload.get('phone') or '').strip(), (payload.get('notes') or '').strip(),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')],
            )
            row = conn.execute('SELECT * FROM contacts WHERE id = ?', [cur.lastrowid]).fetchone()
        finally:
            conn.close()
        self._json({'contact': dict(row)})

    def api_delete_contact(self, contact_id):
        conn = db.connect()
        try:
            conn.execute('DELETE FROM contacts WHERE id = ?', [contact_id])
        finally:
            conn.close()
        self._json({'ok': True})

    # -- visits -------------------------------------------------------------------
    def api_add_visit(self, partner_id):
        payload = self._body()
        channel = payload.get('channel', 'visit')
        if channel not in db.CHANNEL_KEYS:
            return self._error(400, f'Unknown channel: {channel}')
        status = payload.get('status', 'new')
        if status not in db.STATUS_KEYS:
            return self._error(400, f'Unknown status: {status}')
        try:
            promise_amount = float(payload.get('promise_amount') or 0)
        except (TypeError, ValueError):
            promise_amount = 0.0
        contact_id = payload.get('contact_id')
        try:
            contact_id = int(contact_id) if contact_id else None
        except (TypeError, ValueError):
            contact_id = None

        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        promise_date = (payload.get('promise_date') or '').strip()
        next_action_date = (payload.get('next_action_date') or '').strip()
        conn = db.connect()
        try:
            db.ensure_meta(conn, partner_id)
            cur = conn.execute(
                'INSERT INTO visits (partner_id, contact_id, channel, status, outcome,'
                ' promise_date, promise_amount, next_action_date, notes, created_at)'
                ' VALUES (?,?,?,?,?,?,?,?,?,?)',
                [partner_id, contact_id, channel, status,
                 (payload.get('outcome') or '').strip(), promise_date, promise_amount,
                 next_action_date, (payload.get('notes') or '').strip(), now],
            )
            # The current-state row (customer_meta) mirrors the latest interaction —
            # exactly how followups tracked the single "current" state in the
            # receivables tracker, just triggered here by logging a visit instead
            # of a separate save.
            conn.execute(
                'UPDATE customer_meta SET status = ?, promise_date = ?, promise_amount = ?,'
                ' next_action_date = ?, updated_at = ? WHERE partner_id = ?',
                [status, promise_date, promise_amount, next_action_date, now, partner_id],
            )
            row = conn.execute('SELECT * FROM visits WHERE id = ?', [cur.lastrowid]).fetchone()
        finally:
            conn.close()
        self._json({'visit': dict(row)})

    # -- reconciliation -------------------------------------------------------------
    def api_add_reconciliation(self, partner_id):
        payload = self._body()
        try:
            amount = float(payload.get('amount') or 0)
        except (TypeError, ValueError):
            return self._error(400, 'Amount must be a number')
        reconciled_date = (payload.get('reconciled_date') or '').strip() or business_today().isoformat()
        image_b64 = payload.get('image_base64') or ''
        if image_b64:
            # Strip a data: URL prefix if the browser sent one whole, and check
            # the actual decoded size rather than trusting the string length —
            # base64 inflates size by ~33%, but the real cap is on stored bytes.
            image_b64 = re.sub(r'^data:image/\w+;base64,', '', image_b64)
            try:
                decoded_len = len(base64.b64decode(image_b64, validate=True))
            except (binascii.Error, ValueError):
                return self._error(400, 'That image did not decode — try a different photo.')
            if decoded_len > MAX_IMAGE_BYTES:
                return self._error(400, 'Photo is too large (max ~6MB) — the app compresses '
                                         'photos automatically, so try retaking it.')
        conn = db.connect()
        try:
            cur = conn.execute(
                'INSERT INTO reconciliations (partner_id, amount, reconciled_date, signed_by,'
                ' signed_position, image_base64, notes, created_at)'
                ' VALUES (?,?,?,?,?,?,?,?)',
                [partner_id, amount, reconciled_date, (payload.get('signed_by') or '').strip(),
                 (payload.get('signed_position') or '').strip(), image_b64,
                 (payload.get('notes') or '').strip(),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')],
            )
            row = conn.execute(
                'SELECT id, partner_id, amount, reconciled_date, signed_by, signed_position,'
                ' notes, created_at, (image_base64 != \'\') AS has_image'
                ' FROM reconciliations WHERE id = ?', [cur.lastrowid]).fetchone()
        finally:
            conn.close()
        self._json({'reconciliation': dict(row)})

    def api_reconciliation_image(self, reconciliation_id):
        conn = db.connect()
        try:
            row = conn.execute('SELECT image_base64 FROM reconciliations WHERE id = ?',
                               [reconciliation_id]).fetchone()
        finally:
            conn.close()
        if not row or not row['image_base64']:
            return self._error(404, 'No photo on this reconciliation')
        try:
            raw = base64.b64decode(row['image_base64'], validate=True)
        except (binascii.Error, ValueError):
            return self._error(500, 'Stored photo is not valid image data')
        # compressImage() in app.js always encodes as JPEG before upload, so
        # this is a safe assumption for every photo that came through the
        # app's own upload flow rather than something worth storing per row.
        self._send(200, raw, 'image/jpeg', {'Cache-Control': 'private, max-age=86400'})

    # -- script bank -------------------------------------------------------------
    def api_scripts_list(self, params):
        conn = db.connect()
        try:
            category = params.get('category')
            if category:
                rows = conn.execute(
                    'SELECT * FROM scripts WHERE category = ? ORDER BY sort_order, id',
                    [category]).fetchall()
            else:
                rows = conn.execute('SELECT * FROM scripts ORDER BY category, sort_order, id',
                                    []).fetchall()
            categories = conn.execute(
                'SELECT DISTINCT category FROM scripts ORDER BY category', []).fetchall()
        finally:
            conn.close()
        self._json({'scripts': [dict(r) for r in rows],
                    'categories': [r['category'] for r in categories]})

    def api_scripts_add(self):
        payload = self._body()
        script_ar = (payload.get('script_ar') or '').strip()
        if not script_ar:
            return self._error(400, 'The Arabic script text is required')
        conn = db.connect()
        try:
            cur = conn.execute(
                'INSERT INTO scripts (category, trigger_text, script_ar, tip, sort_order,'
                ' is_custom, created_at) VALUES (?,?,?,?,999,1,?)',
                [(payload.get('category') or 'Custom').strip(),
                 (payload.get('trigger_text') or '').strip(), script_ar,
                 (payload.get('tip') or '').strip(),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')],
            )
            row = conn.execute('SELECT * FROM scripts WHERE id = ?', [cur.lastrowid]).fetchone()
        finally:
            conn.close()
        self._json({'script': dict(row)})

    def api_delete_script(self, script_id):
        conn = db.connect()
        try:
            conn.execute('DELETE FROM scripts WHERE id = ?', [script_id])
        finally:
            conn.close()
        self._json({'ok': True})

    # -- weekly plan -------------------------------------------------------------
    def api_plan_get(self, params):
        start = params.get('week') or week_start(business_today()).isoformat()
        end = (aging.parse_date(start) + timedelta(days=6)).isoformat()
        conn = db.connect()
        try:
            rows = conn.execute(
                'SELECT p.id, p.partner_id, p.planned_date, p.status, p.note, p.reason,'
                '       c.name, c.phone, c.city, m.lat, m.lng, m.status AS customer_status,'
                '       (SELECT ROUND(SUM(d.residual), 2) FROM documents d'
                '         WHERE d.partner_id = p.partner_id) AS total_open,'
                '       (SELECT COUNT(*) FROM visits v WHERE v.partner_id = p.partner_id) AS visit_count'
                '  FROM visit_plan p'
                '  LEFT JOIN customers c ON c.partner_id = p.partner_id'
                '  LEFT JOIN customer_meta m ON m.partner_id = p.partner_id'
                ' WHERE p.planned_date >= ? AND p.planned_date <= ?'
                ' ORDER BY p.planned_date, p.id',
                [start, end]).fetchall()
        finally:
            conn.close()
        stops = [dict(r) for r in rows]
        for s in stops:
            # "New to me" — never contacted before this plan was made, so a
            # visit here is a first introduction, not a follow-up. Framed
            # around visit_count rather than status alone: a customer can be
            # reset to 'new' after a failed contact attempt and still not
            # really be new to the collector.
            s['is_new'] = (s['customer_status'] == 'new' and s['visit_count'] == 0)
        self._json({'week_start': start, 'week_end': end, 'stops': stops})

    def api_plan_set(self):
        payload = self._body()
        partner_id = payload.get('partner_id')
        planned_date = (payload.get('planned_date') or '').strip()
        if not partner_id or not planned_date:
            return self._error(400, 'partner_id and planned_date are required')
        status = payload.get('status', 'planned')
        conn = db.connect()
        try:
            conn.execute(
                'INSERT INTO visit_plan (partner_id, planned_date, status, note, reason, created_at)'
                ' VALUES (?,?,?,?,?,?)'
                ' ON CONFLICT(partner_id, planned_date) DO UPDATE SET'
                '   status = excluded.status, note = excluded.note, reason = excluded.reason',
                [int(partner_id), planned_date, status, (payload.get('note') or '').strip(),
                 (payload.get('reason') or '').strip(),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')],
            )
            row = conn.execute(
                'SELECT * FROM visit_plan WHERE partner_id = ? AND planned_date = ?',
                [int(partner_id), planned_date]).fetchone()
        finally:
            conn.close()
        self._json({'stop': dict(row)})

    def api_delete_plan(self, plan_id):
        conn = db.connect()
        try:
            conn.execute('DELETE FROM visit_plan WHERE id = ?', [plan_id])
        finally:
            conn.close()
        self._json({'ok': True})

    def api_plan_generate(self):
        payload = self._body()
        start = (payload.get('week') or '').strip() or week_start(business_today()).isoformat()
        days = int(payload.get('days') or 6)
        conn = db.connect()
        try:
            everything = aging.build(conn, business_today())
            today_iso = business_today().isoformat()
            for c in everything:
                c['promise_broken'] = promise_broken(c, today_iso)
            candidates = [c for c in everything if c['assigned'] and not c['agency'] and
                         (c['overdue_total'] > 0 or c['needs_visit'])]
            plan, unplaced = routing.suggest_plan(candidates, days=days)

            start_date = aging.parse_date(start)
            conn.execute(
                'DELETE FROM visit_plan WHERE planned_date >= ? AND planned_date <= ?'
                " AND status = 'planned'",
                [start, (start_date + timedelta(days=6)).isoformat()],
            )
            now = datetime.now(timezone.utc).isoformat(timespec='seconds')
            rows = []
            for offset, day_stops in enumerate(plan):
                planned_date = (start_date + timedelta(days=offset)).isoformat()
                for stop in day_stops:
                    rows.append({'partner_id': stop['partner_id'], 'planned_date': planned_date,
                                'status': 'planned',
                                'note': (f"~{stop['hop_km']}km from previous stop"
                                        if stop.get('hop_km') else ''),
                                'created_at': now})
            if rows:
                conn.executemany(
                    'INSERT INTO visit_plan (partner_id, planned_date, status, note, created_at)'
                    ' VALUES (:partner_id,:planned_date,:status,:note,:created_at)'
                    ' ON CONFLICT(partner_id, planned_date) DO NOTHING',
                    rows,
                )
        finally:
            conn.close()
        self._json({
            'week_start': start,
            'planned': len(rows),
            'unplaced': [{'partner_id': c['partner_id'], 'name': c['name'],
                         'overdue_total': c['overdue_total']} for c in unplaced],
        })

    # -- calendar -------------------------------------------------------------
    def api_calendar(self, params):
        month = params.get('month') or business_today().strftime('%Y-%m')
        conn = db.connect()
        try:
            plan_res, promises_res, actions_res = conn.select_many([
                ('SELECT p.planned_date AS d, c.name, p.status, p.partner_id'
                 '  FROM visit_plan p LEFT JOIN customers c ON c.partner_id = p.partner_id'
                 ' WHERE substr(p.planned_date,1,7) = ?', [month]),
                ('SELECT m.promise_date AS d, c.name, m.promise_amount, m.partner_id'
                 '  FROM customer_meta m LEFT JOIN customers c ON c.partner_id = m.partner_id'
                 " WHERE substr(m.promise_date,1,7) = ? AND m.assigned = 1 AND m.agency = 0", [month]),
                ('SELECT m.next_action_date AS d, c.name, m.partner_id'
                 '  FROM customer_meta m LEFT JOIN customers c ON c.partner_id = m.partner_id'
                 " WHERE substr(m.next_action_date,1,7) = ? AND m.assigned = 1 AND m.agency = 0", [month]),
            ])
            days = {}
            for r in plan_res.fetchall():
                days.setdefault(r['d'], {'visits': [], 'promises': [], 'actions': []})
                days[r['d']]['visits'].append({'partner_id': r['partner_id'], 'name': r['name'],
                                               'status': r['status']})
            for r in promises_res.fetchall():
                days.setdefault(r['d'], {'visits': [], 'promises': [], 'actions': []})
                days[r['d']]['promises'].append({'partner_id': r['partner_id'], 'name': r['name'],
                                                 'amount': r['promise_amount']})
            for r in actions_res.fetchall():
                days.setdefault(r['d'], {'visits': [], 'promises': [], 'actions': []})
                days[r['d']]['actions'].append({'partner_id': r['partner_id'], 'name': r['name']})
        finally:
            conn.close()
        self._json({'month': month, 'days': days})

    # -- assign -------------------------------------------------------------
    def api_assign(self):
        payload = self._body()
        names = payload.get('names') or []
        if isinstance(names, str):
            names = [n.strip() for n in names.split('\n') if n.strip()]
        if not names:
            return self._error(400, 'Provide a list of customer names')
        conn = db.connect()
        try:
            result = assign.apply(conn, names)
        finally:
            conn.close()
        self._json(result)

    # -- export -------------------------------------------------------------
    def api_export(self):
        conn = db.connect()
        try:
            everything = aging.build(conn, business_today())
            assigned = [c for c in everything if c['assigned']]
            xlsx = export.to_bytes(assigned, business_today().isoformat(),
                                   CONFIG.get('currency', 'SAR'), conn)
        finally:
            conn.close()
        stamp = business_today().strftime('%Y-%m-%d')
        self._send(
            200, xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            {'Content-Disposition': f'attachment; filename="Collector_{stamp}.xlsx"'},
        )

    # -- sync -------------------------------------------------------------
    def api_sync_status(self):
        conn = db.connect()
        try:
            last = conn.execute(
                'SELECT synced_at, lines, customers, total_open FROM sync_log'
                ' ORDER BY id DESC LIMIT 1', []).fetchone()
        finally:
            conn.close()
        self._json({'last_sync': dict(last) if last else None})

    def api_sync_trigger(self):
        is_cron = CRON_SECRET and self.headers.get('X-Cron-Secret') == CRON_SECRET
        if not is_cron:
            conn = db.connect()
            try:
                gap = seconds_since_last_sync(conn)
            finally:
                conn.close()
            if gap is not None and gap < MIN_SYNC_GAP_SECONDS:
                return self._error(429, f'Synced {int(gap)}s ago — try again in '
                                         f'{MIN_SYNC_GAP_SECONDS - int(gap)}s.')
        try:
            result = run_sync()
        except Exception as exc:
            traceback.print_exc()
            return self._error(502, f'Sync failed: {exc}')
        self._json({'result': result})


# --------------------------------------------------------------------------- local dev

def main():
    import argparse
    import threading
    import webbrowser

    parser = argparse.ArgumentParser(description='Derma City Collector (dev server)')
    parser.add_argument('--port', type=int, default=CONFIG.get('port', 5090))
    parser.add_argument('--sync', action='store_true')
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()

    if args.sync:
        result = odoo_sync.sync(CONFIG, lambda m: print('  ' + m))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler)
    url = f'http://localhost:{args.port}'
    print(f'\n  {CONFIG.get("app_label", "Derma City Collector")} (dev)', flush=True)
    print(f'  {url}  (frontend served from public/ — see README)', flush=True)
    print('  Ctrl+C to stop\n', flush=True)
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
        server.shutdown()


if __name__ == '__main__':
    main()
