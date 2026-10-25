# Reuses the verified local private browser image; no package or network install.
FROM sniper-browser-view:readonly-78f109f
USER root
COPY scripts/discogs_hosted_readonly.mjs /app/scripts/discogs_hosted_readonly.mjs
COPY scripts/discogs_cart_inspector.mjs /app/scripts/discogs_cart_inspector.mjs
COPY scripts/discogs_listing_detail_snapshot.mjs /app/scripts/discogs_listing_detail_snapshot.mjs
COPY scripts/discogs_hosted_browser_runtime.mjs /app/scripts/discogs_hosted_browser_runtime.mjs
COPY scripts/sniper-browser-bridge-runner.mjs /app/scripts/sniper-browser-runner.mjs
USER 10001:10001
