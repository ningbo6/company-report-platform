"""Read-only report browsing, real admin login and QR download; leaves report contents intact."""
import io
import json
import zipfile
from pathlib import Path

import zxingcpp
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'test-results'
OUT.mkdir(exist_ok=True)
password = next(s.split('：', 1)[1] for s in (ROOT / '本地管理员凭据.txt').read_text().splitlines() if s.startswith('密码：'))
expected = json.loads((ROOT / 'catalog/附件目录.json').read_text())['entries']
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1440, 'height': 1100})
    page.set_default_timeout(10000)
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.goto('http://127.0.0.1:18880/')
    assert page.locator('tr[data-document]').count() == len(expected)
    page.screenshot(path=str(OUT / 'catalog-desktop.png'), full_page=True)
    page.get_by_label('搜索企业名称或编号').fill('博柯莱')
    page.get_by_role('button', name='搜索报告').click()
    assert page.locator('tr[data-document]').count() == 1
    page.locator('a.view-report').click()
    assert '河北博柯莱智能装备科技股份有限公司' in page.locator('h1').inner_text()
    page.screenshot(path=str(OUT / 'detail-desktop.png'), full_page=True)
    with page.expect_download() as pdf_download:
        page.get_by_role('link', name='下载 PDF').click()
    pdf_download.value.save_as(str(OUT / 'browser-report-download.pdf'))
    assert (OUT / 'browser-report-download.pdf').read_bytes().startswith(b'%PDF-')
    page.goto('http://127.0.0.1:18880/admin/')
    page.get_by_label('账号', exact=True).fill('admin')
    page.get_by_label('密码', exact=True).fill(password)
    page.get_by_role('button', name='登录后台').click()
    page.wait_for_url('**/admin/')
    assert page.locator('tbody tr').count() == len(expected)
    page.screenshot(path=str(OUT / 'admin-desktop.png'), full_page=True)
    page.locator('a.manage-link').first.click()
    page.screenshot(path=str(OUT / 'edit-desktop.png'), full_page=True)
    with page.expect_download(timeout=60000) as dl:
        page.get_by_role('button', name='导出本企业二维码').click()
    dl.value.save_as(str(OUT / 'browser-single-export.zip'))
    with zipfile.ZipFile(OUT / 'browser-single-export.zip') as z:
        assert len(json.loads(z.read('verification.json'))) == 1
    mobile = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
    for slug, url in [('catalog', '/'), ('detail', '/d/0001/')]:
        mobile.goto('http://127.0.0.1:18880' + url)
        assert mobile.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        mobile.screenshot(path=str(OUT / f'{slug}-mobile.png'), full_page=True)
    mobile.goto('http://127.0.0.1:18880/admin/login')
    mobile.get_by_label('账号', exact=True).fill('admin')
    mobile.get_by_label('密码', exact=True).fill(password)
    mobile.get_by_role('button', name='登录后台').click()
    mobile.wait_for_url('**/admin/')
    assert mobile.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    mobile.screenshot(path=str(OUT / 'admin-mobile.png'), full_page=True)
    svg_count = 0
    with zipfile.ZipFile(ROOT / 'output/qrcodes.zip') as z:
        for name in z.namelist():
            if name.endswith('.svg'):
                svg = OUT / Path(name).name
                svg.write_bytes(z.read(name))
                page.goto(svg.as_uri())
                page.set_viewport_size({'width': 1100, 'height': 1100})
                decoded = zxingcpp.read_barcode(Image.open(io.BytesIO(page.locator('svg').screenshot())))
                assert decoded and decoded.text.startswith('http://127.0.0.1:18880/'), name
                svg_count += 1
    assert not errors, errors
    browser.close()
print(f'PASS: desktop/mobile catalog ({len(expected)} entries), search, detail, admin, QR download; all {svg_count} SVG codes decode; no page errors.')
