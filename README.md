# 企业画像报告二维码资料库

当前版本：`v0.0.2`。后续常规提交发布依次递增补丁版本号：`v0.0.3`、`v0.0.4`，以此类推；项目版本与 Git 标签保持一致。

服务器入口已拆分为公网只读服务与内网管理服务。公网开放只读查询页、企业报告页、PDF、企业二维码预览/下载和必要样式，管理台仅在内网使用。部署步骤与访问验收见 [deploy/README.md](deploy/README.md)，内网管理使用独立 URL 和 HTTPS。下文的本地 HTTP 地址仅用于开发；公网不再提供总目录码。

访客可按企业名称或编号查询企业画像报告，阅读或下载最新 PDF，也可扫码进入对应企业的报告页。管理员可在列表中新增、编辑、替换、删除及查询报告，也可下架/恢复发布。每份资料使用永久编号；替换 PDF 不改变资料链接，旧 PDF 不保留。

本地目录以《职业技术学院函（科技局）》第 2–4 页附件为准，共 56 家企业，编号 1–56。已关联同名报告 9 份，另有 47 项标记“待上传”。原有 11 份 PDF 均保留；钢诺和立讯两条不同名称的旧记录单列“待核对”，不自动合并。学校校徽当前使用官网小图裁切预览，**高清校徽待补充，当前二维码仅供测试**。

## 企业目录与固定二维码

- 每家企业有独立目录记录，保存在 `companies`；其报告及永久访问地址由 `documents` 记录承接。名称不一致的原报告不会猜测归并。
- **目录序号**依附件排列；页面显示、导出文件名前缀与 CSV 的“编号”均使用该序号。**二维码固定编号**是 `/d/0001/` 等地址中的编号，独立保存，不随目录顺序改变。CSV 额外给出固定码编号和报告状态，不能根据目录序号拼接网址，应直接使用“固定网址”列。
- 待上传条目不伪造 PDF，也不显示虚假页数、大小或下载按钮。其详情页显示“报告待上传”，二维码可先查看/导出；首次上传与今后替换均保持原固定码。
- 后台的“待核对”单列原钢诺与苏州立讯记录，原报告仍可经原地址访问，主目录和批量导出不混入这两条旧记录；可从原记录单独导出二维码。
- `catalog/附件目录.json` 保存逐项编号、名称、源页码及 PDF 哈希，`catalog/企业目录.txt` 为按附件顺序的导入名单。先前手工发送的 61 项名单仅保留为参考文件，不作为当前目录依据。

重建或复核目录时，先恢复既有数据库以保留二维码，再运行以下幂等命令；重复执行不会新建重复企业或改变旧地址：

```bash
.venv/bin/python -m library.cli import-catalog catalog/企业目录.txt --review-file catalog/待核对原名称.txt
```

## 本地打开

- 报告目录：<http://127.0.0.1:18888/>
- 管理后台：<http://127.0.0.1:18889/admin/>
- 管理员：`admin`，当前密码保存在项目根目录的 `本地管理员凭据.txt`，权限为 600。不要将该文件放入公开网站、源代码包或群聊。
- 双击 `start.command` 启动 Docker Desktop 并构建、启动 Compose 服务。容器在后台运行，关闭终端不会停止。停止用 `docker compose stop`，再次启动用 `docker compose up -d --wait`；查看日志用 `docker compose logs --tail=100 app`。

本机的 `127.0.0.1` 二维码只能用于本机验证，手机扫码不会访问到这台 Mac。手机外网扫码应在正式域名部署后进行。

可选的 Python 开发运行方式（Python 3.13；有 uv 时也可以用 `uv venv --python 3.13 .venv`；与 Docker 服务不能同时占用 18880 端口）：

```bash
cd '/你的路径/pdf-library'
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m library.cli init --auto
.venv/bin/python -m library.cli import '../文件资料'
.venv/bin/python -m library.cli import-catalog catalog/企业目录.txt --review-file catalog/待核对原名称.txt
.venv/bin/python -m library.cli export
.venv/bin/python -m uvicorn library.main:create_app --factory --host 127.0.0.1 --port 18880 --no-server-header
```

如果是在迁移已有资料库，**应恢复数据库与 objects，不要重新初始化后按顺序导入**，否则无法保证旧二维码的编号对应关系。

## 管理操作

