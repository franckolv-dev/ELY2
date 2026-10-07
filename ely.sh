#!/usr/bin/env bash
# Ely — installation et lancement (macOS / Linux)
#   ./ely.sh            installe si besoin puis lance Ely (supervisé)
#   ./ely.sh update     récupère la dernière version (en gardant les améliorations faites par Ely) et redémarre le service
#   ./ely.sh install    installe / met à jour les dépendances
#   ./ely.sh service    démarrage automatique à l'ouverture de session (macOS)
#   ./ely.sh unservice  retire le démarrage automatique
#   ./ely.sh test       lance la suite de tests
#
# Le superviseur redémarre Ely quand elle se met à jour elle-même, vérifie que la
# nouvelle version démarre correctement, et revient à la précédente sinon.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"

env_value() { grep -E "^(export[[:space:]]+)?$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*//' | tr -d '"' | tr -d "'" ; }

install() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "→ installation de uv (gestionnaire Python)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  fi
  [ -x "$PY" ] || uv venv .venv --python 3.12
  echo "→ dépendances…"
  local extras="dev"
  # Claude (Agent SDK, ~250 Mo avec son CLI) : seulement si une clé Anthropic ou un jeton Claude Code est dans .env
  if [ -n "$(env_value ANTHROPIC_API_KEY)" ] || [ -n "$(env_value CLAUDE_CODE_OAUTH_TOKEN)" ]; then extras="dev,claude"; fi
  uv pip install --python "$PY" -q -e ".[$extras]"
  if [ -z "$(env_value ELY_BROWSER_CHANNEL)" ] && [ -z "$(env_value ELY_BROWSER_EXECUTABLE)" ]; then
    echo "→ navigateur Chromium pour l'agent…"
    "$PY" -m playwright install chromium >/dev/null
  fi
  if [ ! -f .env ]; then
    cp .env.example .env
    echo "→ fichier .env créé : ajoutez vos clés d'API (ou lancez simplement LM Studio)."
  fi
  sha1sum pyproject.toml 2>/dev/null | cut -d' ' -f1 > .venv/.deps || shasum pyproject.toml | cut -d' ' -f1 > .venv/.deps
  echo "✓ installation terminée"
}

deps_changed() {
  local now
  now=$(sha1sum pyproject.toml 2>/dev/null | cut -d' ' -f1 || shasum pyproject.toml | cut -d' ' -f1)
  [ "$now" != "$(cat .venv/.deps 2>/dev/null)" ]
}

version() {
  local v
  v=$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' "$ROOT/ely/__init__.py" 2>/dev/null)
  echo "${v:+$v · }$(git log -1 --format='%h (%cd)' --date=format:%d/%m/%Y 2>/dev/null)"
}

elyport() {
  # Même conversion que config._int : une faute de frappe ne doit pas faire
  # échouer le contrôle de santé alors que Python écoute sur le port par défaut.
  # Une variable d'environnement définie mais vide prime aussi sur le .env.
  "$PY" -c 'import sys
try:
    port = int(sys.argv[1].strip() or 8000)
except ValueError:
    port = 8000
print(port)' "${ELY_PORT-$(env_value ELY_PORT)}"
}

