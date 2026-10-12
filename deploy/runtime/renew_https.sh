#!/usr/bin/env bash
set -Eeuo pipefail
root=/opt/smart-lost-found
exec 9>"$root/deploy.lock"
flock -w 900 9
release="$(readlink -f "$root/current")"
docker run --rm --memory 128m -v "$root/letsencrypt:/etc/letsencrypt" -v "$root/acme:/var/www/acme" \
  certbot/certbot:v4.0.0 renew --webroot --webroot-path /var/www/acme --non-interactive --quiet
docker compose --project-name smart-lost-found-prod --env-file "$release/compose.env" -f "$release/compose.yaml" exec -T nginx nginx -s reload