1. 登录后台，使用“新增企业画像报告”批量上传。每份 PDF 的文件名（去掉后缀）作为企业全称，首次按文件名排序分配编号；后续新资料仅追加编号。
2. 同名且内容完全相同的文件跳过；同名但内容不同会提示使用“替换 PDF”，不会自动覆盖。批量上传逐份处理并汇报成功/跳过/失败，不会因一份无效而撤销其他成功的文件。
3. 进入对应企业的“管理”，可在正式导出前校正企业全称，或选择该企业的新版 PDF 并“确认替换”。确认框显示企业全称、固定编号和所选文件名。验证通过才切换当前文件并清理旧文件；失败则保留原文件。
4. 下架后，总目录隐藏该企业，原资料页返回 HTTP 410 并说明下架，PDF 打开、下载及 Range 请求均停止提供内容。已下载到访客设备的文件不受此操作影响。恢复发布沿用原编号。
5. “批量导出”生成 ZIP；单份资料的管理页面也可以单独导出。导出时逐张解码校验，失败则不交付该次 ZIP。
6. 列表每行直接提供“编辑 / 替换报告 / 查看 / 删除”。删除须确认后移入回收站，公开目录与后台主列表隐藏该报告，旧二维码显示“资料已删除”，PDF 入口返回 410。删除不物理清除当前 PDF，企业名称与编号继续保留，避免误删后二维码失效或被复用。
7. 后台切换到“回收站”可查询并恢复报告。恢复沿用原企业与编号，默认保持下架；确认或替换报告后，再点击“恢复发布”。同名企业在回收站时不能重新上传成新编号，应先恢复原记录。
8. 列表点击“查看”进入企业报告详情，页面直接展示该企业的二维码，可点击看原图，或下载方形二维码 PNG 和带企业全称的标签图片。预览及下载与批量导出使用同一生成逻辑，内容一致；下架或删除后关闭这些入口。
9. 缺少 PDF 时，可在“新增企业画像报告”面板仅填写名称，先建“待上传”条目。随后在对应行点“上传报告”；批量上传同名 PDF 也会补齐现有待上传条目，不会重新分配二维码。已有 PDF 的条目仍须显式使用“替换报告”。

默认上限为单份 100 MB、每批 30 份、单批请求总计 1 GB。拒绝非 PDF、损坏、零页或加密文件。允许不同企业上传相同内容，但不会凭内容合并企业身份。报告可移入回收站，固定编号记录不删除、不重用。

修改管理员密码（旧会话立即失效）：

```bash
.venv/bin/python -m library.cli reset-password
```

修改后同步更新或移除旧的本地凭据文件。密码只在数据库中保存 PBKDF2 哈希；配置中的 SESSION_SECRET 用于签名会话，不能公开。后台写操作校验会话及 CSRF，登录失败有速率限制。

## 二维码与打印交付

当前本机与服务器的二维码地址统一为 `http://www.handan.tech:9996/d/固定编号/`。管理员进入后台的“二维码设置”，填写并保存“二维码地址前缀”即可修改；配置存入数据库，公开端、管理端预览、单张下载和新导出 ZIP 立即生效，无需重启。数据库配置优先于 `.env` 中的 BASE_URL，后者仅作为未配置时的初始值；本机与服务器是独立资料库，需分别设置。

更换前缀不会重排企业编号，也不会修改报告。管理员须确认旧二维码继续可访问或尚未使用；已下载的图片、已张贴的二维码及已有 ZIP 不会自动改写，需要重新下载或导出。已正式打印绑定的地址继续受保护，不能直接改成另一地址。该域名的 HTTPS 证书尚未就绪，当前仍为测试模式。

`output/qrcodes.zip` 与后台下载 ZIP 使用相同格式：

```text
labels/0001_企业全称.png   中央校徽 + 方形二维码 + 下方企业全称
qr/0001.png                不含企业名称的方形码
qr/0001.svg                矢量二维码路径，校徽为嵌入 PNG
mapping.csv                编号、企业名称、原文件名、固定地址、SHA256、大小、模式
verification.json          每张码的解码验证结果
assets/school-logo-transparent.png  原始透明校徽 PNG
使用说明.txt
```

PNG 的二维码主体至少 1800×1800 像素，按整数模块绘制，不将二维码图案插值缩放；300 DPI，H 级纠错，四模块白边。中央校徽使用透明背景 PNG，完整保留 alpha 通道，不额外添加矩形白底，边界框不超过有效码宽的 15%。二维码外还有留白；印制不得裁切白边或覆盖定位图案。企业名称按中文字体换行，保证完整显示。源校徽四角不透明时会拒绝导出，避免误用带白色方框的素材。

