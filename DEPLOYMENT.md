# Déploiement d'ImmoRenta sur le VPS-4 OVH

ImmoRenta est une page web statique (React chargé via CDN, aucun build
nécessaire) qui va chercher ses données de prix/loyers directement sur
`raw.githubusercontent.com`. Le déploiement consiste donc uniquement à
servir `site/index.html` sur le VPS, via un conteneur Docker (Caddy, avec
HTTPS automatique si un nom de domaine est configuré).

Le pipeline de données (`update_prix.py`, régénération de
`prix-communes.json` via GitHub Actions) est indépendant : il continue de
tourner sur GitHub et n'a rien à voir avec ce déploiement.

## 1. Prérequis

- Accès SSH root (ou sudo) au VPS-4 OVH.
- (Optionnel mais recommandé) Un nom de domaine dont l'enregistrement DNS
  de type A pointe vers l'IP publique du VPS. Sans domaine, le site reste
  accessible en HTTP simple via l'IP.

## 2. Installer Docker sur le VPS

Connectez-vous en SSH, puis (Debian/Ubuntu, image standard OVH) :

```bash
curl -fsSL https://get.docker.com | sh
```

Vérifiez :

```bash
docker --version
docker compose version
```

## 3. Ouvrir les ports

Si `ufw` est actif :

```bash
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow OpenSSH
ufw enable
```

Vérifiez aussi, côté espace client OVH, que le pare-feu réseau du VPS
n'est pas configuré pour bloquer ces ports.

## 4. Récupérer le dépôt

```bash
git clone https://github.com/DanNataf26/ImmoRenta.git
cd ImmoRenta
```

## 5. Configurer

```bash
cp .env.example .env
nano .env
```

- Si vous avez un domaine : `DOMAIN=simulateur.mondomaine.fr` et
  `ACME_EMAIL=vous@mondomaine.fr` (Caddy obtiendra et renouvellera
  automatiquement le certificat Let's Encrypt).
- Sinon, laissez `DOMAIN=localhost` : le site sera servi en HTTP sur le
  port 80, accessible via l'IP du VPS.

## 6. Déployer

```bash
./deploy.sh
```

Ce script construit l'image, lance le conteneur (`docker compose up -d`)
et affiche son état. Le site est alors accessible :

- `https://votre-domaine.fr` si `DOMAIN` est configuré,
- ou `http://<IP-du-VPS>` sinon.

## 7. Mettre à jour le site

Après un nouveau `git push` sur `main` (par exemple une nouvelle version
de `site/index.html`), sur le VPS :

```bash
cd ImmoRenta
./deploy.sh
```

## 8. Vérifications utiles

```bash
docker compose ps            # état du conteneur
docker compose logs -f       # logs Caddy (obtention du certificat, erreurs)
```

## Notes

- Le certificat TLS et sa configuration sont conservés dans les volumes
  Docker `caddy_data`/`caddy_config` : ils survivent aux mises à jour
  (`./deploy.sh`) tant que vous ne faites pas `docker compose down -v`.
- Aucune base de données ni variable secrète n'est nécessaire : toute la
  logique de calcul s'exécute dans le navigateur du visiteur.
