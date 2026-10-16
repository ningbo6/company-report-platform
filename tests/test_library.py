import base64
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
import zxingcpp
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter

from library.config import ROOT, Settings
from library.main import create_app
from library.qr import export_bundle, load_logo, make_code
from library.security import hash_password
from library.store import Store


def pdf_bytes(width=200):
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def setup(tmp_path):
    cfg = Settings(data_dir=tmp_path / 'data', secret='test-secret-' * 6)
    app = create_app(cfg)
    store = app.state.store
    store.set_setting('admin_username', 'admin')
    store.set_setting('admin_password_hash', hash_password('test-password-123'))
    with TestClient(app) as client:
        yield client, store


def token(response):
    return re.search(r'name="csrf" value="([^"]+)"', response.text)[1]


def login(client):
    csrf = token(client.get('/admin/login'))
    response = client.post('/admin/login', data={'csrf': csrf, 'username': 'admin', 'password': 'test-password-123'})
    assert response.status_code == 200
    return token(response)


def add(store, title='示例企业有限公司', width=200):
    return store.ingest(io.BytesIO(pdf_bytes(width)), title + '.pdf')[0]


def test_replace_keeps_id_url_and_deletes_old_file(setup):
    client, store = setup
    id_ = add(store)
    old = store.get(id_)
    csrf = login(client)
    response = client.post(f'/admin/documents/{id_}/replace', data={'csrf': csrf},
                           files={'file': ('新版文件.pdf', pdf_bytes(500), 'application/pdf')})
    assert response.status_code == 200
    current = store.get(id_)
    assert current['id'] == old['id'] and current['title'] == old['title']
    assert current['sha256'] != old['sha256']
    assert not (store.objects / old['object_name']).exists()
    assert len(list(store.objects.iterdir())) == 1
    url = f'/d/{id_:04d}/'
    assert client.get(url).status_code == 200
    assert client.get(url + 'file').content == pdf_bytes(500)


def test_invalid_replacement_preserves_current_and_cleans_staging(setup):
    client, store = setup
    id_ = add(store)
    before = store.get(id_)
    csrf = login(client)
    for name, payload in [('bad.pdf', b'%PDF-corrupt'), ('not.txt', pdf_bytes())]:
        assert client.post(f'/admin/documents/{id_}/replace', data={'csrf': csrf},
                           files={'file': (name, payload)}).status_code == 400
        assert store.get(id_) == before
        assert not list(store.staging.iterdir())
    store.cfg.max_file_bytes = 20
    with pytest.raises(ValueError, match='上限'):
        store.ingest(io.BytesIO(pdf_bytes(500)), 'oversize.pdf', id_)
    assert store.get(id_) == before


def test_disable_blocks_existing_file_link_and_range_restore_same_id(setup):
    client, store = setup
    id_ = add(store)
    csrf = login(client)
    endpoint = f'/admin/documents/{id_}/status'
    url = f'/d/{id_:04d}/'
    normal = client.get(url + 'file', headers={'Range': 'bytes=0-9'})
    assert normal.status_code == 206 and len(normal.content) == 10
    assert 'no-store' in normal.headers['cache-control']
    assert client.head(url + 'file').status_code == 200
    assert 'attachment;' in client.get(url + 'file?download=true').headers['content-disposition']
    client.post(endpoint, data={'csrf': csrf, 'action': 'disable'})
    assert client.get(url).status_code == 410
    assert client.get(url + 'file').status_code == 410
    assert client.get(url + 'file?download=true', headers={'Range': 'bytes=0-9'}).status_code == 410
    assert client.head(url + 'file').status_code == 410
    assert '示例企业有限公司' not in client.get('/').text
    client.post(f'/admin/documents/{id_}/replace', data={'csrf': csrf}, files={'file': ('新版.pdf', pdf_bytes(500))})
    assert client.get(url + 'file').status_code == 410
    client.post(endpoint, data={'csrf': csrf, 'action': 'enable'})
    assert client.get(url).status_code == 200
    assert client.get(url + 'file').content == pdf_bytes(500)


