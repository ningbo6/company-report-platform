# 10.9.0.98 部署

- SSH：`ssh hd@10.9.0.98`，使用本机密钥。
- 项目：`/home/hd/Projects/enterprise-qr/`，包含应用、原始文件资料、历史备份和输出。
- 应用工作目录：`/home/hd/Projects/enterprise-qr/pdf-library/`。
- 查询：<https://10.9.0.98:18888/>
- 管理：<https://10.9.0.98:18889/admin/>
- 管理账号、密码沿用原资料库，见本地私有文件 `本地管理员凭据.txt`。

对外端口与开发环境一致。服务器使用 HTTPS；临时自签名证书需要在客户端明确信任，证书位于服务器 `deploy/certs/server.crt`，私钥不回传。现有 80/443 业务不受影响。

管理入口仅允许此次部署电脑的来源地址 `10.0.101.63` 和服务器自身 `10.9.0.98`，其他来源返回 403。电脑出口地址改变后，需按实际授权修改 `deploy/nginx.10.9.0.98.conf`，再执行 `docker compose exec gateway nginx -t` 和 `docker compose exec gateway nginx -s reload`。

服务器 `compose.override.yaml` 使用 `deploy/compose.10.9.0.98.yaml` 的配置。应用容器只绑定回环 `19889`（管理）和 `19888`（查询），由独立 Nginx 在服务器内网 IP 的 `18889`、`18888` 提供 HTTPS。公开入口剥离会话 Cookie，应用继续限制只读路由。

在服务器应用目录运行：

```bash
docker compose ps
docker compose logs --tail=100 app public gateway
docker compose exec -T app python -m library.cli audit
docker compose exec -T app python -m library.cli backup
docker compose up -d --build --wait
```

迁移使用 `backups/server-migration-20261015/` 中的一致性数据库与 objects，保留 58 条记录、11 份 PDF、企业目录和管理员密码。二维码地址前缀改为当前服务器的查询 URL，固定编号不变。历史输出中的旧二维码仍含原地址；应使用服务器重新导出的版本。当前 `QR_MODE=test`，待长期域名、可信证书、高清校徽到位后再执行正式导出流程。内网部署不构成手机移动网络或实物打印验收。

迁移后本机和服务器是两份独立资料库；后续业务资料更新应在服务器后台进行。

## 已完成的服务器验证

- app/public 健康检查通过，独立 gateway 已运行，三个服务均配置自动重启。
- 服务器 audit：58 条记录、11 份 PDF，文件 SHA256 全部一致。
- 从部署电脑通过 HTTPS 核验全部 56 个公开企业页面、待上传状态、PDF 完整下载哈希、HEAD 和 Range。
- 现有管理员实际登录成功，浏览器渲染查询列表和后台列表通过；会话 Cookie 带 Secure。
- 公开入口的管理页、健康信息、配置、备份、批量导出和管理 POST 返回 404；带管理员 Cookie 仍保持只读。单企业二维码预览、PNG 和企业标签下载已向公开访客开放。
- 未授权 Docker 网段访问管理入口返回 403；部署电脑无法连接内部 19888/19889 端口。
- 新镜像在临时数据上通过上传、替换、下架、删除、恢复、固定链接、二维码解码和备份检查，未改动真实报告。
- 服务器 `output/qrcodes.zip` 已重新导出并逐码解码校验，指向 `https://10.9.0.98:18888`；旧历史输出仍保留。
- 部署后备份：`backups/server-deployed-20261015/`。

本地核验证据：`test-results/server-validation.json`、`server-public.png`、`server-admin.png`；服务器也保存同一副本。二维码包另存本地 `output/server-10.9.0.98-qrcodes.zip`。

服务器时钟比部署电脑快约 8 小时 9 分钟，未修改系统时间。临时证书有效期已覆盖两端当前时间；服务器新建记录和日志时间可能偏移。

首次构建遇到依赖下载缓慢，通过仅针对构建的现有代理完成，没有修改系统代理。必要时可用：

```bash
docker build --network host \
  --build-arg HTTPS_PROXY=http://127.0.0.1:7890 \
  --build-arg HTTP_PROXY=http://127.0.0.1:7890 \
  -t pdf-library-app -t pdf-library-public .
docker compose up -d --no-build --wait
```

## 2026-10-16 双协议查询入口

查询端口 18888 已同时支持 HTTP 和 HTTPS，HTTP 直接返回查询页，不强制跳转。Nginx stream 根据 TLS 握手分流到回环 28887（HTTP）和 28888（HTTPS），使用 PROXY protocol 保留客户端地址供限流和日志使用。管理入口 18889 的 HTTPS 与 IP 白名单保持原配置。实测两种协议下查询和 PDF 均为 200，管理路径为 404；入口机侧访问域名 9996 两种协议均为 200。HTTPS 仍为自签名证书，独立公网可达性与可信证书限制见 PUBLIC-9996-DIAGNOSIS.md。

