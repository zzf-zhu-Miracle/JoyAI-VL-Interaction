#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$INSTALL_DIR/.." && pwd)"
SERVICES_DIR="$REPO_ROOT/services"
WEBUI_DIR="$SERVICES_DIR/webui"
BACKGROUND_AGENT_DIR="$SERVICES_DIR/background-agent"
VENV_DIR="${VENV_DIR:-$SERVICES_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
UV_BIN="${UV_BIN:-uv}"
MAX_SUBAGENTS="6"
INSTALL_BACKGROUND_AGENT=0
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage: install.sh [options]

安装 JoyVL WebUI（推理走阿里云百炼 Qwen-Omni-Realtime 云端 API，本地不再安装
vLLM / PyTorch / 模型权重 / 音频运行时）。

Options:
  --with-background-agent    安装后台 agent API 服务包。
  --with-all                 启用以上全部可选包。
  --max-subagents N          配置后台 agent 最大子代理数，默认 6。
  --dry-run                  只打印将要执行的命令，不真正安装。
  -h, --help                 显示帮助。

Environment overrides:
  VENV_DIR=/path/to/venv
  PYTHON_BIN=python3.12
  UV_BIN=/path/to/uv

运行 WebUI 前需要配置百炼 Omni realtime 环境变量:
  DASHSCOPE_API_KEY=sk-...        # 必填
  OMNI_REALTIME_URL=wss://...     # 可选，有默认值
  OMNI_MODEL=qwen3.5-omni-flash-realtime  # 可选，有默认值
USAGE
}

die() {
  echo "错误: $*" >&2
  exit 1
}

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

require_dir() {
  [ -d "$1" ] || die "目录不存在: $1"
}

require_file() {
  [ -f "$1" ] || die "文件不存在: $1"
}

copy_template() {
  local source="$1"
  local target="$2"
  require_file "$source"
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '+ cp %q %q\n' "$source" "$target"
  else
    cp "$source" "$target"
  fi
}

pip_install_editable() {
  local package_dir="$1"
  local extra="${2:-}"
  if [ -n "$extra" ]; then
    uv_pip_install -e "$package_dir[$extra]"
  else
    uv_pip_install -e "$package_dir"
  fi
}

uv_pip_install() {
  run "$UV_BIN" pip install --python "$VENV_DIR/bin/python" "$@"
}

write_background_agent_runtime() {
  local env_file="$BACKGROUND_AGENT_DIR/background-agent.env"

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "+ write $env_file"
    return
  fi

  cat >"$env_file" <<EOF
CODEX_API_MAX_SUBAGENTS=$MAX_SUBAGENTS
BACKGROUND_MAX_SUBAGENTS=$MAX_SUBAGENTS
BACKGROUND_AGENT_API_URL=http://127.0.0.1:8079
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --with-background-agent)
      INSTALL_BACKGROUND_AGENT=1
      ;;
    --with-all)
      INSTALL_BACKGROUND_AGENT=1
      ;;
    --max-subagents)
      shift
      [ "${1:-}" ] || die "--max-subagents requires a value"
      [[ "$1" =~ ^[0-9]+$ ]] || die "--max-subagents 必须是正整数"
      [ "$1" -ge 1 ] || die "--max-subagents 必须 >= 1"
      MAX_SUBAGENTS="$1"
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "未知选项: $1"
      ;;
  esac
  shift
done

if [ "$DRY_RUN" -eq 0 ] && ! command -v "$UV_BIN" >/dev/null 2>&1; then
  die "找不到 uv: $UV_BIN。请先安装 uv，或通过 UV_BIN=/path/to/uv 指定路径"
fi

require_dir "$WEBUI_DIR"
copy_template "$INSTALL_DIR/pyproject.toml" "$WEBUI_DIR/pyproject.toml"

if [ "$INSTALL_BACKGROUND_AGENT" -eq 1 ]; then
  require_dir "$BACKGROUND_AGENT_DIR"
  copy_template "$INSTALL_DIR/pyproject.background-agent.toml" "$BACKGROUND_AGENT_DIR/pyproject.toml"
fi

run "$UV_BIN" venv --python "$PYTHON_BIN" --seed "$VENV_DIR"
pip_install_editable "$WEBUI_DIR"

if [ "$INSTALL_BACKGROUND_AGENT" -eq 1 ]; then
  pip_install_editable "$BACKGROUND_AGENT_DIR"
  write_background_agent_runtime
fi

echo "安装完成。"
echo "激活环境: source $VENV_DIR/bin/activate"
echo "启动前请设置: export DASHSCOPE_API_KEY=sk-..."
if [ "$INSTALL_BACKGROUND_AGENT" -eq 1 ]; then
  echo "后台 agent 环境配置: $BACKGROUND_AGENT_DIR/background-agent.env"
  echo "后台 agent 启动脚本: $BACKGROUND_AGENT_DIR/scripts/run.sh"
fi
