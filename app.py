"""Unified entry point: python app.py (or pixi run dev)."""
import os
import signal
from marginlab import create_app
app = create_app()

if __name__ == '__main__':
    def stop(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    feed = app.extensions['tq_feed']
    if os.environ.get('MARGIN_AUTOCOLLECT','1') == '1':
        feed.start_background()
    from marginlab.logging_setup import event
    event('service_started',host=os.environ.get('HOST','127.0.0.1'),port=int(os.environ.get('PORT',5000)),read_only=feed.read_only)
    try:
        app.run(host=os.environ.get('HOST','127.0.0.1'), port=int(os.environ.get('PORT',5000)), debug=False)
    finally:
        event('service_stopping')
        feed.stop()
        if feed.thread:feed.thread.join(timeout=10)
        if feed.supervisor:feed.supervisor.join(timeout=5)
