"""Feishu custom-webhook cards and conservative delivery of a durable outbox."""
import http.client
import json
import re
import os
import math
import time
from copy import deepcopy
from datetime import datetime
from urllib.parse import urlsplit

from .coverage import ZONE

from .logging_setup import event

TTL = 120
WATCH_TTL = 30
WATCH_LIMIT = 3
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


def rule_text(item):
    rule = item['rule']
    up = item['direction'] == 'up'
    move = '升波' if up else '降波'
    if rule in ('residual','residual_watch'):
        title = f'累计异常{move}' if rule == 'residual' else f'累计{move}首次越界'
        detail = '连续 3 根越界' if rule == 'residual' else '首次越界，连续 3 根可确认'
        return title, (f"累计残差 **{item['value']:+.2f}**　·　{item['percentile']} 分位阈值 "
                       f"{item['threshold']:+.2f}\n最近 16 根累计，{detail}。单位为对数变化 × 100。")
    if rule == 'baseline':
        title = '同价位基准抬升' if up else '同价位基准回落'
        return title, (f"历史匹配中位数 **{item['baseline']:.3f}**　·　差值 **{item['difference']:+.3f} 点**\n"
                       f"{item['entry']}，历史匹配 {item['matches']} 次。")
    if rule == 'revisit':
        return f'同价位{move}已确认', (f"同价位差 **{item['value']:+.2f}**　·　锚点 {item['anchor']:,.2f}\n"
                                  f"{item['entry']}，历史匹配 {item['matches']} 次。单位为对数变化 × 100。")
    if rule == 'comove':
        return ('现货与 MVIX 同涨' if up else '现货与 MVIX 同跌',
                f"近 5 分钟　现货 **{item['spot']:+.3f}%**　·　MVIX **{item['mvix']:+.3f} 点**")
    if rule == 'correlation':
        return '相关性换区', f"30 根收益率相关　**{item['value']:+.2f}**"
    _, expiry, side = rule.split(':')
    return '波动率微笑翼差扩大', f"{expiry}　{'认购' if side=='CALL' else '认沽'}翼　5 分钟扩大 **{item['change']:+.3f} IV 点**"


def card(product, stamp, row, triggers):
    if not isinstance(row, dict):
        row = vars(row)
    order = {'confirmed':0,'watch':1,'context':2}
    triggers = sorted(triggers, key=lambda t:order[t.get('level','confirmed')])
    first = triggers[0]
    level = first.get('level','confirmed')
    labels = {'confirmed':'异常确认','watch':'盯盘观察','context':'行情记录'}
    label = '观察升级' if level=='confirmed' and first.get('related_event') else labels[level]
    color = 'wathet' if level != 'confirmed' else ('orange' if first['direction']=='up' else 'blue')
    title = f"{label}｜{PRODUCT_NAMES[product]} · {rule_text(first)[0]}"
    metrics = f"**MVIX　{row['vix']:.3f}**　　现货收盘　{row['close']:,.2f}"
    if all(row[k] is not None and math.isfinite(row[k]) for k in ('close_change','vix_change')):
        metrics += f"\n近 16 根　MVIX {row['vix_change']:+.2f}%　·　现货 {row['close_change']:+.2f}%"
    if row['corr_return'] is not None and math.isfinite(row['corr_return']):
        zone = {'positive':'明显正相关','negative':'明显负相关','weak':'弱联动'}.get(row.get('corr_zone'), '')
        metrics += f"\n30 根收益率相关　{row['corr_return']:+.2f}　{zone}".rstrip()
    sections = [dict(tag='markdown', content=metrics)]
    for item in triggers:
        heading, body = rule_text(item)
        sections.extend([dict(tag='hr'), dict(tag='markdown', content=f"**{heading}**\n{body}")])
    return {'msg_type':'interactive','card':{'schema':'2.0',
            'header':{'template':color,'title':{'tag':'plain_text','content':title},
                      'subtitle':{'tag':'plain_text','content':f"{stamp.replace('T',' ')} 上海时间 · 1分钟"}},
            'body':{'elements':sections}}}


def preview_card(payload):
    """Mark both the open card and the collapsed notification on a detached copy."""
    message = deepcopy(payload)
    card_body = message['card']
    title = card_body['header']['title']
    prefix = '【本地测试】'
    if not title['content'].startswith(prefix):
        title['content'] = prefix + title['content']
    card_body['header']['template'] = 'purple'
    card_body.setdefault('config', {})['summary'] = {'content': title['content']}
    return message


