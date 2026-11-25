"""O agendador do ES volta sozinho depois de queda de conexao ou reinicio, sem mudar o codigo congelado.

O codigo congelado (qhos2.operacao, regra R6) deixa a guarda do ES em QUARENTENA depois de qualquer encerramento sem
logout confirmado, e o agendador pula todos os pregoes seguintes ate alguem liberar a mao. Aqui essa liberacao e
feita sozinha, com o mesmo `GuardaCaptura.liberar` do codigo congelado (que guarda o historico em liberacao-*.json),
so quando TODAS valem:
  * o agendador NAO parou por login recusado ou logout forcado (estado.json dele em QUARENTENA com esse motivo);
  * nenhuma captura esta em andamento (o lock da guarda e da captura viva: se estiver preso, nada e feito);
  * o encerramento tem pelo menos ESPERA_S (o servidor ja descartou a sessao morta).
Roda no lancador do agendador (antes de cada partida) e no timer diario das 07:30 NY (antes do pregao).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from qhos2.operacao import GuardaCaptura, OperacaoError, estado_padrao

ESPERA_S = 300.0
FALHAS_DE_LOGIN = frozenset({"login_recusado", "logout_forcado"})


def liberar_se_queda(raiz_guarda: Path, estado_agenda: Path, *, espera_s: float = ESPERA_S,
                     agora_ns=time.time_ns) -> str:
    try:
        agenda = json.loads(Path(estado_agenda).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        agenda = {}
    if agenda.get("estado") == "QUARENTENA" and agenda.get("motivo") in FALHAS_DE_LOGIN:
        return f"quarentena_por_{agenda['motivo']}"
    try:
        with GuardaCaptura(Path(raiz_guarda)) as guarda:
            estado = dict(guarda.estado)
            if estado.get("estado") == "LIVRE":
                return "livre"
            referencia = estado.get("fim_ns") or estado.get("inicio_ns") or 0
            if (agora_ns() - referencia) / 1e9 < espera_s:
                return "aguardando_a_espera"
            guarda.liberar(estado["geracao"])
            return "liberada"
    except OperacaoError as erro:
        return str(erro)  # captura_ja_em_execucao: o ES esta capturando, nada a fazer
    except (OSError, KeyError) as erro:
        return type(erro).__name__


def main(argv=None) -> int:
    from qhos2.config import Settings
    estado_agenda = Settings.from_environ().data_root / "agenda" / "estado.json"
    resultado = liberar_se_queda(estado_padrao(), estado_agenda)
    print(f"guarda do ES: {resultado}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
