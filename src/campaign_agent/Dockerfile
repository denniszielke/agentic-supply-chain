FROM python:3.13-slim
WORKDIR /app
COPY ./src/campaign_agent/requirements.txt /tmp/requirements.txt
RUN if [ -f /tmp/requirements.txt ]; then \
        pip install --no-cache-dir -r /tmp/requirements.txt; \
    else \
        echo "No requirements.txt found"; \
    fi
COPY ./src /app/src
CMD ["python", "-m", "src.campaign_agent.agent"]
