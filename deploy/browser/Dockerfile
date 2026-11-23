# syntax=docker/dockerfile:1
#
# The browser service for the screenshot Shuttle (Compose profile
# `screenshots`): Playwright's browser server, which launches Chromium for each
# worker connection. Built from Microsoft's Playwright image (Chromium and its
# system libraries) plus the matching playwright-core package.
#
# The Python `playwright` package in the backend image must have the same major
# and minor version (shuttles/screenshot/pyproject.toml).
#
# It has no network of its own: Compose puts it on an internal-only network
# shared with the worker, and every request a page makes is intercepted and
# answered by the worker through its guarded HTTP client. Clients may not set
# unsafe launch options (no --unsafe).

ARG PLAYWRIGHT_VERSION=1.65.1
FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-noble

ARG PLAYWRIGHT_VERSION
# The browsers are already in the image (/ms-playwright).
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    NODE_ENV=production

WORKDIR /opt/browser
RUN npm install --omit=dev --no-audit --no-fund --no-save "playwright-core@${PLAYWRIGHT_VERSION}" \
    && npm cache clean --force

USER pwuser
EXPOSE 3000

# Healthy once the server accepts connections.
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD ["node", "-e", "require('net').connect(3000, '127.0.0.1').on('connect', () => process.exit(0)).on('error', () => process.exit(1))"]

CMD ["node", "/opt/browser/node_modules/playwright-core/cli.js", "run-server", "--port", "3000", "--host", "0.0.0.0", "--max-clients", "8"]
