# Étapes CI pour l'API (pour le membre 4, `.github/workflows/ci.yml`)

À intégrer dans `ci.yml` ; ce fichier n'est pas appliqué par le membre 2.

- ruff utilise le `ruff.toml` de la racine du dépôt ;
- les tests ont besoin de `model/requirements.txt` (vérification des versions de scikit-learn) ;
- l'image est publiée sur GHCR avec deux étiquettes : la version de l'API (`app/__init__.py`)
  et le SHA court du commit ;
- la publication n'a lieu que sur `main` ; sur une pull request, l'image est seulement construite.

```yaml
  api:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write          # publication sur ghcr.io avec GITHUB_TOKEN
    defaults:
      run:
        working-directory: api
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: api/requirements*.txt

      - name: Dépendances
        run: pip install -r requirements-dev.txt

      - name: Lint (ruff)
        run: |
          ruff check .
          ruff format --check .

      - name: Tests (pytest)
        run: pytest -q

      - name: Version de l'API
        id: version
        run: echo "version=$(python -c 'import app; print(app.__version__)')" >> "$GITHUB_OUTPUT"

      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        if: github.event_name == 'push' && github.ref == 'refs/heads/main'
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ github.repository }}/api
          tags: |
            type=raw,value=${{ steps.version.outputs.version }}
            type=sha,prefix=sha-

      - name: Construction et publication de l'image
        uses: docker/build-push-action@v6
        with:
          context: api
          platforms: linux/amd64
          push: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

Déclencheurs suggérés en haut de `ci.yml` :

```yaml
on:
  push:
    branches: [main]
  pull_request:
    paths: ["api/**", "model/requirements.txt", "ruff.toml", ".github/workflows/ci.yml"]
```

Après la première publication, le paquet `api` apparaît dans l'onglet *Packages* de GitHub. Le passer en
**public** (le dépôt l'est déjà) évite d'avoir à fournir des identifiants à Container Apps ; sinon, voir
`deploy/containerapp.md`.

Un `type=raw` réutilisé (même version) écrase l'étiquette précédente : changer `__version__` dans
`api/app/__init__.py` à chaque version publiée et utiliser l'étiquette `sha-…` pour un déploiement exact.
