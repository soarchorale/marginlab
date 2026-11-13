"""Unified entry point: python app.py (or pixi run dev)."""
import os
from marginlab import create_app
app = create_app()

if __name__ == '__main__':
    feed = app.extensions['tq_feed']
    if os.environ.get('MARGIN_AUTOCOLLECT','1') == '1':
        feed.start_background()
    try:
        app.run(host='127.0.0.1', port=int(os.environ.get('PORT',5000)), debug=False)
    finally:
        feed.stop()
