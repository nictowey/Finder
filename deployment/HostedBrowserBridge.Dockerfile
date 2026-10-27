# Reuses the verified local private browser image; no package or network install.
FROM sniper-browser-view:readonly-78f109f
USER root
COPY scripts/discogs_hosted_readonly.mjs /app/scripts/discogs_hosted_readonly.mjs
COPY scripts/discogs_cart_inspector.mjs /app/scripts/discogs_cart_inspector.mjs
COPY scripts/discogs_listing_detail_snapshot.mjs /app/scripts/discogs_listing_detail_snapshot.mjs
COPY scripts/discogs_release_snapshot.mjs /app/scripts/discogs_release_snapshot.mjs
COPY scripts/discogs_browser_memory.mjs /app/scripts/discogs_browser_memory.mjs
COPY scripts/discogs_hosted_browser_runtime.mjs /app/scripts/discogs_hosted_browser_runtime.mjs
COPY scripts/discogs_hosted_guest_prepare.mjs /app/scripts/discogs_hosted_guest_prepare.mjs
COPY scripts/discogs_hosted_guest_socket.mjs /app/scripts/discogs_hosted_guest_socket.mjs
COPY scripts/discogs_hosted_guest_qualification.mjs /app/scripts/discogs_hosted_guest_qualification.mjs
COPY scripts/discogs_enhanced_checkout.mjs /app/scripts/discogs_enhanced_checkout.mjs
COPY scripts/discogs_enhanced_checkout_coordinator.mjs /app/scripts/discogs_enhanced_checkout_coordinator.mjs
COPY scripts/discogs_enhanced_payment_quote.mjs /app/scripts/discogs_enhanced_payment_quote.mjs
COPY scripts/discogs_enhanced_payment_adapter.mjs /app/scripts/discogs_enhanced_payment_adapter.mjs
COPY scripts/discogs_enhanced_order_ledger_port.mjs /app/scripts/discogs_enhanced_order_ledger_port.mjs
COPY scripts/discogs_merchant_order_evidence.mjs /app/scripts/discogs_merchant_order_evidence.mjs
COPY scripts/discogs_order_ledger_port.py /app/scripts/discogs_order_ledger_port.py
COPY src/finder/discogs_order_reconciliation.py /app/src/finder/discogs_order_reconciliation.py
COPY scripts/discogs_guest_card_prepare.mjs /app/scripts/discogs_guest_card_prepare.mjs
COPY scripts/discogs_guest_checkout_operator.mjs /app/scripts/discogs_guest_checkout_operator.mjs
COPY scripts/sniper-browser-bridge-runner.mjs /app/scripts/sniper-browser-runner.mjs
USER 10001:10001
RUN python3 -c 'import fcntl' \
    && node --input-type=module -e "await import('/app/scripts/discogs_hosted_guest_socket.mjs'); await import('/app/scripts/sniper-browser-runner.mjs')"
