"""Record provider calls and report on the ledger without reading chat transcripts."""
import csv
from datetime import date, datetime, timedelta, timezone
import io
import math
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Body, Depends, Query
from fastapi.responses import StreamingResponse

from .pricing import entry_cost
from .store import SUMS

DAY = 86400
COSTS = ', '.join(f"sum(CASE WHEN currency='{c}' THEN cost END) AS {c}" for c in ('USD', 'CNY'))
TEXT_COLS = {'session', 'source', 'profile_id', 'profile_name', 'format', 'model', 'effort', 'status', 'error', 'currency'}


def _csv_cell(col, value):
    """Neutralise spreadsheet formulas on text columns while leaving numbers untouched."""
    if value is None or col not in TEXT_COLS:
        return value
    s = str(value)
    if s and (s[0] in ('=', '+', '-', '@', '\t', '\r') or s.lstrip()[:1] in ('=', '+', '-', '@')):
        return "'" + s
    return s


def recorder(ctx, store, table, profile, provider, source, session=None):
    """Price each call from the pricing table as it is written: the model the reply names first, then the requested
    one (see pricing.Pricing.lookup). The cost is a snapshot; later price changes leave it alone."""

    def record(row):
        if session is not None:
            session.update(model=row['model'], profile_id=profile['id'], profile_name=profile['name'])
        try:
            entry = table.lookup(row.get('model'), provider.model)
            cost = entry_cost(entry, row) if entry else None
            store.add_usage({**row, 'source': source, 'session': session['id'] if session else None,
                             'ts': time.time(), 'format': profile['format'], 'effort': provider.effort,
                             'profile_id': profile['id'], 'profile_name': profile['name'],
                             'cost': cost, 'currency': entry['currency'] if cost is not None else None})
        except Exception as e:  # noqa: BLE001  accounting must never interrupt the provider loop
            ctx.log.warn(f'usage record not saved: {e}')
    return record


def with_cost(row):
    row = dict(row)
    row['cost'] = [{'currency': c, 'amount': row[c]} for c in ('CNY', 'USD') if row[c] is not None]
    del row['USD'], row['CNY']
    return row


