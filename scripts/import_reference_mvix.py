"""Explicit one-day reference import. Never used by the live collector."""
import argparse
from collections import defaultdict, Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zipfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from marginlab.mvix import calculate
from marginlab.history import boundaries


def read_workbook(path):
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    quotes = defaultdict(dict)
    counts = Counter()
    with zipfile.ZipFile(path) as archive:
        strings = [''.join(t.text or '' for t in s.iter(ns+'t'))
                   for s in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        # The aggregate sheet is inspected by name rather than relying on tab order.
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        relations = {r.get('Id'): r.get('Target') for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        sheet = next(s for s in workbook.find(ns+'sheets') if s.get('name') == '全部')
        target = relations[sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
        member = target.lstrip('/') if target.startswith('/') else 'xl/'+target
        with archive.open(member) as stream:
            for _, row in ET.iterparse(stream, events=['end']):
                if row.tag != ns+'row':
                    continue
                data = {}
                for cell in row:
                    value = cell.find(ns+'v')
                    value = value.text if value is not None else ''
                    if cell.get('t') == 's':
                        value = strings[int(value)]
                    data[re.sub(r'\d', '', cell.get('r'))] = value
                if row.get('r') == '1':
                    if data.get('C') != 'Wind代码' or data.get('G') != '时间' or data.get('H') != '收盘价':
                        raise ValueError('Unexpected workbook columns')
                    row.clear()
                    continue
                match = re.fullmatch(r'(HO|IO|MO)(2611|2612)-([CP])-(\d+)\.CFE', data['C'])
                if not match:
                    raise ValueError('Unexpected option contract')
                prefix, month, side, strike = match.groups()
                product = {'HO':'50','IO':'300','MO':'1000'}[prefix]
                close = float(data['H'])
                if not math.isfinite(close) or close < 0:
                    raise ValueError('Invalid reference close')
                symbol = 'CFFEX.'+data['C'].removesuffix('.CFE')
                expiry = datetime.fromisoformat(data['F']+'T15:00:00').replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
                key = (product, '2026-11-09T'+data['G']+':00')
                if symbol in quotes[key]:
                    raise ValueError('Duplicate workbook contract/minute')
                quotes[key][symbol] = dict(symbol=symbol,expiry=expiry,strike=float(strike),side={'C':'CALL','P':'PUT'}[side],
                    bid=close,ask=close,source='excel_close_as_reference_mid',reference=True,
                    workbook_row=int(row.get('r')),workbook_sheet='全部',volume=float(data['I']))
                counts[product] += 1
                row.clear()
    return quotes, dict(counts)


def prepare(db, quotes, digest):
    report = {'date':'2026-11-09','sha256':digest,'timestamp_convention':'Workbook minute labels treated as endpoints; 09:30 and 13:00 excluded.', 'products':{}}
    inserts = []
    db.row_factory = sqlite3.Row
    for product in ('50','300','1000'):
        existing = {r['timestamp']:dict(r) for r in db.execute("SELECT * FROM tq_snapshots WHERE product=? AND timestamp LIKE '2026-11-09%'", (product,))}
        evidence = defaultdict(dict)
        for row in db.execute("SELECT * FROM tq_option_minutes WHERE product=? AND timestamp LIKE '2026-11-09%'", (product,)):
            evidence[row['timestamp']][row['symbol']] = dict(row)
        stats = Counter(); failures = []
        for stamp in boundaries('2026-11-09T09:31:00','2026-11-09T15:00:00'):
            key = stamp.tz_localize(None).isoformat()
            if existing.get(key,{}).get('quality') in ('valid','reference'):
                stats['preserved'] += 1
                continue
            # Preserve genuine usable quotes; use the explicit reference only where missing.
            rows = dict(quotes.get((product,key),{}))
            for symbol, row in evidence[key].items():
                bid, ask = row['bid'], row['ask']
                if bid is not None and ask is not None and math.isfinite(bid) and math.isfinite(ask) and 0 <= bid <= ask and ask > 0:
                    rows[symbol] = row
            # The reference series uses only paired strikes when the supplied
            # workbook and stored evidence together omit a contract side.
            sides = defaultdict(set)
            for row in rows.values():
                sides[(row['expiry'],row['strike'])].add(row['side'])
            omitted = [symbol for symbol,row in rows.items() if len(sides[(row['expiry'],row['strike'])]) < 2]
            rows = {symbol:row for symbol,row in rows.items() if symbol not in omitted}
            try:
                result = calculate(list(rows.values()), stamp.timestamp(), .02)
            except (ValueError, TypeError) as exc:
                failures.append({'timestamp':key,'reason':str(exc)})
                continue
            refs = sum(r.get('reference',False) for r in rows.values())
            result.update(quality='reference',boundary=key,sampling='explicit-excel-reference-import',
                          source='excel_close_as_reference_mid_with_available_tqsdk_quotes',workbook_sha256=digest,
                          reference_quote_count=refs,omitted_unpaired_contracts=omitted,quote_count=len(rows),date_restricted='2026-11-09',
                          timestamp_convention=report['timestamp_convention'],
                          limitation='Trade closes substitute for missing mids. Workbook strike coverage and zero-bid tail truncation are not verified.')
            inserts.append((product,key,result['value'],'reference',json.dumps(result,allow_nan=False),json.dumps(list(rows.values()),allow_nan=False)))
            stats['reference_insertions'] += 1
        report['products'][product] = dict(stats,failures=failures)
    return inserts, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook',type=Path)
    parser.add_argument('--database',type=Path,default=ROOT/'instance/margin.sqlite3')
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    if not args.workbook.name.startswith('20261109_'):
        parser.error('Only the authorized 20261109 workbook is supported')
    digest = hashlib.sha256(args.workbook.read_bytes()).hexdigest()
    quotes, counts = read_workbook(args.workbook)
    with sqlite3.connect(f'file:{args.database.resolve()}?mode=ro',uri=True) as db:
        inserts, report = prepare(db, quotes, digest)
    report['workbook_rows'] = counts
    if args.apply:
        audit = args.database.parent/'reference-imports'/datetime.now().strftime('%Y%m%d-%H%M%S')
        audit.mkdir(parents=True,exist_ok=False)
        with sqlite3.connect(args.database) as db, sqlite3.connect(audit/'before.sqlite3') as backup:
            db.backup(backup)
        with sqlite3.connect(args.database) as db:
            db.execute('BEGIN IMMEDIATE')
            before = db.total_changes
            db.executemany('''INSERT INTO tq_snapshots VALUES(?,?,?,?,?,?)
                ON CONFLICT(product,timestamp) DO UPDATE SET value=excluded.value,quality=excluded.quality,
                details=excluded.details,quotes=excluded.quotes WHERE tq_snapshots.quality NOT IN ('valid','reference')''', inserts)
            report['written'] = db.total_changes-before
        (audit/'report.json').write_text(json.dumps(report,indent=2))
        report['audit_directory'] = str(audit)
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
