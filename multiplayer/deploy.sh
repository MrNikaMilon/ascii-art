#!/bin/sh
# Copy the game to a server over SSH and (re)start it behind Caddy with HTTPS.
#
# Usage: ./deploy.sh SSH_HOST SITE_ADDRESS [ACME_PROFILE]
#   ./deploy.sh flies-server 203.0.113.5                      # HTTPS on the bare IP
#   ./deploy.sh flies-server 203-0-113-5.sslip.io classic     # fallback hostname
#
# The server needs Docker with Compose, and ports 80 and 443 open.
# Redeploying ends any games in progress; certificates are kept.
set -eu

HOST="${1:?Usage: ./deploy.sh SSH_HOST SITE_ADDRESS [ACME_PROFILE]}"
SITE="${2:?Usage: ./deploy.sh SSH_HOST SITE_ADDRESS [ACME_PROFILE]}"
PROFILE="${3:-shortlived}"
case "$SITE" in *[!A-Za-z0-9.:-]*) echo "SITE_ADDRESS may only contain letters, digits, '.', ':' and '-'" >&2; exit 1 ;; esac
case "$PROFILE" in shortlived|classic|tlsserver) ;; *) echo "ACME_PROFILE must be shortlived, classic or tlsserver" >&2; exit 1 ;; esac
case "$SITE" in *:*) URL_HOST="[$SITE]" ;; *) URL_HOST="$SITE" ;; esac

cd "$(dirname "$0")"
COPYFILE_DISABLE=1 tar -czf - Dockerfile .dockerignore compose.yaml Caddyfile requirements.txt app.py game.py static |
  ssh "$HOST" 'mkdir -p ~/last-fly-standing && tar -xzf - -C ~/last-fly-standing'

ssh "$HOST" "cd ~/last-fly-standing
  if [ \"\$(id -u)\" -eq 0 ] || docker info >/dev/null 2>&1; then DOCKER=docker; else DOCKER='sudo docker'; fi
  \$DOCKER compose version >/dev/null || { echo 'Docker Compose is not installed' >&2; exit 1; }
  printf 'SITE_ADDRESS=%s\nACME_PROFILE=%s\n' '$SITE' '$PROFILE' > .env
  \$DOCKER compose up -d --build --remove-orphans || exit 1
  # The Caddyfile is a bind-mounted file that was just replaced, so restart
  # Caddy to load the new copy. Certificates are kept in its data volume.
  \$DOCKER compose restart caddy || exit 1
  echo 'Waiting for the HTTPS certificate (can take a minute on first deploy)...'
  for i in \$(seq 1 45); do
    if curl -fsS --max-time 3 'https://$URL_HOST/healthz' >/dev/null 2>&1; then
      echo 'Live at https://$URL_HOST'
      exit 0
    fi
    sleep 2
  done
  echo 'HTTPS did not come up. Recent Caddy logs:' >&2
  \$DOCKER compose logs --tail=40 caddy
  exit 1"
