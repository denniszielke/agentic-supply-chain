FROM python:3.13-slim
WORKDIR /app
COPY ./src/shopping_harness/requirements.txt /tmp/requirements.txt
RUN if [ -f /tmp/requirements.txt ]; then \
        pip install --no-cache-dir -r /tmp/requirements.txt; \
    else \
        echo "No requirements.txt found"; \
    fi
COPY ./src /app/src
ENV PORT=8088
EXPOSE 8088
CMD ["python", "-m", "src.shopping_harness.agent"]
