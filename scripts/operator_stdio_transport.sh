#!/bin/sh
# Tool-owned PTY only: canonical mode can truncate/buffer long JSON lines.
# Never pass card data, credentials or browser payloads through this channel.
set -eu
if [ -t 0 ]; then
  stty -icanon -echo
fi
exec "$@"
