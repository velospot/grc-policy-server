# Docker Compose Development Blueprint

This is a blueprint, not a drop-in production file. Adjust image names and model paths.

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: grc
      POSTGRES_PASSWORD: grc
      POSTGRES_DB: grc
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7
    ports:
      - "6379:6379"

  weaviate:
    image: semitechnologies/weaviate:latest
    ports:
      - "8080:8080"
    environment:
      QUERY_DEFAULTS_LIMIT: 25
      AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED: "true"
      PERSISTENCE_DATA_PATH: /var/lib/weaviate
      DEFAULT_VECTORIZER_MODULE: none
      ENABLE_MODULES: ""
    volumes:
      - weaviate_data:/var/lib/weaviate

  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    environment:
      OLLAMA_NUM_PARALLEL: 1
      OLLAMA_MAX_QUEUE: 64
    volumes:
      - ./models/ollama:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]

  api:
    build:
      context: .
      dockerfile: docker/api.Dockerfile
    environment:
      APP_ENV: dev
      DATABASE_URL: postgresql://grc:grc@postgres:5432/grc
      REDIS_URL: redis://redis:6379/0
      VECTOR_BACKEND: weaviate
      WEAVIATE_URL: http://weaviate:8080
      LLM_PROVIDER: ollama
      LLM_BASE_URL: http://ollama:11434
      OBJECT_STORE_MODE: filesystem
      OBJECT_STORE_PATH: /data/object_store
      OFFLINE_MODE: "true"
    ports:
      - "8000:8000"
    volumes:
      - .:/workspace
      - object_store:/data/object_store
    depends_on:
      - postgres
      - redis
      - weaviate
      - ollama

  worker:
    build:
      context: .
      dockerfile: docker/api.Dockerfile
    command: celery -A app.workers.tasks worker --loglevel=INFO --concurrency=2
    environment:
      APP_ENV: dev
      DATABASE_URL: postgresql://grc:grc@postgres:5432/grc
      REDIS_URL: redis://redis:6379/0
      VECTOR_BACKEND: weaviate
      WEAVIATE_URL: http://weaviate:8080
      LLM_PROVIDER: ollama
      LLM_BASE_URL: http://ollama:11434
      OBJECT_STORE_MODE: filesystem
      OBJECT_STORE_PATH: /data/object_store
      OFFLINE_MODE: "true"
    volumes:
      - .:/workspace
      - object_store:/data/object_store
    depends_on:
      - api

  frontend:
    build:
      context: ./frontend
    environment:
      NEXT_PUBLIC_API_BASE_URL: http://localhost:8000
    ports:
      - "3000:3000"
    volumes:
      - ./frontend:/app
    depends_on:
      - api

volumes:
  pgdata:
  weaviate_data:
  object_store:
```

## Developer notes

- Pin image versions before production.
- Replace `latest` tags in production.
- Do not allow containers to pull images in offline production.
- For Qdrant, replace Weaviate service with Qdrant and update VECTOR_BACKEND.
- For llama.cpp server, replace Ollama service with a local llama-server image and model mount.
```
