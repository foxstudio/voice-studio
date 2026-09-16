#!/usr/bin/env bash
#
# start.sh — Voice Studio 一键启动脚本
#
# Usage:
#   ./start.sh          启动（如果服务健康则跳过）
#   ./start.sh --force  强制重启
#   ./start.sh --dev-reload  API 热更新；本土化 worker 独立稳定运行
#   ./start.sh --doctor      只执行环境检查
#   ./start.sh --help   显示帮助
#
# 启动后端 (uvicorn :8000) 和前端 (vite :5173)，
# 等待健康检查通过后打印进程信息和访问地址。
#
set -u
# 不使用 set -e，手动处理错误

BACKEND_PORT="${VOICE_STUDIO_BACKEND_PORT:-8000}"
FRONTEND_PORT="${VOICE_STUDIO_FRONTEND_PORT:-5173}"
BACKEND_DIR="${VOICE_STUDIO_BACKEND_DIR:-backend}"
FRONTEND_DIR="${VOICE_STUDIO_FRONTEND_DIR:-frontend}"
VOICE_STUDIO_DATA_ROOT="${VOICE_STUDIO_DATA_DIR:-${HOME}/VoiceStudio}"
VOICE_STUDIO_LOG_ROOT="${VOICE_STUDIO_LOGS_DIR:-${VOICE_STUDIO_DATA_ROOT}/logs}"
BACKEND_LOG="${VOICE_STUDIO_BACKEND_LOG:-${VOICE_STUDIO_LOG_ROOT}/backend.log}"
FRONTEND_LOG="${VOICE_STUDIO_FRONTEND_LOG:-${VOICE_STUDIO_LOG_ROOT}/frontend.log}"
VIDEO_LOCALIZATION_WORKER_LOG="${VOICE_STUDIO_VIDEO_LOCALIZATION_WORKER_LOG:-${VOICE_STUDIO_LOG_ROOT}/video-localization-worker.log}"
VIDEO_LOCALIZATION_WORKER_PID_FILE="${VOICE_STUDIO_VIDEO_LOCALIZATION_WORKER_PID_FILE:-${VOICE_STUDIO_LOG_ROOT}/video-localization-worker.pid}"
BACKEND_HEALTH_URL="http://localhost:${BACKEND_PORT}/api/health"
FRONTEND_HEALTH_URL="http://localhost:${FRONTEND_PORT}/"
BACKEND_STARTUP_TIMEOUT="${VOICE_STUDIO_BACKEND_STARTUP_TIMEOUT:-120}"
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
FORCE="${FORCE:-false}"
DEV_RELOAD="${DEV_RELOAD:-false}"
DOCTOR_ONLY="false"
BACKEND_PID=""
FRONTEND_PID=""
VIDEO_LOCALIZATION_WORKER_PID=""

mkdir -p "$(dirname "$BACKEND_LOG")" "$(dirname "$FRONTEND_LOG")"

# ── helpers ──────────────────────────────────────────────────────────

usage() {
  sed -n '3,11p' "$0"
  exit 0
}

log()  { printf "\e[32m[%s]\e[0m %s\n" "$(date '+%H:%M:%S')" "$*"; }
warn() { printf "\e[33m[%s]\e[0m %s\n" "$(date '+%H:%M:%S')" "$*"; }
err()  { printf "\e[31m[%s]\e[0m %s\n" "$(date '+%H:%M:%S')" "$*" >&2; }

# 等待 URL 返回 200（最多 timeout 秒）
wait_for_url() {
  local url="$1"
  local label="$2"
  local timeout="${3:-30}"
  local elapsed=0
  while [ "$elapsed" -lt "$timeout" ]; do
    if curl -sf "$url" >/dev/null 2>&1; then
      log "$label — up ($((elapsed+1))s)"
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  warn "$label — 未在 ${timeout}s 内响应"
  return 1
}

# 查找真正监听端口的 PID（空格分隔）。
# 不能使用 `lsof -ti :PORT`，否则浏览器等客户端建立到该端口的连接时，
# 也会被误判为端口占用者。
port_pids() {
  lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

# 检查进程是否属于 voice-studio（避免误杀）
is_voice_studio_process() {
  local pid="$1"
  local cmd
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  echo "$cmd" | grep -qE 'uvicorn.*app\.main:app|vite' 2>/dev/null
}

# 获取进程命令字符串
process_command() {
  ps -p "$1" -o command= 2>/dev/null || echo ""
}

stop_external_video_localization_worker() {
  if [ ! -f "$VIDEO_LOCALIZATION_WORKER_PID_FILE" ]; then
    return 0
  fi
  local pid
  pid="$(sed -n '1p' "$VIDEO_LOCALIZATION_WORKER_PID_FILE" 2>/dev/null || true)"
  if [ -n "$pid" ]; then
    local cmd
    cmd="$(process_command "$pid")"
    if echo "$cmd" | grep -qE 'python.*run_video_localization_worker\.py' 2>/dev/null; then
      log "停止独立视频本土化 worker (PID $pid)"
      kill "$pid" 2>/dev/null || true
      local waited=0
      while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 5 ]; do
        sleep 1
        waited=$((waited + 1))
      done
      if kill -0 "$pid" 2>/dev/null; then
        warn "独立 worker 未及时退出，强制终止 PID $pid"
        kill -9 "$pid" 2>/dev/null || true
      fi
    fi
  fi
  rm -f "$VIDEO_LOCALIZATION_WORKER_PID_FILE"
}

