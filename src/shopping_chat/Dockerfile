FROM python:3.13-slim
WORKDIR /app
COPY ./src/shopping_chat/requirements.txt /tmp/requirements.txt
RUN if [ -f /tmp/requirements.txt ]; then \
        pip install --no-cache-dir -r /tmp/requirements.txt; \
    else \
        echo "No requirements.txt found"; \
    fi
COPY ./src /app/src
EXPOSE 8080
CMD ["uvicorn", "src.shopping_chat.app:app", "--host", "0.0.0.0", "--port", "8080"]
