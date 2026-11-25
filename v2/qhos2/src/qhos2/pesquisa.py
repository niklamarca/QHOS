"""Registro local de pesquisa: hipotese, fonte, codigo, dados e reproducoes.

SQLite fornece transacoes entre processos; SHA-256 identifica os bytes, nao a
verdade de uma afirmacao. O relogio local nao constitui carimbo de tempo externo.
Nenhuma funcao envia ordens, inicia captura ou usa textos como instrucoes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import platform
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from qhos2 import avaliador as av
from qhos2.config import Settings, validar_data_root

ESQUEMA = "qhos2.pesquisa.v1"
PROTOCOLO = "desequilibrio_esz6_v1"
# Regra 14 do plano, imposta no codigo (N2, 28/10): no maximo MAX_TESTES planos registrados ate PRAZO_FINAL, cada um
# com alfa ALFA_TOTAL/MAX_TESTES, e hipotese nova so depois de a anterior ter avaliacao registrada.
ALFA_TOTAL = 0.05
MAX_TESTES = 3
ALFA_POR_TESTE = ALFA_TOTAL / MAX_TESTES
PRAZO_FINAL = date(2027, 4, 30)


class PesquisaError(ValueError):
    """Falha com codigo estatico; nunca ecoa texto privado ou dado de mercado."""


def _json(valor):
    try:
        return json.dumps(valor, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise PesquisaError("registro_nao_serializavel") from None


def _hash(valor):
    return hashlib.sha256(_json(valor).encode()).hexdigest()


@contextmanager
def _abrir_regular(path):
    """Recusa links e arquivos especiais antes de qualquer leitura bloqueante."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise PesquisaError("arquivo_nao_regular")
        yield stream


def _arquivo(path):
    digest = hashlib.sha256()
    with _abrir_regular(path) as stream:
        for bloco in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def identidade_codigo():
    """Inclui todos os fontes Python do pacote executado; independe de Git/V1."""
    raiz = Path(__file__).parent
    arquivos = {p.relative_to(raiz).as_posix(): _arquivo(p) for p in sorted(raiz.rglob("*.py"))}
    return {"sha256": _hash(arquivos), "arquivos": arquivos,
            "python": platform.python_version(), "implementacao": platform.python_implementation()}


def _agora(agora=None):
    agora = datetime.now(timezone.utc) if agora is None else agora
    if not isinstance(agora, datetime) or agora.tzinfo is None:
        raise PesquisaError("instante_sem_fuso")
    return agora.astimezone(timezone.utc)


def _texto(valor, limite):
    if not isinstance(valor, str) or not valor.strip() or len(valor) > limite:
        raise PesquisaError("texto_invalido")
    return valor


def _janela(valor):
    try:
        if not isinstance(valor, (list, tuple)) or len(valor) != 2:
            raise ValueError()
        inicio, fim = (date.fromisoformat(x) for x in valor)
        if inicio > fim:
            raise ValueError()
        return inicio, fim
    except (ValueError, TypeError):
        raise PesquisaError("janela_invalida") from None


