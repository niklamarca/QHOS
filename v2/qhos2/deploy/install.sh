#!/usr/bin/env bash
# Instala em um destino novo. Nao altera servicos nem inicia conexoes de mercado.
set -euo pipefail
umask 077
SOURCE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_ROOT="${1:-$HOME/qhos-v2}"
if [[ "$TARGET_ROOT" != /* || -e "$TARGET_ROOT" ]]; then
  echo 'PARE: informe uma pasta absoluta que ainda nao existe.' >&2
  exit 2
fi
python3 -m venv "$TARGET_ROOT/.venv"
"$TARGET_ROOT/.venv/bin/python" -m pip install --disable-pip-version-check -c "$SOURCE_ROOT/requirements-linux.lock" "$SOURCE_ROOT[brain]"
"$TARGET_ROOT/.venv/bin/python" -m pip check
"$TARGET_ROOT/.venv/bin/python" -m qhos2.diagnostico
echo 'Instalacao concluida. Nenhum servico ou captura foi iniciado.'
