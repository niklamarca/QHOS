#!/usr/bin/env bash
# Troca a versao instalada em ~/qhos-v2 pelo commit pedido, pelo mesmo caminho da 0.2.3 e da 0.2.5: wheel construido de
# um clone limpo, `pip --no-deps --force-reinstall` no venv existente, guardado em ~/qhos-v2/releases/<commit>/ com
# previous_release.json, install.log e diagnostico.json; RELEASE.json atualizado.
#     instalar_release.sh <commit> <versao> <identidade_codigo esperada>
# Duas travas a mais (passo 10 da etapa 3): o `identidade_codigo` do clone tem de ser o congelado ANTES do wheel, e os
# arquivos .py instalados (fora `terminal/`, que a instalacao acrescenta de outra pasta) tem de dar o mesmo hash DEPOIS.
# Nao reinicia servico nem o agendador do tmux: processos que ja rodam ficam com o codigo que carregaram; o coletor que
# o supervisor abre a cada captura e a vigia ja nascem com o novo.
set -euo pipefail
umask 077
COMMIT=${1:?commit}; VERSAO=${2:?versao}; ESPERADO=${3:?identidade_codigo esperada}
ORIGEM=${QHOS2_ORIGEM:-/mnt/c/Users/usuario/OneDrive/Desktop/QHOSv2}
PREFIX=${QHOS2_PREFIX:-/home/usuario/qhos-v2}
PY=$PREFIX/.venv/bin/python
export PYTHONDONTWRITEBYTECODE=1

identidade() {   # $1 = pasta do pacote qhos2; o mesmo calculo de pesquisa.identidade_codigo, sem a pasta terminal/
  "$PY" -S - "$1" <<'PYEOF'
import hashlib, json, pathlib, sys
raiz = pathlib.Path(sys.argv[1])
arquivos = {p.relative_to(raiz).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(raiz.rglob("*.py")) if p.relative_to(raiz).parts[0] != "terminal"}
texto = json.dumps(arquivos, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
print(hashlib.sha256(texto.encode()).hexdigest(), len(arquivos))
PYEOF
}

T=$(mktemp -d /tmp/qhos-release.XXXXXX)
git clone -q --no-checkout "$ORIGEM" "$T/repo"
git -C "$T/repo" checkout -q "$COMMIT"
COMPLETO=$(git -C "$T/repo" rev-parse HEAD)
[[ "$COMPLETO" == "$COMMIT"* ]] || { echo "PARE: HEAD do clone e $COMPLETO, pedido $COMMIT"; exit 2; }
[[ -z "$(git -C "$T/repo" status --porcelain)" ]] || { echo "PARE: clone sujo"; exit 2; }
grep -q "^version = \"$VERSAO\"$" "$T/repo/qhos2/pyproject.toml" || { echo "PARE: pyproject nao e a $VERSAO"; exit 2; }

read -r NO_CLONE N <<<"$(identidade "$T/repo/qhos2/src/qhos2")"
OFICIAL=$(cd "$T" && PYTHONPATH="$T/repo/qhos2/src" "$PY" -c 'import qhos2.pesquisa as p; print(p.identidade_codigo()["sha256"])')
echo "identidade_codigo do clone: $OFICIAL ($N arquivos); recalculado: $NO_CLONE"
[[ "$OFICIAL" == "$ESPERADO" && "$NO_CLONE" == "$ESPERADO" ]] || { echo "PARE: o codigo do clone nao e o congelado ($ESPERADO)"; exit 2; }

echo "== wheel =="
"$PY" -m pip wheel --disable-pip-version-check --no-deps -w "$T/dist" "$T/repo/qhos2" >"$T/wheel.log" 2>&1 \
  || { tail -20 "$T/wheel.log"; echo "PARE: wheel falhou"; exit 2; }
WHEEL=$(ls "$T"/dist/qhos2-"$VERSAO"-*.whl)
SHA=$(sha256sum "$WHEEL" | cut -d' ' -f1)
echo "wheel: $(basename "$WHEEL") sha256=$SHA"

DEST=$PREFIX/releases/$COMPLETO
[[ -e "$DEST" ]] && { echo "PARE: $DEST ja existe"; exit 2; }
mkdir -p "$DEST"
cp "$WHEEL" "$DEST/"
cp "$PREFIX/RELEASE.json" "$DEST/previous_release.json"
ANTES=$("$PY" -c 'import qhos2; print(qhos2.__version__)')

echo "== instalacao (antes: $ANTES) =="
"$PY" -m pip install --disable-pip-version-check --no-deps --force-reinstall "$DEST/$(basename "$WHEEL")" >"$DEST/install.log" 2>&1 \
  || { tail -20 "$DEST/install.log"; echo "PARE: pip install falhou"; exit 2; }
"$PY" -m pip check >>"$DEST/install.log" 2>&1 || echo "aviso: pip check reclamou (ver install.log)"
DEPOIS=$(cd /tmp && "$PY" -c 'import qhos2; print(qhos2.__version__)')
[[ "$DEPOIS" == "$VERSAO" ]] || { echo "PARE: versao instalada e $DEPOIS"; exit 2; }
INSTALADO=$(cd /tmp && "$PY" -c 'import os, qhos2; print(os.path.dirname(qhos2.__file__))')
read -r NO_PACOTE N <<<"$(identidade "$INSTALADO")"
echo "instalado em $INSTALADO: $NO_PACOTE ($N arquivos, sem terminal/)"
[[ "$NO_PACOTE" == "$ESPERADO" ]] || { echo "PARE: os arquivos instalados nao sao os congelados; desfaca com o rollback abaixo"; exit 2; }
(cd /tmp && "$PY" -m qhos2.diagnostico) >"$DEST/diagnostico.json" 2>"$DEST/diagnostico.err" \
  || echo "aviso: diagnostico saiu com erro (ver diagnostico.err)"

"$PY" - "$PREFIX" "$COMPLETO" "$SHA" "$DEST" "$VERSAO" "$ESPERADO" <<'PYEOF'
import datetime, json, pathlib, sys
prefix, commit, sha, dest, versao, identidade = sys.argv[1:7]
antigo = json.loads(pathlib.Path(dest, "previous_release.json").read_text())
novo = {"schema": antigo.get("schema", "qhos2.deployment.v1"), "commit": commit, "wheel_sha256": sha,
        "installed_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "prefix": prefix,
        "python": f"{prefix}/.venv/bin/python", "services_started": antigo.get("services_started", True),
        "previous_commit": antigo.get("commit"), "version": versao, "identidade_codigo": identidade}
pathlib.Path(prefix, "RELEASE.json").write_text(json.dumps(novo, indent=2) + "\n")
print(json.dumps(novo, indent=2))
PYEOF

ANTERIOR=$("$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["commit"])' "$DEST/previous_release.json")
echo "instalado: $VERSAO ($COMPLETO); anterior guardado em $DEST/previous_release.json"
echo "rollback: $PY -m pip install --no-deps --force-reinstall $PREFIX/releases/$ANTERIOR/qhos2-*.whl && cp $DEST/previous_release.json $PREFIX/RELEASE.json"
rm -rf "$T"
