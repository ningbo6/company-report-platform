import fcntl
import hmac
import io
import json
import re
import secrets
import tempfile
import time
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.middleware.sessions import SessionMiddleware

from .config import ROOT, Settings
from .qr import export_bundle, label_image, make_code
from .security import password_tag, verify_password
from .store import Store


class PayloadTooLarge(Exception):
    pass


class SecurityMiddleware:
    def __init__(self, app, cfg):
        self.app, self.cfg = app, cfg

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        started = False
        read = 0
        limit = min(self.cfg.max_body_bytes, 16384) if scope['path'] == '/admin/login' else self.cfg.max_body_bytes

        async def guarded_receive():
            nonlocal read
            message = await receive()
            read += len(message.get('body', b''))
            if read > limit:
                raise PayloadTooLarge()
            return message

        async def guarded_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                message['headers'] += [
                    (b'x-content-type-options', b'nosniff'),
                    (b'referrer-policy', b'same-origin'),
                    (b'x-frame-options', b'SAMEORIGIN'),
                    (b'content-security-policy', b"default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; form-action 'self'; frame-ancestors 'self'; base-uri 'none'; object-src 'none'"),
                    (b'cache-control', b'no-store'),
                ]
            await send(message)

        try:
            try:
                content_length = int(headers.get(b'content-length', b'0'))
            except ValueError:
                return await Response('Content-Length 无效', status_code=400)(scope, receive, guarded_send)
            if content_length < 0:
                return await Response('Content-Length 无效', status_code=400)(scope, receive, guarded_send)
            if content_length > limit:
                raise PayloadTooLarge()
            await self.app(scope, guarded_receive, guarded_send)
        except PayloadTooLarge:
            if started:
                raise
            await Response('请求内容超过大小上限，请减少内容或分批上传。', status_code=413)(scope, receive, guarded_send)