def test_auth_csrf_and_private_storage(setup):
    client, store = setup
    id_ = add(store)
    assert client.get('/admin/', follow_redirects=False).status_code == 303
    assert client.post('/admin/upload', files={'files': ('企业.pdf', pdf_bytes())}).status_code == 401
    csrf = login(client)
    assert client.post(f'/admin/documents/{id_}/edit', data={'title': '篡改'}).status_code == 403
    assert client.post('/admin/logout', data={'csrf': 'bad'}).status_code == 403
    assert client.post(f'/admin/documents/{id_}/status', data={'csrf': csrf, 'action': 'wrong'}).status_code == 400
    assert client.get('/data/objects/' + store.get(id_)['object_name']).status_code == 404
    assert client.get('/.env').status_code == 404
    assert store.get(id_)['title'] == '示例企业有限公司'
    client.post('/admin/logout', data={'csrf': csrf})
    assert client.post('/admin/export', data={'csrf': csrf}).status_code == 401


def test_batch_sorted_idempotent_duplicate_titles_never_overwrite(setup):
    client, store = setup
    csrf = login(client)
    files = [('files', ('乙公司.pdf', pdf_bytes())), ('files', ('丙公司.pdf', pdf_bytes())), ('files', ('甲公司.pdf', pdf_bytes()))]
    assert client.post('/admin/upload', data={'csrf': csrf}, files=files).status_code == 200
    rows = store.list()
    assert [r['title'] for r in rows] == sorted(['乙公司', '丙公司', '甲公司'])
    before = [(r['id'], r['sha256']) for r in rows]
    client.post('/admin/upload', data={'csrf': csrf}, files=files)
    assert [(r['id'], r['sha256']) for r in store.list()] == before
    r = client.post('/admin/upload', data={'csrf': csrf}, files={'files': ('乙公司.pdf', pdf_bytes(300))})
    assert '替换 PDF' in r.text
    assert [(r['id'], r['sha256']) for r in store.list()] == before
    store.edit(1, active=False)
    assert add(store, '新增企业') == 4
    assert store.get(1)['active'] == 0


def test_title_edit_and_search_do_not_change_document_identity(setup):
    client, store = setup
    id_ = add(store, '原企业')
    csrf = login(client)
    old = store.get(id_)
    client.post(f'/admin/documents/{id_}/edit', data={'csrf': csrf, 'title': '新企业（邯郸）有限公司'})
    assert store.get(id_)['object_name'] == old['object_name']
    assert '新企业（邯郸）有限公司' in client.get('/?q=邯郸').text
    assert '新企业（邯郸）有限公司' in client.get('/?q=0001').text
    assert '没有找到' in client.get('/?q=不存在的企业').text
    assert '&lt;script&gt;' in client.get('/?q=<script>').text


def test_login_throttle_and_password_reset_invalidates_session(setup):
    client, store = setup
    csrf = login(client)
    store.set_setting('admin_password_hash', hash_password('another-password-123'))
    assert client.post('/admin/export', data={'csrf': csrf}).status_code == 401
    for _ in range(5):
        csrf = token(client.get('/admin/login'))
        r = client.post('/admin/login', data={'csrf': csrf, 'username': 'admin', 'password': 'wrong'})
        assert r.status_code == 401
    assert client.post('/admin/login', data={'csrf': csrf, 'username': 'admin', 'password': 'wrong'}).status_code == 429


def test_backup_restore_and_interrupted_write_recovery(setup, tmp_path):
    _, store = setup
    add(store)
    dest = store.backup(tmp_path / 'backup')
    assert (dest / 'manifest.json').exists()
    restored = Store(Settings(data_dir=dest, secret='backup-secret-' * 4))
    assert restored.list() == store.list()
    assert len(list(restored.objects.glob('*.pdf'))) == 1
    (store.objects / 'orphan.pdf').write_bytes(b'orphan')
    (store.staging / 'interrupted.pdf').write_bytes(b'incomplete')
    Store(store.cfg)
    assert not (store.objects / 'orphan.pdf').exists()
    assert not list(store.staging.iterdir())


def test_concurrent_uploads_allocate_distinct_ids(setup):
    _, store = setup
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda n: add(store, f'并发企业{n}'), range(8)))
    assert sorted(ids) == list(range(1, 9))
    assert len(list(store.objects.glob('*.pdf'))) == 8


