# ── Mars Colonization Specialist — container image ───────────────────────────
# Two usage modes (set CMD at runtime or via docker-compose):
#
#   Dashboard:  python -m mars_agent.dashboard
#   MCP server: python -m mars_agent.mcp
#
# Build:  docker build -t mars-agent .
# Run:    docker run -p 8000:8000 mars-agent
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Security: run as non-root
RUN addgroup --system mars && adduser --system --ingroup mars mars

WORKDIR /app

# Copy everything pip needs to build and install the package
COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs
RUN pip install --no-cache-dir .

# Persistent data directory (mount a volume here in production)
RUN mkdir -p /data && chown mars:mars /data

USER mars

# Expose dashboard port
EXPOSE 8000

# Default: run the dashboard; override with `python -m mars_agent.mcp` for MCP
CMD ["python", "-m", "mars_agent.dashboard"]
