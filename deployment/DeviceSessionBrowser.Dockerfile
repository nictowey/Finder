# Test only. Browser package is the existing official locked Playwright dependency.
FROM node:24.22.1-bookworm
ENV DEBIAN_FRONTEND=noninteractive PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp/home
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY tests/cloud_browser/package.json tests/cloud_browser/package-lock.json ./tests/cloud_browser/
RUN cd tests/cloud_browser && npm ci --ignore-scripts \
    && npx playwright install --with-deps chromium webkit \
    && rm -rf /var/lib/apt/lists/* /root/.npm
COPY pyproject.toml README.md ./
COPY src ./src
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir .
COPY tests/device_session_browser ./tests/device_session_browser/
RUN groupadd --gid 10001 browsercheck && useradd --uid 10001 --gid 10001 --no-create-home browsercheck
USER 10001:10001
CMD ["node", "tests/device_session_browser/native_form.mjs"]
