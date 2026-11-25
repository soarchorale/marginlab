"""Feishu custom-webhook cards and conservative delivery of a durable outbox."""
import http.client
import json
import re
import os
import math
import time
from urllib.parse import urlsplit

from .logging_setup import event

TTL = 120
MAX_ATTEMPTS = 3
GROUP_WINDOW = 600
GROUP_LIMIT = 6
HOOK = re.compile(r'/open-apis/bot/v2/hook/[A-Za-z0-9-]+')
PRODUCT_NAMES = {'50': '上证50 · HO', '300': '沪深300 · IO', '1000': '中证1000 · MO'}


def webhook_path(value):
    parts = urlsplit(value)
    if (parts.scheme != 'https' or parts.netloc != 'open.feishu.cn' or
            not HOOK.fullmatch(parts.path) or parts.query or parts.fragment):
        raise ValueError('Invalid Feishu custom webhook configuration')
    return parts.path


def load_webhook(file):
    value = os.environ.get('MARGIN_FEISHU_WEBHOOK')
    if value is None:
        try:
            config = json.loads(file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ValueError('Cannot read Feishu configuration') from None
        value = config.get('webhook') if isinstance(config, dict) else None
    if not isinstance(value, str):
        raise ValueError('Missing Feishu webhook configuration')
    return webhook_path(value)


def card(product, stamp, row, triggers):
    directions = {item['direction'] for item in triggers}
    direction = next(iter(directions)) if len(directions) == 1 else 'mixed'
    label, color = {'up': ('升波', 'orange'), 'down': ('降波', 'blue'),
                    'mixed': ('信号分歧', 'grey')}[direction]
    title = f"{PRODUCT_NAMES[product]} ｜ {label}"
    metrics = f"**MVIX　{row.vix:.2f}**　　现货收盘　{row.close:,.2f}"
    if math.isfinite(row.close_change) and math.isfinite(row.vix_change):
        metrics += (f"\n近 16 根　MVIX {row.vix_change:+.2f}%　·　"
                    f"现货 {row.close_change:+.2f}%")
    if math.isfinite(row.corr_return):
        metrics += f"\n30 根收益率相关　{row.corr_return:+.2f}"
    sections = [dict(tag='markdown', content=metrics)]
    for item in triggers:
        if item['rule'] == 'residual':
            text = (f"**累计异常{'升波' if item['direction'] == 'up' else '降波'}**\n"
                    f"累计残差 **{item['value']:+.2f}**　·　"
                    f"{item['percentile']} 分位阈值 {item['threshold']:+.2f}\n"
                    "最近 16 根累计，连续 3 根越界。数值为对数变化 × 100。")
        else:
            text = (f"**同价位{'升波' if item['direction'] == 'up' else '降波'}已确认**\n"
                    f"同价位差 **{item['value']:+.2f}**　·　锚点 {item['anchor']:,.2f}\n"
                    f"{item['entry']}，近 5 个交易日有 {item['matches']} 次历史匹配。"
                    "差值为相对历史中位数的对数变化 × 100。")
        sections.extend([dict(tag='hr'), dict(tag='markdown', content=text)])
    return {'msg_type': 'interactive', 'card': {'schema': '2.0',
            'header': {'template': color, 'title': {'tag': 'plain_text', 'content': title},
                       'subtitle': {'tag': 'plain_text', 'content': f'{stamp.replace("T", " ")} 上海时间 · 1分钟'}},
            'body': {'elements': sections}}}


def post(path, payload):
    conn = http.client.HTTPSConnection('open.feishu.cn', timeout=4)
    try:
        try:
            conn.connect()
        except OSError:
            return 'retry', 'connect_failed'
        # After transmission starts, only an explicit rate rejection is safe to retry.
        conn.request('POST', path, payload.encode(), {'Content-Type': 'application/json'})
        response = conn.getresponse()
        if response.status == 429:
            return 'retry', 'rate_limited'
        if response.status >= 500:
            return 'unknown', f'http_{response.status}'
        if response.status != 200:
            return 'failed', f'http_{response.status}'
        data = json.loads(response.read(8192))
        code = data.get('code') if isinstance(data, dict) else None
        if type(code) is not int:
            return 'unknown', 'unexpected_response'
        if code == 0:
            return 'sent', 'accepted'
        if code == 9499:
            return 'retry', 'rate_limited'
        return 'failed', f'feishu_{code}'
    except (OSError, http.client.HTTPException, ValueError):
        return 'unknown', 'no_confirmation'
    finally:
        conn.close()


def dispatch_one(db, path, transport=post, clock=time.time):
    now = clock()
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE outbox SET status='expired',reason='ttl',finished=? WHERE status='pending' AND expires<=?", (now, now))
        row = db.execute("""SELECT * FROM outbox WHERE status='pending' AND next_try<=?
                            ORDER BY endpoint,product LIMIT 1""", (now,)).fetchone()
        if row is None:
            return False
        recent = db.execute("""SELECT count(*) FROM outbox
                               WHERE status IN ('sent','unknown','sending')
                               AND coalesce(finished,created)>?""", (now-GROUP_WINDOW,)).fetchone()[0]
        if recent >= GROUP_LIMIT:
            db.execute("UPDATE outbox SET status='suppressed',reason='group_limit',finished=? WHERE id=?",
                       (now, row['id']))
            return True
        attempt = row['attempts'] + 1
        if attempt > MAX_ATTEMPTS:
            db.execute("UPDATE outbox SET status='failed',reason='attempt_limit',finished=? WHERE id=?",
                       (now, row['id']))
            return True
        db.execute("UPDATE outbox SET status='sending', attempts=? WHERE id=?", (attempt, row['id']))
        db.execute("INSERT INTO delivery_attempts(event_id,attempt,started,outcome) VALUES(?,?,?,'sending')",
                   (row['id'], attempt, now))
    status, reason = transport(path, row['payload'])
    finished = clock()
    delay = min(30, 2 ** min(5, row['attempts'] + 1))
    outcome = status
    final_reason = reason
    if status == 'retry':
        if attempt >= MAX_ATTEMPTS:
            status, final_reason = 'failed', 'attempt_limit'
        else:
            status = 'pending' if finished + delay < row['expires'] else 'expired'
    with db:
        db.execute('UPDATE outbox SET status=?, reason=?, next_try=?, finished=? WHERE id=?',
                   (status, final_reason, finished + delay, finished, row['id']))
        db.execute("""UPDATE delivery_attempts SET finished=?,outcome=?,reason=?
                      WHERE event_id=? AND attempt=?""", (finished, outcome, reason, row['id'], attempt))
    event('signal_delivery', product=row['product'], endpoint=row['endpoint'], status=status,
          reason=final_reason, elapsed_ms=round((finished-row['created'])*1000))
    return True
