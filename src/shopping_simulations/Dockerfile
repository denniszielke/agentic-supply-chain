FROM python:3.13-slim
WORKDIR /app
COPY ./src/shopping_simulations/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY ./src /app/src
ENV PORT=8080
ENV HOST=0.0.0.0
EXPOSE 8080
CMD ["python", "-m", "src.shopping_simulations.server"]
