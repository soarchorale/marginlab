import re
import tempfile
from pathlib import Path
from marginlab import create_app


def app_for_test(case, **overrides):
    temporary = tempfile.TemporaryDirectory()
    case.addCleanup(temporary.cleanup)
    return create_app(dict(TESTING=True, DATABASE=str(Path(temporary.name)/'test.sqlite3'), **overrides))


def form_token(response):
    return re.search(r'name="csrf_token" value="([^"]+)"',response.get_data(as_text=True)).group(1)


def login(client, password='opsearch', next='/'):
    csrf = form_token(client.get('/login'))
    response = client.post('/login', data=dict(username='admin',password=password,csrf_token=csrf,next=next))
    if response.status_code == 302:
        csrf = re.search(r'name="csrf-token" content="([^"]+)"',client.get('/').get_data(as_text=True)).group(1)
        client.environ_base['HTTP_X_CSRF_TOKEN'] = csrf
    return response
