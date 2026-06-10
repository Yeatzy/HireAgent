#!/bin/zsh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

existing_pid=$(/usr/sbin/lsof -tiTCP:8010 -sTCP:LISTEN 2>/dev/null)
if [[ -n "$existing_pid" ]]; then
  echo "正在重启 HireAgent..."
  kill "$existing_pid" 2>/dev/null
  sleep 1
fi

exec python3 run.py