导出的标签图片只显示二维码、中央透明校徽与企业全称，不含英文测试字样、资料编号或底部说明。编号与运行模式保留在 CSV 对照表中，环境说明保留在网页及 ZIP 内的使用说明里。当前仅有约 110 像素校徽，放大用于版式预览，并不构成高清原件。素材来源详见 `assets/SOURCES.md`。

正式导出条件：

1. 部署长期域名和 HTTPS，在后台“二维码设置”保存 `https://实际域名`；`.env` 设置 `COOKIE_SECURE=true`。未配置过后台前缀时可用 BASE_URL 提供初始值。长期保留已印制二维码的入口。
2. 补充清晰的独立校徽（至少 512×512，不能仅把小图放大），放入 `assets/`。配置 `LOGO_PATH=assets/school-emblem.png`、`LOGO_READY=true`，然后重启服务。
3. 运行 `.venv/bin/python -m library.cli verify-origin`。程序访问该域名的一条已发布企业资料页，并通过响应头核对资料库实例编号，不接受重定向或其他网站。
4. 设置 `QR_MODE=production`，重新启动服务、重新导出。首次正式导出会锁定域名及每家企业的“编号—企业全称”绑定关系，防止已印制二维码改绑其他企业；后续仍可替换本企业的 PDF。以后换域名必须另行设计旧域名转发方案，程序不允许直接导出新域名覆盖记录。
5. 用手机移动网络分别从微信和浏览器查看/下载；以实际尺寸打印一张，确认清晰度、校徽、企业全称与报告对应，再批量印制。

本地解码通过不等于已完成实物打印或手机外网验收。不要将测试地址印在展示区。

## 数据与备份恢复

```text
data/library.sqlite3     永久编号、企业名称、当前对象、发布状态、管理员密码哈希
data/objects/            每份资料当前唯一的 PDF；不直接通过 Nginx 或 StaticFiles 发布
data/staging/            验证中的文件；验证失败清理，异常退出后启动时回收
.env                    域名、会话密钥、校徽与导出模式配置
assets/                 校徽、字体及许可
```

数据库启动时为旧版补充回收站、待上传、待核对字段与独立企业目录表，不改既有报告的企业名称、固定编号、文件哈希或发布状态。备份包含企业目录、待上传、待核对及回收站记录；audit 只核验真实存在的 PDF，不要求待上传条目有文件。公开列表和批量导出排除待核对及回收站条目。

替换使用写锁、独立暂存、数据库事务及文件切换，启动时回收中断操作的孤立文件。文件下载持有共享锁直至响应结束，替换/下架会等待正在发送的下载完成；这保证响应过程中文件稳定。运行中的导出以生成时读取到的名称和哈希制作对照表。

每次集中更新后执行一次备份，并复制到另一台设备：

```bash
.venv/bin/python -m library.cli audit
.venv/bin/python -m library.cli backup
```

备份位于 `backups/时间戳/`，含 SQLite 一致性快照、所有当前 PDF、对应清单、`.env` 和校徽字体。备份期间阻止写入；配置文件应在无并行修改时备份。备份含管理员哈希和会话密钥，应按私有文件保管。独立备份不是后台历史版本或回退功能。

恢复步骤：

1. 停止服务，保留当前 data 与 .env 的独立副本，不覆盖唯一原件。
2. 在新的项目目录恢复备份中的 `library.sqlite3` 到 `data/library.sqlite3`，`objects/` 到 `data/objects/`；复制 `.env` 和 `assets/`，保持编号及原域名不变。`manifest.json` 仅为核验清单，不需要导入数据库。
3. 如备份有 `custom-logo.png`，配置 LOGO_PATH 为该文件的新路径；Docker 环境下使用容器内路径。若 DATA_DIR、LOGO_PATH 含旧机器的绝对路径，改为当前路径。不要更改 BASE_URL 或重新编号。
4. 确保 data 的属主为运行用户，目录权限 700、数据库和 .env 权限 600。
5. 执行 `audit`，确认文件数及哈希；启动后抽查旧二维码对应资料页、下载及下架状态。

## Docker 与未来服务器部署

当前本机已通过 Docker Compose 构建并运行，服务为 `pdf-library-app-1`，分别绑定宿主机 `127.0.0.1:18889`（管理）和 `127.0.0.1:18888`（公开只读），不占 80/443。已验证容器健康检查、重建后数据保留、原企业编号和 PDF 哈希一致；管理员凭据与固定二维码地址沿用原配置。