json_get() { "$PY" -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" "$1" "$2" 2>/dev/null; }

rollback() {  # $1 : version précédente, $2 : raison ; $data : dossier de données
  local bad
  bad="$(git rev-parse HEAD)"
  echo "✗ $2 : retour à ${1:0:10}"
  if ! git reset -q --keep "$1" 2>/dev/null; then
    # fichiers suivis modifiés à la main : mis de côté (git stash list), jamais écrasés
    if git stash push -q -m "modifications locales mises de côté avant le retour arrière d'Ely" && git reset -q --keep "$1"; then
      echo "  vos modifications locales sont mises de côté : « git stash list », puis « git stash pop » pour les retrouver"
    else
      echo "✗ retour arrière impossible : Ely reste en ${bad:0:10}"
      return 1
    fi
  fi
  printf '{"bad": "%s", "prev": "%s", "reason": "%s"}' "$bad" "$1" "$2" > "$data/selfdev/rollback.json"
  deps_changed && install
  return 0
}

healthy() {  # attend jusqu'à 90 s que /api/health réponde ok
  local port="$1"
  for _ in $(seq 1 90); do
    if curl -fs "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then return 0; fi
    kill -0 "$CHILD" 2>/dev/null || return 1
    sleep 1
  done
  return 1
}

start() {
  [ -x "$PY" ] || install
  deps_changed && install
  export ELY_SUPERVISED=1
  local data port
  # l'environnement a priorité sur .env (comme côté Python)
  data="${ELY_DATA_DIR:-$(env_value ELY_DATA_DIR)}"; data="${data:-$ROOT/data}"
  port="$(elyport)"
  case "$data" in /*) ;; *) data="$ROOT/$data" ;; esac
  mkdir -p "$data/selfdev"
  trap 'kill "$CHILD" 2>/dev/null; wait "$CHILD" 2>/dev/null; exit 0' INT TERM
  echo "→ Ely $(version) démarre sur http://localhost:${port}"
  local watch="" crashes=0 started
  while true; do
    rm -f "$data/selfdev/restart_requested"
    "$PY" -m ely &
    CHILD=$!
    started=$(date +%s)
    if [ -f "$data/selfdev/pending_check" ]; then
      prev="$(cat "$data/selfdev/pending_check")"
      rm -f "$data/selfdev/pending_check"
      if healthy "$port"; then
        echo "✓ nouvelle version d'Ely en bonne santé"
        watch="$prev"; crashes=0  # encore surveillée : des plantages répétés ramènent à la version précédente
      else
        kill "$CHILD" 2>/dev/null; wait "$CHILD" 2>/dev/null
        rollback "$prev" "échec du contrôle de santé après mise à jour"
        continue
      fi
    fi
    wait "$CHILD"
    code=$?
    if [ -f "$data/selfdev/restart_requested" ]; then
      deploy="$data/selfdev/last_deploy.json"
      if [ -f "$deploy" ] && [ "$(json_get "$deploy" new)" = "$(git rev-parse HEAD)" ]; then
        json_get "$deploy" prev > "$data/selfdev/pending_check"
        mv "$deploy" "$deploy.done"
      fi
      deps_changed && install
      echo "↻ redémarrage d'Ely…"
      continue
    fi
    if [ "$code" -eq 3 ]; then  # port occupé : le message est déjà affiché, inutile de réessayer en boucle
      exit 3
    fi
    if [ "$code" -ne 0 ] && [ "$code" -ne 130 ] && [ "$code" -ne 143 ]; then
      if [ -n "$watch" ] && [ $(( $(date +%s) - started )) -lt 600 ]; then
        crashes=$((crashes + 1))
        if [ "$crashes" -ge 3 ] && rollback "$watch" "plantages répétés après mise à jour"; then
          watch=""; crashes=0
          continue
        fi
      else
        watch=""; crashes=0  # a tenu dix minutes : la nouvelle version est adoptée
      fi
      echo "✗ Ely s'est arrêtée (code $code), redémarrage dans 5 s…"
      sleep 5
      continue
    fi
    break
  done
}

update() {
  # fusion (et non rebase) : les commits qu'Ely a faits elle-même en s'améliorant sont conservés
  echo "→ récupération de la dernière version…"
  if ! git pull --no-rebase --no-edit; then
    git merge --abort 2>/dev/null
    echo "✗ mise à jour impossible (voir le message de git ci-dessus) : Ely reste en version $(version)."
    return 1
  fi
  deps_changed && install
  echo "✓ Ely est à jour : version $(version)"
  if [ "$(uname)" = "Darwin" ] && [ -f "$HOME/Library/LaunchAgents/fr.ely.agent.plist" ]; then
    launchctl kickstart -k "gui/$(id -u)/fr.ely.agent" && echo "↻ service Ely redémarré sur la nouvelle version"
  elif curl -fs "http://127.0.0.1:$(elyport)/api/health" >/dev/null 2>&1; then
    echo "⚠ Ely tourne encore avec l'ancienne version : arrêtez-la (Ctrl+C dans sa fenêtre), puis relancez ./ely.sh"
  else
    echo "→ lancez ./ely.sh"
  fi
}

service() {
  if [ "$(uname)" != "Darwin" ]; then
    echo "Sur Linux, crée un service systemd qui lance : $ROOT/ely.sh"; exit 1
  fi
  local plist="$HOME/Library/LaunchAgents/fr.ely.agent.plist"
  mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/data"
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>fr.ely.agent</string>
  <key>ProgramArguments</key><array><string>$ROOT/ely.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key><string>$ROOT/data/ely.log</string>
  <key>StandardErrorPath</key><string>$ROOT/data/ely.log</string>
</dict></plist>
PLIST
  launchctl unload "$plist" 2>/dev/null
  launchctl load "$plist"
  echo "✓ Ely démarrera automatiquement. Journal : $ROOT/data/ely.log"
}

case "${1:-start}" in
  install) install ;;
  update) update ;;
  start) start ;;
  service) install && service ;;
  unservice) launchctl unload "$HOME/Library/LaunchAgents/fr.ely.agent.plist" 2>/dev/null; rm -f "$HOME/Library/LaunchAgents/fr.ely.agent.plist"; echo "✓ retiré" ;;
  test) [ -x "$PY" ] || install; "$PY" -m pytest -q ;;
  *) echo "usage : ./ely.sh [start|update|install|service|unservice|test]"; exit 1 ;;
esac
