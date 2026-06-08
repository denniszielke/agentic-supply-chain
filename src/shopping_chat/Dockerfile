FROM python:3.12-slim
WORKDIR /app
COPY ./src/mcp_app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY ./src /app/src
EXPOSE 8080
CMD ["uvicorn", "src.mcp_app.app:app", "--host", "0.0.0.0", "--port", "8080"]