def test_export_all_codes_and_labels_decode_and_match_mapping(setup, tmp_path):
    _, store = setup
    add(store, '中船（邯郸）派瑞特种气体股份有限公司')
    add(store, '河北博柯莱智能装备科技股份有限公司')
    path = export_bundle(store, tmp_path / 'output')
    with zipfile.ZipFile(path) as z:
        verify = json.loads(z.read('verification.json'))
        assert len(verify) == 2
        assert all(r['decoded'] for r in verify)
        for name in z.namelist():
            if not name.endswith('.png') or not name.startswith(('qr/', 'labels/')):
                continue
            im = Image.open(io.BytesIO(z.read(name)))
            assert im.width >= 1800
            result = zxingcpp.read_barcode(im)
            assert result and result.text in [r['url'] for r in verify]
        assert '本地测试' in z.read('mapping.csv').decode('utf-8-sig') or '测试' in z.read('mapping.csv').decode('utf-8-sig')
        with Image.open(io.BytesIO(z.read('assets/school-logo-transparent.png'))) as logo:
            assert logo.mode == 'RGBA' and logo.getchannel('A').getpixel((0, 0)) == 0
    # No permanent-ID state is changed by repeat QR exports.
    assert len(store.list()) == 2


def test_production_export_guards(setup, tmp_path):
    _, store = setup
    add(store)
    store.cfg.qr_mode = 'production'
    with pytest.raises(ValueError, match='HTTPS'):
        export_bundle(store, tmp_path / 'out')
    store.cfg.base_url = 'https://reports.example.org'
    with pytest.raises(ValueError, match='verify-origin'):
        export_bundle(store, tmp_path / 'out')
    store.set_setting('verified_base_url', store.cfg.base_url)
    with pytest.raises(ValueError, match='高清校徽'):
        export_bundle(store, tmp_path / 'out')


def test_printed_company_binding_replacement_keeps_same_qr(setup, tmp_path):
    client, store = setup
    id_ = add(store, '固定企业有限公司')
    before = export_bundle(store, tmp_path / 'before', id_)
    store.bind_for_print(store.list())
    csrf = login(client)
    r = client.post(f'/admin/documents/{id_}/edit', data={'csrf': csrf, 'title': '另一家企业'})
    assert r.status_code == 400
    assert '不能改成其他企业' in r.text
    client.post(f'/admin/documents/{id_}/replace', data={'csrf': csrf}, files={'file': ('企业画像新版.pdf', pdf_bytes(333))})
    after = export_bundle(store, tmp_path / 'after', id_)
    with zipfile.ZipFile(before) as a, zipfile.ZipFile(after) as b:
        assert a.read(f'qr/{id_:04d}.png') == b.read(f'qr/{id_:04d}.png')
        assert a.read(f'qr/{id_:04d}.svg') == b.read(f'qr/{id_:04d}.svg')
        label = next(n for n in a.namelist() if n.startswith('labels/'))
        assert a.read(label) == b.read(label)
        assert a.read('mapping.csv') != b.read('mapping.csv')  # Current PDF hash changes, company and QR do not.
    assert store.get(id_)['title'] == '固定企业有限公司'
    assert client.get(f'/d/{id_:04d}/file').content == pdf_bytes(333)


def test_request_body_limit_before_upload(setup):
    client, store = setup
    store.cfg.max_body_bytes = 50
    r = client.post('/admin/upload', content=b'x' * 51)
    assert r.status_code == 413
    assert not store.list()


def test_delete_hides_report_blocks_access_and_retains_binding(setup, tmp_path):
    client, store = setup
    id_ = add(store, '企业一')
    original = store.get(id_)
    endpoint = f'/admin/documents/{id_}/delete'
    assert client.post(endpoint).status_code == 401
    csrf = login(client)
    assert client.post(endpoint).status_code == 403
    assert not store.get(id_)['deleted_at']
    assert client.post(endpoint, data={'csrf': csrf}).status_code == 200
    assert store.get(id_)['deleted_at']
    assert store.get(id_)['id'] == id_ and store.get(id_)['title'] == original['title']
    assert (store.objects / original['object_name']).is_file()
    assert store.list(include_disabled=True) == []
    assert len(store.list(include_disabled=True, include_deleted=True)) == 1
    assert '企业一' not in client.get('/').text
    assert '企业一' not in client.get('/admin/').text
    assert '企业一' in client.get('/admin/?view=deleted').text
    assert '资料已删除' in client.get(f'/d/{id_:04d}/').text
    assert client.get(f'/d/{id_:04d}/').status_code == 410
    for path in ('file', 'file?download=true'):
        assert client.get(f'/d/{id_:04d}/' + path, headers={'Range': 'bytes=0-9'}).status_code == 410
        assert client.head(f'/d/{id_:04d}/' + path).status_code == 410
    with pytest.raises(ValueError, match='回收站'):
        store.ingest(io.BytesIO(pdf_bytes()), '企业一.pdf')
    with pytest.raises(ValueError, match='先从回收站恢复'):
        store.ingest(io.BytesIO(pdf_bytes(300)), '新版.pdf', id_)
    with pytest.raises(ValueError, match='先从回收站恢复'):
        store.edit(id_, active=True)
    with pytest.raises(ValueError, match='不存在或已下架'):
        export_bundle(store, tmp_path / 'out', id_)
    assert add(store, '新企业') == id_ + 1
    backup = store.backup(tmp_path / 'deleted-backup')
    assert len(json.loads((backup / 'manifest.json').read_text())) == 2
    recovered = Store(Settings(data_dir=backup, secret='recovery-secret-' * 4))
    assert recovered.get(id_)['deleted_at']


