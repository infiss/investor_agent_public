# kquant 컨테이너 (P8). 코드만 이미지에 넣고 데이터는 /data 볼륨(KQUANT_DATA_DIR)으로 붙인다.
#   빌드:   docker build -t kquant .
#   테스트: docker build --target test -t kquant-test . && docker run --rm kquant-test
#   운영:   docker compose run --rm kquant run-grid   (compose.yaml 참고)

FROM python:3.13-slim AS base
# uv 버전 고정(재현성). 배포 환경에서는 digest(@sha256:…)로 고정하는 것을 권장한다.
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
# 의존성 층을 먼저 만들어 코드만 바뀌면 다시 받지 않는다
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

# 테스트 단계: dev 의존성 + tests/ (실데이터 테스트는 /data가 없으면 skip)
FROM base AS test
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-editable
COPY tests ./tests
ENV PATH=/opt/venv/bin:$PATH \
    KQUANT_DATA_DIR=/data \
    KQUANT_TEST_IN_IMAGE=1
CMD ["pytest", "-q", "-p", "no:cacheprovider"]

FROM python:3.13-slim AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    KQUANT_DATA_DIR=/data
COPY --from=base /opt/venv /opt/venv
RUN useradd --create-home --uid 1000 kquant && mkdir -p /data && chown kquant:kquant /data
USER kquant
WORKDIR /home/kquant
VOLUME ["/data"]
ENTRYPOINT ["kquant"]
CMD ["--help"]