@contextmanager
def _banco(raiz):
    # O mesmo guard do armazenamento: pesquisa/textos/dados nunca dentro do Git.
    pasta = validar_data_root(raiz) / "pesquisa"
    pasta.mkdir(parents=True, exist_ok=True, mode=0o700)
    caminho = pasta / "registro.sqlite3"
    if pasta.is_symlink() or caminho.is_symlink():
        raise PesquisaError("registro_link_recusado")
    conn = sqlite3.connect(caminho, timeout=5)
    try:
        caminho.chmod(0o600)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        conn.executescript("""
          CREATE TABLE IF NOT EXISTS objetos(id TEXT PRIMARY KEY, tipo TEXT NOT NULL, corpo TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS execucoes(n INTEGER PRIMARY KEY, plano TEXT NOT NULL REFERENCES objetos(id),
              corpo TEXT NOT NULL, sha256 TEXT NOT NULL, anterior TEXT NOT NULL);
          CREATE TRIGGER IF NOT EXISTS objeto_sem_update BEFORE UPDATE ON objetos BEGIN SELECT RAISE(ABORT,'imutavel'); END;
          CREATE TRIGGER IF NOT EXISTS objeto_sem_delete BEFORE DELETE ON objetos BEGIN SELECT RAISE(ABORT,'imutavel'); END;
          CREATE TRIGGER IF NOT EXISTS execucao_sem_update BEFORE UPDATE ON execucoes BEGIN SELECT RAISE(ABORT,'imutavel'); END;
          CREATE TRIGGER IF NOT EXISTS execucao_sem_delete BEFORE DELETE ON execucoes BEGIN SELECT RAISE(ABORT,'imutavel'); END;
        """)
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _guardar(conn, tipo, corpo):
    chave, texto = _hash(corpo), _json(corpo)
    anterior = conn.execute("SELECT tipo,corpo FROM objetos WHERE id=?", (chave,)).fetchone()
    if anterior is not None and anterior != (tipo, texto):
        raise PesquisaError("objeto_adulterado")
    conn.execute("INSERT OR IGNORE INTO objetos VALUES(?,?,?)", (chave, tipo, texto))
    return chave


def _ler(conn, chave, tipo):
    registro = conn.execute("SELECT tipo,corpo FROM objetos WHERE id=?", (chave,)).fetchone()
    if registro is None or registro[0] != tipo:
        raise PesquisaError("objeto_ausente_ou_tipo_incorreto")
    try:
        corpo = json.loads(registro[1])
    except (ValueError, TypeError):
        raise PesquisaError("objeto_adulterado") from None
    if _hash(corpo) != chave:
        raise PesquisaError("objeto_adulterado")
    return corpo


def registrar_texto(raiz, *, texto, origem, classe="AFIRMACAO", agora=None):
    """Texto preservado localmente, sempre NAO_VERIFICADO ate evidencia independente.

    Origem e classificacao sao declaracoes do autor, nao verificacoes automaticas.
    O registro nao acessa URLs nem arquivos citados no texto.
    """
    if classe not in {"AFIRMACAO", "HIPOTESE", "REFERENCIA", "OBSERVACAO"}:
        raise PesquisaError("classe_de_texto_invalida")
    corpo = {"esquema": ESQUEMA, "classe": classe, "texto": _texto(texto, 16000),
             "origem": _texto(origem, 2048), "status": "NAO_VERIFICADO",
             "registrado_em": _agora(agora).isoformat(), "pode_executar": False}
    with _banco(raiz) as conn:
        return _guardar(conn, "texto", corpo)


