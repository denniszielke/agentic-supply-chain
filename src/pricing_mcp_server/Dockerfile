FROM python:3.13-slim
WORKDIR /app
COPY ./src/pricing_mcp_server/requirements.txt /tmp/requirements.txt
RUN if [ -f /tmp/requirements.txt ]; then \
        pip install --no-cache-dir -r /tmp/requirements.txt; \
    else \
        echo "No requirements.txt found"; \
    fi
COPY ./src /app/src
ENV PRICING_MCP_HOST=0.0.0.0
ENV PRICING_MCP_PORT=8091
EXPOSE 8091
CMD ["python", "-m", "src.pricing_mcp_server.server"]
