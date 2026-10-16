#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if ! command -v docker >/dev/null 2>&1; then
  echo '请先安装 Docker Desktop，再按 README.md 配置 .env 和数据目录。'
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  open -a Docker
  echo '正在等待 Docker Desktop 启动…'
  for attempt in {1..60}; do
    if docker info >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi
docker compose up -d --build --wait --wait-timeout 90
echo '企业画像报告已在 Docker 后台运行：http://127.0.0.1:18888/'
echo '内网管理台：http://127.0.0.1:18889/admin/'
echo '关闭此终端不会停止服务；停止请运行 docker compose stop。'
open 'http://127.0.0.1:18888/'
