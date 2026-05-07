FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    texlive-latex-base texlive-fonts-recommended \
    wireguard-tools iproute2 iptables \
    curl procps \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y openresolv \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y texlive-latex-extra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install backend dependency (tool imports from apps/backend)
COPY apps/backend /app/apps/backend
RUN pip install --no-cache-dir -e /app/apps/backend

# Install tool dependencies
COPY tools/job-tailor/requirements.txt /app/tools/job-tailor/requirements.txt
RUN pip install --no-cache-dir -r /app/tools/job-tailor/requirements.txt

# Copy tool source
COPY tools/job-tailor/src /app/tools/job-tailor/src

COPY tools/job-tailor/docker/start.sh /start.sh
RUN chmod +x /start.sh

WORKDIR /app/tools/job-tailor

ENTRYPOINT ["/start.sh"]
