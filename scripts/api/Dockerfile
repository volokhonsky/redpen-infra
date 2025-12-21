# Base image
FROM python:3.12-slim

# Install tzdata (optional) and build deps minimal
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 10001 app
WORKDIR /app

# Copy requirements and install
COPY scripts/api/requirements-api.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application code
COPY . /app/

# Create logs directory with proper permissions BEFORE user switch
RUN mkdir -p /app/logs && chown app:app /app/logs

USER app
EXPOSE 8080

# Run the service
CMD ["uvicorn", "main:app", "--app-dir", "scripts/api", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers"]
