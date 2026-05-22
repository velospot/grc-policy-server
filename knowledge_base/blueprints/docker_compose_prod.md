# Docker Compose Production Blueprint

This is a local production blueprint. Pin all image tags and checksums in real deployment.

```yaml
services:
  reverse_proxy:
    image: local/nginx-grc:1.0.0
    ports:
      - "443:443"
    volumes:
      - ./certs:/etc/nginx/certs:ro
    depends_on:
      - frontend
      - api

  frontend:
    image: local/grc-frontend:1.0.0
    environment:
      NEXT_PUBLIC_API_BASE_URL: /api
    restart: unless-stopped

  api:
    image: local/grc-api:1.0.0
    environment:
      APP_ENV: prod
      DATABASE_URL_FILE: /run/secrets/database_url
      REDIS_URL: redis://redis:6379/0
      VECTOR_BACKEND: weaviate
      WEAVIATE_URL: http://weaviate:8080
      LLM_PROVIDER: llamacpp
      LLM_BASE_URL: http://llm:8080
      OBJECT_STORE_MODE: minio
      MINIO_ENDPOINT: http://minio:9000
      OFFLINE_MODE: "true"
      DISABLE_TELEMETRY: "true"
    secrets:
      - database_url
      - minio_access_key
      - minio_secret_key
    depends_on:
      - postgres
      - redis
      - weaviate
      - minio
      - llm
    restart: unless-stopped

  worker_extract:
    image: local/grc-api:1.0.0
    command: celery -A app.workers.tasks worker -Q extraction --loglevel=INFO --concurrency=2
    environment:
      APP_ENV: prod
      OFFLINE_MODE: "true"
    depends_on:
      - api
    restart: unless-stopped

  worker_compare:
    image: local/grc-api:1.0.0
    command: celery -A app.workers.tasks worker -Q compare --loglevel=INFO --concurrency=1
    environment:
      APP_ENV: prod
      OFFLINE_MODE: "true"
    depends_on:
      - api
    restart: unless-stopped

  worker_llm:
    image: local/grc-api:1.0.0
    command: celery -A app.workers.tasks worker -Q llm --loglevel=INFO --concurrency=1
    environment:
      APP_ENV: prod
      OFFLINE_MODE: "true"
    depends_on:
      - api
      - llm
    restart: unless-stopped

  postgres:
    image: local/postgres:16
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./backups:/backups
    restart: unless-stopped

  redis:
    image: local/redis:7
    command: redis-server --appendonly yes
    volumes:
      - redisdata:/data
    restart: unless-stopped

  weaviate:
    image: local/weaviate:1.x-pinned
    environment:
      AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED: "false"
      PERSISTENCE_DATA_PATH: /var/lib/weaviate
      DEFAULT_VECTORIZER_MODULE: none
      ENABLE_MODULES: ""
    volumes:
      - weaviate_data:/var/lib/weaviate
    restart: unless-stopped

  minio:
    image: local/minio:RELEASE-pinned
    command: server /data --console-address ":9001"
    volumes:
      - minio_data:/data
    restart: unless-stopped

  llm:
    image: local/llama-cpp-server-cuda:pinned
    environment:
      LLAMA_ARG_MODEL: /models/granite/model.gguf
      LLAMA_ARG_N_GPU_LAYERS: 999
      LLAMA_ARG_CTX_SIZE: 8192
      LLAMA_ARG_PARALLEL: 1
    volumes:
      - ./models:/models:ro
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    restart: unless-stopped

volumes:
  pgdata:
  redisdata:
  weaviate_data:
  minio_data:

secrets:
  database_url:
    file: ./secrets/database_url.txt
  minio_access_key:
    file: ./secrets/minio_access_key.txt
  minio_secret_key:
    file: ./secrets/minio_secret_key.txt
```

## Production notes

- Replace every placeholder image with a pinned local image.
- Disable anonymous vector DB access in production.
- Use secrets, not plain environment variables, for credentials.
- Validate no container can reach the public internet.
- Run backup jobs outside this file or as a dedicated service.
```
