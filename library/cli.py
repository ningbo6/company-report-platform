import argparse
import getpass
import hashlib
import os
import secrets
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from .config import ROOT, Settings
from .qr import export_bundle
from .security import hash_password
from .store import Store


def private_write(path, text):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)


def main():
    parser = argparse.ArgumentParser(description='企业画像报告管理')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='初始化配置和管理员')
    init.add_argument('--auto', action='store_true', help='生成随机管理员密码并保存到本地私有文件')
    imp = commands.add_parser('import', help='按文件名排序导入目录；已有相同文件跳过，同名不同内容报错')
    imp.add_argument('directory', type=Path)
    catalog = commands.add_parser('import-catalog', help='按名单顺序建立独立目录，无 PDF 的条目标记待上传')
    catalog.add_argument('file', type=Path)
    catalog.add_argument('--review-file', type=Path)
    commands.add_parser('export', help='导出二维码到 output 目录')
    commands.add_parser('verify-origin', help='通过 HTTPS 验证 BASE_URL 指向当前资料库')
    commands.add_parser('reset-password', help='交互式重置管理员密码并使旧会话失效')
    commands.add_parser('audit', help='核对当前文件、编号与 SHA256')
    backup = commands.add_parser('backup', help='一致性备份数据库、当前 PDF 和配置')
    backup.add_argument('--dest', type=Path)
    args = parser.parse_args()
    if args.command == 'init':
        env = ROOT / '.env'
        if not env.exists() and not os.getenv('SESSION_SECRET'):
            private_write(env, '# 本地开发配置；生产配置请阅读 README.md\n'
                          f'SESSION_SECRET={secrets.token_urlsafe(48)}\n'
                          'BASE_URL=http://127.0.0.1:18880\nCOOKIE_SECURE=false\n'
                          'MAX_FILE_MB=100\nQR_MODE=test\nLOGO_READY=false\n')
        store = Store(Settings.from_env())
        if store.setting('admin_password_hash'):
            print('已初始化；原管理员账号及密码保持不变。')
            return
        password = secrets.token_urlsafe(18) if args.auto else getpass.getpass('管理员密码（至少 12 位）：')
        encoded = hash_password(password)
        store.set_setting('admin_username', 'admin')
        store.set_setting('admin_password_hash', encoded)
        if args.auto:
            credentials = ROOT / '本地管理员凭据.txt' if os.access(ROOT, os.W_OK) else store.root / '本地管理员凭据.txt'
            private_write(credentials, f'后台：http://127.0.0.1:18880/admin/\n账号：admin\n密码：{password}\n\n仅保存在本地，请妥善保管；不要加入源码包或上传公开网站。\n')
            print(f'已初始化。随机密码已保存到 {credentials}（权限 600）。')
        else:
            print('已初始化管理员 admin。')
        return
    store = Store(Settings.from_env())
    if args.command == 'import-catalog':
        names = args.file.read_text(encoding='utf-8-sig').splitlines()
        review = args.review_file.read_text(encoding='utf-8-sig').splitlines() if args.review_file else []
        created, matched = store.import_catalog(names, review)
        print(f'目录已建立：共 {created + matched} 项，新增待上传 {created} 项，关联已有记录 {matched} 项；按名单顺序显示。')
    elif args.command == 'import':
        files = sorted(p for p in args.directory.iterdir() if p.is_file() and p.suffix.lower() == '.pdf')
        if not files:
            raise ValueError('目录中没有 PDF 文件')
        failed = 0
        for p in files:
            try:
                with p.open('rb') as f:
                    number, changed = store.ingest(f, p.name)
                print(f'{number:04d} {p.stem}：{"已导入" if changed else "重复跳过"}')
            except ValueError as exc:
                failed += 1
                print(f'失败 {p.name}：{exc}', file=sys.stderr)
        if failed:
            raise SystemExit(1)
    elif args.command == 'export':
        print(export_bundle(store, ROOT / 'output'))
    elif args.command == 'verify-origin':
        url = store.cfg.base_url
        if not url.startswith('https://'):
            raise ValueError('正式地址必须使用 HTTPS')
        locked = store.setting('printed_base_url')
        if locked and locked != url:
            raise ValueError('已有正式二维码绑定其他域名，不能覆盖')
        rows = [r for r in store.list() if not r['needs_review']]
        if not rows:
            raise ValueError('请先创建至少一条公开企业目录再验证域名')
        check_url = url + f"/d/{rows[0]['id']:04d}/"
        with urllib.request.urlopen(check_url, timeout=20) as response:
            if response.url != check_url:
                raise ValueError('域名验证不允许重定向，请配置最终 HTTPS 地址')
            install_id = response.headers.get('X-Library-Install-ID')
        if install_id != store.setting('install_id'):
            raise ValueError('域名没有指向当前资料库')
        store.set_setting('verified_base_url', url)
        print('正式域名已验证。正式导出仍需高清校徽，且须完成手机外网扫码验收。')
    elif args.command == 'reset-password':
        first = getpass.getpass('新密码：')
        if first != getpass.getpass('再次输入：'):
            raise ValueError('两次密码不一致')
        store.set_setting('admin_password_hash', hash_password(first))
        print('密码已更新，旧登录会话已失效。请同步更新或移除本地旧凭据文件。')
    elif args.command == 'backup':
        dest = args.dest or ROOT / 'backups' / datetime.now().strftime('%Y%m%d-%H%M%S')
        dest = store.backup(dest)
        os.chmod(dest, 0o700)
        if (ROOT / '.env').exists():
            shutil.copy2(ROOT / '.env', dest / '.env')
            os.chmod(dest / '.env', 0o600)
        else:
            # In Docker configuration is injected through env_file, not baked into the image.
            keys = ('SESSION_SECRET', 'BASE_URL', 'COOKIE_SECURE', 'MAX_FILE_MB', 'QR_MODE', 'LOGO_READY', 'LOGO_PATH')
            private_write(dest / '.env', '\n'.join(f'{key}={os.environ[key]}' for key in keys if key in os.environ) + '\n')
        shutil.copytree(ROOT / 'assets', dest / 'assets')
        if (ROOT / 'catalog').exists():
            shutil.copytree(ROOT / 'catalog', dest / 'catalog')
        if store.cfg.logo_path.resolve().parent != (ROOT / 'assets').resolve():
            shutil.copy2(store.cfg.logo_path, dest / 'custom-logo.png')
        print(f'备份完成：{dest}')
    elif args.command == 'audit':
        rows = store.list(include_disabled=True, include_deleted=True)
        reports = [r for r in rows if r['has_report']]
        for row in reports:
            path = store.objects / row['object_name']
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
                raise ValueError(f'{row["id"]:04d} 文件缺失或哈希不符')
        if len(list(store.objects.glob('*.pdf'))) != len(reports):
            raise ValueError('文件数量与编号记录不一致')
        print(f'核验通过：{len(rows)} 条记录，{len(reports)} 份 PDF，固定编号唯一，文件 SHA256 全部一致。')


if __name__ == '__main__':
    try:
        main()
    except ValueError as exc:
        raise SystemExit(str(exc))
