# ImmoRenta

Simulateur de rentabilité locative.

## Contenu du dépôt

- `site/index.html` — l'application : une page unique (React chargé via
  CDN, aucune étape de build) qui calcule la rentabilité d'un
  investissement locatif à partir des paramètres saisis par l'utilisateur.
  Au chargement, elle va chercher les prix/loyers de référence par commune
  dans `prix-communes.json`, publié sur ce dépôt.
- `prix-communes.json` — données de prix (DVF), loyers (Carte des loyers
  ANIL) et vacance (LOVAC) par commune et département, régénérées
  automatiquement.
- `update_prix.py` — script qui régénère `prix-communes.json` à partir des
  sources officielles (data.gouv.fr). Exécuté automatiquement par
  `.github/workflows/update-prix.yml`.
- `Dockerfile`, `docker-compose.yml`, `Caddyfile`, `deploy.sh` — de quoi
  servir `site/index.html` sur un serveur (HTTPS automatique via Caddy si
  un nom de domaine est configuré).

## Déploiement

Voir [`DEPLOYMENT.md`](./DEPLOYMENT.md) pour les instructions détaillées
de déploiement sur un VPS (ex: VPS-4 OVH).

## Développement local

Aucun build n'est nécessaire : `site/index.html` est autonome. Pour le
tester localement :

```bash
python3 -m http.server -d site 8000
```

Puis ouvrez `http://localhost:8000`.
