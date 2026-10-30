#!/usr/bin/env bash
# Editor de Ritmos CT-X800 — inicia a interface gráfica.
set -eu
cd "$(dirname "$(readlink -f "$0")")"
exec python3 main.py "$@"