def prepare_delivery(db, row, now):
    """Check the latest committed rule states and render this attempt's immutable wire payload."""
    if row['context'] is None:
        return row['payload'], None
    stamp = datetime.fromisoformat(row['endpoint']).replace(tzinfo=ZONE)
    age = now - stamp.timestamp()
    cp = db.execute('SELECT endpoint,states FROM checkpoints WHERE product=? AND version=?',
                    (row['product'],row['id'].split('/')[0])).fetchone()
    if not cp:
        return None, 'unverified'
    checked, states = cp['endpoint'], json.loads(cp['states'])
    current_minute = datetime.fromtimestamp(now,ZONE).replace(tzinfo=None,second=0,microsecond=0).isoformat()
    if age >= 60 and checked < current_minute:
        return None, 'wait_for_minute'
    evidence = db.execute('SELECT evidence FROM signal_triggers WHERE event_id=? ORDER BY rule', (row['id'],))
    eligible = []
    for record in evidence:
        t = json.loads(record[0])
        if t['policy'] != 'notify' or (t['level']=='watch' and age>WATCH_TTL):
            continue
        if (states['active'].get(t['rule'])==t['direction']
                and states['since'].get(t['rule'])==t['since']):
            eligible.append(t)
    if not eligible or (row['tier']=='confirmed' and not any(t['level']=='confirmed' for t in eligible)):
        return None, 'superseded'
    msg = card(row['product'],row['endpoint'],json.loads(row['context']),eligible)
    color, label = ('turquoise','及时') if age<=10 else ('yellow','稍有延迟') if age<=30 else ('orange','延迟确认')
    sent = datetime.fromtimestamp(now,ZONE).strftime('%H:%M:%S')
    timing = (f"<text_tag color='{color}'>{label} · {math.ceil(age)} 秒</text_tag>\n"
              f"触发 **{stamp:%H:%M:%S}**　发出 **{sent}**　上海时间")
    if age>30:
        timing += f"\n最新复核　{checked[11:19]}"
    msg['card']['body']['elements'].insert(0,dict(tag='markdown',content=timing))
    return json.dumps(msg,ensure_ascii=False,allow_nan=False), None



def post(path, payload, before_send=None):
    conn = http.client.HTTPSConnection('open.feishu.cn', timeout=4)
    try:
        try:
            conn.connect()
        except OSError:
            return 'retry', 'connect_failed'
        if before_send is not None:
            payload, reason = before_send()
            if reason:
                if reason == 'wait_for_minute':
                    return 'retry', reason
                return ('expired' if reason=='ttl' else 'suppressed'), reason
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
    with db:
        db.execute('BEGIN IMMEDIATE')
        now = clock()
        db.execute("""UPDATE outbox SET status='expired',reason='ttl',finished=?
                      WHERE status='pending' AND (expires<? OR (expires=? AND tier!='watch'))""", (now, now, now))
        row = db.execute("""SELECT * FROM outbox WHERE status='pending' AND next_try<=?
                            ORDER BY CASE tier WHEN 'confirmed' THEN 0 ELSE 1 END,endpoint,product LIMIT 1""", (now,)).fetchone()
        if row is None:
            return False
        payload, reject = prepare_delivery(db, row, now)
        if reject == 'wait_for_minute':
            db.execute('UPDATE outbox SET next_try=? WHERE id=?',(now+0.5,row['id']))
            return True
        if reject:
            db.execute("UPDATE outbox SET status='suppressed',reason=?,finished=? WHERE id=?", (reject,now,row['id']))
            return True
        recent, watches = db.execute("""SELECT count(*),coalesce(sum(tier='watch'),0) FROM outbox
                               WHERE status IN ('sent','unknown','sending')
                               AND coalesce(finished,created)>?""", (now-GROUP_WINDOW,)).fetchone()
        if recent >= GROUP_LIMIT or (row['tier']=='watch' and watches >= WATCH_LIMIT):
            db.execute("UPDATE outbox SET status='suppressed',reason=?,finished=? WHERE id=?",
                       ('group_limit' if recent>=GROUP_LIMIT else 'watch_limit',now,row['id']))
            return True
        attempt = row['attempts'] + 1
        if attempt > MAX_ATTEMPTS:
            db.execute("UPDATE outbox SET status='failed',reason='attempt_limit',finished=? WHERE id=?",
                       (now, row['id']))
            return True
        db.execute("UPDATE outbox SET status='sending', attempts=? WHERE id=?", (attempt, row['id']))
        db.execute("INSERT INTO delivery_attempts(event_id,attempt,started,outcome,payload) VALUES(?,?,?,'sending',?)",
                   (row['id'], attempt, now, None if transport is post else payload))
    def before_send():
        # TLS setup may cross a minute boundary, the freshness band or expiry.
        with db:
            db.execute('BEGIN IMMEDIATE')
            started = clock()
            if started > row['expires'] or (started == row['expires'] and row['tier'] != 'watch'):
                wire, reason = None, 'ttl'
            else:
                wire, reason = prepare_delivery(db, row, started)
            db.execute('UPDATE delivery_attempts SET payload=? WHERE event_id=? AND attempt=?',
                       (wire,row['id'],attempt))
            return wire, reason

    if transport is post:
        status, reason = post(path, payload, before_send=before_send)
    else:
        status, reason = transport(path, payload)
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
