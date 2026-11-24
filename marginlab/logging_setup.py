"""Private JSON lines, rotated by Shanghai calendar day within one process."""
import json
import logging
import os
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from flask import g, request

ZONE = ZoneInfo('Asia/Shanghai')
EVENT_LOGGER = logging.getLogger('marginlab')


class DailyLogHandler(logging.Handler):
    def __init__(self, directory, days=30):
        super().__init__()
        if days < 1:
            raise ValueError('LOG_RETENTION_DAYS must be positive')
        self.directory = Path(directory).resolve()
        self.days = days
        self.day = self.stream = None
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def emit(self, record):
        try:
            now = datetime.fromtimestamp(record.created, ZONE)
            if now.date() != self.day:
                if self.stream:
                    self.stream.close()
                self.day = now.date()
                path = self.directory / ('marginlab-' + self.day.isoformat() + '.log')
                fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
                self.stream = os.fdopen(fd, 'a', encoding='utf-8')
                cutoff = self.day - timedelta(days=self.days - 1)
                for old in self.directory.glob('marginlab-????-??-??.log'):
                    try:
                        day = datetime.strptime(old.stem, 'marginlab-%Y-%m-%d').date()
                    except ValueError:
                        continue
                    if day < cutoff:
                        old.unlink(missing_ok=True)
            data = dict(time=now.isoformat(timespec='milliseconds'), level=record.levelname,
                        logger=record.name, event=record.getMessage(), pid=record.process,
                        thread=record.threadName)
            if hasattr(record, 'event_fields'):
                data['details'] = record.event_fields
            if record.exc_info:
                data['exception_type'] = record.exc_info[0].__name__
                data['stack'] = [dict(file=Path(f.filename).name, line=f.lineno, function=f.name)
                                 for f in traceback.extract_tb(record.exc_info[2])]
            self.stream.write(json.dumps(data, ensure_ascii=False, allow_nan=False, default=str) + '\n')
            self.stream.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        self.acquire()
        try:
            if self.stream:
                self.stream.close()
                self.stream = None
            super().close()
        finally:
            self.release()


def event(name, level=logging.INFO, **fields):
    EVENT_LOGGER.log(level, name, extra={'event_fields': fields})


def configure(app):
    if app.testing and not app.config.get('LOG_ENABLED', False):
        return
    root = Path(__file__).resolve().parent.parent
    directory = Path(app.config.get('LOG_DIR') or Path(app.config['DATABASE']).parent / 'logs')
    if not directory.is_absolute():
        directory = root / directory
    days = int(app.config['LOG_RETENTION_DAYS'])
    handler = next((h for h in app.logger.handlers if isinstance(h, DailyLogHandler)
                    and h.directory == directory.resolve() and h.days == days), None)
    handler = handler or DailyLogHandler(directory, days)
    for logger in dict.fromkeys((EVENT_LOGGER, app.logger, logging.getLogger('werkzeug'), logging.getLogger('waitress'))):
        for old in list(logger.handlers):
            logger.removeHandler(old)
            if isinstance(old, DailyLogHandler) and old is not handler:
                old.close()
        logger.addHandler(handler)
        logger.propagate = False
        logger.setLevel(logging.WARNING if logger.name == 'werkzeug' else logging.INFO)
    app.extensions['daily_log_handler'] = handler

    @app.before_request
    def request_started():
        g.log_started = time.monotonic()

    @app.after_request
    def request_finished(response):
        event('http_request', logging.WARNING if response.status_code >= 400 else logging.INFO,
              method=request.method, route=str(request.url_rule) if request.url_rule else '<unmatched>',
              status=response.status_code,
              duration_ms=round((time.monotonic() - g.log_started) * 1000, 1))
        return response

    event('application_initialized', log_retention_days=days)
