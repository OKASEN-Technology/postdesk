# The app: API, web UI, scheduler loop. Two stages: Node builds the web
# app into the package's static directory, then a slim Python image
# installs the package (with the S3 extra) and runs it.

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY --from=web /src/postdesk/static/ ./src/postdesk/static/
RUN pip install ".[s3]"
# The examples, so `postdesk init` can start from them inside the container.
COPY .env.example examples/ ./examples/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /data
VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
CMD ["postdesk", "serve"]