def test_restore_preserves_qr_requires_explicit_publication_and_binding(setup):
    client, store = setup
    id_ = add(store, '固定企业')
    store.bind_for_print(store.list())
    old = store.get(id_)
    store.delete(id_)
    assert client.post(f'/admin/documents/{id_}/restore').status_code == 401
    csrf = login(client)
    assert client.post(f'/admin/documents/{id_}/restore', data={'csrf': 'bad'}).status_code == 403
    response = client.post(f'/admin/documents/{id_}/restore', data={'csrf': csrf})
    assert response.status_code == 200
    assert not store.get(id_)['deleted_at'] and not store.get(id_)['active']
    assert store.get(id_)['object_name'] == old['object_name']
    assert client.get(f'/d/{id_:04d}/file').status_code == 410
    assert client.post(f'/admin/documents/{id_}/edit', data={'csrf': csrf, 'title': '其他企业'}).status_code == 400
    client.post(f'/admin/documents/{id_}/status', data={'csrf': csrf, 'action': 'enable'})
    assert client.get(f'/d/{id_:04d}/file').content == pdf_bytes()
    assert store.get(id_)['title'] == old['title']


def test_public_list_and_admin_crud_controls(setup):
    client, store = setup
    id_ = add(store, '列表企业')
    page = client.get('/').text
    assert '企业画像报告列表' in page and f'data-document="{id_}"' in page
    assert '删除列表企业' not in page
    assert 'class="cards"' not in page
    login(client)
    page = client.get('/admin/').text
    assert '新增报告' in page and '替换报告' in page and '回收站' in page
    assert '删除列表企业' in page
    assert '编辑' in page and '搜索' in page
    assert client.get('/admin/?view=invalid').status_code == 400


def test_logo_retains_alpha_and_no_rectangular_white_plate(setup, tmp_path):
    _, store = setup
    code, svg = make_code('http://127.0.0.1:18880/d/0001/', store.cfg.logo_path)
    root = ET.fromstring(svg)
    ns = {'svg': 'http://www.w3.org/2000/svg'}
    # Only the full QR canvas is white; no extra white rectangle under the logo.
    assert len(root.findall('svg:rect', ns)) == 1
    embedded = root.find('svg:image', ns)
    with Image.open(io.BytesIO(base64.b64decode(embedded.attrib['href'].split(',', 1)[1]))) as logo:
        assert logo.format == 'PNG' and logo.mode == 'RGBA'
        alpha = logo.getchannel('A')
        assert alpha.getpixel((0, 0)) == 0
        ox, oy = int(embedded.attrib['x']), int(embedded.attrib['y'])
        assert any(alpha.getpixel((x, y)) == 0 and code.getpixel((ox + x, oy + y)) == (0, 0, 0)
                   for y in range(logo.height) for x in range(logo.width))
    opaque = tmp_path / 'opaque.png'
    Image.new('RGB', (100, 100), 'white').save(opaque)
    with pytest.raises(ValueError, match='透明'):
        load_logo(opaque)


