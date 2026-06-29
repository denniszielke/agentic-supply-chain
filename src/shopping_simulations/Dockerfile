FROM python:3.13-slim
WORKDIR /app
COPY ./src/shopping_simulations/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY ./src /app/src
ENV PORT=8080
ENV HOST=0.0.0.0
# Pin a DevUI bearer token here, or leave empty to auto-generate one at startup
# (the token is printed to the console / Container Apps log stream).
ENV DEVUI_AUTH_TOKEN=""
EXPOSE 8080
CMD ["python", "-m", "src.shopping_simulations.server"]