def setup(ctx, store):
    def invalid(field):
        return ctx.error(400, 'usage_invalid', field=field)

    def number(value, field):
        try:
            if isinstance(value, bool):
                raise ValueError
            result = float(value)
            # Keep second-aligned bucket starts representable by SQLite and JSON clients.
            if not abs(result) < 2**53:
                raise ValueError
            return result
        except (ValueError, OverflowError, TypeError):
            raise invalid(field)

    def period(from_: str = Query(None, alias='from'), to: str = None, profile: str = None, model: str = None):
        end = time.time() if to is None else number(to, 'to')
        start = end - 30 * DAY if from_ is None else number(from_, 'from')
        if start < 0 or start >= end:
            raise invalid('to' if start >= end else 'from')
        where, args = 'WHERE ts >= :start AND ts < :end', {'start': start, 'end': end}
        for field, value in [('profile_id', profile), ('model', model)]:
            if value:
                where += f' AND {field} = :{field}'
                args[field] = value
        return where, args

    @ctx.router.get('/usage/summary')
    def summary(window=Depends(period)):
        where, args = window
        with store.connection() as db:
            db.execute('BEGIN')
            out = with_cost(db.execute(f'''SELECT count(*) calls, count(DISTINCT session) sessions, {SUMS}, {COSTS},
                coalesce(avg(ms),0) ms_avg, count(CASE WHEN status='error' THEN 1 END) errors
                FROM usage {where}''', args).fetchone())
            out['ms_p90'] = 0
            if out['calls']:
                rank = (9 * out['calls'] + 9) // 10
                out['ms_p90'] = db.execute(f'SELECT ms FROM usage {where} ORDER BY ms LIMIT 1 OFFSET :n',
                                           dict(args, n=rank-1)).fetchone()[0]
            out['unpriced_models'] = [r[0] for r in db.execute(
                f'SELECT DISTINCT model FROM usage {where} AND cost IS NULL ORDER BY model', args)]
            return out

    @ctx.router.get('/usage/series')
    def series(bucket: str = 'day', tz: str = '0', tz_name: str = '', window=Depends(period)):
        if bucket not in ('day', 'hour'):
            raise invalid('bucket')
        if tz_name:
            try:
                tzinfo = ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, KeyError, ValueError):
                raise invalid('tz_name')
        else:
            try:
                offset = int(tz) * 60
            except ValueError:
                raise invalid('tz')
            if abs(offset) > DAY:
                raise invalid('tz')
            # JS getTimezoneOffset() is UTC minus local minutes.
            tzinfo = timezone(timedelta(seconds=-offset))
        step = DAY if bucket == 'day' else 3600
        where, args = window
        with store.connection() as db:
            db.execute('BEGIN')
            eff_start = args['start']
            empty_all_time = False
            if eff_start == 0:
                first_ts = db.execute(f'SELECT min(ts) FROM usage {where}', args).fetchone()[0]
                empty_all_time = first_ts is None
                if first_ts is not None:
                    eff_start = first_ts
                elif bucket == 'hour':
                    eff_start = max(0.0, args['end'] - DAY)

            if bucket == 'day':
                d0 = datetime.fromtimestamp(eff_start, tzinfo).date()
                d1 = datetime.fromtimestamp(max(eff_start, args['end'] - 1e-6), tzinfo).date()
                if empty_all_time:
                    d0 = max(d0, d1 - timedelta(days=29))
                count = (d1 - d0).days + 1
                if count > 10000:
                    if args['start'] == 0:
                        d0 = d1 - timedelta(days=9999)
                        count = 10000
                    else:
                        raise invalid('to')
                days = [d0 + timedelta(days=i) for i in range(count)]
                slots = {d: {'t': int(datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tzinfo).timestamp()),
                             'calls': 0, 'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0,
                             'reasoning': 0, '_cost': {}} for d in days}
            else:
                def hour_key(ts):
                    return int(datetime.fromtimestamp(ts, tzinfo).replace(minute=0, second=0, microsecond=0).timestamp())

                last_t = math.ceil(args['end'])
                first_t = hour_key(eff_start)
                if (last_t - first_t) / 3600 > 10000:
                    if args['start'] == 0:
                        first_t = hour_key(last_t - 10000 * 3600)
                    else:
                        raise invalid('to')
                # Walk in quarter hours and key each instant by its local wall-clock hour, the same key the records
                # get below: zones that shift by 30 minutes (Lord Howe) or 45 minutes stay aligned.
                keys, t = [], first_t
                while t < last_t:
                    k = hour_key(t)
                    if not keys or keys[-1] != k:
                        keys.append(k)
                    t += 900
                slots = {k: {'t': k, 'calls': 0, 'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0,
                             'reasoning': 0, '_cost': {}} for k in keys}

            rows = db.execute(f'''SELECT ts, input, output, cache_read, cache_write, reasoning, cost, currency
                                  FROM usage {where}''', args)
            for r in rows:
                ts = r['ts']
                if bucket == 'day':
                    k = datetime.fromtimestamp(ts, tzinfo).date()
                else:
                    k = int(datetime.fromtimestamp(ts, tzinfo).replace(minute=0, second=0, microsecond=0).timestamp())
                slot = slots.get(k)
                if slot is None:
                    continue
                slot['calls'] += 1
                slot['input'] += r['input'] or 0
                slot['output'] += r['output'] or 0
                slot['cache_read'] += r['cache_read'] or 0
                slot['cache_write'] += r['cache_write'] or 0
                slot['reasoning'] += r['reasoning'] or 0
                if r['cost'] is not None and r['currency'] in ('CNY', 'USD'):
                    slot['_cost'][r['currency']] = slot['_cost'].get(r['currency'], 0.0) + r['cost']

        points = []
        for slot in slots.values():
            costs = slot.pop('_cost')
            slot['cost'] = [{'currency': c, 'amount': costs[c]} for c in ('CNY', 'USD') if c in costs]
            points.append(slot)
        return {'bucket': bucket, 'points': points}

    @ctx.router.get('/usage/breakdown')
    def breakdown(by: str = 'model', window=Depends(period)):
        key = {'model': 'model', 'profile': 'profile_id', 'session': 'session'}.get(by)
        if not key:
            raise invalid('by')
        where, args = window
        # SQLite selects bare profile_name from a row having the group's max(ts).
        with store.connection() as db:
            db.execute('BEGIN')
            rows = db.execute(f'''SELECT {key} AS key, profile_name, count(*) calls, {SUMS}, {COSTS},
                avg(ms) ms_avg, max(ts) last FROM usage {where} AND {key} IS NOT NULL GROUP BY {key}
                ORDER BY sum(input+output+cache_read+cache_write) DESC, last DESC, key LIMIT 200''', args)
            out = []
            for r in rows:
                row = with_cost(r)
                row['label'] = row['key']
                if by == 'profile':
                    row['label'] = row['profile_name']
                elif by == 'session':
                    item = db.execute('SELECT title FROM sessions_index WHERE id=?', (row['key'],)).fetchone()
                    row.update(session_exists=item is not None, title=item[0] if item else None)
                    row['label'] = row['title'] or row['key']
                else:
                    del row['profile_name']
                out.append(row)
            return {'rows': out}

    @ctx.router.get('/usage/filters')
    def filters():
        with store.connection() as db:
            db.execute('BEGIN')
            rows = db.execute('''SELECT profile_id, profile_name, max(ts) FROM usage
                                 GROUP BY profile_id ORDER BY profile_name, profile_id''')
            return {'profiles': [{'id': r[0], 'name': r[1]} for r in rows],
                    'models': [r[0] for r in db.execute('SELECT DISTINCT model FROM usage ORDER BY model')]}

    @ctx.router.get('/usage/export')
    def export(format: str = 'csv', window=Depends(period)):
        if format != 'csv':
            raise invalid('format')
        where, args = window

        def lines():
            # A streaming response can resume on a different thread; only this iterator owns its connection.
            with store.connection(streaming=True) as db:
                rows = db.execute(f'SELECT * FROM usage {where} ORDER BY ts, id', args)
                buf = io.StringIO(newline='')
                writer = csv.writer(buf)
                cols = [d[0] for d in rows.description]
                writer.writerow(cols)
                for row in rows:
                    writer.writerow([_csv_cell(col, row[i]) for i, col in enumerate(cols)])
                    if buf.tell() >= 65536:
                        yield buf.getvalue()
                        buf.seek(0)
                        buf.truncate()
                yield buf.getvalue()
        return StreamingResponse(lines(), media_type='text/csv',
                                 headers={'Content-Disposition': 'attachment; filename="usage.csv"'})

    @ctx.router.get('/usage/prune/preview')
    def preview(before: str = None):
        cutoff = None if before in (None, '') else number(before, 'before')
        return {'rows': store.prune_usage(cutoff, preview=True)}

    @ctx.router.post('/usage/prune')
    def prune(body: dict = Body(default={})):
        if 'before' not in body:
            raise invalid('before')
        cutoff = body['before']
        if cutoff is not None:
            cutoff = number(cutoff, 'before')
        return {'deleted': store.prune_usage(cutoff)}
