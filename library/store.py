import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader


def now():
    return datetime.now(UTC).isoformat(timespec='seconds')


class InvalidPDF(ValueError):
    pass


class Store:
    def __init__(self, settings, initialize=True):
        self.cfg = settings
        self.root = settings.data_dir
        self.objects = self.root / 'objects'
        self.staging = self.root / 'staging'
        self.db_path = self.root / 'library.sqlite3'
        if not initialize:
            if not self.db_path.is_file():
                raise RuntimeError('请先启动管理服务并初始化资料库')
            return
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.objects.mkdir(exist_ok=True)
        self.staging.mkdir(exist_ok=True)
        self.db_path = self.root / 'library.sqlite3'
        with self.lock(), self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL UNIQUE,
                    original_name TEXT NOT NULL,
                    object_name TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    pages INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT NOT NULL, ts REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL UNIQUE,
                    document_id INTEGER NOT NULL UNIQUE REFERENCES documents(id),
                    display_order INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
            ''')
            if 'deleted_at' not in {r[1] for r in db.execute('PRAGMA table_info(documents)')}:
                db.execute('ALTER TABLE documents ADD COLUMN deleted_at TEXT')
            columns = {r[1] for r in db.execute('PRAGMA table_info(documents)')}
            for name, definition in [('has_report', 'INTEGER NOT NULL DEFAULT 1'),
                                     ('needs_review', 'INTEGER NOT NULL DEFAULT 0')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE documents ADD COLUMN {name} {definition}')
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('install_id', str(uuid.uuid4())))
            # Filesystem switch happens before commit; recover orphans after interrupted writes.
            referenced = {r[0] for r in db.execute('SELECT object_name FROM documents')}
            for p in self.objects.glob('*.pdf'):
                if p.name not in referenced:
                    p.unlink()
            for p in self.staging.iterdir():
                if p.is_file():
                    p.unlink()
        os.chmod(self.db_path, 0o600)

    @contextmanager
    def lock(self):
        with (self.root / '.write.lock').open('a') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def setting(self, key, default=None):
        with self.db() as db:
            r = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return r[0] if r else default

    def set_setting(self, key, value):
        with self.lock(), self.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, value))

    def list(self, query='', include_disabled=False, include_deleted=False):
        with self.db() as db:
            rows = db.execute('''SELECT d.*,c.id AS company_id,c.display_order AS catalog_order
                FROM documents d LEFT JOIN companies c ON c.document_id=d.id
                ORDER BY c.display_order IS NULL,c.display_order,d.id''').fetchall()
        return [dict(r) for r in rows if (not r['deleted_at'] or include_deleted)
                and (r['active'] or include_disabled)
                and (query.casefold() in r['title'].casefold() or query in f'{r["id"]:04d}'
                     or (r['catalog_order'] is not None and query in f'{r["catalog_order"]:04d}'))]

    def get(self, doc_id):
        with self.db() as db:
            r = db.execute('''SELECT d.*,c.id AS company_id,c.display_order AS catalog_order
                FROM documents d LEFT JOIN companies c ON c.document_id=d.id WHERE d.id=?''', (doc_id,)).fetchone()
        return dict(r) if r else None

    @staticmethod
    def normalized_title(value):
        return ''.join(unicodedata.normalize('NFKC', value).split()).casefold()

    @staticmethod
    def _link_company(db, doc_id, title, position=None):
        if position is None:
            position = db.execute('SELECT COALESCE(MAX(display_order),0)+1 FROM companies').fetchone()[0]
        existing = db.execute('SELECT id FROM companies WHERE document_id=?', (doc_id,)).fetchone()
        if existing:
            db.execute('UPDATE companies SET display_order=? WHERE document_id=?', (position, doc_id))
        else:
            db.execute('INSERT INTO companies (title,document_id,display_order,created_at) VALUES (?,?,?,?)',
                       (title, doc_id, position, now()))

    @staticmethod
    def _pending_document(db, title):
        stamp = now()
        return db.execute('''INSERT INTO documents
            (title,original_name,object_name,sha256,size,pages,active,created_at,updated_at,has_report)
            VALUES (?,?,?,'',0,0,1,?,?,0)''',
                          (title, '', f'pending-{uuid.uuid4().hex}', stamp, stamp)).lastrowid

    def create_entry(self, title):
        title = self.title(title)
        with self.lock(), self.db() as db:
            names = [r[0] for r in db.execute('SELECT title FROM documents')]
            if any(self.normalized_title(n) == self.normalized_title(title) for n in names):
                raise ValueError('该名称已有记录，请使用现有条目；若已删除，请先从回收站恢复')
            doc_id = self._pending_document(db, title)
            self._link_company(db, doc_id, title)
        return doc_id

    def import_catalog(self, names, review_names=()):
        names = [self.title(n) for n in names if n.strip()]
        if not names:
            raise ValueError('目录名单不能为空')
        keys = [self.normalized_title(n) for n in names]
        if len(keys) != len(set(keys)):
            raise ValueError('目录名单存在重复名称，请先核对')
        review_keys = {self.normalized_title(n) for n in review_names}
        if set(keys) & review_keys:
            raise ValueError('正式目录和待核对原名称不能重叠')
        created = matched = 0
        with self.lock(), self.db() as db:
            records = {self.normalized_title(r['title']): r for r in db.execute('SELECT * FROM documents')}
            for position, (title, key) in enumerate(zip(names, keys), 1):
                if key in records:
                    doc_id = records[key]['id']
                    matched += 1
                else:
                    doc_id = self._pending_document(db, title)
                    created += 1
                self._link_company(db, doc_id, title, position)
                db.execute('UPDATE documents SET needs_review=0 WHERE id=?', (doc_id,))
            # Existing entries outside this roster follow it; their identities are never reassigned.
            extras = db.execute('SELECT id,title FROM companies ORDER BY display_order,id').fetchall()
            position = len(names)
            for entry in extras:
                if self.normalized_title(entry['title']) not in set(keys):
                    position += 1
                    db.execute('UPDATE companies SET display_order=? WHERE id=?', (position, entry['id']))
            for key in review_keys:
                if key in records:
                    db.execute('UPDATE documents SET needs_review=1 WHERE id=?', (records[key]['id'],))
        return created, matched

    def _stage(self, source, filename):
        filename = Path(filename.replace('\\', '/')).name
        if not filename.lower().endswith('.pdf'):
            raise InvalidPDF('仅支持 PDF 文件')
        fd, name = tempfile.mkstemp(dir=self.staging, suffix='.pdf')
        p = Path(name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, 'wb') as f:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.cfg.max_file_bytes:
                        raise InvalidPDF(f'文件超过 {self.cfg.max_file_bytes // 1024 // 1024} MB 上限')
                    digest.update(chunk)
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
            with p.open('rb') as f:
                if not f.read(8).startswith(b'%PDF-'):
                    raise InvalidPDF('不是有效的 PDF 文件')
                f.seek(0)
                try:
                    reader = PdfReader(f, strict=True)
                    if reader.is_encrypted:
                        raise InvalidPDF('不支持加密 PDF，请先提供无密码版本')
                    pages = len(reader.pages)
                    if pages < 1:
                        raise InvalidPDF('PDF 没有可读页面')
                    for page in reader.pages:
                        _ = page.mediabox
                except InvalidPDF:
                    raise
                except Exception as exc:
                    raise InvalidPDF('PDF 结构损坏，原文件未改变') from exc
            return p, filename, digest.hexdigest(), size, pages
        except BaseException:
            p.unlink(missing_ok=True)
            raise

    @staticmethod
    def title(value):
        value = value.strip()
        if not value or len(value) > 200 or any(ord(c) < 32 for c in value):
            raise ValueError('企业名称须为 1–200 个可见字符')
        return value

    def ingest(self, source, filename, doc_id=None):
        # Lock covers staging too: startup recovery and backups cannot race uploads.
        with self.lock():
            p, filename, sha, size, pages = self._stage(source, filename)
            obj = self.objects / f'{uuid.uuid4().hex}.pdf'
            old = None
            committed = False
            try:
                with self.db() as db:
                    if doc_id is None:
                        title = self.title(Path(filename).stem)
                        existing = next((r for r in db.execute('SELECT * FROM documents')
                                         if self.normalized_title(r['title']) == self.normalized_title(title)), None)
                        if existing:
                            if existing['deleted_at']:
                                raise ValueError('该企业已在回收站，请先恢复原资料，保留原编号和二维码')
                            if existing['has_report']:
                                if existing['sha256'] == sha:
                                    return existing['id'], False
                                raise ValueError('同名企业已存在，请进入该资料的“替换 PDF”操作')
                            doc_id = existing['id']
                    if doc_id is None:
                        os.replace(p, obj)
                        cur = db.execute('''INSERT INTO documents
                            (title,original_name,object_name,sha256,size,pages,created_at,updated_at)
                            VALUES (?,?,?,?,?,?,?,?)''', (title, filename, obj.name, sha, size, pages, now(), now()))
                        doc_id = cur.lastrowid
                        self._link_company(db, doc_id, title)
                    else:
                        row = db.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
                        if not row:
                            raise ValueError('资料不存在')
                        if row['deleted_at']:
                            raise ValueError('资料已删除，请先从回收站恢复')
                        old = self.objects / row['object_name'] if row['has_report'] else None
                        os.replace(p, obj)
                        db.execute('''UPDATE documents SET original_name=?,object_name=?,sha256=?,
                            size=?,pages=?,updated_at=?,has_report=1 WHERE id=?''', (filename, obj.name, sha, size, pages, now(), doc_id))
                committed = True
                if old:
                    old.unlink(missing_ok=True)
                return doc_id, True
            finally:
                p.unlink(missing_ok=True)
                if not committed:
                    obj.unlink(missing_ok=True)

    def edit(self, doc_id, title=None, active=None):
        with self.lock(), self.db() as db:
            row = db.execute('SELECT id,deleted_at FROM documents WHERE id=?', (doc_id,)).fetchone()
            if not row:
                raise ValueError('资料不存在')
            if row['deleted_at']:
                raise ValueError('资料已删除，请先从回收站恢复')
            try:
                if title is not None:
                    title = self.title(title)
                    binding = db.execute('SELECT value FROM settings WHERE key=?', ('printed_company_names',)).fetchone()
                    names = json.loads(binding[0]) if binding else {}
                    if str(doc_id) in names and names[str(doc_id)] != title:
                        raise ValueError('该编号已与企业名称正式绑定，不能改成其他企业。更新报告请使用“替换 PDF”；新增企业请单独上传')
                    db.execute('UPDATE documents SET title=?,updated_at=? WHERE id=?', (title, now(), doc_id))
                    db.execute('UPDATE companies SET title=? WHERE document_id=?', (title, doc_id))
                if active is not None:
                    db.execute('UPDATE documents SET active=?,updated_at=? WHERE id=?', (int(active), now(), doc_id))
            except sqlite3.IntegrityError as exc:
                raise ValueError('已有同名企业，请使用不同名称') from exc

    def delete(self, doc_id):
        # Logical deletion preserves the company binding and the current PDF for recovery.
        with self.lock(), self.db() as db:
            row = db.execute('SELECT id,deleted_at FROM documents WHERE id=?', (doc_id,)).fetchone()
            if not row:
                raise ValueError('资料不存在')
            if not row['deleted_at']:
                db.execute('UPDATE documents SET deleted_at=?,active=0,updated_at=? WHERE id=?', (now(), now(), doc_id))

    def restore(self, doc_id):
        with self.lock(), self.db() as db:
            row = db.execute('SELECT id,deleted_at FROM documents WHERE id=?', (doc_id,)).fetchone()
            if not row or not row['deleted_at']:
                raise ValueError('回收站中没有这份资料')
            db.execute('UPDATE documents SET deleted_at=NULL,active=0,updated_at=? WHERE id=?', (now(), doc_id))

    def bind_for_print(self, rows, base_url=None):
        """Once formally exported, printed company names may not be rebound to another company."""
        with self.lock(), self.db() as db:
            if base_url:
                old = db.execute('SELECT value FROM settings WHERE key=?', ('printed_base_url',)).fetchone()
                if old and old[0] != base_url:
                    raise ValueError('域名已用于正式二维码，不能直接更改')
            r = db.execute('SELECT value FROM settings WHERE key=?', ('printed_company_names',)).fetchone()
            bindings = json.loads(r[0]) if r else {}
            for row in rows:
                current = db.execute('SELECT title,active,sha256,deleted_at FROM documents WHERE id=?', (row['id'],)).fetchone()
                if (not current or current['deleted_at'] or not current['active'] or current['title'] != row['title']
                        or current['sha256'] != row['sha256']):
                    raise ValueError('企业名称、文件或发布状态刚刚发生变化，请重新导出')
                bindings[str(row['id'])] = row['title']
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                       ('printed_company_names', json.dumps(bindings, ensure_ascii=False)))
            if base_url:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('printed_base_url', base_url))

    def backup(self, dest):
        # Writes are excluded throughout database snapshot + current PDF copy.
        dest = Path(dest).resolve()
        if dest == self.root.resolve() or self.root.resolve() in dest.parents:
            raise ValueError('备份目录必须在 data 目录之外')
        dest.mkdir(parents=True, exist_ok=False)
        with self.lock():
            with sqlite3.connect(self.db_path) as src, sqlite3.connect(dest / 'library.sqlite3') as target:
                src.backup(target)
            shutil.copytree(self.objects, dest / 'objects')
            records = self.list(include_disabled=True, include_deleted=True)
            (dest / 'manifest.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
        return dest
