FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
       -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && printf '%s\n' \
       "Types: deb" \
       "URIs: https://download.docker.com/linux/debian" \
       "Suites: bookworm" \
       "Components: stable" \
       "Architectures: $(dpkg --print-architecture)" \
       "Signed-By: /etc/apt/keyrings/docker.asc" \
       > /etc/apt/sources.list.d/docker.sources

RUN apt-get update && apt-get install -y \
    docker-ce-cli \
    docker-compose-plugin \
    docker-buildx-plugin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

CMD ["sleep", "infinity"]