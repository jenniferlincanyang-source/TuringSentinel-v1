#!/bin/bash
# ============================================================
#  图灵哨兵 · RiskCopilot —— 一键启动完整 Demo（双击本文件即可）
# ------------------------------------------------------------
#  这个脚本干五件事：
#    1) 自检 Python 与依赖（缺什么给出确切安装命令，不擅自改你的环境）
#    2) 检查 LLM 凭据（有 → 深度研判/控辩/编排全功能；无 → 自动降级规则版，不崩）
#    3) 启动后端 backend.py（仅监听 127.0.0.1:8199，不开放外网）
#    4) 后端就绪后自动用浏览器打开前端页面
#    5) 可选预热缓存（演示/录屏前建议，之后点按钮秒出）
#
#  ⚠️ 本包是完整功能版：所有结果均由后端真实计算（真实年报 + 真实 LLM 推理），
#     没有内置任何"演示用假数据"。所以后端必须启动，这是设计如此。
#
#  关闭服务：本窗口按 Ctrl+C，或直接关掉窗口。
# ============================================================

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
PORT=8199

printf '\033[1;36m'
cat <<'BANNER'
  ┌──────────────────────────────────────────────────────┐
  │  图灵哨兵 · RiskCopilot                              │
  │  公开市场价值发现与财务舞弊研判的多 Agent 副驾       │
  │  机器发现错配，人判断机会还是陷阱                    │
  └──────────────────────────────────────────────────────┘
BANNER
printf '\033[0m\n'

# ── 1. Python 自检 ────────────────────────────────────────
PY=""
for c in python3 python3.13 python3.12 python3.11 python3.10 python3.9 /usr/bin/python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "❌ 没找到 python3。"
  echo "   macOS 安装：终端执行  xcode-select --install"
  echo ""
  read -r -p "按回车关闭…" _; exit 1
fi
VER=$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
echo "✅ Python $VER  [$PY]"

# ── 2. 必需文件自检 ───────────────────────────────────────
for f in demo/backend.py demo/RiskCopilot-demo.html signals/engine.py signals/signals_registry.py; do
  if [ ! -f "$ROOT/$f" ]; then
    echo "❌ 缺少 $f —— 请确认整个文件夹完整解压后再双击本文件。"
    read -r -p "按回车关闭…" _; exit 1
  fi
done
echo "✅ 代码完整（demo/ + signals/ 信号真相源 + law/ 法条库）"

# ── 3. 依赖自检（缺了给命令，不自动装）────────────────────
MISSING=$("$PY" - <<'PY'
need = ["fastapi", "uvicorn", "akshare", "pandas"]
missing = []
for m in need:
    try:
        __import__(m)
    except Exception:
        missing.append(m)
print(" ".join(missing))
PY
)
if [ -n "$MISSING" ]; then
  echo ""
  echo "❌ 缺少依赖：$MISSING"
  echo "   请复制下面这行到终端执行，装完再双击本文件："
  echo ""
  echo "      $PY -m pip install $MISSING"
  echo ""
  read -r -p "按回车关闭…" _; exit 1
fi
echo "✅ 依赖齐备（fastapi / uvicorn / akshare / pandas）"

# ── 4. LLM 凭据自检（决定深度研判走真实推理还是降级）──────
if [ -n "$ANTHROPIC_BASE_URL" ] && { [ -n "$ANTHROPIC_AUTH_TOKEN" ] || [ -n "$ANTHROPIC_API_KEY" ]; }; then
  echo "✅ LLM 凭据已配（深度研判 / 控辩互搏 / 总控编排 走真实推理）"
  HAS_LLM=1
else
  echo "⚠️  未配 LLM 凭据 —— 深度研判/控辩/编排将自动降级为规则版（功能仍可跑，推理文本较简）"
  echo "    要开全功能，先在终端 export 两个变量再双击本文件："
  echo "       export ANTHROPIC_BASE_URL=\"<你的兼容接口地址>\""
  echo "       export ANTHROPIC_AUTH_TOKEN=\"<你的密钥>\""
  echo "    （凭据只从环境变量读，代码里不硬编码，也不会被写进本包任何文件）"
  HAS_LLM=0
