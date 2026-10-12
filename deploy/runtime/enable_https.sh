#!/usr/bin/env bash
set -Eeuo pipefail
domain="${1:?domain required}"
email="${2:?certificate contact email required}"
[[ "$domain" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$ ]]
[[ "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]
root=/opt/smart-lost-found
release="$(readlink -f "$root/current")"
compose=(docker compose --project-name smart-lost-found-prod --env-file "$release/compose.env" -f "$release/compose.yaml")

docker run --rm --memory 128m -v "$root/letsencrypt:/etc/letsencrypt" -v "$root/acme:/var/www/acme" \
  certbot/certbot:v4.0.0 certonly --webroot --webroot-path /var/www/acme --non-interactive \
  --agree-tos --email "$email" --domain "$domain" --keep-until-expiring
sed "s/__DOMAIN__/$domain/g" "$release/nginx.https.conf.template" > "$root/proxy/default.conf"
"${compose[@]}" exec -T nginx nginx -t
"${compose[@]}" exec -T nginx nginx -s reload
install -m 700 "$release/renew_https.sh" "$root/renew_https.sh"
cat > /etc/systemd/system/smart-lost-found-cert-renew.service <<'EOF'
[Unit]
Description=Renew Smart Lost Found TLS certificate
After=docker.service
[Service]
Type=oneshot
ExecStart=/bin/bash /opt/smart-lost-found/renew_https.sh
EOF
cat > /etc/systemd/system/smart-lost-found-cert-renew.timer <<'EOF'
[Unit]
Description=Check Smart Lost Found TLS certificate twice daily
[Timer]
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=3600
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now smart-lost-found-cert-renew.timer
echo 'HTTPS enabled and certificate renewal scheduled.'
