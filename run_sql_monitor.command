#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

if command -v python3.12 >/dev/null 2>&1; then
  exec python3.12 sql_monitor.py
fi

echo "未找到 python3.12，请先安装 Python 3.12。"
echo "按回车键关闭窗口。"
read -r
