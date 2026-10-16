"""Read-only live checks, including login; never modify report data."""
import hashlib
import http.cookiejar
import io
import json
import os
import re
import ssl
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image
from dotenv import dotenv_values
import zxingcpp

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = 'https://10.9.0.98:18888'
ADMIN = 'https://10.9.0.98:18889'
QR_BASE = (os.getenv('BASE_URL') or dotenv_values(ROOT / '.env').get('BASE_URL') or PUBLIC).rstrip('/')
with sqlite3.connect(f'file:{ROOT / "data/library.sqlite3"}?mode=ro', uri=True) as database:
    database.row_factory = sqlite3.Row
    configured = database.execute("SELECT value FROM settings WHERE key='qr_base_url'").fetchone()
    if configured:
        QR_BASE = configured[0]
    manifest = [dict(row) for row in database.execute('SELECT * FROM documents ORDER BY id')]
certificate = ROOT / 'test-results/server.crt'
if not certificate.exists():
    certificate = ROOT / 'deploy/certs/server.crt'
ctx = ssl.create_default_context(cafile=str(certificate))
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx),
                                    urllib.request.HTTPCookieProcessor(jar))


def request(base, path, method='GET', data=None, headers=None):
    time.sleep(.22)
    req = urllib.request.Request(base + path, data=data, headers=headers or {}, method=method)
    try:
        response = opener.open(req, timeout=60)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, response.headers, response.read(), response.url


checks = []
assert request(PUBLIC, '/')[0] == 200
for path in ['/admin/', '/admin/login', '/admin/settings', '/healthz', '/.env', '/backups/']:
    assert request(PUBLIC, path)[0] == 404, path
assert request(PUBLIC, '/admin/login', method='POST', data=b'')[0] == 404
checks.append('public browsing and management isolation')
visible = [r for r in manifest if not r['needs_review'] and r['active'] and not r['deleted_at']]
for row in visible:
    status, headers, body, _ = request(PUBLIC, f'/d/{row["id"]:04d}/')
    assert status == 200 and row['title'] in body.decode()
    assert '/qr.png' in body.decode()
    assert headers.get('X-Library-Install-ID')
checks.append(f'{len(visible)} fixed report pages, including pending reports')
report = next(r for r in visible if r['has_report'])
path = f'/d/{report["id"]:04d}/file'
status, headers, body, _ = request(PUBLIC, path)
assert status == 200 and hashlib.sha256(body).hexdigest() == report['sha256']
assert request(PUBLIC, path, method='HEAD')[0] == 200
status, headers, chunk, _ = request(PUBLIC, path, headers={'Range': 'bytes=0-99'})
assert status == 206 and chunk == body[:100] and headers.get('Content-Range')
checks.append('PDF download SHA256, HEAD and Range')
status, _, body, _ = request(ADMIN, '/admin/login')
assert status == 200, f'Administrator login page returned {status}; check the source IP allowlist'
csrf = re.search(r'name="csrf" value="([^"]+)"', body.decode()).group(1)
password = next(s.split('：', 1)[1] for s in (ROOT / '本地管理员凭据.txt').read_text().splitlines()
                if s.startswith('密码：'))
data = urllib.parse.urlencode({'csrf': csrf, 'username': 'admin', 'password': password}).encode()
status, _, body, url = request(ADMIN, '/admin/login', method='POST', data=data)
assert status == 200 and url.endswith('/admin/') and '退出' in body.decode()
assert any(cookie.secure for cookie in jar)
checks.append('existing administrator login and Secure session cookie')
status, _, body, _ = request(ADMIN, '/admin/settings')
assert status == 200 and QR_BASE in body.decode() and '二维码地址前缀' in body.decode()
checks.append('administrator QR settings show the effective configured prefix')
status, _, body, _ = request(ADMIN, f'/d/{report["id"]:04d}/qr.png')
assert status == 200
code = zxingcpp.read_barcode(Image.open(io.BytesIO(body)))
assert code and code.text == f'{QR_BASE}/d/{report["id"]:04d}/'
checks.append(f'live QR decode points to configured public origin {QR_BASE}')
for suffix in ['', '?download=true', '?label=true&download=true']:
    qr_path = f'/d/{report["id"]:04d}/qr.png' + suffix
    status, headers, image, _ = request(PUBLIC, qr_path)
    assert status == 200 and headers.get('Content-Type') == 'image/png'
    assert image == request(ADMIN, qr_path)[2]
    if 'download=true' in suffix:
        assert 'attachment;' in headers.get('Content-Disposition', '')
    code = zxingcpp.read_barcode(Image.open(io.BytesIO(image)))
    assert code and code.text == f'{QR_BASE}/d/{report["id"]:04d}/'
    assert request(PUBLIC, qr_path, method='HEAD')[0] == 200
assert request(PUBLIC, qr_path, method='POST', data=b'')[0] == 405
checks.append('public/private QR PNG and label bytes match, downloads and HEAD work, QR POST denied')
assert request(PUBLIC, '/admin/')[0] == 404
assert request(PUBLIC, '/')[0] == 200
checks.append('public remains read-only with administrator cookies')
out = {'status': 'PASS', 'public_url': PUBLIC, 'admin_url': ADMIN + '/admin/', 'checks': checks}
(ROOT / 'test-results/server-validation.json').write_text(json.dumps(out, ensure_ascii=False, indent=2))
print(json.dumps(out, ensure_ascii=False, indent=2))
