#!/usr/bin/env bash
# Grava usuario e senha da Rithmic Paper UMA vez, para os servicos subirem sozinhos depois de reiniciar.
# Roda so no terminal do autor. A senha nao aparece na tela e nao vai a log, argumento nem variavel exportada.
# Arquivo: ~/.config/qhos2/rithmic-paper.cred (0600, so o seu usuario Linux le). Quem controla a sua conta do
# Windows tambem enxerga o disco do WSL: e uma protecao contra leitura casual, nao um cofre.
set -euo pipefail
umask 077
destino="$HOME/.config/qhos2/rithmic-paper.cred"
mkdir -p "$(dirname "$destino")"
IFS= read -r -p "Usuario Rithmic Paper: " usuario
IFS= read -r -s -p "Senha (nao aparece na tela): " senha; echo
IFS= read -r -s -p "Senha de novo: " confere; echo
if [ -z "$usuario" ] || [ -z "$senha" ] || [ "$senha" != "$confere" ]; then
  unset senha confere
  echo "nada foi gravado: usuario vazio ou senhas diferentes"
  exit 2
fi
tmp=$(mktemp "$destino.XXXXXX")
printf '%s\n%s\n' "$usuario" "$senha" > "$tmp"
unset senha confere
chmod 600 "$tmp"
mv -f "$tmp" "$destino"
echo "gravado em $destino (permissao $(stat -c %a "$destino")); a senha nao foi mostrada"