def registrar_plano(raiz, proposta, *, agora=None):
    """Congela antes do teste. O orcamento nao e campo do plano: e fixo no codigo (regra 14).

    No maximo MAX_TESTES planos no registro, cada um com alfa ALFA_POR_TESTE (Bonferroni), janela de teste ate
    PRAZO_FINAL e registro ate essa data; hipotese nova so depois de a anterior ter avaliacao registrada. Repetir
    um plano identico (mesma proposta, mesmo codigo) devolve a mesma chave e nao gasta teste; qualquer diferenca,
    inclusive no codigo, e hipotese nova. Isso controla somente os testes declarados; nao descobre experimentos
    ocultos.
    """
    agora = _agora(agora)
    campos = {"pergunta", "refutacao", "familia", "parametros", "calibracao", "teste", "fontes"}
    if not isinstance(proposta, dict) or set(proposta) != campos:
        raise PesquisaError("campos_do_plano_invalidos")
    # Compare exatamente a representacao congelada, inclusive listas/tuplas.
    proposta = json.loads(_json(proposta))
    for k in ("pergunta", "refutacao", "familia"):
        _texto(proposta[k], 2048 if k != "familia" else 120)
    calibracao, teste = _janela(proposta["calibracao"]), _janela(proposta["teste"])
    if calibracao[1] >= teste[0]:
        raise PesquisaError("calibracao_sobrepoe_teste")
    if agora.date() > PRAZO_FINAL:
        raise PesquisaError("prazo_dos_testes_encerrado")
    if teste[1] > PRAZO_FINAL:
        raise PesquisaError("teste_depois_do_prazo")
    # Um registro deve acontecer depois da calibracao e antes da janela de teste.
    if not calibracao[1] < agora.date() < teste[0]:
        raise PesquisaError("registro_fora_da_janela_pre_teste")
    try:
        pars = dict(proposta["parametros"])
        pars["fomc"] = tuple(date.fromisoformat(d) for d in pars.get("fomc", []))
        par = av.Parametros(**pars)
        av.validar_parametros(par)
    except (ValueError, TypeError):
        raise PesquisaError("parametros_invalidos") from None
    if not math.isclose(par.alfa, ALFA_POR_TESTE, rel_tol=0, abs_tol=1e-12):
        raise PesquisaError("alfa_diferente_do_orcamento")
    if not isinstance(proposta["fontes"], list) or len(proposta["fontes"]) > 100:
        raise PesquisaError("fontes_invalidas")
    corpo = {"esquema": ESQUEMA, "protocolo": PROTOCOLO, "proposta": proposta,
             "orcamento": {"alfa_total": ALFA_TOTAL, "max_testes": MAX_TESTES, "prazo": PRAZO_FINAL.isoformat()},
             "codigo": identidade_codigo(), "registrado_em": agora.isoformat(),
             "relogio": "LOCAL_NAO_ATESTADO", "pode_executar": False}
    with _banco(raiz) as conn:
        for chave in proposta["fontes"]:
            _ler(conn, chave, "texto")
        anteriores = []
        for chave, in conn.execute("SELECT id FROM objetos WHERE tipo='plano'"):
            antigo = _ler(conn, chave, "plano")
            if antigo["proposta"] == proposta and antigo["codigo"] == corpo["codigo"]:
                return chave  # Repetir o registro nao cria outro teste.
            anteriores.append(chave)
        if len(anteriores) >= MAX_TESTES:
            raise PesquisaError("orcamento_de_testes_esgotado")
        avaliados = {plano for plano, in conn.execute("SELECT DISTINCT plano FROM execucoes")}
        if any(chave not in avaliados for chave in anteriores):
            raise PesquisaError("teste_anterior_sem_resultado")
        return _guardar(conn, "plano", corpo)


def _identificar_capturas(pastas):
    saida, vistos = [], set()
    for pasta in pastas:
        pasta = Path(pasta)
        manifesto = pasta / "manifest.json"
        quadros = pasta / "market_frames.jsonl"
        try:
            # Nao le dados arbitrarios indicados pelo manifesto.
            if manifesto.is_symlink() or quadros.is_symlink():
                raise PesquisaError("captura_link_recusado")
            with _abrir_regular(manifesto) as stream:
                bruto = stream.read(1024 * 1024 + 1)
            if len(bruto) > 1024 * 1024:
                raise PesquisaError("manifesto_muito_grande")
            m = json.loads(bruto.decode("utf-8"))
            if not isinstance(m, dict) or not isinstance(m.get("status"), dict):
                raise PesquisaError("manifesto_invalido")
            ident = m["status"]
            if (ident.get("requested_symbol"), ident.get("exchange")) != ("ESZ6", "CME"):
                raise PesquisaError("instrumento_fora_do_protocolo")
            sessao = ident.get("sessao", {})
            if not isinstance(sessao, dict) or not isinstance(sessao.get("referencia"), dict):
                raise PesquisaError("manifesto_invalido")
            if (m.get("transport_closed_normally") is not True or sessao.get("estado") != "FECHADA"
                    or sessao.get("retrato_completo") is not True or sessao.get("erro") is not None
                    or (sessao.get("referencia") or {}).get("min_qprice_change") != 0.25):
                raise PesquisaError("captura_sem_qualificacao_positiva")
            from qhos2.catalogo import classificar_ambiente
            sistema = ident.get("system_name")
            row = {"manifesto_sha256": _arquivo(manifesto), "quadros_sha256": _arquivo(quadros),
                   "ambiente": classificar_ambiente(sistema),
                   "origem": "sintetico" if sistema == "QHOS2 Sintetico" else classificar_ambiente(sistema)}
        except (OSError, ValueError, TypeError) as erro:
            if isinstance(erro, PesquisaError):
                raise
            raise PesquisaError("captura_inacessivel_ou_invalida") from None
        if row["quadros_sha256"] in vistos:
            raise PesquisaError("captura_duplicada")
        vistos.add(row["quadros_sha256"])
        saida.append(row)
    if not saida:
        raise PesquisaError("capturas_ausentes")
    return sorted(saida, key=lambda x: x["quadros_sha256"])


