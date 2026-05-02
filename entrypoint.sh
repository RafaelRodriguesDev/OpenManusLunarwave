#!/bin/bash
set -e

mkdir -p /app/OpenManus/config

if [ -z "$LLM_API_KEY" ]; then
  echo "Erro: variável LLM_API_KEY não foi definida."
  echo "Configure LLM_API_KEY no Portainer em Environment variables."
  exit 1
fi

cat > /app/OpenManus/config/config.toml <<EOF
# Global LLM configuration
[llm]
model = "${LLM_MODEL:-deepseek-chat}"
base_url = "${LLM_BASE_URL:-https://api.deepseek.com}"
api_key = "${LLM_API_KEY}"
max_tokens = ${LLM_MAX_TOKENS:-8192}
temperature = ${LLM_TEMPERATURE:-0.0}

# Optional configuration for specific LLM models
[llm.vision]
model = "${VISION_MODEL:-deepseek-chat}"
base_url = "${VISION_BASE_URL:-https://api.deepseek.com}"
api_key = "${VISION_API_KEY:-${LLM_API_KEY}}"
max_tokens = ${VISION_MAX_TOKENS:-8192}
temperature = ${VISION_TEMPERATURE:-0.0}

# MCP configuration
[mcp]
server_reference = "app.mcp.server"

# Optional Runflow configuration
[runflow]
use_data_analysis_agent = false
EOF

cd /app/OpenManus

exec python main.py