def test_detail_qr_preview_and_download_follow_current_company(setup, tmp_path):
    client, store = setup
    id_ = add(store, '二维码预览企业')
    url = f'/d/{id_:04d}/qr.png'
    page = client.get(f'/d/{id_:04d}/')
    assert '企业二维码' in page.text and f'src="{url}"' in page.text
    response = client.get(url)
    assert response.status_code == 200 and response.headers['content-type'] == 'image/png'
    image = Image.open(io.BytesIO(response.content))
    assert zxingcpp.read_barcode(image).text == f'{store.cfg.base_url}/d/{id_:04d}/'
    download = client.get(url + '?download=true')
    assert 'attachment;' in download.headers['content-disposition']
    label = client.get(url + '?label=true&download=true')
    assert 'attachment;' in label.headers['content-disposition']
    assert Image.open(io.BytesIO(label.content)).height > image.height
    package = export_bundle(store, tmp_path / 'export', id_)
    with zipfile.ZipFile(package) as z:
        assert z.read(f'qr/{id_:04d}.png') == response.content
        name = next(n for n in z.namelist() if n.startswith('labels/'))
        assert z.read(name) == label.content
    store.ingest(io.BytesIO(pdf_bytes(300)), '新版报告.pdf', id_)
    assert client.get(url).content == response.content
    store.edit(id_, active=False)
    assert client.get(url).status_code == 410
    assert '企业二维码' not in client.get(f'/d/{id_:04d}/').text
    store.delete(id_)
    assert client.get(url).status_code == 410
    assert client.get('/d/9999/qr.png').status_code == 404


def test_independent_catalog_order_preserves_old_report_and_review_binding(setup):
    client, store = setup
    old_id = add(store, '已有企业')
    review_id = add(store, '另一主体原名称')
    old = store.get(old_id)
    assert store.import_catalog(['新企业', '已有企业'], ['另一主体原名称']) == (1, 1)
    main = [r for r in store.list() if not r['needs_review']]
    assert [r['title'] for r in main] == ['新企业', '已有企业']
    assert [r['catalog_order'] for r in main] == [1, 2]
    assert main[1]['id'] == old_id
    for key in ('title', 'sha256', 'object_name', 'active'):
        assert store.get(old_id)[key] == old[key]
    identities = [(r['company_id'], r['id']) for r in main]
    assert store.import_catalog(['新企业', '已有企业'], ['另一主体原名称']) == (0, 2)
    assert [(r['company_id'], r['id']) for r in store.list() if not r['needs_review']] == identities
    pending = main[0]
    assert not pending['has_report'] and pending['size'] == 0 and pending['pages'] == 0
    page = client.get(f'/d/{pending["id"]:04d}/')
    assert page.status_code == 200 and '报告待上传' in page.text and '打开 PDF' not in page.text
    assert client.get(f'/d/{pending["id"]:04d}/file').status_code == 404
    assert client.get(f'/d/{pending["id"]:04d}/qr.png').status_code == 200
    assert '另一主体原名称' not in client.get('/').text
    assert client.get(f'/d/{review_id:04d}/file').content == pdf_bytes()
    login(client)
    assert '另一主体原名称' in client.get('/admin/?view=review').text


def test_create_pending_entry_and_first_upload_keep_permanent_qr(setup, tmp_path):
    client, store = setup
    assert client.post('/admin/entries', data={'title': '新目录企业'}).status_code == 401
    csrf = login(client)
    assert client.post('/admin/entries', data={'title': '新目录企业'}).status_code == 403
    response = client.post('/admin/entries', data={'csrf': csrf, 'title': '新目录企业'})
    assert response.status_code == 200
    row = store.list()[0]
    assert not row['has_report'] and not list(store.objects.glob('*.pdf'))
    # A backup of a pending record has no fake PDF, yet restores its exact permanent identity.
    backup = store.backup(tmp_path / 'pending-backup')
    restored = Store(Settings(data_dir=backup, secret='restored-pending-' * 4))
    assert restored.get(row['id']) == row
    qr_url = f'/d/{row["id"]:04d}/qr.png'
    before = client.get(qr_url).content
    store.bind_for_print([row])
    response = client.post('/admin/upload', data={'csrf': csrf}, files={'files': ('新目录企业.pdf', pdf_bytes(345))})
    assert response.status_code == 200
    assert len(store.list()) == 1 and store.get(row['id'])['has_report']
    assert client.get(qr_url).content == before
    assert client.get(f'/d/{row["id"]:04d}/file').content == pdf_bytes(345)
    assert store.get(row['id'])['catalog_order'] == row['catalog_order']
    assert client.post('/admin/entries', data={'csrf': csrf, 'title': '新目录企业'}).status_code == 400


