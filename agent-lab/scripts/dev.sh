#!/usr/bin/env bash
# AgentLab yerel geliştirme yardımcısı: setup | test | serve | demo | claude "<görev>" | proof
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
VENV="$HERE/.venv"
PY="$VENV/bin/python"
export PYTHONPATH="$HERE/services"
PORT="${AGENTLAB_PORT:-8799}"

os() { case "$(uname -s)" in Linux*) echo linux;; Darwin*) echo mac;; MINGW*|MSYS*|CYGWIN*) echo win;; *) echo other;; esac; }

# Linux'ta gerçek DISPLAY yoksa Xvfb altında çalıştır; mac/win'de doğrudan (gerçek masaüstü!).
with_display() {
  if [ "$(os)" = linux ] && [ -z "${DISPLAY:-}" ]; then
    command -v xvfb-run >/dev/null || { echo "xvfb-run yok: sudo apt install xvfb"; exit 1; }
    xvfb-run -a -s "-screen 0 1280x800x24" "$@"
  else
    "$@"
  fi
}

case "${1:-help}" in
  setup)
    [ -x "$PY" ] || python3 -m venv "$VENV"
    "$PY" -m pip install -q --upgrade pip
    "$PY" -m pip install -q fastapi "uvicorn[standard]" sse-starlette mss pynput pillow "anthropic>=0.80" jsonschema pytest httpx
    echo "ok: $VENV hazır. $(os) / $("$PY" --version)"
    [ "$(os)" = linux ] && { command -v xvfb-run >/dev/null && echo "ok: xvfb-run var" || echo "uyarı: xvfb-run yok (sudo apt install xvfb) — gerçek DISPLAY ile de çalışır"; }
    [ "$(os)" = mac ] && echo "mac notu: Sistem Ayarları → Gizlilik → Ekran Kaydı + Erişilebilirlik izinlerini terminal uygulamana ver."
    ;;
  test)
    with_display "$PY" -m pytest -q "${@:2}"
    ;;
  serve)
    echo "→ http://127.0.0.1:$PORT   (Ctrl+C ile durdur)"
    with_display "$PY" -m agentlab.gateway --port "$PORT" --store-dir "$HERE/sessions" "${@:2}"
    ;;
  demo)
    post() { curl -s -X POST "http://127.0.0.1:$PORT/sessions/new/commands" -H 'content-type: application/json' -d "$1"; echo; }
    post '{"cmd":"start","task":"loop","driver":"scripted","agent":"goat","pace":0.35}'
    post '{"cmd":"start","task":"git-push","driver":"scripted","agent":"pengu","pace":0.5}'
    echo "UI'da PENGU 'git push' için onay bekleyecek → ONAYLA'ya bas."
    ;;
  claude)
    : "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY gerekli}"
    shift
    echo "UYARI: bu sürücü DISPLAY'deki masaüstünü gerçekten kontrol eder. İzole VM/kullanıcı oturumu önerilir."
    with_display "$PY" -m agentlab.cli run --driver claude --with-display --task "$*"
    ;;
  proof)
    NODE_PATH="$(npm root -g)" node scripts/proof.mjs "http://127.0.0.1:$PORT" proof
    ;;
  *)
    sed -n 2p "$0"; exit 1;;
esac
