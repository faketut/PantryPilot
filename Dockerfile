# PantryPilot — Cloud Run image
# Builds a single container that runs the FastAPI app on $PORT (default 8080).
# The MongoDB MCP sidecar is launched in-container by scripts/start.sh.

FROM node:20-slim AS node-base

FROM python:3.13-slim
COPY --from=node-base /usr/local/bin/node /usr/local/bin/node
COPY --from=node-base /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates lsof procps \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml ./
COPY uv.lock* ./
RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

# Pre-install MongoDB MCP server globally so the runtime never pays the npx
# download cost on first call.
RUN npm install -g mongodb-mcp-server@latest

COPY . .

ENV PORT=8080
EXPOSE 8080

# Cloud Run sends SIGTERM; start.sh traps it and cleans up the MCP sidecar.
CMD ["bash", "scripts/start.sh"]