def test_attachment_roster_56_entries_exact_order_and_47_pending(setup):
    client, store = setup
    source = json.loads((ROOT / 'catalog/附件目录.json').read_text())['entries']
    assert [r['number'] for r in source] == list(range(1, 57))
    names = [r['name'] for r in source]
    existing_numbers = [2, 8, 10, 11, 17, 18, 22, 44, 48]
    existing = {}
    for number in existing_numbers:
        id_ = add(store, names[number - 1])
        existing[id_] = store.get(id_)
    review_names = ['河北钢诺新材料科技有限公司', '立讯精密工业（苏州）有限公司']
    for title in review_names:
        id_ = add(store, title)
        existing[id_] = store.get(id_)
    assert store.import_catalog(names, review_names) == (47, 9)
    main = [r for r in store.list() if not r['needs_review']]
    assert [r['title'] for r in main] == names
    assert [r['catalog_order'] for r in main] == list(range(1, 57))
    assert sum(not r['has_report'] for r in main) == 47
    assert len(store.list()) == 58 and len(list(store.objects.glob('*.pdf'))) == 11
    for id_, old in existing.items():
        row = store.get(id_)
        assert all(row[key] == old[key] for key in ('id', 'title', 'object_name', 'sha256', 'active'))
    assert client.get('/').text.count('data-document=') == 56
    assert store.import_catalog(names, review_names) == (0, 56)
    assert len(store.list()) == 58


def test_export_number_is_catalog_sequence_and_url_keeps_fixed_identity(setup, tmp_path):
    client, store = setup
    old_id = add(store, '已有企业')
    store.import_catalog(['新增企业', '已有企业'])
    rows = store.list()
    assert rows[0]['id'] != old_id and rows[0]['catalog_order'] == 1
    path = export_bundle(store, tmp_path / 'catalog-export')
    with zipfile.ZipFile(path) as z:
        first = Image.open(io.BytesIO(z.read('qr/0001.png')))
        second = Image.open(io.BytesIO(z.read('qr/0002.png')))
        assert zxingcpp.read_barcode(first).text == f'{store.cfg.base_url}/d/{rows[0]["id"]:04d}/'
        assert zxingcpp.read_barcode(second).text == f'{store.cfg.base_url}/d/{old_id:04d}/'
        assert client.get(f'/d/{old_id:04d}/qr.png').content == z.read('qr/0002.png')
        assert "UTF-8''0002_" in client.get(f'/d/{old_id:04d}/qr.png?download=true').headers['content-disposition']
        assert '待上传' in z.read('mapping.csv').decode('utf-8-sig')
        assert [v['id'] for v in json.loads(z.read('verification.json'))] == ['0001', '0002']


def test_public_boundary_and_private_admin_remain_separate(setup):
    from library.main import create_public_app

    private, store = setup
    doc_id = add(store)
    # Starting public service must not remove a private upload in progress.
    staging = store.staging / 'in-progress.pdf'
    staging.write_bytes(b'upload in progress')
    with TestClient(create_public_app(store.cfg)) as public:
        assert staging.exists()
        for path in ['/admin/', '/admin/login', '/healthz', '/openapi.json',
                     '/static/app.js', '/.env', '/data/library.sqlite3',
                     '/%61dmin/', '/d/1/../../admin/']:
            assert public.get(path).status_code == 404, path
        for method in ['post', 'put', 'delete']:
            assert getattr(public, method)(f'/d/{doc_id}/file').status_code == 405
            assert getattr(public, method)('/admin/login').status_code == 404
        page = public.get(f'/d/{doc_id:04d}/')
        assert page.status_code == 200
        assert page.headers['X-Library-Install-ID'] == store.setting('install_id')
        assert 'set-cookie' not in page.headers
        assert public.get('/admin/', headers={'X-Forwarded-For': '127.0.0.1', 'Host': 'reports-admin.internal.example.org'}).status_code == 404
        assert public.get(f'/d/{doc_id}/', headers={'Cookie': 'library_session=invalid'}).status_code == 200
        assert '/admin/' not in page.text and '/qr.png' in page.text
        assert '返回报告查询' in page.text
        assert public.get('/static/style.css').status_code == 200
        endpoint = f'/d/{doc_id}/file'
        assert public.head(endpoint).status_code == 200
        assert public.get(endpoint + '?download=true').content == pdf_bytes()
        assert public.get(endpoint, headers={'Range': 'bytes=0-9'}).status_code == 206
        with store.db() as db:
            db.execute('UPDATE documents SET needs_review=1 WHERE id=?', (doc_id,))
        assert public.get(endpoint).status_code == 404
        assert public.get(f'/d/{doc_id}/').status_code == 404
        with store.db() as db:
            db.execute('UPDATE documents SET needs_review=0 WHERE id=?', (doc_id,))
        store.edit(doc_id, active=False)
        assert public.get(endpoint).status_code == 410
        store.delete(doc_id)
        assert public.get(endpoint).status_code == 410
    assert private.get('/admin/login').status_code == 200
    csrf = login(private)
    assert csrf


