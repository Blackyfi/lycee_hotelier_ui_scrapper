FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_HOME=/app \
    DATA_DIR=/data \
    TZ=Europe/Paris

RUN (getent group 1000 >/dev/null || groupadd --system --gid 1000 appuser) \
    && (getent passwd 1000 >/dev/null || useradd --system --uid 1000 --gid 1000 --no-create-home appuser)

WORKDIR ${APP_HOME}

RUN apt-get update && apt-get install -y --no-install-recommends tini tzdata \
    && ln -fs /usr/share/zoneinfo/${TZ} /etc/localtime \
    && dpkg-reconfigure -f noninteractive tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

RUN mkdir -p ${DATA_DIR}/menu_images /tls \
    && chown -R 1000:1000 ${APP_HOME} ${DATA_DIR} /tls \
    && chmod +x ./scripts/start.sh

USER 1000

EXPOSE 8000

ENTRYPOINT ["tini", "--"]

CMD ["/app/scripts/start.sh"]