class PublicOnlyMiddleware:
    """An independent allowlist; never trust a client header to select admin access."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            path = scope['path']
            allowed = (re.fullmatch(r'/d/[0-9]+/(?:file)?', path)
                       or path in ('/', '/static/style.css'))
            if not allowed:
                return await Response('Not Found', status_code=404)(scope, receive, send)
            if scope['method'] not in ('GET', 'HEAD'):
                return await Response(status_code=405, headers={'Allow': 'GET, HEAD'})(scope, receive, send)
        return await self.app(scope, receive, send)


class DocumentResponse(Response):
    """Keep publication state and file stable for the lifetime of a range/download response."""
    def __init__(self, store, doc_id, download):
        super().__init__()
        self.store, self.doc_id, self.download = store, doc_id, download

    async def __call__(self, scope, receive, send):
        lock = (self.store.root / '.write.lock').open('a')
        try:
            await run_in_threadpool(fcntl.flock, lock, fcntl.LOCK_SH)
            row = await run_in_threadpool(self.store.get, self.doc_id)
            if not row:
                return await Response('资料不存在', status_code=404)(scope, receive, send)
            if row['deleted_at']:
                return await Response('资料已删除', status_code=410)(scope, receive, send)
            if not row['active']:
                return await Response('资料已下架', status_code=410)(scope, receive, send)
            if not row['has_report']:
                return await Response('报告待上传', status_code=404)(scope, receive, send)
            path = self.store.objects / row['object_name']
            if not path.is_file():
                return await Response('资料文件暂不可用', status_code=503)(scope, receive, send)
            response = FileResponse(path, media_type='application/pdf', filename=row['title'] + '.pdf',
                                    content_disposition_type='attachment' if self.download else 'inline',
                                    headers={'ETag': f'"{row["sha256"]}"'})
            await response(scope, receive, send)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()


def create_app(settings=None, *, public_only=False):
    cfg = settings or Settings.from_env()
    store = Store(cfg, initialize=not public_only)
    app = FastAPI(title='企业画像报告', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.cfg = cfg
    if not public_only:
        app.add_middleware(SessionMiddleware, secret_key=cfg.secret, session_cookie='library_session',
                           max_age=12 * 3600, same_site='strict', https_only=cfg.secure_cookie)
    app.add_middleware(SecurityMiddleware, cfg=cfg)
    if public_only:
        app.add_middleware(PublicOnlyMiddleware)
    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
    templates = Jinja2Templates(directory=ROOT / 'templates')
    templates.env.filters['size'] = lambda n: f'{n / 1024 / 1024:.1f} MB'
    templates.env.filters['localtime'] = lambda s: datetime.fromisoformat(s).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')

    def authenticated(request):
        pw = store.setting('admin_password_hash', '')
        return bool(pw and request.session.get('admin') == password_tag(pw))

    def render(request, template, status_code=200, **context):
        if not public_only:
            request.session.setdefault('csrf', secrets.token_urlsafe(32))
        return templates.TemplateResponse(request=request, name=template, context={
            'cfg': cfg, 'csrf': '' if public_only else request.session.get('csrf', ''),
            'public_only': public_only, 'is_admin': False if public_only else authenticated(request),
            'flash': None if public_only else request.session.pop('flash', None), **context}, status_code=status_code)

    def admin_required(request):
        if not authenticated(request):
            raise HTTPException(401, '请先登录管理后台')

    async def form(request, login=False):
        if not login:
            admin_required(request)
        data = await request.form(max_files=0 if login else cfg.max_batch_files, max_fields=10, max_part_size=8192)
        expected = request.session.get('csrf', '')
        supplied = str(data.get('csrf', ''))
        if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
            await data.close()
            raise HTTPException(403, '页面已过期或校验失败，请刷新后重试')
        return data

    def redirect(request, message, url='/admin/'):
        request.session['flash'] = message
        return RedirectResponse(url, status_code=303)

    @app.exception_handler(HTTPException)
    async def error(request, exc):
        return render(request, 'error.html', status_code=exc.status_code, message=exc.detail)

    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'install_id': store.setting('install_id')}

    @app.get('/', response_class=HTMLResponse)
    def index(request: Request, q: str = ''):
        rows = [r for r in store.list(q.strip()) if not r['needs_review']]
        return render(request, 'index.html', rows=rows, q=q,
                      public_only=True, is_admin=False, flash=None)

    @app.get('/d/{doc_id}/', response_class=HTMLResponse)
    def detail(request: Request, doc_id: int):
        row = store.get(doc_id)
        if row is None or (public_only and row['needs_review']):
            raise HTTPException(404, '资料不存在')
        if doc_id < 1:
            raise HTTPException(404, '资料不存在')
        response = render(request, 'detail.html', row=row, status_code=200 if row['active'] and not row['deleted_at'] else 410)
        response.headers['X-Library-Install-ID'] = store.setting('install_id')
        return response

    @app.api_route('/d/{doc_id}/file', methods=['GET', 'HEAD'])
    def pdf(doc_id: int, download: bool = False):
        if public_only:
            row = store.get(doc_id)
            if row and row['needs_review']:
                raise HTTPException(404, '资料不存在')
        return DocumentResponse(store, doc_id, download)

    @app.get('/d/{doc_id}/qr.png')
    def document_qr(doc_id: int, label: bool = False, download: bool = False):
        row = store.get(doc_id)
        if not row:
            raise HTTPException(404, '资料不存在')
        if row['deleted_at'] or not row['active']:
            raise HTTPException(410, '资料已删除' if row['deleted_at'] else '资料已下架')
        try:
            code, _ = make_code(f'{cfg.base_url}/d/{doc_id:04d}/', cfg.logo_path)
            image = label_image(code, row['title']) if label else code
            buffer = io.BytesIO()
            image.save(buffer, format='PNG', dpi=(300, 300))
        except (ValueError, OSError) as exc:
            raise HTTPException(503, '二维码暂时无法生成，请联系管理员') from exc
        headers = {}
        if download:
            number = row['catalog_order'] or doc_id
            filename = f'{number:04d}_{row["title"]}{"_标签" if label else "_二维码"}.png'
            headers['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
        return Response(buffer.getvalue(), media_type='image/png', headers=headers)

    @app.get('/admin/login')
    def login_page(request: Request):
        if authenticated(request):
            return RedirectResponse('/admin/', status_code=303)
        return render(request, 'login.html')

    @app.post('/admin/login')
    async def login(request: Request):
        data = await form(request, login=True)
        try:
            ip = request.client.host if request.client else 'unknown'
            ts = time.time()
            with store.db() as db:
                db.execute('DELETE FROM login_attempts WHERE ts < ?', (ts - 900,))
                count = db.execute('SELECT COUNT(*) FROM login_attempts WHERE ip=?', (ip,)).fetchone()[0]
                total = db.execute('SELECT COUNT(*) FROM login_attempts').fetchone()[0]
                if count >= 5 or total >= 100:
                    raise HTTPException(429, '登录尝试过多，请 15 分钟后再试')
                db.execute('INSERT INTO login_attempts VALUES (?,?)', (ip, ts))
            encoded = store.setting('admin_password_hash', '')
            username = str(data.get('username', ''))
            good = await run_in_threadpool(verify_password, str(data.get('password', '')), encoded)
            if not good or not hmac.compare_digest(username.encode(), store.setting('admin_username', 'admin').encode()):
                return render(request, 'login.html', status_code=401, error='账号或密码不正确')
            with store.db() as db:
                db.execute('DELETE FROM login_attempts WHERE ip=?', (ip,))
            request.session.clear()
            request.session.update(admin=password_tag(encoded), csrf=secrets.token_urlsafe(32))
            return RedirectResponse('/admin/', status_code=303)
        finally:
            await data.close()

    @app.post('/admin/logout')
    async def logout(request: Request):
        data = await form(request)
        await data.close()
        request.session.clear()
        return RedirectResponse('/', status_code=303)

    @app.get('/admin/')
    def admin(request: Request, q: str = '', view: str = ''):
        if not authenticated(request):
            return RedirectResponse('/admin/login', status_code=303)
        if view not in ('', 'deleted', 'review'):
            raise HTTPException(400, '列表类型无效')
        rows = store.list(q.strip(), include_disabled=True, include_deleted=view == 'deleted')
        if view == 'deleted':
            rows = [r for r in rows if r['deleted_at']]
        elif view == 'review':
            rows = [r for r in rows if r['needs_review']]
        else:
            rows = [r for r in rows if not r['needs_review']]
        all_records = store.list(include_disabled=True)
        all_rows = [r for r in all_records if not r['needs_review']]
        deleted_count = sum(bool(r['deleted_at']) for r in store.list(include_disabled=True, include_deleted=True))
        return render(request, 'admin.html', rows=rows, q=q, view=view, deleted_count=deleted_count,
                      count=len(all_rows), active=sum(r['active'] and r['has_report'] for r in all_rows),
                      pending=sum(not r['has_report'] for r in all_rows),
                      review_count=sum(r['needs_review'] for r in all_records))

    @app.post('/admin/entries')
    async def create_entry(request: Request):
        data = await form(request)
        try:
            doc_id = await run_in_threadpool(store.create_entry, str(data.get('title', '')))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            await data.close()
        return redirect(request, '目录条目已建立，报告待上传；已分配永久二维码地址。', f'/admin/documents/{doc_id}')

    @app.post('/admin/upload')
    async def upload(request: Request):
        data = await form(request)
        try:
            files = [f for f in data.getlist('files') if isinstance(f, UploadFile) and f.filename]
            if not files:
                raise HTTPException(400, '请选择至少一份 PDF')
            added, skipped, errors = 0, 0, []
            for f in sorted(files, key=lambda f: f.filename):
                try:
                    _, changed = await run_in_threadpool(store.ingest, f.file, f.filename)
                    added += int(changed)
                    skipped += int(not changed)
                except ValueError as exc:
                    errors.append(f'{f.filename[:50]}：{exc}')
            message = f'导入成功 {added} 份，重复跳过 {skipped} 份，失败 {len(errors)} 份。'
            if errors:
                message += '；'.join(errors[:5])
            return redirect(request, message)
        finally:
            await data.close()

    @app.get('/admin/documents/{doc_id}')
    def edit_page(request: Request, doc_id: int):
        admin_required(request)
        row = store.get(doc_id)
        if not row:
            raise HTTPException(404, '资料不存在')
        if row['deleted_at']:
            return render(request, 'deleted.html', row=row)
        bindings = json.loads(store.setting('printed_company_names', '{}'))
        return render(request, 'edit.html', row=row, fixed_url=f'{cfg.base_url}/d/{doc_id:04d}/',
                      name_locked=str(doc_id) in bindings)

    @app.post('/admin/documents/{doc_id}/edit')
    async def edit(request: Request, doc_id: int):
        data = await form(request)
        try:
            await run_in_threadpool(store.edit, doc_id, title=str(data.get('title', '')))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            await data.close()
        return redirect(request, '企业名称已更新，资料编号及二维码保持不变。', f'/admin/documents/{doc_id}')

    @app.post('/admin/documents/{doc_id}/replace')
    async def replace(request: Request, doc_id: int):
        data = await form(request)
        try:
            f = data.get('file')
            if not isinstance(f, UploadFile) or not f.filename:
                raise ValueError('请选择新版 PDF')
            await run_in_threadpool(store.ingest, f.file, f.filename, doc_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            await data.close()
        return redirect(request, '报告已保存，只保留当前 PDF；原编号及二维码继续有效，发布状态保持不变。', f'/admin/documents/{doc_id}')

    @app.post('/admin/documents/{doc_id}/status')
    async def status(request: Request, doc_id: int):
        data = await form(request)
        try:
            action = str(data.get('action', ''))
            if action not in ('disable', 'enable'):
                raise ValueError('操作无效')
            await run_in_threadpool(store.edit, doc_id, active=action == 'enable')
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            await data.close()
        return redirect(request, '资料已恢复发布。' if action == 'enable' else '资料已下架，原链接及文件入口均已停止提供内容。', f'/admin/documents/{doc_id}')

    @app.post('/admin/export')
    async def export(request: Request):
        data = await form(request)
        try:
            raw = str(data.get('doc_id', ''))
            only_id = int(raw) if raw else None
        except ValueError as exc:
            raise HTTPException(400, '编号无效') from exc
        finally:
            await data.close()
        tmp = tempfile.TemporaryDirectory(prefix='library-export-')
        try:
            package = await run_in_threadpool(export_bundle, store, tmp.name, only_id)
        except ValueError as exc:
            tmp.cleanup()
            raise HTTPException(400, str(exc)) from exc
        except BaseException:
            tmp.cleanup()
            raise
        return FileResponse(package, filename='qrcodes-test.zip' if cfg.qr_mode == 'test' else 'qrcodes.zip',
                            background=BackgroundTask(tmp.cleanup))

    @app.post('/admin/documents/{doc_id}/delete')
    async def delete(request: Request, doc_id: int):
        data = await form(request)
        try:
            await run_in_threadpool(store.delete, doc_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            await data.close()
        return redirect(request, '报告已移入回收站，旧二维码显示“资料已删除”；企业编号保留，可恢复。')

    @app.post('/admin/documents/{doc_id}/restore')
    async def restore(request: Request, doc_id: int):
        data = await form(request)
        try:
            await run_in_threadpool(store.restore, doc_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            await data.close()
        return redirect(request, '报告已恢复，原编号和二维码保持不变。当前保持下架，可确认或替换报告后再恢复发布。', f'/admin/documents/{doc_id}')

    return app


def create_public_app(settings=None):
    return create_app(settings, public_only=True)
