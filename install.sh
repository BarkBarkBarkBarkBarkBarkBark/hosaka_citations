#!/usr/bin/env bash
# Portable installer for Hosaka Citations (extension, local web app, cite CLI, and hosted wall).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="${HOSAKA_REPO:-$(cd "$ROOT/../Hosaka" && pwd)}"
GATEWAY="${HOSAKA_GATEWAY_REPO:-$(cd "$ROOT/../hosaka-gateway" && pwd)}"
PREFIX="${HOSAKA_PREFIX:-$HOME/.local}"
DEV=0
LINK=1
HOSTED_WALL=1

while (( $# )); do
  case "$1" in
    --dev) DEV=1 ;;
    --no-link) LINK=0 ;;
    --no-hosted-wall) HOSTED_WALL=0 ;;
    --prefix) PREFIX="$2"; shift ;;
    --core) CORE="$(cd "$2" && pwd)"; shift ;;
    --gateway) GATEWAY="$(cd "$2" && pwd)"; shift ;;
    --help|-h)
      echo "Usage: bash install.sh [--dev] [--no-link] [--no-hosted-wall] [--prefix PATH] [--core PATH] [--gateway PATH]"
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

PYTHON="${PYTHON:-$CORE/.hosakavenv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="$CORE/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="python3"

INSTALL_ARGS=(--core "$CORE" --prefix "$PREFIX" --citations "$ROOT")
(( ! LINK )) || INSTALL_ARGS+=(--link)
(( ! DEV )) || INSTALL_ARGS+=(--dev)
"$PYTHON" "$GATEWAY/install.py" "${INSTALL_ARGS[@]}"

if (( HOSTED_WALL )) && [[ -d "$ROOT/apps/hosted-wall" ]]; then
  WALL="$ROOT/apps/hosted-wall"
  if [[ -d "$ROOT/../citations-gate/.vercel" && ! -d "$WALL/.vercel" ]]; then
    cp -R "$ROOT/../citations-gate/.vercel" "$WALL/.vercel"
    echo "Migrated .vercel project link into apps/hosted-wall/.vercel"
  fi
  if [[ -f "$WALL/.vercel/project.json" && ! -f "$ROOT/.vercel/project.json" ]]; then
    mkdir -p "$ROOT/.vercel"
    cp "$WALL/.vercel/project.json" "$ROOT/.vercel/project.json"
  fi
  "$PYTHON" - "$ROOT" "$WALL" <<'PY'
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

root = Path(sys.argv[1])
wall = Path(sys.argv[2])
target = wall / ".env.local"
hosaka_dir = Path(os.environ.get("HOSAKA_DATA_DIR", str(Path.home() / ".hosaka")))
auth_file = hosaka_dir / "extension-data/citations/supabase-auth.json"
if target.exists():
    text = target.read_text(encoding="utf-8")
    updated = text.replace("extension-data/halloween-citations/", "extension-data/citations/")
    if updated != text:
        target.write_text(updated, encoding="utf-8")
if not target.exists():
    values = {}
    legacy_env = root.parent / "citations-gate/.env.local"
    if legacy_env.is_file():
        for line in legacy_env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    gate_env = hosaka_dir / "citations-gate.env"
    if gate_env.is_file():
        for line in gate_env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                values.setdefault(k.strip(), v.strip())
    cfg_file = hosaka_dir / "extension-data/citations/citations-config.json"
    if cfg_file.is_file():
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        if cfg.get("supabase_url"):
            values.setdefault("SUPABASE_URL", cfg["supabase_url"])
        if cfg.get("publishable_key"):
            values.setdefault("SUPABASE_PUBLISHABLE_KEY", cfg["publishable_key"])
        if cfg.get("workspace_id"):
            values.setdefault("SUPABASE_WORKSPACE_ID", cfg["workspace_id"])
    if auth_file.is_file():
        values.setdefault("SUPABASE_AUTH_FILE", str(auth_file))
    if "GATE_SECRET" not in values:
        values["GATE_SECRET"] = values.get("CITATIONS_GATE_TOKEN") or secrets.token_urlsafe(32)
    values.setdefault("GATE_ACCESS_CODE", "pumpkin")
    values.setdefault("SUPABASE_SESSION_VERSION", "v2")
    if values.get("SUPABASE_URL") and values.get("SUPABASE_PUBLISHABLE_KEY") and values.get("SUPABASE_WORKSPACE_ID"):
        fd, tmp = tempfile.mkstemp(prefix=".env-local-", dir=wall)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                for k, v in values.items():
                    stream.write(f"{k}={v}\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
            print("Migrated private apps/hosted-wall/.env.local for local dev.")
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
PY
  if command -v npm >/dev/null 2>&1 && [[ ! -d "$WALL/node_modules" ]]; then
    (cd "$WALL" && npm ci)
  fi
fi
