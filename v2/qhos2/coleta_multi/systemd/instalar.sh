#!/usr/bin/env bash
# Instala as 3 unidades de usuario e as HABILITA para o proximo boot. Nao inicia nada: o agendador, o coletor e a
# vigia que ja rodam no tmux continuam como estao ate o PC/WSL reiniciar (os locks impedem processo duplicado).
set -euo pipefail
aqui=$(cd "$(dirname "$0")" && pwd)
alvo="$HOME/.config/systemd/user"
mkdir -p "$alvo"
unidades="qhos2-agenda.service qhos2-coleta-multi.service qhos2-vigia.service qhos2-garantir.service qhos2-garantir.timer"
for u in $unidades; do
  install -m 0644 "$aqui/$u" "$alvo/$u"
done
systemctl --user daemon-reload
(cd "$alvo" && systemd-analyze --user verify $unidades)
systemctl --user enable qhos2-agenda.service qhos2-coleta-multi.service qhos2-vigia.service qhos2-garantir.timer
systemctl --user is-enabled qhos2-agenda.service qhos2-coleta-multi.service qhos2-vigia.service qhos2-garantir.timer
# O timer e so um relogio (nao inicia nada agora); a primeira execucao e a proxima 07:30 de NY.
systemctl --user start qhos2-garantir.timer
systemctl --user list-timers qhos2-garantir.timer --no-pager | head -3
if [ -f "$HOME/.config/qhos2/rithmic-paper.cred" ]; then
  echo "credencial presente ($(stat -c %a "$HOME/.config/qhos2/rithmic-paper.cred"))"
else
  echo "ATENCAO: sem ~/.config/qhos2/rithmic-paper.cred os servicos com login NAO sobem no boot (rode guardar_credencial.sh)"
fi
