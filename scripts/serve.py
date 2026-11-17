"""Single-process production WSGI service plus independent market collector."""
from pathlib import Path
import os
import signal
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from marginlab import create_app
from marginlab.deployment import load_config


def main():
    from waitress import create_server
    root=Path(__file__).resolve().parents[1]
    app=create_app(load_config(root) if (root/'instance/server.json').exists() else None)
    feed=app.extensions['tq_feed']
    host=os.environ.get('HOST','127.0.0.1');port=int(os.environ.get('PORT','5001'))
    server=create_server(app,host=host,port=port,threads=4,
                         trusted_proxy='127.0.0.1',
                         trusted_proxy_headers={'x-forwarded-for','x-forwarded-proto','x-forwarded-host'},
                         max_request_body_size=app.config['MAX_CONTENT_LENGTH'])
    def stop(signum,frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    feed.start_background()
    from marginlab.logging_setup import event
    event('service_started',host=host,port=port,read_only=feed.read_only)
    try:server.run()
    finally:
        event('service_stopping')
        feed.stop();server.close()
        server.task_dispatcher.shutdown(timeout=5)
        if feed.thread:feed.thread.join(timeout=10)
        if feed.supervisor:feed.supervisor.join(timeout=5)

if __name__=='__main__':main()
