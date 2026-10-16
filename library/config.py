import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: ROOT / 'data')
    base_url: str = 'http://127.0.0.1:18888'
    secret: str = ''
    secure_cookie: bool = False
    max_file_bytes: int = 100 * 1024 * 1024
    max_batch_files: int = 30
    max_body_bytes: int = 1024 * 1024 * 1024
    logo_path: Path = field(default_factory=lambda: ROOT / 'assets/school-emblem-preview.png')
    logo_ready: bool = False
    qr_mode: str = 'test'

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        self.logo_path = Path(self.logo_path)
        self.base_url = self.base_url.rstrip('/')
        u = urlsplit(self.base_url)
        if (u.scheme not in ('http', 'https') or not u.netloc or u.path
                or u.query or u.fragment or u.username or u.password):
            raise ValueError('BASE_URL 必须是完整网站根地址，不含路径、账号或参数')
        if self.qr_mode not in ('test', 'production'):
            raise ValueError('QR_MODE 只能为 test 或 production')
        if len(self.secret) < 32:
            raise ValueError('尚未初始化或 SESSION_SECRET 太短，请先运行 python -m library.cli init')

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / '.env')
        return cls(
            data_dir=Path(os.getenv('DATA_DIR', str(ROOT / 'data'))),
            base_url=os.getenv('BASE_URL', 'http://127.0.0.1:18888'),
            secret=os.getenv('SESSION_SECRET', ''),
            secure_cookie=os.getenv('COOKIE_SECURE', 'false').lower() == 'true',
            max_file_bytes=int(os.getenv('MAX_FILE_MB', '100')) * 1024 * 1024,
            logo_path=Path(os.getenv('LOGO_PATH', str(ROOT / 'assets/school-emblem-preview.png'))),
            logo_ready=os.getenv('LOGO_READY', 'false').lower() == 'true',
            qr_mode=os.getenv('QR_MODE', 'test'),
        )
