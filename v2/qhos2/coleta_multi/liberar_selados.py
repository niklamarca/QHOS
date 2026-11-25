"""Move os pregoes da pasta selada para DataSet\\<dia>\\ a partir da data da pasta (roda todo dia pelo timer).

Antes da data nao faz nada. Na data (sabado 21/11/2026, 07:30 de NY) e em qualquer dia depois, cada dia selado vai
para <dia>/ com o SHA-256 conferido no destino, e o INDEX.json e o SHA256SUMS.txt do dia sao reescritos. Nao
conecta e nao toca em captura nenhuma: so mexe dentro do DataSet.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from qhos2 import agenda

from coleta_multi import coletor, fechamento


def main(argv=None) -> int:
    destino = Path(argv[0]) if argv else coletor.DATASET_PADRAO
    hoje = datetime.now(agenda.NY).date()
    r = fechamento.liberar_selados(destino, destino / coletor.SELADO_PASTA, hoje, coletor.SELADO_LIBERA_EM)
    if hoje < coletor.SELADO_LIBERA_EM:
        print(f"pasta selada fica fechada ate {coletor.SELADO_LIBERA_EM}")
    else:
        print(f"pasta selada: {dict(r) or 'nada a mover'}")
    return 0 if all(v == "liberado" for _, v in r) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