fi

# ── 5. 端口占用处理 ───────────────────────────────────────
if lsof -i :$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo ""
  echo "ℹ️  端口 $PORT 已被占用（可能是上次启动的后端还在跑）。"
  printf "   要结束旧进程并重新启动吗？[y/N] "
  read -r KILLOLD
  case "$KILLOLD" in
    y|Y|yes|YES)
      lsof -ti:$PORT | xargs kill -9 2>/dev/null
      sleep 1
      echo "   ✓ 旧进程已结束"
      ;;
    *)
      echo "   → 直接打开页面，用现有后端。"
      open "$ROOT/demo/RiskCopilot-demo.html"
      read -r -p "按回车关闭本窗口…" _; exit 0
      ;;
  esac
fi

# ── 6. 是否预热缓存 ───────────────────────────────────────
echo ""
echo "────────────────────────────────────────────────────────"
echo "是否预热缓存？"
echo "  深度研判 / 控辩互搏 / 价值发现 每次要真调 LLM 跑多段推理，"
echo "  首次点击约等 1-3 分钟。预热 = 提前把这些跑完存进内存缓存，"
echo "  正式演示时点按钮秒出（缓存的是真实算过的结果，不是假数据）。"
echo "  · 要演示 / 录屏 → 输入 y（约 2-3 分钟，请勿关窗）"
echo "  · 平时开发调试 → 直接回车跳过"
echo "────────────────────────────────────────────────────────"
printf "  预热请输入 y 回车 [默认 n]: "
read -r WARMUP

# ── 7. 后台：等后端就绪 → 开页面 →（可选）预热 ────────────
FRONT="$ROOT/demo/RiskCopilot-demo.html"
(
  for i in $(seq 1 80); do
    if curl -s -m 3 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then break; fi
    sleep 0.5
  done
  if [ -f "$FRONT" ]; then
    open "$FRONT"
    echo ""
    echo "✓ 已打开前端页面（页面右上角状态灯转绿 = 后端在线）"
  fi
  case "$WARMUP" in
    y|Y|yes|YES)
      echo "→ 预热中，请勿关窗…"
      curl -s -m 600 "http://127.0.0.1:$PORT/api/warmup?codes=600518,600519" >/dev/null 2>&1
      echo "  ✓ 深度研判 + 控辩互搏（康美 600518 / 茅台 600519）已预热"
      curl -s -m 600 "http://127.0.0.1:$PORT/api/value_scan?chain=%E5%85%89%E9%80%9A%E4%BF%A1" >/dev/null 2>&1
      echo "  ✓ 价值发现（光通信链）已预热"
      echo "  ▶ 现在点页面上的按钮全部秒出。"
      ;;
  esac
) &

# ── 8. 前台启动后端（窗口开着=服务在跑）───────────────────
echo ""
echo "🚀 启动后端 → http://127.0.0.1:$PORT"
echo "   安全边界：仅绑定 127.0.0.1，不监听外网，无身份认证；"
echo "             只读公开财经数据（东财/巨潮），不接任何私有或个人数据。"
echo ""
echo "────────────────────────────────────────────────────────"
echo "  想在命令行单独验证「哪些是确定性的、可复现的」，另开终端跑："
echo "     cd signals && $PY run.py            # 7 条财务信号跑康美真实年报"
echo "     cd law     && $PY penalty_calc.py   # 法条库追责测算（零幻觉·可溯源）"
echo "     cd demo    && $PY value_scan.py     # 价值发现四步闭环（端到端）"
echo "────────────────────────────────────────────────────────"
echo ""
echo "⏹  按 Ctrl+C 或关掉本窗口 = 停止后端"
echo ""

cd "$ROOT/demo" || exit 1
trap 'echo ""; echo "已停止后端。"; exit 0' INT TERM
exec "$PY" backend.py
