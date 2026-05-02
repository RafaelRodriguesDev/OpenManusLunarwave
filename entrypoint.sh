#!/bin/bash
set -e

mkdir -p /app/OpenManus/config
mkdir -p /workspace
mkdir -p /root/.vnc

if [ -z "$LLM_API_KEY" ]; then
  echo "Erro: variável LLM_API_KEY não foi definida."
  echo "Configure LLM_API_KEY no Portainer em Environment variables."
  exit 1
fi

cat > /app/OpenManus/config/config.toml <<EOF
[llm]
model = "${LLM_MODEL:-deepseek-chat}"
base_url = "${LLM_BASE_URL:-https://api.deepseek.com}"
api_key = "${LLM_API_KEY}"
max_tokens = ${LLM_MAX_TOKENS:-8192}
temperature = ${LLM_TEMPERATURE:-0.0}

[llm.vision]
model = "${VISION_MODEL:-deepseek-chat}"
base_url = "${VISION_BASE_URL:-https://api.deepseek.com}"
api_key = "${VISION_API_KEY:-${LLM_API_KEY}}"
max_tokens = ${VISION_MAX_TOKENS:-8192}
temperature = ${VISION_TEMPERATURE:-0.0}

[browser]
headless = false
disable_security = true
extra_chromium_args = [
  "--no-sandbox",
  "--disable-dev-shm-usage",
  "--disable-gpu",
  "--window-size=1365,768"
]

[mcp]
server_reference = "app.mcp.server"

[runflow]
use_data_analysis_agent = false
EOF

echo "Iniciando Xvfb..."
Xvfb :99 -screen 0 1365x768x24 -ac +extension GLX +render -noreset &

sleep 2

echo "Iniciando Fluxbox..."
fluxbox &

sleep 2

echo "Iniciando x11vnc..."
x11vnc -display :99 -forever -shared -nopw -listen 0.0.0.0 -xkb &

sleep 2

echo "Iniciando noVNC..."
websockify --web=/usr/share/novnc/ 0.0.0.0:6080 localhost:5900 &

cd /app/OpenManus

. .venv/bin/activate

echo "OpenManusWeb iniciado."
echo "Interface: http://IP_DA_VPS:8000"
echo "noVNC: http://IP_DA_VPS:6080/vnc.html"

exec uvicorn web_server:app --host 0.0.0.0 --port 8000