#!/usr/bin/env bash
# Déploie/met à jour ImmoRenta sur le VPS.
# À exécuter depuis la racine du dépôt, sur le serveur.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "Aucun fichier .env trouvé : copie de .env.example -> .env" >&2
  cp .env.example .env
fi

git pull --ff-only

docker compose build
docker compose up -d

docker compose ps
