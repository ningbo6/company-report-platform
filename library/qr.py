import base64
import csv
import io
import json
import math
import os
import re
import tempfile
import zipfile
from pathlib import Path

import qrcode
import zxingcpp
from PIL import Image, ImageDraw, ImageFont

from .config import ROOT


def load_logo(path):
    with Image.open(path) as source:
        if source.format != 'PNG':
            raise ValueError('校徽必须使用透明背景的 PNG 文件')
        logo = source.convert('RGBA')
    alpha = logo.getchannel('A')
    corners = [(0, 0), (logo.width - 1, 0), (0, logo.height - 1), (logo.width - 1, logo.height - 1)]
    if any(alpha.getpixel(point) != 0 for point in corners):
        raise ValueError('校徽 PNG 的四角须为透明背景，请提供带透明通道的校徽')
    return logo


def check_export(store, base_url=None):
    cfg = store.cfg
    base_url = base_url or store.qr_base_url()
    if cfg.qr_mode == 'production':
        if not base_url.startswith('https://'):
            raise ValueError('正式二维码必须使用长期 HTTPS 域名')
        if store.setting('verified_base_url') != base_url:
            raise ValueError('请先运行 verify-origin，验证正式域名已指向本资料库')
        with load_logo(cfg.logo_path) as logo:
            if not cfg.logo_ready or min(logo.size) < 512:
                raise ValueError('高清校徽待补充：正式导出需要至少 512×512 的校徽，并设置 LOGO_READY=true')
        locked = store.setting('printed_base_url')
        if locked and locked != base_url:
            raise ValueError('域名已用于正式二维码，不能直接更改；请先设计旧域名迁移方案')


def _decode(image, expected):
    for size in (image.width, 900, 450):
        im = image if size == image.width else image.resize((size, round(image.height * size / image.width)))
        result = zxingcpp.read_barcode(im)
        if not result or result.text != expected:
            raise ValueError(f'二维码解码验证失败：{expected}（宽度 {size}）')


def make_code(url, logo_path):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, border=4, box_size=1)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    scale = math.ceil(1800 / n)
    side = n * scale
    margin = 120
    total = side + 2 * margin
    image = Image.new('RGB', (total, total), 'white')
    draw = ImageDraw.Draw(image)
    paths = []
    for y, row in enumerate(matrix):
        for x, black in enumerate(row):
            if black:
                xx, yy = margin + x * scale, margin + y * scale
                draw.rectangle((xx, yy, xx + scale - 1, yy + scale - 1), fill='black')
                paths.append(f'M{xx},{yy}h{scale}v{scale}h-{scale}z')
    # Preserve the PNG alpha: no rectangular white plate behind the circular emblem.
    # The entire emblem bounding box covers <= 15% of symbol width, excluding quiet zone.
    plate = int((n - 8) * scale * .15)
    with load_logo(logo_path) as logo:
        logo.thumbnail((plate - 16, plate - 16), Image.Resampling.LANCZOS)
        # Upscale only the explicitly identified preview; production requires a high-res source.
        if max(logo.size) < plate - 16:
            ratio = (plate - 16) / max(logo.size)
            logo = logo.resize((round(logo.width * ratio), round(logo.height * ratio)), Image.Resampling.LANCZOS)
        xy = ((total - logo.width) // 2, (total - logo.height) // 2)
        image.paste(logo, xy, logo)
        buf = io.BytesIO()
        logo.save(buf, format='PNG')
    encoded = base64.b64encode(buf.getvalue()).decode()
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{total}" viewBox="0 0 {total} {total}">'
           f'<rect width="100%" height="100%" fill="white"/><path d="{"".join(paths)}" fill="black"/>'
           f'<image x="{xy[0]}" y="{xy[1]}" width="{logo.width}" height="{logo.height}" href="data:image/png;base64,{encoded}"/></svg>')
    _decode(image, url)
    return image, svg