# 过滤出端口上 voice-studio 的 PID（空格分隔）
voice_studio_pids_on_port() {
  local port="$1"
  local pids pid cmd result
  result=""
  pids="$(port_pids "$port")"
  [ -z "$pids" ] && return 0
  for pid in $pids; do
    [ -z "$pid" ] && continue
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if echo "$cmd" | grep -qE 'uvicorn.*app\.main:app|vite' 2>/dev/null; then
      [ -n "$result" ] && result="$result "
      result="$result$pid"
    fi
  done
  echo "$result"
}

# ── flags ────────────────────────────────────────────────────────────

for arg in "$@"; do
  case "$arg" in
    --help|-h) usage ;;
    --force|-f) FORCE="true" ;;
    --dev-reload)
      DEV_RELOAD="true"
      FORCE="true"
      ;;
    --doctor) DOCTOR_ONLY="true" ;;
  esac
done

if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  BOOTSTRAP_PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  BOOTSTRAP_PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  BOOTSTRAP_PYTHON="python"
else
  err "未找到 Python 3.10+，无法执行启动前检查"
  exit 1
fi

"$BOOTSTRAP_PYTHON" "$PROJECT_ROOT/scripts/voice_studio_doctor.py" --launcher posix --strict
DOCTOR_STATUS=$?
if [ "$DOCTOR_ONLY" = "true" ] || [ "$DOCTOR_STATUS" -ne 0 ]; then
  exit "$DOCTOR_STATUS"
fi

# ── 检查并终止进程 ─────────────────────────────────────────────────

cleanup_port() {
  local port="$1"
  local label="$2"
  local vs_pids
  vs_pids="$(voice_studio_pids_on_port "$port")"

  if [ -z "$vs_pids" ]; then
    local all_pids
    all_pids="$(port_pids "$port")"
    if [ -n "$all_pids" ]; then
      local first_pid
      first_pid="$(echo "$all_pids" | awk '{print $1}')"
      local other_cmd
      other_cmd="$(process_command "$first_pid")"
      err "端口 $port 被非 voice-studio 进程占用: $other_cmd"
      err "请手动处理后再试"
      exit 1
    fi
    log "端口 $port — 空闲"
    return 0
  fi

  for pid in $vs_pids; do
    [ -z "$pid" ] && continue
    log "端口 $port — 停止已有服务 (PID $pid)"
    kill "$pid" 2>/dev/null || true
  done

  local waited=0
  while [ "$waited" -lt 3 ]; do
    local remaining
    remaining="$(voice_studio_pids_on_port "$port")"
    [ -z "$remaining" ] && break
    sleep 1
    waited=$((waited + 1))
  done

  local remaining
  remaining="$(voice_studio_pids_on_port "$port")"
  if [ -n "$remaining" ]; then
    warn "部分进程未响应 SIGTERM，使用 SIGKILL"
    for pid in $remaining; do
      [ -z "$pid" ] && continue
      warn "强制终止 PID $pid"
      kill -9 "$pid" 2>/dev/null || true
    done
    sleep 1
  fi

  log "端口 $port — 已释放"
}

# ── 健康检查（幂等） ────────────────────────────────────────────────

check_healthy() {
  local port="$1"
  local url="$2"

  local vs_pids
  vs_pids="$(voice_studio_pids_on_port "$port")"
  [ -z "$vs_pids" ] && return 1

  curl -sf "$url" >/dev/null 2>&1 || return 1

  return 0
}

backend_runtime_ready() {
  local payload
  payload="$(curl -sf "$BACKEND_HEALTH_URL" 2>/dev/null || true)"
  echo "$payload" | grep -q '"runtime_ready":true'
}

# ── 主逻辑 ──────────────────────────────────────────────────────────

cd "$PROJECT_ROOT"

