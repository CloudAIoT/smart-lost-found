#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

release="$(cd -- "$(dirname -- "$0")" && pwd)"
root=/opt/smart-lost-found
image="${1:?image digest required}"
region="${2:?region required}"
parameter="${3:?SSM parameter required}"
domain="${4:-}"
email="${5:-}"

mkdir -p "$root" "$root/proxy" "$root/letsencrypt" "$root/acme"
# The nginx worker must be able to read Certbot's public challenge files.
chmod 755 "$root/acme"
exec 9>"$root/deploy.lock"
flock -w 900 9
[[ "$image" =~ ^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9/_-]+@sha256:[a-f0-9]{64}$ ]]
[[ "$release" == "$root"/releases/* ]]
if [[ -n "$domain" ]]; then
  [[ "$domain" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$ ]]
  [[ "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]
fi

docker run --rm --user 0 --memory 128m -v "$release:/config" "$image" \
  python /opt/deploy/render_config.py --parameter "$parameter" --region "$region" --output /config
printf 'BACKEND_IMAGE=%s\n' "$image" > "$release/compose.env"
compose=(docker compose --project-name smart-lost-found-prod --env-file "$release/compose.env" -f "$release/compose.yaml")
previous="$(readlink -f "$root/current" 2>/dev/null || true)"

# Changing POSTGRES_PASSWORD does not rotate an existing PostgreSQL role password.
# Stop before changing containers; arrange an explicit DB rotation instead.
if [[ -n "$previous" && -f "$previous/db.env" ]] && ! cmp -s "$previous/db.env" "$release/db.env"; then
  echo 'Database settings changed. Perform a planned database credential migration before redeploying.' >&2
  exit 1
fi
# Refresh HTTP routes on redeployment; configured TLS is rendered by enable_https.sh.
if [[ -z "$domain" || ! -f "$root/proxy/default.conf" ]]; then
  cp "$release/nginx.http.conf" "$root/proxy/default.conf"
fi

"${compose[@]}" pull db nginx
"${compose[@]}" up -d --wait --wait-timeout 120 db

# Keep a pre-migration backup on the persistent host disk; no automatic downgrade.
mkdir -p "$root/backups"
# The variables are expanded inside the database container, not on the EC2 host.
# shellcheck disable=SC2016
"${compose[@]}" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$root/backups/$(basename "$release").dump"
"${compose[@]}" run --rm --no-deps backend alembic upgrade head

rollback() {
  echo 'Deployment failed. Database schema is retained; inspect migrations before retrying.' >&2
  if [[ -n "$previous" && -f "$previous/compose.env" ]]; then
    docker compose --project-name smart-lost-found-prod --env-file "$previous/compose.env" -f "$previous/compose.yaml" \
      up -d --no-deps --wait --wait-timeout 120 backend || true
    docker compose --project-name smart-lost-found-prod --env-file "$previous/compose.env" -f "$previous/compose.yaml" \
      restart nginx || true
  fi
}
trap rollback ERR
"${compose[@]}" up -d --no-deps --wait --wait-timeout 120 backend
"${compose[@]}" up -d --no-deps nginx
"${compose[@]}" exec -T nginx nginx -t
"${compose[@]}" exec -T nginx nginx -s reload
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8000/ready >/dev/null
code="$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8000/api/kiosks)"
[[ "$code" == 503 ]]
ln -sfn "$release" "$root/current"
trap - ERR
echo 'Backend deployed; business APIs remain disabled.'

# TLS failure is reported separately from a successful backend deployment.
if [[ -n "$domain" ]]; then
  bash "$release/enable_https.sh" "$domain" "$email"
fi
