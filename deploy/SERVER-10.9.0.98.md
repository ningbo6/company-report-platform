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
- 公开入口的管理页、健康信息、配置、备份、二维码生成和管理 POST 返回 404；带管理员 Cookie 仍保持只读。
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
