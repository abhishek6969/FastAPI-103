# =============================================================================
# Production Dockerfile for FastAPI on Azure App Service
# =============================================================================

FROM python:3.12-slim

WORKDIR /app

# 1. Install system utilities and OpenSSH server for Azure App Service SSH
# Azure App Service requires OpenSSH running on port 2222 with root password 'Docker!'
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openssh-server \
        curl \
    && echo "root:Docker!" | chpasswd \
    && rm -rf /var/lib/apt/lists/*

# Copy custom SSH configuration for Azure App Service
COPY sshd_config /etc/ssh/

# 2. Install Python dependencies (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Copy application code and entrypoint
COPY . .

# Ensure entrypoint script has Linux line endings (LF) and executable permissions
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# 4. Expose application port (8000) and Azure SSH port (2222)
EXPOSE 8000 2222

# 5. Set entrypoint to initialize SSH, run migrations, and launch Gunicorn
ENTRYPOINT ["/app/entrypoint.sh"]