def _verificar_cadeia(conn):
    anterior = "0" * 64
    for corpo, digest, prev in conn.execute("SELECT corpo,sha256,anterior FROM execucoes ORDER BY n"):
        try:
            valor = json.loads(corpo)
        except ValueError:
            raise PesquisaError("historico_adulterado") from None
        if prev != anterior or _hash({"anterior": prev, "corpo": valor}) != digest:
            raise PesquisaError("historico_adulterado")
        anterior = digest
    return anterior


def executar_registrado(raiz, plano_id, pastas, *, agora=None):
    """Avalia bytes identificados e registra cada reproducao, sem trocar parametros."""
    agora, pastas = _agora(agora), list(pastas)
    with _banco(raiz) as conn:
        plano = _ler(conn, plano_id, "plano")
    proposta = plano["proposta"]
    janela = _janela(proposta["teste"])
    if agora.date() <= janela[1]:
        raise PesquisaError("avaliacao_antes_do_fim_do_teste")
    codigo = identidade_codigo()
    if codigo != plano["codigo"]:
        raise PesquisaError("codigo_diferente_do_pre_registro")
    antes = _identificar_capturas(pastas)
    origens = sorted({c["origem"] for c in antes})
    if len(origens) != 1:
        raise PesquisaError("ambientes_de_pesquisa_misturados")
    pars = dict(proposta["parametros"])
    pars["fomc"] = tuple(date.fromisoformat(d) for d in pars.get("fomc", []))
    entrada = _hash({"plano": plano_id, "capturas": antes, "codigo": codigo})
    # A reserva de escrita abrange verificacao, calculo e registro. Outro processo
    # nao pode iniciar um calculo divergente entre a consulta e a publicacao.
    with _banco(raiz) as conn:
        if _ler(conn, plano_id, "plano") != plano:
            raise PesquisaError("objeto_adulterado")
        anterior = _verificar_cadeia(conn)
        anteriores = [json.loads(r[0]) for r in conn.execute("SELECT corpo FROM execucoes WHERE plano=?", (plano_id,))]
        if any(r["entrada_sha256"] != entrada for r in anteriores):
            raise PesquisaError("dados_diferentes_da_avaliacao_oficial")
        resultado = av.avaliar(pastas, av.Parametros(**pars), janela=janela)
        resultado["modo"] = "PRE_REGISTRADO_LOCAL"
        resultado["origens_dos_dados"] = origens
        if antes != _identificar_capturas(pastas) or codigo != identidade_codigo():
            raise PesquisaError("entrada_mudou_durante_a_avaliacao")
        if anteriores and any(r["resultado"] != resultado for r in anteriores):
            raise PesquisaError("reproducao_divergente")
        corpo = {"plano": plano_id, "entrada_sha256": entrada, "capturas": antes,
                 "tipo": "REPRODUCAO" if anteriores else "AVALIACAO", "executado_em": agora.isoformat(),
                 "resultado": resultado, "pode_executar": False,
                 "rentabilidade_validada": False}
        digest = _hash({"anterior": anterior, "corpo": corpo})
        conn.execute("INSERT INTO execucoes(plano,corpo,sha256,anterior) VALUES(?,?,?,?)",
                     (plano_id, _json(corpo), digest, anterior))
    return {**corpo, "registro_sha256": digest}


