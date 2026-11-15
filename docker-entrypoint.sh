#!/bin/sh
set -eu

# Render mounts the persistent disk at /app/data after the image is built.
# Ensure the non-root application user can write to that mounted filesystem.
mkdir -p /app/data
chown -R emberlens:emberlens /app/data

exec gosu emberlens "$@"
