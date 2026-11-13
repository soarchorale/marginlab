"""Single-process production WSGI service plus independent market collector."""
from pathlib import Path
import signal
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from marginlab import create_app
from marginlab.deployment import load_config


def main():
    from waitress import create_server
    root=Path(__file__).resolve().parents[1]
    app=create_app(load_config(root))
    feed=app.extensions['tq_feed']
    server=create_server(app,host='127.0.0.1',port=5001,threads=4,
                         trusted_proxy='127.0.0.1',
                         trusted_proxy_headers={'x-forwarded-for','x-forwarded-proto','x-forwarded-host'},
                         max_request_body_size=app.config['MAX_CONTENT_LENGTH'])
    def stop(signum,frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    feed.start_background()
    print('marginlab: http://127.0.0.1:5001; automatic market collection enabled',flush=True)
    try:server.run()
    finally:
        feed.stop();server.close()
        server.task_dispatcher.shutdown(timeout=5)
        if feed.thread:feed.thread.join(timeout=10)
        if feed.supervisor:feed.supervisor.join(timeout=5)

if __name__=='__main__':main()