def particionar_temporal(linhas, *, fim_treino_ns, inicio_teste_ns):
    """Separa amostras causais; remove do treino rotulos que alcancam o teste.

    Cada linha: recebido_ns (decisao), disponivel_ns (atributos), alvo_ns (rotulo),
    atributos (mapa numerico), alvo (numero). Nao ordena nem normaliza os dados.
    O treino so recebe rotulos conhecidos ate fim_treino_ns; o intervalo ate
    inicio_teste_ns e embargo. Isso nao treina modelo nem prova qualidade economica.
    """
    if any(type(x) is not int or x <= 0 for x in (fim_treino_ns, inicio_teste_ns)) or fim_treino_ns >= inicio_teste_ns:
        raise PesquisaError("divisao_temporal_invalida")
    treino, teste, purgadas, anterior = [], [], 0, None
    for row in linhas:
        if not isinstance(row, dict):
            raise PesquisaError("amostra_invalida")
        t, conhecida, alvo_t = (row.get(k) for k in ("recebido_ns", "disponivel_ns", "alvo_ns"))
        if any(type(x) is not int or x <= 0 for x in (t, conhecida, alvo_t)) or conhecida > t or alvo_t < t:
            raise PesquisaError("amostra_nao_causal")
        if anterior is not None and t <= anterior:
            raise PesquisaError("amostras_fora_de_ordem_ou_duplicadas")
        anterior = t
        attrs = row.get("atributos")
        if not isinstance(attrs, dict) or not attrs or not all(isinstance(k, str) and k for k in attrs):
            raise PesquisaError("atributos_invalidos")
        for v in [*attrs.values(), row.get("alvo")]:
            try:
                valido = type(v) in (int, float) and math.isfinite(v)
            except OverflowError:
                valido = False
            if not valido:
                raise PesquisaError("numero_nao_finito")
        copia = json.loads(_json(row))
        if t <= fim_treino_ns and alvo_t <= fim_treino_ns:
            treino.append(copia)
        elif t >= inicio_teste_ns:
            teste.append(copia)
        else:
            purgadas += 1
    return {"treino": treino, "teste": teste, "purgadas": purgadas, "modelo_treinado": False}


