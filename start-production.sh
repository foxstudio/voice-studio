#!/usr/bin/env bash
# Build the WebUI and serve the complete local application from one process.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${VOICE_STUDIO_BACKEND_PORT:-8000}"
BACKEND_HOST="${VOICE_STUDIO_BACKEND_HOST:-127.0.0.1}"

if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  BOOTSTRAP_PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  BOOTSTRAP_PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  BOOTSTRAP_PYTHON="python"
else
  printf '未找到 Python 3.10+，无法执行启动前检查。\n' >&2
  exit 1
fi

"$BOOTSTRAP_PYTHON" "$PROJECT_ROOT/scripts/voice_studio_doctor.py" \
  --launcher posix --strict

printf '构建 Voice Studio WebUI...\n'
pnpm --dir "$PROJECT_ROOT/frontend" build

printf '启动 Voice Studio: http://%s:%s\n' "$BACKEND_HOST" "$BACKEND_PORT"
cd "$PROJECT_ROOT/backend"
VOICE_STUDIO_SERVE_FRONTEND=1 \
VOICE_STUDIO_FRONTEND_DIST="$PROJECT_ROOT/frontend/build" \
VOICE_STUDIO_VIDEO_LOCALIZATION_WORKER_MODE=embedded \
  exec uv run --extra server uvicorn app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