def label_image(code, title):
    font = ImageFont.truetype(str(ROOT / 'assets/NotoSansCJKsc-Regular.otf'), 70)
    lines, current = [], ''
    for c in title:
        if font.getlength(current + c) > code.width - 260 and current:
            lines.append(current)
            current = c
        else:
            current += c
    if current:
        lines.append(current)
    height = code.height + 100 * len(lines) + 80
    im = Image.new('RGB', (code.width, height), 'white')
    im.paste(code, (0, 0))
    draw = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        draw.text((im.width / 2, code.height + 15 + i * 100), line, font=font, fill='#173551', anchor='mt')
    return im


def _build_bundle(store, output, only_id=None, base_url=None):
    cfg = store.cfg
    base_url = base_url or store.qr_base_url()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows = store.list()
    if only_id is not None:
        rows = [r for r in rows if r['id'] == only_id]
        if not rows:
            raise ValueError('资料不存在或已下架')
    else:
        rows = [r for r in rows if not r['needs_review']]
    test = cfg.qr_mode != 'production'
    package = output / 'qrcodes.zip'
    mapping = io.StringIO(newline='')
    writer = csv.writer(mapping)
    writer.writerow(['编号', '企业名称', '当前原文件名', '固定网址', 'SHA256', '字节数', '模式', '二维码固定编号', '报告状态'])
    verification = []
    with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        entries = [(f'{r["catalog_order"] or r["id"]:04d}', r['title'], f'{base_url}/d/{r["id"]:04d}/', r) for r in rows]
        for number, title, url, row in entries:
            code, svg = make_code(url, cfg.logo_path)
            label = label_image(code, title)
            _decode(label, url)
            safe_title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', title)
            for name, image in [(f'labels/{number}_{safe_title}.png', label), (f'qr/{number}.png', code)]:
                b = io.BytesIO()
                image.save(b, 'PNG', dpi=(300, 300))
                z.writestr(name, b.getvalue())
            z.writestr(f'qr/{number}.svg', svg)
            verification.append({'id': number, 'url': url, 'decoded': True, 'widths': [code.width, 900, 450],
                                 'document_id': row['id'] if row else None})
            if row:
                def csv_safe(value):
                    value = str(value)
                    return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value
                writer.writerow([number, csv_safe(title), csv_safe(row['original_name']), url, row['sha256'], row['size'],
                                 '测试' if test else '正式', f'{row["id"]:04d}', '已有报告' if row['has_report'] else '待上传'])
        csv_bytes = ('\ufeff' + mapping.getvalue()).encode('utf-8')
        z.writestr('mapping.csv', csv_bytes)
        z.writestr('verification.json', json.dumps(verification, ensure_ascii=False, indent=2))
        with load_logo(cfg.logo_path) as logo:
            buf = io.BytesIO()
            logo.save(buf, format='PNG')
            z.writestr('assets/school-logo-transparent.png', buf.getvalue())
        z.writestr('使用说明.txt', 'labels 为含企业全称的独立标签，qr 为方形二维码。\n'
                   + ('此包仅供本地测试，不能正式打印张贴。高清校徽和长期 HTTPS 域名尚需确认。\n' if test else '正式打印前请按实际尺寸试印，并用手机移动网络扫码。\n')
                   + '二维码周围白边必须完整保留。SVG 中的二维码为矢量，中心校徽为嵌入图片。\n')
    (output / 'mapping.csv').write_bytes(csv_bytes)
    (output / 'verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2))
    if not test:
        store.bind_for_print(rows, base_url)
    return package


def export_bundle(store, output, only_id=None):
    base_url = store.qr_base_url()
    check_export(store, base_url)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output, prefix='.building-') as temporary:
        package = _build_bundle(store, temporary, only_id, base_url)
        for name in ('mapping.csv', 'verification.json'):
            os.replace(Path(temporary) / name, output / name)
        with zipfile.ZipFile(package) as z:
            (output / 'school-logo-transparent.png').write_bytes(z.read('assets/school-logo-transparent.png'))
        os.replace(package, output / 'qrcodes.zip')
    return output / 'qrcodes.zip'
