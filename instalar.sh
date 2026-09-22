#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if ! command -v python3.12 >/dev/null 2>&1; then
    echo 'Install Python 3.12 and python3.12-venv using your system package manager. Then run ./instalar.sh again.'
    exit 1
fi
[ -x .venv/bin/python ] || python3.12 -m venv .venv
.venv/bin/python instalar.py
if [ "${1:-}" != '--solo-dependencias' ]; then
    .venv/bin/python agente.py --configurar
fi
