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
chrome_instance_path = ""
wss_url = ""
cdp_url = ""

[search]
engine = "DuckDuckGo"
fallback_engines = ["Bing", "Google"]
retry_delay = 60
max_retries = 3
lang = "pt"
country = "br"

[sandbox]
use_sandbox = false
image = "python:3.12-slim"
work_dir = "/workspace"
memory_limit = "1g"
cpu_limit = 2.0
timeout = 300
network_enabled = true

[daytona]
daytona_api_key = "${DAYTONA_API_KEY:-not-used}"

[sandbox.daytona]
daytona_api_key = "${DAYTONA_API_KEY:-not-used}"

[mcp]
server_reference = "app.mcp.server"

[runflow]
use_data_analysis_agent = false
EOF

echo "Config gerado em /app/OpenManus/config/config.toml"

echo "Iniciando Xvfb..."
Xvfb :99 -screen 0 1365x768x24 -ac +extension GLX +render -noreset &

sleep 2

echo "Iniciando Fluxbox..."
fluxbox &

sleep 2

echo "Iniciando x11vnc..."
x11vnc -display :99 -forever -shared -nopw -listen 127.0.0.1 -xkb &

sleep 2

cd /app/OpenManus

. .venv/bin/activate

echo "OpenManusWeb iniciado."
echo "Interface pública: http://IP_DA_VPS:8001"
echo "Preview interno: /vnc/vnc.html?autoconnect=true&resize=scale&path=vnc/websockify"

exec uvicorn web_server:app --host 0.0.0.0 --port 8000