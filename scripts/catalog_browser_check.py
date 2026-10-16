"""Verify all attachment names and numbers against the live UI without changing reports."""
import io
import json
from pathlib import Path

import zxingcpp
from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
source = json.loads((ROOT / 'catalog/附件目录.json').read_text())['entries']
password = next(s.split('：', 1)[1] for s in (ROOT / '本地管理员凭据.txt').read_text().splitlines() if s.startswith('密码：'))
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1440, 'height': 1100})
    page.goto('http://127.0.0.1:18880/')
    rows = page.locator('tr[data-document]')
    assert rows.count() == 56
    actual = rows.evaluate_all('''rows => rows.map(r => [
        r.querySelector('.doc-number').textContent.trim(),
        r.querySelector('.report-company').textContent.trim()])''')
    assert actual == [[str(r['number']), r['name']] for r in source]
    assert page.locator('.badge.pending').count() == 47
    page.screenshot(path=str(ROOT / 'test-results/attachment-catalog-desktop.png'), full_page=False)
    first_id = int(rows.first.get_attribute('data-document'))
    rows.first.locator('a.view-report').click()
    expect(page.get_by_role('heading', name='报告待上传')).to_be_visible()
    assert page.get_by_role('link', name='打开 PDF').count() == 0
    preview = page.locator('.qr-preview img')
    expect(preview).to_be_visible()
    expect(preview).to_have_js_property('complete', True)
    assert preview.evaluate('image => image.naturalWidth') > 0
    result = zxingcpp.read_barcode(Image.open(io.BytesIO(preview.screenshot())))
    assert result and result.text == f'http://127.0.0.1:18880/d/{first_id:04d}/'
    page.screenshot(path=str(ROOT / 'test-results/attachment-pending-detail.png'), full_page=True)
    assert page.request.get(f'http://127.0.0.1:18880/d/{first_id:04d}/file').status == 404
    page.goto('http://127.0.0.1:18880/admin/')
    page.get_by_label('账号', exact=True).fill('admin')
    page.get_by_label('密码', exact=True).fill(password)
    page.get_by_role('button', name='登录后台').click()
    page.wait_for_url('**/admin/')
    assert page.locator('tr[data-document]').count() == 56
    assert '47 项待上传' in page.locator('.admin-heading').inner_text()
    page.screenshot(path=str(ROOT / 'test-results/attachment-admin-desktop.png'), full_page=False)
    page.get_by_role('link', name='待核对 2', exact=True).click()
    assert page.locator('tr[data-document]').count() == 2
    assert '河北钢诺新材料科技有限公司' in page.locator('tbody').inner_text()
    assert '立讯精密工业（苏州）有限公司' in page.locator('tbody').inner_text()
    page.screenshot(path=str(ROOT / 'test-results/attachment-review-desktop.png'), full_page=True)
    mobile = browser.new_page(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True)
    mobile.goto('http://127.0.0.1:18880/')
    assert mobile.locator('tr[data-document]').count() == 56
    assert mobile.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    mobile.screenshot(path=str(ROOT / 'test-results/attachment-catalog-mobile.png'), full_page=False)
    browser.close()
print('PASS: all 56 attachment numbers and names match; 47 pending entries, 2 review records, mobile layout and pending QR verified.')
