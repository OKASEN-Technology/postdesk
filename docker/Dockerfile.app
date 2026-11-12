# Build the web UI and renderer, then run them with the Python app in one image.

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM node:22-slim AS renderer
WORKDIR /renderer
COPY renderer/package.json renderer/package-lock.json ./
RUN npm ci
COPY renderer/ ./
RUN npm run build && npm prune --omit=dev

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
ENV POSTDESK_BUNDLED_RENDERER=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY --from=web /src/postdesk/static/ ./src/postdesk/static/
COPY --from=renderer /usr/local/bin/node /usr/local/bin/node
COPY --from=renderer /renderer /renderer
RUN pip install ".[s3]"
# The examples, so `postdesk init` can start from them inside the container.
COPY .env.example examples/ ./examples/
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/supervise.py /supervise.py
RUN chmod +x /entrypoint.sh && mkdir -p /data
VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
CMD ["postdesk", "serve"]
