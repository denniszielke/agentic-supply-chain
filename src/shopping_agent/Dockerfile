FROM python:3.13-slim
WORKDIR /app
COPY ./src/shopping_agent/requirements.txt /tmp/requirements.txt
RUN if [ -f /tmp/requirements.txt ]; then \
        pip install --no-cache-dir -r /tmp/requirements.txt; \
    else \
        echo "No requirements.txt found"; \
    fi
COPY ./src /app/src
EXPOSE 8090
CMD ["python", "-m", "src.shopping_agent.shopping_agent"]
