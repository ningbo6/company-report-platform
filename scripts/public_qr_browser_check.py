"""Read-only browser checks for public QR previews and real PNG downloads."""
import argparse
import json
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright
import zxingcpp

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url', default='http://127.0.0.1:18888')
parser.add_argument('--expected-qr-base', required=True)
parser.add_argument('--tag', default='local')
args = parser.parse_args()
out = Path(__file__).resolve().parents[1] / 'test-results'
out.mkdir(exist_ok=True)
errors = []
checks = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    for width in [1440, 390]:
        context = browser.new_context(viewport={'width': width, 'height': 900},
                                      is_mobile=width == 390, has_touch=width == 390)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        for doc_id in ['0008', '0012']:
            page.goto(f'{args.base_url.rstrip("/")}/d/{doc_id}/')
            image = page.locator('.qr-preview img')
            image.wait_for()
            page.wait_for_function('document.querySelector(".qr-preview img").naturalWidth > 0')
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert page.locator('a[href^="/admin/"]').count() == 0
            assert page.locator('.qr-downloads a').count() == 2
            page.screenshot(path=str(out / f'public-qr-{args.tag}-{doc_id}-{width}.png'), full_page=True)
            if width == 390:
                for label, text in [('qr', '下载二维码 PNG'), ('label', '下载企业标签')]:
                    with page.expect_download() as event:
                        page.get_by_role('link', name=text).click()
                    download = event.value
                    assert download.failure() is None
                    path = out / f'public-{args.tag}-{doc_id}-{label}-download.png'
                    download.save_as(path)
                    with Image.open(path) as png:
                        assert png.width >= 1800
                        code = zxingcpp.read_barcode(png)
                        assert code and code.text == f'{args.expected_qr_base.rstrip("/")}/d/{doc_id}/'
            checks.append(f'{doc_id} width {width}: QR preview, downloads, layout and no admin controls')
        assert context.request.get(args.base_url + '/admin/').status == 404
        assert context.request.post(args.base_url + '/d/0008/qr.png').status == 405
        context.close()
    browser.close()
assert not errors, errors
result = {'status': 'PASS', 'base_url': args.base_url, 'checks': checks,
          'downloaded_images_decoded': 4, 'page_errors': errors}
(out / f'public-qr-browser-{args.tag}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False, indent=2))