# --- 幂等性检查 ---
if [ "$FORCE" != "true" ]; then
  BACKEND_HEALTHY=false
  FRONTEND_HEALTHY=false

  if check_healthy "$BACKEND_PORT" "$BACKEND_HEALTH_URL"; then
    BACKEND_HEALTHY=true
    BACKEND_PID="$(voice_studio_pids_on_port "$BACKEND_PORT" | awk '{print $1}')"
    if ! backend_runtime_ready; then
      warn "后端接口可访问，但部分可选媒体能力不可用；基础功能继续运行"
    fi
  fi
  if check_healthy "$FRONTEND_PORT" "$FRONTEND_HEALTH_URL"; then
    FRONTEND_HEALTHY=true
    FRONTEND_PID="$(voice_studio_pids_on_port "$FRONTEND_PORT" | awk '{print $1}')"
  fi

  if [ "$BACKEND_HEALTHY" = true ] && [ "$FRONTEND_HEALTHY" = true ]; then
    echo ""
    log "============================================"
    log " Voice Studio 已在运行 — 无需重启"
    log "============================================"
    echo ""
    log "后端 (PID $BACKEND_PID):  http://localhost:${BACKEND_PORT}"
    log "前端 (PID $FRONTEND_PID): http://localhost:${FRONTEND_PORT}"
    log "后端日志: ${BACKEND_LOG}"
    log "前端日志: ${FRONTEND_LOG}"
    echo ""
    log "提示: 使用 --force 强制重启"
    echo ""
    exit 0
  elif [ "$BACKEND_HEALTHY" = true ] || [ "$FRONTEND_HEALTHY" = true ]; then
    warn "部分服务健康 — 按需重启..."
  fi
fi

# --- 端口清理 ---
log "检查端口冲突..."
stop_external_video_localization_worker
cleanup_port "$BACKEND_PORT" "后端 ($BACKEND_PORT)"
cleanup_port "$FRONTEND_PORT" "前端 ($FRONTEND_PORT)"
echo ""

# ── 启动后端 ────────────────────────────────────────────────────────

log "启动后端..."
cd "$PROJECT_ROOT/$BACKEND_DIR"
if [ "$DEV_RELOAD" = "true" ]; then
  log "启动独立视频本土化 worker（不热重载）..."
  uv run --extra server python run_video_localization_worker.py \
    > "$VIDEO_LOCALIZATION_WORKER_LOG" 2>&1 &
  VIDEO_LOCALIZATION_WORKER_PID=$!
  printf '%s\n' "$VIDEO_LOCALIZATION_WORKER_PID" \
    > "$VIDEO_LOCALIZATION_WORKER_PID_FILE"
  disown
  sleep 1
  if ! kill -0 "$VIDEO_LOCALIZATION_WORKER_PID" 2>/dev/null; then
    err "独立视频本土化 worker 启动失败，查看日志: $VIDEO_LOCALIZATION_WORKER_LOG"
    exit 1
  fi
  log "独立视频本土化 worker 已启动 (PID $VIDEO_LOCALIZATION_WORKER_PID)"
  VOICE_STUDIO_VIDEO_LOCALIZATION_WORKER_MODE=external \
    uv run --extra server uvicorn app.main:app --port "$BACKEND_PORT" --reload \
    > "$BACKEND_LOG" 2>&1 &
else
  VOICE_STUDIO_VIDEO_LOCALIZATION_WORKER_MODE=embedded \
    uv run --extra server uvicorn app.main:app --port "$BACKEND_PORT" \
    > "$BACKEND_LOG" 2>&1 &
fi
BACKEND_PID=$!
disown
log "后端启动中... (PID $BACKEND_PID)"

if ! wait_for_url "$BACKEND_HEALTH_URL" "后端健康检查" "$BACKEND_STARTUP_TIMEOUT"; then
  stop_external_video_localization_worker
  err "后端启动失败，查看日志: $BACKEND_LOG"
  exit 1
fi
if ! backend_runtime_ready; then
  warn "后端已启动，但部分可选媒体能力不可用；相关功能会在界面中提示"
fi

echo ""

# ── 启动前端 ────────────────────────────────────────────────────────

log "启动前端..."
cd "$PROJECT_ROOT/$FRONTEND_DIR"
VITE_API_TARGET="http://127.0.0.1:${BACKEND_PORT}" \
  pnpm dev --port "$FRONTEND_PORT" \
  > "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
disown
log "前端启动中... (PID $FRONTEND_PID)"

if ! wait_for_url "$FRONTEND_HEALTH_URL" "前端健康检查" 60; then
  err "前端启动失败，查看日志: $FRONTEND_LOG"
  exit 1
fi

echo ""

# ── 输出信息 ────────────────────────────────────────────────────────

log "============================================"
log " Voice Studio 启动完成！"
log "============================================"
echo ""
log "后端 (PID $BACKEND_PID):  http://localhost:${BACKEND_PORT}"
log "前端 (PID $FRONTEND_PID): http://localhost:${FRONTEND_PORT}"
if [ "$DEV_RELOAD" = "true" ]; then
  log "视频本土化 worker (PID $VIDEO_LOCALIZATION_WORKER_PID): 独立稳定进程"
fi
echo ""
log "后端日志: ${BACKEND_LOG}"
log "前端日志: ${FRONTEND_LOG}"
if [ "$DEV_RELOAD" = "true" ]; then
  log "视频本土化 worker 日志: ${VIDEO_LOCALIZATION_WORKER_LOG}"
fi
echo ""
log "提示: tail -f ${BACKEND_LOG}"
log "提示: tail -f ${FRONTEND_LOG}"
echo ""
