"""Browser CRUD checks against an isolated temporary library, never the real reports.

Run with: python -m scripts.crud_browser_check
"""
import io
import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright
from pypdf import PdfWriter

from library.config import ROOT, Settings
from library.main import create_app
from library.security import hash_password


def pdf(width):
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=200)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def main():
    with tempfile.TemporaryDirectory() as tmp, socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        cfg = Settings(data_dir=Path(tmp) / 'data', secret='temporary-browser-test-' * 3, base_url=origin)
        app = create_app(cfg)
        store = app.state.store
        store.set_setting('admin_username', 'admin')
        store.set_setting('admin_password_hash', hash_password('browser-check-password'))
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            assert server.started
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={'width': 1280, 'height': 1000})
                page.set_default_timeout(10000)
                confirmations = []

                def confirm(dialog):
                    confirmations.append(dialog.message)
                    dialog.accept()

                page.on('dialog', confirm)
                page.goto(origin + '/admin/')
                page.get_by_label('账号', exact=True).fill('admin')
                page.get_by_label('密码', exact=True).fill('browser-check-password')
                page.get_by_role('button', name='登录后台').click()
                page.wait_for_url('**/admin/')
                page.get_by_role('link', name='+ 新增报告', exact=True).click()
                expect(page.locator('#upload')).to_have_attribute('open', '')
                page.locator('#files').set_input_files({'name': '浏览器验证企业.pdf', 'mimeType': 'application/pdf', 'buffer': pdf(200)})
                page.get_by_role('button', name='上传并发布').click()
                page.wait_for_url('**/admin/')
                assert page.locator('tr[data-document]').count() == 1
                page.locator('.manage-link').click()
                page.get_by_label('企业全称', exact=True).fill('浏览器验证企业（更正名称）')
                page.get_by_role('button', name='保存名称').click()
                page.wait_for_url('**/admin/documents/1')
                assert page.locator('h1').inner_text() == '浏览器验证企业（更正名称）'
                page.locator('#file').set_input_files({'name': '企业新版画像.pdf', 'mimeType': 'application/pdf', 'buffer': pdf(400)})
                page.get_by_role('button', name='确认替换', exact=True).click()
                page.wait_for_url('**/admin/documents/1')
                assert page.request.get(origin + '/d/0001/file').body() == pdf(400)
                assert any('企业新版画像.pdf' in message for message in confirmations)
                page.get_by_role('button', name='删除浏览器验证企业（更正名称）', exact=True).click()
                page.wait_for_url('**/admin/')
                assert page.locator('tr[data-document]').count() == 0
                assert page.request.get(origin + '/d/0001/file').status == 410
                page.get_by_role('link', name='回收站 1', exact=True).click()
                assert page.locator('tr[data-document]').count() == 1
                page.get_by_role('button', name='恢复报告', exact=True).click()
                page.wait_for_url('**/admin/documents/1')
                assert page.request.get(origin + '/d/0001/file').status == 410
                page.get_by_role('button', name='恢复发布', exact=True).click()
                page.wait_for_url('**/admin/documents/1')
                assert page.request.get(origin + '/d/0001/file').body() == pdf(400)
                assert store.get(1)['title'] == '浏览器验证企业（更正名称）'
                assert not store.get(1)['deleted_at']
                assert len(list(store.objects.glob('*.pdf'))) == 1
                (ROOT / 'test-results').mkdir(exist_ok=True)
                page.screenshot(path=str(ROOT / 'test-results/crud-browser-verified.png'), full_page=True)
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()
    print('PASS: isolated browser create, rename, replace, delete, recycle-bin restore and republish; fixed ID preserved.')


if __name__ == '__main__':
    main()
