FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && groupadd -g 10001 library && useradd -u 10001 -g library library
COPY library ./library
COPY templates ./templates
COPY static ./static
COPY assets ./assets
COPY catalog ./catalog
RUN mkdir /app/data /app/output /app/backups && chown -R 10001:10001 /app/data /app/output /app/backups
USER 10001:10001
EXPOSE 18880
CMD ["uvicorn", "library.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "18880", "--workers", "1", "--no-server-header"]
