#!/bin/bash
set -e

mkdir -p /app/OpenManus/config
mkdir -p /workspace

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

[mcp]
server_reference = "app.mcp.server"

[runflow]
use_data_analysis_agent = false
EOF

cd /app/OpenManus

. .venv/bin/activate

echo "OpenManusWeb iniciado."
echo "Acesse: http://IP_DA_VPS:8000"

exec uvicorn web_server:app --host 0.0.0.0 --port 8000