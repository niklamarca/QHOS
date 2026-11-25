"""Teto local de quadros por segundo: quanto o caminho de recepcao da V2 aguenta nesta maquina.

Repassa os quadros brutos de uma captura real, em velocidade maxima, pelo mesmo caminho que a sessao ao vivo usa
por quadro (decodificar a mensagem + gravar: json, base64, write, flush, fsync a cada 1000, checagem de disco a
cada 1000). Sem rede. Grava numa raiz temporaria e apaga no fim. So leitura na captura de origem.

Uso: python teto_local.py <pasta da captura> <raiz temporaria, absoluta, fora de git> [max_quadros=1500000]
Saida: quadros/s do caminho completo e do gravador sozinho, p50/p99/max do tempo por quadro, e quantos quadros
passaram de 10 ms (sao as pausas de fsync/disco que seguram a fila).
"""
import base64
import json
import shutil
import statistics
import sys
import time
from pathlib import Path

from qhos2 import rprotocol as p
from qhos2.gravador import Gravador

IDENT = {"system_name": "Rithmic Paper Trading", "requested_symbol": "ESZ6", "exchange": "CME"}


def ler(pasta, maximo):
    with open(Path(pasta) / "market_frames.jsonl", "rb") as f:
        for n, linha in enumerate(f):
            if n >= maximo:
                break
            r = json.loads(linha)
            yield base64.b64decode(r["protobuf_base64"]), r["template_id"], r["received_ns"]


def medir(nome, quadros, passo):
    tempos = []
    t0 = time.perf_counter()
    for q in quadros:
        a = time.perf_counter()
        passo(*q)
        tempos.append(time.perf_counter() - a)
    total = time.perf_counter() - t0
    tempos.sort()
    n = len(tempos)
    print(f"{nome}: quadros={n} segundos={total:.1f} quadros_por_s={n / total:.0f} "
          f"por_quadro_ms p50={tempos[n // 2] * 1e3:.3f} p99={tempos[int(n * 0.99)] * 1e3:.3f} max={tempos[-1] * 1e3:.1f} "
          f"acima_de_10ms={sum(1 for t in tempos if t > 0.01)}")
    return n / total


def main(pasta, raiz, maximo):
    raiz = Path(raiz)
    if raiz.exists():
        sys.exit(f"PARE: {raiz} ja existe")
    try:
        quadros = list(ler(pasta, maximo))  # tudo em memoria para medir so o caminho de recepcao
        print(f"quadros carregados: {len(quadros)} bytes_protobuf={sum(len(d) for d, _, _ in quadros)}")

        g1 = Gravador(raiz, identidade=IDENT, max_bytes=8 * 1024 ** 3)
        medir("gravador sozinho", quadros, lambda d, t, ns: g1(d, t, ns))
        g1.fechar({"estado": "TESTE"}, fechamento_normal=True)

        g2 = Gravador(raiz, identidade=IDENT, max_bytes=8 * 1024 ** 3)
        def completo(d, t, ns):
            m = p.decodificar(d)
            if m.campos.get("rp_code"):
                p.sucesso(m)
            g2(d, m.template_id, ns)
        medir("decodificar + gravador (caminho da sessao)", quadros, completo)
        g2.fechar({"estado": "TESTE"}, fechamento_normal=True)

        medir("decodificar sozinho", quadros, lambda d, t, ns: p.decodificar(d))
    finally:
        shutil.rmtree(raiz, ignore_errors=True)
        print("raiz temporaria apagada:", raiz)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1_500_000)
