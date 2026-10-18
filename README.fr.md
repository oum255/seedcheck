# Seedcheck

[![CI](https://github.com/oum255/seedcheck/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/oum255/seedcheck/actions/workflows/ci.yml)

*[English version](README.md)*

Seedcheck lance ta configuration cloud-init sur plusieurs versions d'Ubuntu
en même temps, dans des conteneurs LXD, et te dit où elle marche et où elle
casse.

```text
$ seedcheck run examples/nginx/seedcheck.yaml
Seedcheck 0.1.0   user-data: examples/nginx/user-data.yaml

Check                               22.04                        24.04                       26.04
schema                              PASS                         PASS                        PASS
cloud-init                          PASS                         PASS                        PASS
package: nginx                      PASS (1.18.0-6ubuntu14.21)   PASS (1.24.0-2ubuntu7.18)   PASS (1.28.3-2ubuntu1.11)
package: curl                       PASS (7.81.0-1ubuntu1.29)    PASS (8.5.0-2ubuntu10.15)   PASS (8.18.0-1ubuntu2.7)
service: nginx                      PASS                         PASS                        PASS
file: /etc/motd                     PASS                         PASS                        PASS
user: deploy                        PASS                         PASS                        PASS
command: nginx answers on port 80   PASS                         PASS                        PASS

Result: 3 of 3 releases passed (24 checks, 0 failed, 0 warnings)
```

Quand quelque chose ne va pas, tu vois où et pourquoi (`examples/broken`) :

```text
Check                                  22.04                       24.04                       26.04
package: this-package-does-not-exist   FAIL                        FAIL                        FAIL
file: /etc/seedcheck-demo.conf         FAIL                        FAIL                        FAIL
command: python3 is 3.12               FAIL                        PASS                        FAIL

Failures:
  [22.04] package: this-package-does-not-exist: not installed
  [22.04] file: /etc/seedcheck-demo.conf: expected mode 0644, found 0600
  [22.04] command: python3 is 3.12: stdout does not contain 'Python 3.12'; output: Python 3.10.12
  ...
```

Ces deux sorties viennent de vraies exécutions. La CI affiche le même tableau
à chaque push. L'outil lui-même (messages, options, rapports) est en anglais.

## Pourquoi je l'ai fait

Un fichier cloud-init qui marche sur une version d'Ubuntu peut casser sur la
suivante. Les versions des paquets changent, un service passe en activation
par socket, apt change de format pour ses sources, cloud-init renvoie un
autre code de sortie pour le même avertissement. Je suis tombée sur tout ça
entre 22.04, 24.04 et 26.04 en construisant l'outil (la liste est dans
[docs/design.md](docs/design.md#differences-between-releases), en anglais).

Tester ça à la main, c'est démarrer une VM par version et aller fouiller
dedans. Je voulais une seule commande qui le fasse pour moi et qui donne un
oui ou un non clair pour chaque version.

## Ce qu'il te faut

- Ubuntu 22.04, 24.04 ou 26.04 : installé directement, dans une VM, ou dans
  WSL2 avec systemd.
- LXD 5.0 ou plus récent. J'ai testé la 5.0.10 (celle qui vient avec les
  images Ubuntu 22.04) et la 5.21.8 LTS. C'est un snap, donc le `.deb` ne
  peut pas l'installer pour toi :

  ```bash
  sudo snap install lxd
  sudo lxd init --auto
  sudo usermod -aG lxd "$USER"   # puis déconnecte-toi et reconnecte-toi
  ```

  Ou lance simplement [`scripts/setup-lxd.sh`](scripts/setup-lxd.sh). Si
  LXD n'est pas prêt, `seedcheck run` s'arrête avec le code 3 et te dit ce
  qui manque.
- Un accès réseau depuis les conteneurs, pour télécharger les images et les
  paquets.

## Installation

Avec le `.deb` de la [page des releases](https://github.com/oum255/seedcheck/releases/latest) :

```bash
wget https://github.com/oum255/seedcheck/releases/download/v0.1.0/seedcheck_0.1.0_all.deb
sudo apt install ./seedcheck_0.1.0_all.deb
seedcheck --version
```

C'est du Python pur, donc un seul paquet marche sur 22.04, 24.04 et 26.04,
en amd64 comme en arm64. La seule dépendance en plus est `python3-yaml`. Les
exemples sont installés dans `/usr/share/doc/seedcheck/examples/`, et il y a
une page de manuel (`man seedcheck`).

Depuis les sources (Python 3.10 ou plus ; sur Ubuntu, `venv` est un paquet à
part) :

```bash
sudo apt install git python3-venv
git clone https://github.com/oum255/seedcheck.git
cd seedcheck
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

## Essayer

Une fois installé, depuis un clone du dépôt (c'est là que sont les exemples) :

```bash
cd seedcheck

# Vérifier la configuration sans rien lancer. Pas besoin de LXD.
seedcheck validate examples/nginx/seedcheck.yaml

# Essayer d'abord une seule version, ça prend environ une minute.
seedcheck run examples/nginx/seedcheck.yaml --release 24.04

# Puis les trois, avec les rapports.
seedcheck run examples/nginx/seedcheck.yaml --markdown report.md --json report.json
echo "code de sortie : $?"
```

Le [user-data nginx](examples/nginx/user-data.yaml) installe nginx et curl,
ajoute un utilisateur `deploy`, écrit `/etc/motd` et démarre nginx, et
[`seedcheck.yaml`](examples/nginx/seedcheck.yaml) vérifie chacun de ces
points. Il y a deux autres exemples : [`apt-source`](examples/apt-source/)
ajoute un PPA Launchpad avec le module `apt:` de cloud-init et vérifie que
git vient bien de ce PPA, et [`broken`](examples/broken/) échoue exprès.

## Écrire un `seedcheck.yaml`

Les clés restent en anglais :

```yaml
user_data: user-data.yaml        # obligatoire, chemin relatif à ce fichier
releases: ["22.04", "24.04", "26.04"]  # facultatif, numéro ou nom de code (valeur par défaut)
timeout: 600                     # facultatif, secondes d'attente par version

checks:                          # facultatif, schema et cloud-init sont toujours vérifiés
  packages:                      # doit être installé ; la version s'affiche dans le tableau
    - nginx
    - name: curl                 # pareil que "- curl"
  services:                      # doit être dans cet état (systemctl is-active)
    - nginx                      # active par défaut
    - name: nginx.service        # le .service est facultatif
      state: active
  files:                         # doit exister et être un fichier normal
    - path: /etc/motd
      contains: "Provisioned by cloud-init"  # facultatif, texte simple, peut faire plusieurs lignes
      mode: "0644"               # facultatif, entre guillemets
      owner: root                # facultatif
  users:                         # doit exister
    - name: deploy
      groups: [sudo]             # facultatif, doit faire partie de chacun
  commands:                      # lancée avec sh -c dans le conteneur
    - name: nginx answers on port 80
      run: curl -fsS http://localhost
      expect_exit: 0             # facultatif, 0 par défaut
      expect_stdout_contains: nginx  # facultatif
```

La validation est stricte exprès, pour que les erreurs apparaissent avant de
lancer quoi que ce soit. Une faute de frappe comme `package:` donne
`checks.package: unknown key ... did you mean 'packages'?`, et chaque erreur
indique la clé exacte (comme `checks.files[0].mode`). Mets les numéros de
version et les modes de fichier entre guillemets : sans guillemets, YAML lit
`0644` comme 420 et `24.10` comme 24.1. Les états de service possibles sont
ceux que `systemctl is-active` peut afficher : `active`, `inactive`,
`failed`, `activating`, `deactivating`, `reloading`, `refreshing`,
`maintenance`.

Le user-data doit commencer par `#cloud-config`, sinon cloud-init l'ignore
sans rien dire. Les autres formats (`#!/bin/bash`, `#include`, MIME
multi-part...) sont acceptés, mais la validation du schéma ne marche que sur
`#cloud-config`.

## Commandes

```text
seedcheck run CONFIG          Valide, lance, vérifie et affiche le rapport.
    --release REL             Seulement cette version (22.04 ou jammy). Répétable. Remplace la config.
    --markdown PATH           Écrit aussi le tableau en Markdown (marche dans un résumé de job GitHub).
    --json PATH               Écrit aussi tous les résultats en JSON.
    --artifacts-dir DIR       Garde les journaux cloud-init de chaque conteneur dans DIR/VERSION/.
    --timeout SECONDS         Remplace le délai de la config.
    --keep                    Ne supprime pas les conteneurs (pour déboguer).
    -v, --verbose             Affiche chaque commande lxc et sa sortie.

seedcheck validate CONFIG     Valide seulement. Pas besoin de LXD.
seedcheck releases            Liste les versions, noms de code et images.
seedcheck --version           Affiche la version.
```

La progression va sur stderr et le tableau sur stdout, donc tu peux rediriger
le tableau dans un fichier. Les couleurs n'apparaissent que dans un terminal,
et jamais si `NO_COLOR` est défini.

| Version | Nom de code | Image LXD | Python |
|---|---|---|---|
| 22.04 | jammy | `ubuntu:22.04` | 3.10 |
| 24.04 | noble | `ubuntu:24.04` | 3.12 |
| 26.04 | resolute | `ubuntu:26.04` | 3.14 |

## Codes de sortie

| Code | Signification |
|---|---|
| 0 | Toutes les vérifications de toutes les versions sont PASS ou WARN. |
| 1 | Au moins une vérification est FAIL. |
| 2 | Mauvais arguments, `seedcheck.yaml` ou user-data invalide. Détecté avant de lancer quoi que ce soit. |
| 3 | Problème d'infrastructure : LXD absent ou injoignable, ou une version a fini en ERROR (image introuvable, délai dépassé). Passe avant le 1, parce que les résultats sont incomplets. |
| 130 | Tu as fait Ctrl+C. Les conteneurs sont quand même supprimés. |

Une case peut être PASS, FAIL, WARN (réussi, mais cloud-init a signalé un
détail, comme une clé obsolète), SKIP (pas lancé parce que la version a
échoué avant) ou ERROR.

## Comment ça marche

1. **Valider** `seedcheck.yaml` et le user-data d'abord. Si cloud-init est
   installé sur ta machine, sa commande `schema` est lancée aussi.
2. **Lancer** un conteneur LXD par version à partir des images officielles
   `ubuntu:`, en passant le user-data par la clé `cloud-init.user-data`.
3. **Attendre** que cloud-init ait fini dans chacun (`cloud-init status
   --wait`), dans la limite du délai.
4. **Vérifier** dans chaque conteneur : le `cloud-init schema --system` de
   cette version, le rapport d'erreurs de cloud-init (`cloud-init status
   --long --format json`), puis les paquets (`dpkg-query`), les services
   (`systemctl is-active`), les fichiers (`stat`, `grep -F`), les
   utilisateurs (`id -nG`) et les commandes (`sh -c`).
5. **Afficher** le tableau dans le terminal, plus le Markdown et le JSON si
   tu les demandes.
6. **Nettoyer.** Chaque conteneur est supprimé, que ce soit réussi, raté,
   trop long ou interrompu par Ctrl+C, et le code de sortie dit à ta CI ce
   qui s'est passé.

Les versions passent l'une après l'autre, et si l'une échoue, les autres
continuent. La conception et les choix derrière sont expliqués dans
[docs/design.md](docs/design.md) (en anglais).

## Dans GitHub Actions

```yaml
jobs:
  cloud-init:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v7
      - uses: canonical/setup-lxd@8c6a87bfb56aa48f3fb9b830baa18562d8bfd4ee # v1
        with:
          channel: 5.21/stable
      - name: Install Seedcheck
        run: |
          wget -q https://github.com/oum255/seedcheck/releases/download/v0.1.0/seedcheck_0.1.0_all.deb
          sudo apt-get install -y ./seedcheck_0.1.0_all.deb
      - name: Test the cloud-init config
        run: seedcheck run seedcheck.yaml --markdown report.md --artifacts-dir logs
      - name: Show the table in the job summary
        if: always()
        run: cat report.md >> "$GITHUB_STEP_SUMMARY"
      - uses: actions/upload-artifact@v7
        if: always()
        with:
          name: cloud-init-logs
          path: logs/
```

`canonical/setup-lxd` donne accès à LXD sur le runner et ouvre le pare-feu de
Docker pour le pont LXD (sans ça, les conteneurs n'ont pas de réseau). La
[CI](.github/workflows/ci.yml) de Seedcheck fait exactement ça à chaque push.

## Limites

- **Des conteneurs, pas des VM.** Ils partagent le noyau de la machine, donc
  les modules du noyau, ses réglages, le chargeur d'amorçage et certains
  détails bas niveau de systemd ne sont pas testés. LXD sait aussi lancer
  des VM, ça pourrait être une option plus tard.
- **Ubuntu seulement**, et seulement les trois versions ci-dessus.
- **Une version à la fois**, environ une minute chacune.
- **Tu testes l'architecture de ta machine** (amd64 ou arm64). Si un dépôt
  apt tiers n'a pas de paquets pour elle, la vérification échoue même si ta
  config est bonne.
- **La vérification rapide du schéma sur ta machine utilise ton propre
  cloud-init**, qui n'a pas forcément la même version que celui d'une
  version d'Ubuntu. C'est la ligne `schema` de chaque version qui fait foi.
- Le user-data est passé comme un seul argument en ligne de commande, et
  Linux le limite à 128 Kio.

## Développement

```bash
sudo apt install git python3-venv shellcheck
git clone https://github.com/oum255/seedcheck.git && cd seedcheck
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

ruff check . && ruff format --check . && mypy src   # lint, formatage, types
pytest --cov=seedcheck                              # tests unitaires, pas besoin de LXD
pytest -m integration                               # vrais conteneurs, environ 8 minutes
scripts/build-deb.sh                                # construire le .deb (Ubuntu 24.04+)
scripts/smoke-install.sh 22.04 dist/seedcheck_0.1.0_all.deb
```

Chiffres tirés de vraies exécutions (macOS avec Python 3.13, Ubuntu 22.04
avec Python 3.10, et la CI avec 3.10, 3.12 et 3.14) :

- **287 tests unitaires** qui tournent sans LXD en moins d'une seconde, avec
  **99,75 % de couverture** des lignes et des branches (la CI échoue sous
  90 %). Ils utilisent un faux client LXD, donc ils tournent partout.
- **5 tests d'intégration** sur un vrai LXD, qui lancent chaque exemple sur
  22.04, 24.04 et 26.04 et vérifient qu'aucun conteneur ne reste.

Tu veux ajouter un type de vérification ou une version ? Regarde
[CONTRIBUTING.md](CONTRIBUTING.md) (en anglais).

## Licence

Copyright © 2026 Oumayma Haddour. Tous droits réservés. Le code est public pour
être lu, mais tu ne peux pas le réutiliser sans mon accord. Voir
[LICENSE](LICENSE).