## 2026-10-16 公共二维码下载

服务器与本机同步开放单企业二维码预览、PNG 和企业标签下载，图片内容与各自管理端一致；链接和数据不变。app/public 重建并健康，gateway 配置无需修改。更新前数据备份及 source-before.tar 位于 backups/pre-public-qr-20261016/，旧镜像标记为 before-public-qr-20261016。

从部署电脑实际浏览公共 HTTP 页面并下载解码通过；服务器自身完成 HTTPS、管理员登录、公开/管理图片一致性与 56 个企业详情核验。部署电脑当前出口 10.5.51.206 未在原管理 IP 白名单中，直接访问 18889 返回 403；本次未调整白名单。

可在服务器应用目录执行只读核验（证书及既有凭据保留在服务器，不输出密码）：

```bash
mkdir -p test-results
docker run --rm --network host --read-only --user "$(id -u):$(id -g)" \
  -v "$PWD:/app:ro" -v "$PWD/test-results:/app/test-results" \
  -w /app pdf-library-app python -u deploy/verify_server.py
```

## 2026-10-16 二维码域名入口

按用户确认，本机与服务器 BASE_URL 现为 `http://www.handan.tech:9996`。公开页、管理页的二维码及新导出包均编码该入口，企业固定编号、报告绑定和现有 PDF 不变。已重新导出全部 56 家二维码，备份位于 backups/pre-public-url-20261016/。管理入口仍使用原内网 HTTPS 地址及白名单。

域名 HTTP 入口从部署电脑验证可访问；HTTPS 尚为不匹配该域名的自签名证书，本次未修改证书、网关、转发或管理权限。未来启用 HTTPS 时仍应保留 HTTP 旧入口并转发到同一企业页面，确保本次二维码可继续使用。

## 2026-10-16 管理员配置二维码前缀

管理列表新增“二维码设置”入口 `/admin/settings`，可保存全库二维码访问前缀。当前值为 http://www.handan.tech:9996，已分别在本机和服务器通过管理网页保存。数据库 `settings.qr_base_url` 优先于环境 BASE_URL；公共和管理进程按请求读取，修改无需重启，备份数据库时一同保留。旧图片、既有 ZIP 和已张贴二维码不会自动更改，页面明确提示旧入口的保留要求；正式打印绑定继续受保护。

app/public 均已重建且健康；28 项测试通过。配置页仅限管理员登录，保存校验 CSRF、根地址格式、旧码确认及并发变更。公开端配置页/写入返回 404。更新前备份及源码快照在 backups/pre-admin-qr-settings-20261016/，旧镜像标签为 before-admin-qr-settings-20261016。当前部署电脑直接访问服务器管理端仍受原 IP 白名单限制，服务器管理网页核验从服务器自身完成。

## 2026-10-16 新版 11 份报告

用户提供 文件资料/11种/ 的 11 份新版报告已按封面全称重命名并同步替换到本机与服务器，各自固定编号不变。用户确认龙凤山 0008 更新展示名称为“河北龙凤山新材料科技集团有限公司”；钢诺 0007、苏州立讯 0010 继续保留待核对状态，不公开或改绑其他企业。

替换前完整备份、原导出包、显式编号清单和逐项核验结果位于 backups/pre-replace-11-20261016/。两端 audit 与实际下载哈希验证通过；当前存储中仅保留 11 份新版 PDF。全部 56 张已导出企业二维码 PNG 与替换前逐字节一致，标签与 CSV 已更新。数据变更即时生效，无需重建容器。维护核验脚本现读取当前数据库快照，避免将旧迁移备份中的文件哈希误当成最新版。

## 2026-10-20 补充 36 份报告

本机与服务器均已在现有永久编号中补齐 36 份报告，11 组名称差异经用户确认后同步修改展示名称；目录顺序及二维码地址不变。原有 11 份报告哈希一致、跳过重复导入。当前主目录 56 家中 45 家已有报告、11 家待上传，另两条钢诺/苏州立讯记录保留待核对，共 58 条记录、47 份 PDF。

两端更新前完整备份、输入清单和结果位于 backups/pre-supplement-20261020/，更新后备份位于 backups/post-supplement-20261020/。全部 56 张方形二维码 PNG 与更新前逐字节一致；新企业全称标签、报告哈希与导出 CSV 已更新。逐项对照表在 output/资料补充核对表-20261020.csv。两端 audit、全部新增文件的实际下载哈希、服务器 HTTPS 核验通过。数据更新即时生效，本次未重建应用容器或修改管理白名单。

当前部署网络访问域名 www.handan.tech:9996 超时，内网应用及 .90 转发入口均可返回 200，转发容器运行中；不据此宣称独立公网可用。已保留原二维码域名前缀，未擅自替换成内网 IP。
