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

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY src /app/src
COPY scripts /app/scripts

COPY docker/start.sh /start.sh
RUN chmod +x /start.sh

ENTRYPOINT ["/start.sh"]