def amostras_da_captura(pasta, *, horizonte_ns=10_000_000_000, tolerancia_ns=1_000_000_000,
                       max_amostras=1_000_000):
    """Atributos do livro e rotulo futuro observado, sem antecipar atributos.

    Retorna somente depois de ler/qualificar a captura inteira e conferir seus
    hashes novamente. Um estado nao qualificado rompe todas as janelas pendentes.
    Alvo: variacao do meio em ticks, nao lucro/fill. A lista e limitada para que
    pesquisa nao esgote a memoria do servidor.
    """
    from collections import deque
    from qhos2.microestrutura import estados_da_captura, METODOLOGIA
    if (type(horizonte_ns) is not int or horizonte_ns <= 0 or type(tolerancia_ns) is not int
            or tolerancia_ns < 0 or type(max_amostras) is not int or not 1 <= max_amostras <= 1_000_000):
        raise PesquisaError("parametros_de_amostragem_invalidos")
    antes = _identificar_capturas([pasta])
    codigo = identidade_codigo()
    pendentes, linhas, descartadas, anterior = deque(), [], 0, None
    duplicadas = 0
    for estado, micro in estados_da_captura(pasta):
        if type(estado.recebido_ns) is not int or estado.recebido_ns <= 0:
            raise PesquisaError("amostra_tempo_invalido")
        if anterior is not None and estado.recebido_ns < anterior:
            raise PesquisaError("amostras_fora_de_ordem")
        repetida = anterior == estado.recebido_ns
        anterior = estado.recebido_ns
        if not estado.qualificado or estado.d is None or estado.compra is None or estado.venda is None:
            descartadas += len(pendentes)
            pendentes.clear()
            continue
        # Keep only the first qualified state at a receipt timestamp. A later
        # invalid state above still breaks the window; no duplicated ML rows.
        if repetida:
            duplicadas += 1
            continue
        meio = (estado.compra + estado.venda) / 2
        while pendentes and pendentes[0]["recebido_ns"] + horizonte_ns <= estado.recebido_ns:
            linha = pendentes.popleft()
            if estado.recebido_ns > linha["recebido_ns"] + horizonte_ns + tolerancia_ns:
                descartadas += 1
                continue
            linha["alvo_ns"] = estado.recebido_ns
            linha["alvo"] = meio - linha.pop("meio_ticks")
            linhas.append(linha)
            if len(linhas) > max_amostras:
                raise PesquisaError("limite_de_amostras")
        pendentes.append({"recebido_ns": estado.recebido_ns, "disponivel_ns": estado.recebido_ns,
                          "quadro": estado.indice, "meio_ticks": meio,
                          "atributos": {"d": estado.d, "qc": estado.qc, "qv": estado.qv,
                                        "spread_ticks": estado.venda - estado.compra, **micro}})
        if len(pendentes) + len(linhas) > max_amostras:
            raise PesquisaError("limite_de_amostras")
    if antes != _identificar_capturas([pasta]) or codigo != identidade_codigo():
        raise PesquisaError("entrada_mudou_durante_a_amostragem")
    return {"esquema": "qhos2.amostras.v1", "capturas": antes, "codigo": codigo, "linhas": linhas,
            "instrumento": {"simbolo": "ESZ6", "bolsa": "CME", "tick": .25},
            "microestrutura": dict(METODOLOGIA),
            "descartadas": descartadas + len(pendentes), "timestamps_repetidos": duplicadas,
            "horizonte_ns": horizonte_ns,
            "tolerancia_ns": tolerancia_ns, "rotulo": "mudanca_meio_ticks", "modelo_treinado": False,
            "pode_executar": False}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="qhos2.pesquisa", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="acao", required=True)
    registrar = sub.add_parser("registrar")
    registrar.add_argument("proposta", type=Path)
    avaliar = sub.add_parser("avaliar")
    avaliar.add_argument("plano_id")
    avaliar.add_argument("capturas", nargs="+")
    amostras = sub.add_parser("amostras")
    amostras.add_argument("captura", type=Path)
    amostras.add_argument("--max-amostras", type=int, default=100_000)
    amostras.add_argument("--horizonte-ms", type=int, default=10_000)
    texto = sub.add_parser("texto")
    texto.add_argument("arquivo", type=Path)
    texto.add_argument("--origem", required=True)
    texto.add_argument("--classe", default="AFIRMACAO")
    args = ap.parse_args(argv)
    try:
        raiz = Settings.from_environ().data_root
        if args.acao == "registrar":
            if args.proposta.stat().st_size > 65536:
                raise PesquisaError("proposta_muito_grande")
            result = {"plano_id": registrar_plano(raiz, json.loads(args.proposta.read_text(encoding="utf-8")))}
        elif args.acao == "amostras":
            result = amostras_da_captura(args.captura, horizonte_ns=args.horizonte_ms * 1_000_000,
                                         max_amostras=args.max_amostras)
        elif args.acao == "texto":
            if args.arquivo.stat().st_size > 65536:
                raise PesquisaError("texto_muito_grande")
            result = {"texto_id": registrar_texto(raiz, texto=args.arquivo.read_text(encoding="utf-8"),
                                                 origem=args.origem, classe=args.classe)}
        else:
            result = executar_registrado(raiz, args.plano_id, args.capturas)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
        return 2 if result.get("resultado", {}).get("recusadas") else 0
    except (ValueError, OSError, sqlite3.Error):
        print("pesquisa recusada: registro, entrada ou janela invalida")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
