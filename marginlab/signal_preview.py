"""Authenticated, display-only signal cards for the local replica."""
from contextlib import closing
import json
import os
import re
from pathlib import Path
import sqlite3

from flask import render_template

from .feishu import preview_card


def register_preview(app):
    path = Path(os.environ.get('MARGIN_SIGNAL_PREVIEW_DB') or
                Path(app.config['DATABASE']).with_name('signals.sqlite3')).resolve()

    @app.get('/debug/signal-preview')
    def signal_preview():
        cards = []
        if path.is_file():
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
                columns = {r[1] for r in db.execute('PRAGMA table_info(outbox)')}
                if columns:
                    where = "WHERE tier!='record'" if 'tier' in columns else ''
                    attempts = {r[1] for r in db.execute('PRAGMA table_info(delivery_attempts)')}
                    field = 'e.payload'
                    if 'payload' in attempts:
                        field = """coalesce((SELECT a.payload FROM delivery_attempts a
                                   WHERE a.event_id=e.id AND a.payload IS NOT NULL
                                   ORDER BY a.attempt DESC LIMIT 1), e.payload)"""
                    for payload, in db.execute(f"""SELECT {field} FROM outbox e {where}
                                                   ORDER BY endpoint DESC,id LIMIT 8"""):
                        card = preview_card(json.loads(payload))['card']
                        for part in card['body']['elements']:
                            if part.get('tag') != 'markdown':
                                continue
                            label = re.match(r"^<text_tag color='(turquoise|yellow|orange)'>([^<]+)</text_tag>\n",
                                             part['content'])
                            if label:
                                part['tone'], part['label'] = label.groups()
                                part['content'] = part['content'][label.end():]
                        cards.append(card)
        return render_template('signal_preview.html', cards=cards, page='signal-preview',
                               page_title='信号卡片测试预览')
