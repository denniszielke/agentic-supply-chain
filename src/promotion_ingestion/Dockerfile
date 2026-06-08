FROM python:3.13-slim
WORKDIR /app
COPY ./src/promotion_ingestion/requirements.txt /tmp/requirements.txt
RUN if [ -f /tmp/requirements.txt ]; then \
        pip install --no-cache-dir -r /tmp/requirements.txt; \
    else \
        echo "No requirements.txt found"; \
    fi
COPY ./src /app/src
ENTRYPOINT ["python", "-m", "src.promotion_ingestion.job"]
