FROM python:3.12-slim
WORKDIR /app
COPY ./src/agent/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY ./src /app/src
EXPOSE 8090
CMD ["uvicorn", "src.agent.a2a_api:app", "--host", "0.0.0.0", "--port", "8090"]
