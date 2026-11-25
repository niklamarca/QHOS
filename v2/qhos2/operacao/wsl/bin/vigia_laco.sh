#!/usr/bin/env bash
# Laco da vigia (etapa 3, passo 9): espera cada captura comecar, roda `qhos2.vigia --captura <em andamento>` ate o
# fechamento e volta a esperar. So le a captura; escreve so em <data_root>/vigia/. Nao conecta, nao faz login e nao
# controla o agendador nem o coletor. Instalado em ~/qhos-v2/bin; roda num tmux proprio:
#     tmux new -d -s vigia ~/qhos-v2/bin/vigia_laco.sh
# Se a vigia sair com a captura ainda aberta (leitura falhou, erro), parte de novo ate MAX_PARTIDAS vezes por captura,
# para nao reler gigabytes em laco. Quando a captura encerra sem `<id>.fechamento.json` posterior ao manifesto, roda o
# fechamento dela uma vez (`--captura` numa captura encerrada so faz o fechamento): todo dia seguido termina relido.
set -u
PY=${QHOS2_PY:-$HOME/qhos-v2/.venv/bin/python}
export QHOS2_DATA_ROOT=${QHOS2_DATA_ROOT:-$HOME/qhos-v2-data}
ESPERA_S=${VIGIA_ESPERA_S:-30}
MAX_PARTIDAS=${VIGIA_MAX_PARTIDAS:-3}
PASTA="$QHOS2_DATA_ROOT/vigia"
LOG="$PASTA/laco.log"
umask 077
mkdir -p "$PASTA"

registrar() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"; }

em_andamento() {   # imprime a pasta da unica captura em andamento; sai 0 so se houver uma
  "$PY" -c 'import os, sys
from pathlib import Path
from qhos2.leitura import LeituraError, captura_em_andamento
try:
    print(captura_em_andamento(Path(os.environ["QHOS2_DATA_ROOT"]) / "capturas"))
except LeituraError as erro:
    print(erro)
    sys.exit(3)'
}

vigiar() {   # $1 = pasta da captura, $2 = rotulo do arquivo de saida
  local id saida rc
  id=$(basename "$1")
  saida="$PASTA/$id.laco-$2.out"
  "$PY" -m qhos2.vigia --captura "$1" > "$saida" 2>&1; rc=$?
  registrar "captura $id: vigia ($2) saiu rc=$rc ($(tail -n 1 "$saida" | cut -c1-200))"
}

registrar "inicio pid=$$ python=$PY dados=$QHOS2_DATA_ROOT espera=${ESPERA_S}s max_partidas=$MAX_PARTIDAS"
seguida=""; partidas=0; aviso=""
while true; do
  pasta=$(em_andamento 2>&1); rc=$?
  if [ $rc -ne 0 ]; then
    if [ -n "$seguida" ] && [ -f "$seguida/manifest.json" ]; then
      id=$(basename "$seguida")
      fechado="$PASTA/$id.fechamento.json"
      if [ ! -f "$fechado" ] || [ "$fechado" -ot "$seguida/manifest.json" ]; then   # ausente ou feito antes do fim
        registrar "captura $id: encerrada sem fechamento da vigia; fechamento tardio"
        vigiar "$seguida" fechamento
      fi
      seguida=""
    fi
    if [ "$pasta" != "nenhuma_captura_em_andamento" ] && [ "$pasta" != "$aviso" ]; then
      registrar "sem captura para seguir: $pasta"; aviso=$pasta
    fi
    sleep "$ESPERA_S"; continue
  fi
  aviso=""
  if [ "$pasta" = "$seguida" ]; then
    partidas=$((partidas + 1))
  else
    seguida=$pasta; partidas=1
  fi
  id=$(basename "$pasta")
  if [ "$partidas" -gt "$MAX_PARTIDAS" ]; then
    [ "$partidas" -eq $((MAX_PARTIDAS + 1)) ] && registrar "captura $id: vigia ja partiu $MAX_PARTIDAS vezes; espera o fim da captura"
    sleep "$ESPERA_S"; continue
  fi
  registrar "captura $id: vigia partida $partidas"
  vigiar "$pasta" "partida-$partidas"
  sleep "$ESPERA_S"
done