def test_public_qr_matches_private_downloads_and_publication_state(setup):
    from library.main import create_public_app

    private, store = setup
    doc_id = add(store, '公共二维码企业')
    url = f'/d/{doc_id:04d}/qr.png'
    before = private.get(url).content
    with TestClient(create_public_app(store.cfg)) as public:
        for query in ['', '?download=true', '?label=true&download=true']:
            response = public.get(url + query)
            expected = private.get(url + query)
            assert response.status_code == 200
            assert response.content == expected.content
            assert response.headers['content-type'] == 'image/png'
            assert response.headers.get('content-disposition') == expected.headers.get('content-disposition')
            assert 'set-cookie' not in response.headers
            decoded = zxingcpp.read_barcode(Image.open(io.BytesIO(response.content)))
            assert decoded and decoded.text == f'{store.cfg.base_url}/d/{doc_id:04d}/'
            head = public.head(url + query)
            assert head.status_code == 200 and not head.content
            assert head.headers['content-length'] == str(len(response.content))
        for method in ['post', 'put', 'patch', 'delete']:
            assert getattr(public, method)(url).status_code == 405
        page = public.get(f'/d/{doc_id:04d}/').text
        assert '企业二维码' in page and '下载二维码 PNG' in page and '下载企业标签' in page
        assert '/admin/' not in page
        store.ingest(io.BytesIO(pdf_bytes(500)), '更新.pdf', doc_id)
        assert public.get(url).content == before
        pending = store.create_entry('尚未上传的公共企业')
        assert public.get(f'/d/{pending:04d}/qr.png').status_code == 200
        with store.db() as db:
            db.execute('UPDATE documents SET needs_review=1 WHERE id=?', (doc_id,))
        assert public.get(url).status_code == 404
        assert private.get(url).status_code == 200
        with store.db() as db:
            db.execute('UPDATE documents SET needs_review=0 WHERE id=?', (doc_id,))
        store.edit(doc_id, active=False)
        assert public.get(url).status_code == 410
        assert '企业二维码' not in public.get(f'/d/{doc_id:04d}/').text
        store.edit(doc_id, active=True)
        assert public.get(url).content == before
        store.delete(doc_id)
        assert public.get(url).status_code == 410
        store.restore(doc_id)
        assert public.get(url).status_code == 410
        store.edit(doc_id, active=True)
        assert public.get(url).content == before
        assert public.get('/d/9999/qr.png').status_code == 404


def test_admin_qr_prefix_updates_existing_public_app_exports_and_backups(setup, tmp_path):
    from library.main import create_public_app

    client, store = setup
    doc_id = add(store, '二维码配置企业')
    old_row = store.get(doc_id)
    old_url = store.qr_base_url()
    assert client.get('/admin/settings').status_code == 401
    assert client.post('/admin/settings').status_code == 401
    csrf = login(client)
    assert '二维码设置' in client.get('/admin/').text
    assert old_url in client.get('/admin/settings').text
    data = {'csrf': csrf, 'qr_base_url': 'http://www.handan.tech:9996/', 'previous_base_url': old_url}
    assert client.post('/admin/settings', data=data).status_code == 400
    assert store.qr_base_url() == old_url
    assert client.post('/admin/settings', data={**data, 'csrf': 'bad', 'confirm_url_change': 'yes'}).status_code == 403
    with TestClient(create_public_app(store.cfg)) as public:
        assert public.get('/admin/settings').status_code == 404
        assert public.post('/admin/settings', data=data).status_code == 404
        response = client.post('/admin/settings', data={**data, 'confirm_url_change': 'yes'})
        assert response.status_code == 200 and '已保存' in response.text
        expected_base = 'http://www.handan.tech:9996'
        expected = f'{expected_base}/d/{doc_id:04d}/'
        assert Store(store.cfg, initialize=False).qr_base_url() == expected_base
        assert store.cfg.base_url == old_url
        assert store.get(doc_id) == old_row
        assert expected in client.get(f'/admin/documents/{doc_id}').text
        for suffix in ['', '?label=true&download=true']:
            result = public.get(f'/d/{doc_id:04d}/qr.png' + suffix)
            assert result.content == client.get(f'/d/{doc_id:04d}/qr.png' + suffix).content
            assert zxingcpp.read_barcode(Image.open(io.BytesIO(result.content))).text == expected
        before = public.get(f'/d/{doc_id:04d}/qr.png').content
        store.ingest(io.BytesIO(pdf_bytes(500)), '新版.pdf', doc_id)
        assert public.get(f'/d/{doc_id:04d}/qr.png').content == before
        assert public.get(f'/d/{doc_id:04d}/file').content == pdf_bytes(500)
    response = client.post('/admin/export', data={'csrf': csrf, 'doc_id': str(doc_id)})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert expected in z.read('mapping.csv').decode('utf-8-sig')
        assert json.loads(z.read('verification.json'))[0]['url'] == expected
        assert zxingcpp.read_barcode(Image.open(io.BytesIO(z.read('qr/0001.png')))).text == expected
    backup = store.backup(tmp_path / 'configured-backup')
    assert Store(Settings(data_dir=backup, secret='restore-' * 8)).qr_base_url() == expected_base
    assert client.post('/admin/settings', data={**data, 'confirm_url_change': 'yes'}).status_code == 400
    assert store.qr_base_url() == expected_base