日常运行命令（在本项目目录执行）：

```bash
docker compose up -d --build --wait  # 首次启动、更新程序后重建；需要 Docker Desktop 运行
docker compose ps                  # 检查状态，应为 healthy
docker compose logs --tail=100 app  # 查看日志
docker compose stop                # 停止，不删除数据
docker compose up -d --wait         # 再次启动
```

`data/`、`output/`、`backups/` 均直接绑定宿主机目录；`.env` 通过运行环境注入。重建镜像和容器不会重建资料库。已配置 `restart: unless-stopped`，Docker 引擎重新启动后会恢复未手动停止的服务；Mac 上仍需 Docker Desktop 处于运行状态。本机 `.env` 中 APP_UID/APP_GID 使用现有数据属主的 UID/GID，保留原目录权限。

已有本地数据迁入 Docker 时，迁移整个 `data/` 与 `.env`，不要仅上传 PDF 后重建编号。Linux 上可以使用现有普通用户的 UID/GID 运行容器，保证绑定目录可写：

```bash
mkdir -p data output backups
# 将下面两个变量的实际数字写入 .env；不要将 shell 表达式原样写入。
id -u
id -g
# .env 中增加 APP_UID=实际UID 与 APP_GID=实际GID
docker compose build
docker compose config --quiet
docker compose up -d
```

如果选择默认 UID/GID 10001，应先将 `data/`、`output/`、`backups/` 的属主设为 10001:10001。镜像以非 root 用户运行，根文件系统只读，临时上传文件放在 /tmp 的受限临时卷中。镜像及源码包不包含 .env、报告数据或明文凭据。

全新 Docker 安装时，先在宿主机准备 `.env`（依据 `.env.example` 并生成随机长 SESSION_SECRET）、准备可写的数据目录，然后用交互方式初始化账号：

```bash
docker compose run --rm app python -m library.cli init
```

会在终端询问管理员密码，至少 12 位。通过后台上传 PDF，或显式只读挂载导入来源后运行 CLI。已有 data 时不要重建实例。

容器备份：

```bash
docker compose exec app python -m library.cli backup
docker compose exec app python -m library.cli audit
```

`backups/` 已绑定到宿主机；容器备份会将注入的运行配置写入备份 .env。

本次迁移前备份在 `backups/pre-docker-migration/`，容器重建后生成的备份在 `backups/post-docker-migration/`，均包含数据库、报告、配置和素材。Linux 容器独立检查可执行以下命令，使用临时数据，不挂载真实报告：

```bash
docker run --rm -i --read-only --tmpfs /tmp:size=256m,mode=1777 \
  --user 10001:10001 pdf-library-app python - < scripts/docker_smoke.py
```

HTTPS 接入示例在 `deploy/nginx.conf.example`。部署前由规划者确认实际服务器、端口、域名、证书与代理位置；执行者只接入本服务；验收者按旧码、外网手机、替换及下架场景核验。当前开发验证不代替独立验收。不修改其他业务或系统代理配置。

如果 Nginx 在容器内，不能把该容器的 127.0.0.1 当成宿主机：应将代理与资料库接入同一个私有 Docker 网络，转发至 `app:18880`。按实际代理网络配置 Uvicorn `--forwarded-allow-ips`，只信任该代理，避免滥信外部转发头。不要直接映射 objects 为静态目录，否则会绕过下架检查。上线前检查上传上限、代理超时和 PDF Range。

## 验证与维护

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m playwright install chromium
# 先启动本地服务、完成导出，且本地自动凭据文件仍有效时：
.venv/bin/python scripts/browser_check.py
.venv/bin/python scripts/catalog_browser_check.py
# 使用独立临时资料库验证浏览器增删改查，不操作真实报告：
.venv/bin/python -m scripts.crud_browser_check
```

测试使用临时数据库和模拟 PDF，不替换实际企业报告。浏览器检查只登录、浏览和导出，不改动报告。`test-results/` 保存页面截图，`TEST-REPORT.md` 记录本次验证范围与未完成项目。

公共二维码预览与真实下载可单独核验（`--expected-qr-base` 应与被测实例 BASE_URL 相同）：

```bash
.venv/bin/python scripts/public_qr_browser_check.py --expected-qr-base http://www.handan.tech:9996
```

依赖固定于 `requirements.txt`，开发依赖固定于 `requirements-dev.txt`。后续升级时更新锁定文件，并重新验证替换、下架、CSRF、PDF 下载和二维码解码。
