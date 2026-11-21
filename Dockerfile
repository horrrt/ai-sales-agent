# The sandbox MCP tool server for the Fleet agent. Build from the repo root (the server reads data/).
#   docker build -t sales-tools . && docker run -p 8080:8080 sales-tools
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY mcp/pyproject.toml mcp/uv.lock mcp/
RUN cd mcp && uv sync --frozen --no-dev --no-install-project
COPY mcp/ mcp/
COPY data/ data/
# The deployed sandbox runs on the fictional demo customer (Kvitto), so no TODO brief blocks outreach.
RUN cp data/customer_brief.demo.json data/customer_brief.json
ENV HOST=0.0.0.0 PORT=8080
EXPOSE 8080
WORKDIR /app/mcp
CMD ["uv", "run", "--frozen", "--no-dev", "python", "-m", "sales_tools.server"]
