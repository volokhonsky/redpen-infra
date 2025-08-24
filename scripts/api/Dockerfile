# Base image
FROM python:3.12-slim

# Install tzdata (optional) and build deps minimal
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Create app user and dirs
RUN useradd -m -u 10001 app
WORKDIR /app

# Copy requirements and install
COPY requirements-api.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application code
COPY . /app/

# Create data dir and set ownership
RUN mkdir -p /data && chown -R app:app /data /app

USER app
EXPOSE 8080

# Run the service
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers"]
