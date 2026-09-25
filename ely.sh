#!/usr/bin/env bash
# Ely — installation et lancement (macOS / Linux)
#   ./ely.sh            installe si besoin puis lance Ely (supervisé)
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

env_value() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*//' | tr -d '"' | tr -d "'" ; }

install() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "→ installation de uv (gestionnaire Python)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  fi
  [ -x "$PY" ] || uv venv .venv --python 3.12
  echo "→ dépendances…"
  uv pip install --python "$PY" -q -e ".[dev]"
  if [ -z "$(env_value ELY_BROWSER_CHANNEL)" ] && [ -z "$(env_value ELY_BROWSER_EXECUTABLE)" ]; then
    echo "→ navigateur Chromium pour l'agent…"
    "$PY" -m playwright install chromium >/dev/null
  fi
  if [ ! -f .env ]; then
    cp .env.example .env
    echo "→ fichier .env créé : ajoute tes clés d'API (ou lance simplement LM Studio)."
  fi
  sha1sum pyproject.toml 2>/dev/null | cut -d' ' -f1 > .venv/.deps || shasum pyproject.toml | cut -d' ' -f1 > .venv/.deps
}

deps_changed() {
  local now
  now=$(sha1sum pyproject.toml 2>/dev/null | cut -d' ' -f1 || shasum pyproject.toml | cut -d' ' -f1)
  [ "$now" != "$(cat .venv/.deps 2>/dev/null)" ]
}

json_get() { "$PY" -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" "$1" "$2" 2>/dev/null; }

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
  data="$(env_value ELY_DATA_DIR)"; data="${data:-$ROOT/data}"
  port="$(env_value ELY_PORT)"; port="${port:-8000}"
  mkdir -p "$data/selfdev"
  trap 'kill "$CHILD" 2>/dev/null; wait "$CHILD" 2>/dev/null; exit 0' INT TERM
  echo "→ Ely démarre sur http://localhost:${port}"
  while true; do
    rm -f "$data/selfdev/restart_requested"
    "$PY" -m ely &
    CHILD=$!
    if [ -f "$data/selfdev/pending_check" ]; then
      prev="$(cat "$data/selfdev/pending_check")"
      rm -f "$data/selfdev/pending_check"
      if healthy "$port"; then
        echo "✓ nouvelle version d'Ely en bonne santé"
      else
        bad="$(git rev-parse HEAD)"
        echo "✗ la nouvelle version ne démarre pas : retour à ${prev:0:10}"
        kill "$CHILD" 2>/dev/null; wait "$CHILD" 2>/dev/null
        git reset --keep "$prev" || git reset --hard "$prev"
        printf '{"bad": "%s", "prev": "%s", "reason": "échec du contrôle de santé après mise à jour"}' "$bad" "$prev" > "$data/selfdev/rollback.json"
        deps_changed && install
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
    if [ "$code" -ne 0 ] && [ "$code" -ne 130 ] && [ "$code" -ne 143 ]; then
      echo "✗ Ely s'est arrêtée (code $code), redémarrage dans 5 s…"
      sleep 5
      continue
    fi
    break
  done
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
  start) start ;;
  service) install && service ;;
  unservice) launchctl unload "$HOME/Library/LaunchAgents/fr.ely.agent.plist" 2>/dev/null; rm -f "$HOME/Library/LaunchAgents/fr.ely.agent.plist"; echo "✓ retiré" ;;
  test) [ -x "$PY" ] || install; "$PY" -m pytest -q ;;
  *) echo "usage : ./ely.sh [start|install|service|unservice|test]"; exit 1 ;;
esac
