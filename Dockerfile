FROM node:22-alpine AS dashboard-build
WORKDIR /build
COPY dashboard/package.json dashboard/package-lock.json ./dashboard/
RUN cd dashboard && npm ci
COPY dashboard ./dashboard
COPY app ./app
RUN cd dashboard && npm run build

FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /srv/inferenceiq

RUN addgroup --system inferenceiq && adduser --system --ingroup inferenceiq inferenceiq
COPY pyproject.toml README.md ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY evaluations ./evaluations
COPY --from=dashboard-build /build/app/static/dashboard ./app/static/dashboard
RUN python -m pip install . && chown -R inferenceiq:inferenceiq /srv/inferenceiq

USER inferenceiq
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
