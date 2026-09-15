ARG PYTHON_BASE_IMAGE=docker.chabokan.net/python:3.9-trixie
FROM ${PYTHON_BASE_IMAGE}

ENV TZ=Asia/Tehran
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
WORKDIR /app

RUN apt-get update && apt-get -y install pigz duc default-mysql-client unar nano vim htop net-tools zip unzip iputils-ping \
&& mkdir /backups && mkdir /builds

# Install a versioned Docker CLI for the mounted host daemon.
ARG DOCKER_CLI_VERSION=29.6.1
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
        amd64) docker_archive_url="https://s3.ir-thr-at1.arvanstorage.ir/public-chabok/docker-${DOCKER_CLI_VERSION}.tgz" ;; \
        arm64) docker_archive_url="https://download.docker.com/linux/static/stable/aarch64/docker-${DOCKER_CLI_VERSION}.tgz" ;; \
        *) echo "Unsupported Docker CLI architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    wget --tries=3 --timeout=30 -O /tmp/docker-cli.tgz \
      "$docker_archive_url"; \
    tar -xzf /tmp/docker-cli.tgz -C /tmp docker/docker; \
    install -m 0755 /tmp/docker/docker /usr/bin/docker; \
    rm -rf /tmp/docker-cli.tgz /tmp/docker; \
    docker --version | grep -F "Docker version ${DOCKER_CLI_VERSION},"

COPY requirements.txt /app/
RUN python -m pip install --no-cache-dir --only-binary=:all: -r /app/requirements.txt

ADD start.sh /
RUN chmod +x /start.sh
EXPOSE 80

CMD ["/start.sh"]
