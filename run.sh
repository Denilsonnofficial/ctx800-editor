#!/usr/bin/env bash
# Atalho em inglês para o mesmo inicializador.
set -eu
cd "$(dirname "$(readlink -f "$0")")"
exec ./rodar.sh "$@"
