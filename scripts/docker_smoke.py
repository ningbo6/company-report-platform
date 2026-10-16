"""Exercise the production image using temporary data, without mounting real reports.

docker run --rm -i --read-only --tmpfs /tmp:size=256m,mode=1777 \
    --user 10001:10001 pdf-library-app python - < scripts/docker_smoke.py
"""
import io
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
import zxingcpp
from pypdf import PdfWriter

from library.config import Settings
from library.main import create_app
from library.qr import label_image, make_code
from library.store import InvalidPDF


def pdf(width):
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


with tempfile.TemporaryDirectory() as tmp, socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{sock.getsockname()[1]}'
    app = create_app(Settings(data_dir=Path(tmp) / 'data', secret='docker-smoke-' * 4, base_url=origin))
    store = app.state.store
    doc_id = store.create_entry('容器验证企业有限公司')
    assert not store.get(doc_id)['has_report']
    assert store.ingest(io.BytesIO(pdf(200)), '容器验证企业有限公司.pdf')[0] == doc_id
    before = store.get(doc_id)
    try:
        store.ingest(io.BytesIO(b'broken pdf'), 'broken.pdf', doc_id)
    except InvalidPDF:
        pass
    else:
        raise AssertionError('Invalid PDF was accepted')
    assert store.get(doc_id) == before
    store.ingest(io.BytesIO(pdf(400)), '新版报告.pdf', doc_id)
    assert not (store.objects / before['object_name']).exists()
    assert len(list(store.objects.glob('*.pdf'))) == 1
    store.edit(doc_id, title='容器验证企业（更名）有限公司')
    code, svg = make_code(f'{origin}/d/{doc_id:04d}/', store.cfg.logo_path)
    assert zxingcpp.read_barcode(code).text == f'{origin}/d/{doc_id:04d}/'
    assert zxingcpp.read_barcode(label_image(code, store.get(doc_id)['title'])).text == f'{origin}/d/{doc_id:04d}/'
    assert 'Asia/Shanghai' == ZoneInfo('Asia/Shanghai').key
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()

    def request(path, headers=None):
        req = urllib.request.Request(origin + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        permalink = f'/d/{doc_id:04d}/'
        assert request(permalink)[0] == 200
        assert request(permalink + 'file')[1] == pdf(400)
        status, data = request(permalink + 'file', {'Range': 'bytes=0-9'})
        assert status == 206 and data == pdf(400)[:10]
        store.edit(doc_id, active=False)
        assert request(permalink + 'file')[0] == 410
        store.edit(doc_id, active=True)
        store.delete(doc_id)
        assert request(permalink)[0] == 410
        store.restore(doc_id)
        assert request(permalink + 'file')[0] == 410
        store.edit(doc_id, active=True)
        assert request(permalink + 'file')[1] == pdf(400)
        store.backup(Path(tmp) / 'backup')
        assert (Path(tmp) / 'backup/library.sqlite3').is_file()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
print('PASS: container create, upload, rename, invalid/valid replacement, old-file cleanup, fixed link, PDF Range, disable, delete, restore, QR/label decode, timezone and backup; temporary data only.')