def test_qr_prefix_rejects_invalid_roots_and_preserves_formal_binding(setup):
    from library.qr import check_export

    client, store = setup
    doc_id = add(store)
    csrf = login(client)
    previous = store.qr_base_url()
    for value in ['', 'www.handan.tech:9996', 'javascript:alert(1)', 'ftp://example.org',
                  'http://user:pass@example.org', 'http://example.org/d/0001/',
                  'http://example.org?x=1', 'http://example.org#part', 'http://example.org:99999',
                  'http://example.org:invalid', 'http://exa mple.org', 'http://example.org\\evil']:
        response = client.post('/admin/settings', data={'csrf': csrf, 'qr_base_url': value,
                               'previous_base_url': previous, 'confirm_url_change': 'yes'})
        assert response.status_code == 400, value
        assert store.qr_base_url() == previous
    configured = store.set_qr_base_url('https://reports.example.org', previous)
    store.cfg.qr_mode = 'production'
    store.set_setting('verified_base_url', previous)
    with pytest.raises(ValueError, match='verify-origin'):
        check_export(store)
    store.bind_for_print([store.get(doc_id)], configured)
    with pytest.raises(ValueError, match='正式二维码'):
        store.set_qr_base_url('https://other.example.org', configured)
    assert store.qr_base_url() == configured
    assert store.set_qr_base_url(configured, configured) == configured


def test_public_search_is_read_only_even_with_admin_session(setup):
    from library.main import create_public_app
    private, store = setup
    visible = add(store, '公开测试企业')
    hidden = add(store, '下架企业')
    store.edit(hidden, active=False)
    deleted = add(store, '删除企业')
    store.delete(deleted)
    csrf = login(private)
    with TestClient(create_public_app(store.cfg)) as public:
        public.cookies.update(private.cookies)
        page = public.get('/')
        assert page.status_code == 200
        assert '公开测试企业' in page.text
        assert '下架企业' not in page.text and '删除企业' not in page.text
        for forbidden in ['/admin/', '编辑', '删除', '管理报告', '新增报告']:
            assert forbidden not in page.text
        assert '公开测试企业' in public.get('/?q=公开测试').text
        assert '公开测试企业' not in public.get('/?q=不存在').text
        assert '&lt;script&gt;' in public.get('/?q=<script>').text
        assert public.post('/', data={'q': '公开'}).status_code == 405
        for action in ['edit', 'delete', 'replace', 'status']:
            assert public.post(f'/admin/documents/{visible}/{action}', data={'csrf': csrf, 'title': '被改'}).status_code == 404
        assert store.get(visible)['title'] == '公开测试企业'
        assert private.post(f'/admin/documents/{visible}/edit', data={'csrf': csrf, 'title': '内网已修改'}).status_code == 200


def test_private_query_matches_public_even_after_login(setup):
    from library.main import create_public_app
    private, store = setup
    add(store, '查询一致企业')
    csrf = login(private)
    with TestClient(create_public_app(store.cfg)) as public:
        for query in ['', '?q=查询', '?q=不存在']:
            assert private.get('/' + query).text == public.get('/' + query).text
        page = private.get('/').text
        assert '/admin/' not in page and '编辑' not in page and '删除' not in page
        admin = private.get('/admin/')
        assert admin.status_code == 200
        assert '/admin/documents/' in admin.text
        assert '删除' in admin.text and '编辑' in admin.text
        assert private.post('/admin/entries', data={'csrf': csrf, 'title': '管理员完整权限企业'}).status_code == 200
