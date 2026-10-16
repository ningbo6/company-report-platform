"""Read-only live checks, including login; never modify report data."""
import hashlib
import http.cookiejar
import io
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image
import zxingcpp

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = 'https://10.9.0.98:18888'
ADMIN = 'https://10.9.0.98:18889'
ctx = ssl.create_default_context(cafile=str(ROOT / 'test-results/server.crt'))
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
for path in ['/admin/', '/admin/login', '/healthz', '/.env', '/backups/', '/d/0008/qr.png']:
    assert request(PUBLIC, path)[0] == 404, path
assert request(PUBLIC, '/admin/login', method='POST', data=b'')[0] == 404
checks.append('public browsing and management isolation')
manifest = json.loads((ROOT / 'backups/server-migration-20261015/manifest.json').read_text())
visible = [r for r in manifest if not r['needs_review'] and r['active'] and not r['deleted_at']]
for row in visible:
    status, headers, body, _ = request(PUBLIC, f'/d/{row["id"]:04d}/')
    assert status == 200 and row['title'] in body.decode()
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
assert status == 200
csrf = re.search(r'name="csrf" value="([^"]+)"', body.decode()).group(1)
password = next(s.split('：', 1)[1] for s in (ROOT / '本地管理员凭据.txt').read_text().splitlines()
                if s.startswith('密码：'))
data = urllib.parse.urlencode({'csrf': csrf, 'username': 'admin', 'password': password}).encode()
status, _, body, url = request(ADMIN, '/admin/login', method='POST', data=data)
assert status == 200 and url.endswith('/admin/') and '退出' in body.decode()
assert any(cookie.secure for cookie in jar)
checks.append('existing administrator login and Secure session cookie')
status, _, body, _ = request(ADMIN, f'/d/{report["id"]:04d}/qr.png')
assert status == 200
code = zxingcpp.read_barcode(Image.open(io.BytesIO(body)))
assert code and code.text == f'{PUBLIC}/d/{report["id"]:04d}/'
checks.append('live QR decode points to server on port 18888')
assert request(PUBLIC, '/admin/')[0] == 404
assert request(PUBLIC, '/')[0] == 200
checks.append('public remains read-only with administrator cookies')
out = {'status': 'PASS', 'public_url': PUBLIC, 'admin_url': ADMIN + '/admin/', 'checks': checks}
(ROOT / 'test-results/server-validation.json').write_text(json.dumps(out, ensure_ascii=False, indent=2))
print(json.dumps(out, ensure_ascii=False, indent=2))
