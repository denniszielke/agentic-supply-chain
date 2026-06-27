FROM python:3.13-slim
WORKDIR /app
COPY ./src/joule_agent/requirements.txt /tmp/requirements.txt
RUN if [ -f /tmp/requirements.txt ]; then \
        pip install --no-cache-dir -r /tmp/requirements.txt; \
    else \
        echo "No requirements.txt found"; \
    fi
COPY ./src /app/src
ENV JOULE_AGENT_HOST=0.0.0.0
ENV JOULE_AGENT_PORT=8092
EXPOSE 8092
CMD ["python", "-m", "src.joule_agent.server"]
