FROM caddy:2-alpine

COPY site/ /srv/
COPY Caddyfile /etc/caddy/Caddyfile
