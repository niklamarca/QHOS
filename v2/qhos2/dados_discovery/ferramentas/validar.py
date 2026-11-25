"""Peca 2: valida o conteudo de RAW/<fonte>/<dia>/ sem alterar nada em RAW.

Para cada fonte mede periodo, frequencia, lacunas, duplicatas e anomalias, e confere o SHA-256 de cada
arquivo contra MANIFEST/aquisicoes.jsonl. Escreve so em MANIFEST/validacao/:
  validacao_<dia>_<UTC>.json  (tudo)   e   validacao_<dia>_<UTC>.md  (resumo)
Nao muda classe no catalogo (P/C/D): isso e decisao separada, com o relatorio em maos.

Etiquetas: OBSERVED = lido no arquivo; CALCULATED = conta feita sobre o lido; INFERRED = interpretacao.

  python -I -B ferramentas\\validar.py 20261029
"""
import argparse
import collections
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import statistics
import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adquirir import em_janela_captura, hora_ny, gravar_json_atomico, livre  # noqa: E402

ESQUEMA = "qhos_dados.validacao.v1"
EXEMPLOS = 8
csv.field_size_limit(1 << 30)


# ---------------------------------------------------------------- utilidades
class Dataset:
    def __init__(self, ident, descricao):
        self.d = {"dataset_id": ident, "descricao": descricao, "arquivos": 0, "bytes": 0, "linhas": 0,
                  "periodo": None, "checagens": {}, "achados": [], "nao_validado": []}

    def arquivo(self, p):
        self.d["arquivos"] += 1
        self.d["bytes"] += p.stat().st_size

    def achado(self, texto, n=None, exemplos=None, etiqueta="OBSERVED"):
        a = {"etiqueta": etiqueta, "texto": texto}
        if n is not None:
            a["n"] = n
        if exemplos:
            a["exemplos"] = [str(e) for e in list(exemplos)[:EXEMPLOS]]
        self.d["achados"].append(a)

    def fechar(self):
        d = self.d
        if d["arquivos"] == 0:
            d["veredito"] = "sem arquivos"
        elif d["nao_validado"] and not d["checagens"]:
            d["veredito"] = "nao validado"
        else:
            d["veredito"] = "com achados" if d["achados"] else "sem achados"
        return d


def iso(x):
    return x.isoformat() if x else None


def resumo_datas(datas, uteis=False):
    """datas: lista de date na ordem do arquivo. Devolve contagens (CALCULATED)."""
    u = sorted(set(datas))
    r = {"n": len(datas), "unicas": len(u), "duplicadas": len(datas) - len(u),
         "fora_de_ordem": sum(1 for a, b in zip(datas, datas[1:]) if b < a),
         "inicio": iso(u[0]) if u else None, "fim": iso(u[-1]) if u else None,
         "fim_de_semana": sum(1 for x in u if x.weekday() >= 5)}
    if len(u) > 1:
        passos = [(b - a).days for a, b in zip(u, u[1:])]
        r["passo_mediano_dias"] = statistics.median(passos)
        i = max(range(len(passos)), key=passos.__getitem__)
        r["maior_buraco_dias"], r["maior_buraco_apos"] = passos[i], iso(u[i])
    if uteis and u:
        tem = set(u)
        dia, falta = u[0], []
        while dia <= u[-1]:
            if dia.weekday() < 5 and dia not in tem:
                falta.append(dia)
            dia += dt.timedelta(days=1)
        r["dias_uteis_sem_dado"] = len(falta)
        r["dias_uteis_sem_dado_exemplos"] = [iso(x) for x in falta[-EXEMPLOS:]]
    return r


def num(txt):
    """float de '1.5', '1,5', ' 2 '; None se vazio/NA; levanta ValueError se lixo."""
    t = str(txt).strip().strip('"')
    if t in ("", "NA", "N/A", "null", "."):
        return None
    return float(t.replace(",", ".")) if t.count(",") == 1 and "." not in t else float(t)


def checar_datas(ds, rotulo, datas, uteis=False, permitir_fds=False):
    r = resumo_datas(datas, uteis)
    ds.d["checagens"][rotulo] = r
    if r["duplicadas"]:
        ds.achado(f"{rotulo}: {r['duplicadas']} datas repetidas", r["duplicadas"],
                  [d for d, c in collections.Counter(datas).items() if c > 1], "CALCULATED")
    if r["fim_de_semana"] and not permitir_fds:
        ds.achado(f"{rotulo}: {r['fim_de_semana']} linhas em sabado/domingo", r["fim_de_semana"],
                  [d for d in sorted(set(datas)) if d.weekday() >= 5])
    return r


def periodo(ds, inicio, fim):
    p = ds.d["periodo"] or [None, None]
    ds.d["periodo"] = [min(x for x in (p[0], inicio) if x) if (p[0] or inicio) else None,
                       max(x for x in (p[1], fim) if x) if (p[1] or fim) else None]


def ler_csv(p, encoding="utf-8", delimiter=","):
    with open(p, newline="", encoding=encoding) as f:
        return list(csv.reader(f, delimiter=delimiter))


# ---------------------------------------------------------------- integridade
def integridade(raiz, dia):
    ds = Dataset("INTEGRIDADE", "SHA-256 de cada arquivo em RAW contra MANIFEST/aquisicoes.jsonl")
    reg = {}
    p = raiz / "MANIFEST" / "aquisicoes.jsonl"
    for linha in p.read_text(encoding="utf-8").splitlines():
        x = json.loads(linha)
        if x.get("resultado") == "ok" and f"/{dia}/" in x["destino"]:
            reg[x["destino"]] = x["sha256"]
    vistos, difere, sem_registro = set(), [], []
    for f in sorted((raiz / "RAW").glob(f"*/{dia}/*")):
        if not f.is_file():
            continue
        rel = f.relative_to(raiz).as_posix()
        ds.arquivo(f)
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for bloco in iter(lambda: fh.read(1 << 20), b""):
                h.update(bloco)
        if rel not in reg:
            sem_registro.append(rel)
        elif reg[rel] != h.hexdigest():
            difere.append(rel)
        vistos.add(rel)
    sumidos = sorted(set(reg) - vistos)
    ds.d["checagens"]["sha256"] = {"conferidos": len(vistos), "no_manifesto": len(reg), "diferentes": len(difere),
                                   "sem_registro": len(sem_registro), "registrados_e_ausentes": len(sumidos)}
    if difere:
        ds.achado("SHA-256 diferente do manifesto", len(difere), difere)
    if sem_registro:
        ds.achado("arquivo em RAW sem linha 'ok' no manifesto", len(sem_registro), sem_registro)
    if sumidos:
        ds.achado("arquivo registrado no manifesto e ausente em RAW", len(sumidos), sumidos)
    return ds


# ---------------------------------------------------------------- fontes
def v_cboe(pasta):
    ds = Dataset("B01_CBOE_VOL_IDX", "indices de volatilidade Cboe (CSV diario)")
    datas_vix = []
    for p in sorted(pasta.glob("*_History.csv")):
        ds.arquivo(p)
        nome = p.name.split("_")[0]
        rows = ler_csv(p)
        cab, corpo = rows[0], [r for r in rows[1:] if r]
        ds.d["linhas"] += len(corpo)
        datas, lixo, naopos, ohlc, plano = [], [], [], [], 0
        for r in corpo:
            d = dt.datetime.strptime(r[0], "%m/%d/%Y").date()
            datas.append(d)
            try:
                v = [num(x) for x in r[1:]]
            except ValueError:
                lixo.append(r)
                continue
            if any(x is not None and x <= 0 for x in v):
                naopos.append(d)
            if len(v) == 4 and None not in v:
                o, h, lo, c = v
                if lo > min(o, c) + 1e-9 or h < max(o, c) - 1e-9 or lo > h:
                    ohlc.append(d)
                plano += (o == h == lo == c)
        r = checar_datas(ds, nome, datas, uteis=True)
        r["colunas"] = cab
        if len(cab) == 5:
            r["ohlc_planos"] = plano
        periodo(ds, r["inicio"], r["fim"])
        if lixo:
            ds.achado(f"{nome}: valores nao numericos", len(lixo), lixo)
        if naopos:
            ds.achado(f"{nome}: valores <= 0", len(naopos), naopos)
        if ohlc:
            ds.achado(f"{nome}: OHLC inconsistente (low>min(open,close) ou high<max(open,close))", len(ohlc), ohlc)
        if nome == "VIX":
            datas_vix = sorted(set(datas))
        if plano:
            ds.achado(f"{nome}: {plano} dias com OHLC plano (O=H=L=C), tipico de serie so de fechamento",
                      plano, etiqueta="CALCULATED")
    return ds, datas_vix


def v_treasury(pasta):
    ds = Dataset("E01_UST_CURVE", "curvas diarias do Treasury (um CSV por tipo e ano)")
    grupos = collections.defaultdict(list)
    for p in sorted(pasta.glob("*.csv")):
        m = re.fullmatch(r"(daily_treasury_[a-z_]+)_(\d{4})\.csv", p.name)
        grupos[m.group(1)].append((int(m.group(2)), p))
    for tipo, arqs in sorted(grupos.items()):
        datas, fora_do_ano, lixo, faixa = [], [], [], []
        vazias = collections.Counter()
        celulas = collections.Counter()
        for ano, p in arqs:
            ds.arquivo(p)
            rows = ler_csv(p)
            cab = rows[0]
            for r in rows[1:]:
                if not r:
                    continue
                ds.d["linhas"] += 1
                d = dt.datetime.strptime(r[0], "%m/%d/%Y").date()
                datas.append(d)
                if d.year != ano:
                    fora_do_ano.append(f"{p.name}:{d}")
                for col, x in zip(cab[1:], r[1:]):
                    celulas[col] += 1
                    try:
                        v = num(x)
                    except ValueError:
                        lixo.append(f"{d} {col}={x!r}")
                        continue
                    if v is None:
                        vazias[col] += 1
                    elif not -5 < v < 30:
                        faixa.append(f"{d} {col}={v}")
        r = checar_datas(ds, tipo, datas, uteis=True)
        r["celulas_vazias_pct_por_coluna"] = {c: round(100 * vazias[c] / celulas[c], 2) for c in celulas if vazias[c]}
        periodo(ds, r["inicio"], r["fim"])
        if fora_do_ano:
            ds.achado(f"{tipo}: data fora do ano do arquivo", len(fora_do_ano), fora_do_ano)
        if lixo:
            ds.achado(f"{tipo}: valores nao numericos", len(lixo), lixo)
        if faixa:
            ds.achado(f"{tipo}: taxa fora de (-5, 30)", len(faixa), faixa)
    return ds


def v_gsw(p, ident, desc):
    ds = Dataset(ident, desc)
    if not p.exists():
        return ds
    ds.arquivo(p)
    rows = ler_csv(p)
    i = next(k for k, r in enumerate(rows) if r and r[0] == "Date")
    cab, corpo = rows[i], [r for r in rows[i + 1:] if r]
    ds.d["linhas"] = len(corpo)
    datas, lixo = [], []
    na = collections.Counter()
    for r in corpo:
        datas.append(dt.date.fromisoformat(r[0]))
        for col, x in zip(cab[1:], r[1:]):
            try:
                if num(x) is None:
                    na[col] += 1
            except ValueError:
                lixo.append(f"{r[0]} {col}={x!r}")
    r = checar_datas(ds, p.stem, datas, uteis=True)
    r["linhas_de_nota_antes_do_cabecalho"] = i
    r["colunas"] = len(cab)
    r["colunas_com_NA"] = len(na)
    r["NA_pct_maior"] = max((round(100 * v / len(corpo), 1) for v in na.values()), default=0)
    periodo(ds, r["inicio"], r["fim"])
    if lixo:
        ds.achado("valores nao numericos", len(lixo), lixo)
    ds.achado("arquivo comeca com nota 'not an official Federal Reserve statistical release'",
              etiqueta="OBSERVED")
    return ds


def v_fomc(pasta):
    ds = Dataset("D06_FOMC_CAL", "paginas HTML do calendario do FOMC")
    for p in sorted(pasta.glob("fomc*.htm")):
        ds.arquivo(p)
        ds.d["nao_validado"].append(f"{p.name}: HTML; a extracao das datas de reuniao e peca propria")
    return ds


def v_cftc(pasta):
    ds = Dataset("F01_CFTC_COT_FILES", "COT da CFTC: legado, desagregado e financeiro (ZIP com TXT)")
    familias = {"legado": "deacot", "desagregado": "fut_disagg_txt_", "financeiro": ("fin_fut_txt_", "fut_fin_txt_")}
    for fam, pre in familias.items():
        arqs = [p for p in sorted(pasta.glob("*.zip")) if p.name.startswith(pre)]
        chaves = collections.Counter()
        datas, dias_semana = [], collections.Counter()
        por_arquivo = {}
        formatos_us = collections.Counter()
        for p in arqs:
            ds.arquivo(p)
            with zipfile.ZipFile(p) as zf:
                nomes = zf.namelist()
                with zf.open(nomes[0]) as f:
                    leitor = csv.reader(io.TextIOWrapper(f, encoding="latin-1", newline=""))
                    cab = [c.strip() for c in next(leitor)]
                    i_d = next(k for k, c in enumerate(cab) if "YYYY-MM-DD" in c)
                    i_c = next(k for k, c in enumerate(cab) if c.replace("_", " ").lower() == "cftc contract market code")
                    n, mn, mx = 0, None, None
                    for r in leitor:
                        if not r:
                            continue
                        n += 1
                        txt = r[i_d].strip()
                        try:
                            d = dt.date.fromisoformat(txt)
                        except ValueError:  # visto nos historicos 2006-2016: '12/27/2016 12:00:00 AM'
                            d = dt.datetime.strptime(txt, "%m/%d/%Y %I:%M:%S %p").date()
                            formatos_us[p.name] += 1
                        datas.append(d)
                        dias_semana[d.weekday()] += 1
                        chaves[(r[i_c].strip(), d)] += 1
                        mn, mx = min(mn or d, d), max(mx or d, d)
            ds.d["linhas"] += n
            por_arquivo[p.name] = {"membros": nomes, "linhas": n, "inicio": iso(mn), "fim": iso(mx)}
        u = sorted(set(datas))
        passos = [(b - a).days for a, b in zip(u, u[1:])]
        buracos = [f"{a}->{b} ({(b - a).days} d)" for a, b in zip(u, u[1:]) if (b - a).days > 7]
        dup = {k: c for k, c in chaves.items() if c > 1}
        ds.d["checagens"][fam] = {
            "arquivos": por_arquivo, "linhas": len(datas), "datas_de_referencia": len(u),
            "inicio": iso(u[0]) if u else None, "fim": iso(u[-1]) if u else None,
            "passo_mediano_dias": statistics.median(passos) if passos else None,
            "dia_da_semana": {"seg ter qua qui sex sab dom".split()[k]: v for k, v in sorted(dias_semana.items())},
            "chaves_mercado_data_repetidas": len(dup)}
        periodo(ds, iso(u[0]) if u else None, iso(u[-1]) if u else None)
        if formatos_us:
            ds.achado(f"{fam}: coluna '...YYYY-MM-DD' com data no formato 'MM/DD/AAAA hh:mm:ss AM'",
                      sum(formatos_us.values()), [f"{k} x{v}" for k, v in formatos_us.items()])
        if buracos:
            ds.achado(f"{fam}: intervalos > 7 dias entre datas de referencia", len(buracos), buracos, "CALCULATED")
        if dup:
            ds.achado(f"{fam}: mesmo mercado e data em mais de uma linha", len(dup),
                      [f"{k[0]} {k[1]} x{c}" for k, c in list(dup.items())], "CALCULATED")
        fora_ter = sum(v for k, v in dias_semana.items() if k != 1)
        if fora_ter:
            ds.achado(f"{fam}: {fora_ter} linhas com data de referencia fora de terca-feira", fora_ter,
                      etiqueta="CALCULATED")
    return ds


def v_bcb(pasta, hoje):
    sgs = Dataset("D07_BCB_SGS", "SGS do BCB: 11 (Selic diaria) e 432 (meta Selic), em blocos JSON")
    for cod, uteis, permitir_fds in [("11", True, False), ("432", False, True)]:
        datas, lixo, futuro = [], [], []
        for p in sorted(pasta.glob(f"sgs{cod}_*.json")):
            sgs.arquivo(p)
            for x in json.loads(p.read_text(encoding="utf-8")):
                d = dt.datetime.strptime(x["data"], "%d/%m/%Y").date()
                datas.append(d)
                sgs.d["linhas"] += 1
                try:
                    num(x["valor"])
                except ValueError:
                    lixo.append(x)
                if d > hoje:
                    futuro.append(d)
        r = checar_datas(sgs, f"sgs{cod}", datas, uteis=uteis, permitir_fds=permitir_fds)
        periodo(sgs, r["inicio"], min(r["fim"], iso(hoje)) if r["fim"] else None)
        if lixo:
            sgs.achado(f"sgs{cod}: valores nao numericos", len(lixo), lixo)
        if futuro:
            sgs.achado(f"sgs{cod}: {len(futuro)} datas depois de hoje ({hoje}); ultima {max(futuro)}",
                       len(futuro), sorted(futuro)[:EXEMPLOS])

    ptax = Dataset("C05_BCB_PTAX", "PTAX do dolar (Olinda, CSV)")
    p = pasta / "ptax_dolar.csv"
    if p.exists():
        ptax.arquivo(p)
        rows = ler_csv(p)
        datas, invert = [], []
        for r in rows[1:]:
            if not r:
                continue
            ptax.d["linhas"] += 1
            d = dt.date.fromisoformat(r[2][:10])
            datas.append(d)
            if num(r[0]) > num(r[1]):
                invert.append(d)
        r = checar_datas(ptax, "ptax", datas, uteis=True)
        periodo(ptax, r["inicio"], r["fim"])
        if invert:
            ptax.achado("cotacaoCompra > cotacaoVenda", len(invert), invert)

    focus = Dataset("D08_BCB_FOCUS", "Focus (expectativas anuais e Selic por reuniao)")
    for p in sorted(pasta.glob("focus_*.csv")):
        focus.arquivo(p)
        rows = ler_csv(p)
        cab = rows[0]
        idx = {c: k for k, c in enumerate(cab)}
        chave_cols = [c for c in ("Indicador", "IndicadorDetalhe", "Data", "DataReferencia", "Reuniao", "baseCalculo")
                      if c in idx]
        chaves = collections.Counter()
        datas, lixo, ruim = [], [], []
        for r in rows[1:]:
            if not r:
                continue
            focus.d["linhas"] += 1
            datas.append(dt.date.fromisoformat(r[idx["Data"]]))
            chaves[tuple(r[idx[c]] for c in chave_cols)] += 1
            try:
                mn, md, mx = num(r[idx["Minimo"]]), num(r[idx["Mediana"]]), num(r[idx["Maximo"]])
                if None not in (mn, md, mx) and not mn <= md <= mx:
                    ruim.append(r[idx["Data"]])
            except ValueError:
                lixo.append(r)
        u = sorted(set(datas))
        dup = sum(c - 1 for c in chaves.values() if c > 1)
        focus.d["checagens"][p.stem] = {"linhas": len(datas), "inicio": iso(u[0]), "fim": iso(u[-1]),
                                        "datas_de_coleta": len(u), "chave": chave_cols, "linhas_repetidas_na_chave": dup}
        periodo(focus, iso(u[0]), iso(u[-1]))
        if dup:
            focus.achado(f"{p.stem}: linhas repetidas na chave {chave_cols}", dup,
                         [k for k, c in chaves.items() if c > 1], "CALCULATED")
        if lixo:
            focus.achado(f"{p.stem}: valores nao numericos", len(lixo), lixo)
        if ruim:
            focus.achado(f"{p.stem}: mediana fora de [minimo, maximo]", len(ruim), ruim)
    return [sgs, ptax, focus]


def v_gpr(pasta):
    ds = Dataset("I09_GPR", "Geopolitical Risk (XLS)")
    for p in sorted(pasta.glob("*.xls")):
        ds.arquivo(p)
        magica = p.read_bytes()[:8]
        ok = magica == bytes.fromhex("d0cf11e0a1b11ae1")
        ds.d["nao_validado"].append(f"{p.name}: XLS (BIFF/OLE2, assinatura {'ok' if ok else 'INESPERADA'}); "
                                    "a biblioteca padrao nao le XLS")
        if not ok:
            ds.achado(f"{p.name}: assinatura de arquivo inesperada {magica.hex()}")
    return ds


def v_epu(pasta):
    ds = Dataset("I10_EPU", "Economic Policy Uncertainty (CSV diario e XLSX mensais)")
    p = pasta / "All_Daily_Policy_Data.csv"
    if p.exists():
        ds.arquivo(p)
        rows = ler_csv(p)
        datas, lixo = [], []
        for r in rows[1:]:
            if not r:
                continue
            ds.d["linhas"] += 1
            datas.append(dt.date(int(r[2]), int(r[1]), int(r[0])))
            try:
                if num(r[3]) is None:
                    lixo.append(r)
            except ValueError:
                lixo.append(r)
        r = checar_datas(ds, "epu_diario", datas, permitir_fds=True)
        periodo(ds, r["inicio"], r["fim"])
        dia, falta, tem = datas and min(datas), 0, set(datas)
        while datas and dia <= max(datas):
            falta += dia not in tem
            dia += dt.timedelta(days=1)
        r["dias_corridos_sem_dado"] = falta
        if falta:
            ds.achado(f"epu_diario: {falta} dias corridos sem valor", falta, etiqueta="CALCULATED")
        if lixo:
            ds.achado("epu_diario: valor vazio ou nao numerico", len(lixo), lixo)
    for p in sorted(pasta.glob("*.xlsx")):
        ds.arquivo(p)
        with zipfile.ZipFile(p) as zf:
            folhas = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet")]
            linhas = {n: zf.read(n).count(b"<row ") for n in folhas}
        ds.d["checagens"][p.stem] = {"folhas": len(folhas), "linhas_xml_por_folha": linhas}
        ds.d["nao_validado"].append(f"{p.name}: XLSX aberto como ZIP, so contagem de linhas; valores nao lidos")
    return ds


def v_finra(pasta, datas_vix):
    ds = Dataset("F08_FINRA_SHVOL", "FINRA short volume CNMS (TXT diario, separado por |)")
    datas, cab_ruim, data_ruim, short_maior, negativos, lixo, sym_dup = [], [], [], [], [], [], []
    linhas_por_dia, rodape_ruim, sem_rodape = [], [], []
    for p in sorted(pasta.glob("CNMSshvol*.txt")):
        ds.arquivo(p)
        d_arq = dt.datetime.strptime(p.stem[-8:], "%Y%m%d").date()
        datas.append(d_arq)
        with open(p, encoding="utf-8", newline="") as f:
            cab = f.readline().rstrip("\r\n")
            if cab != "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market":
                cab_ruim.append(p.name)
                continue
            simbolos, n = collections.Counter(), 0
            corpo = f.read().splitlines()
            # a ultima linha e um rodape so com o numero de registros (visto em 29/10)
            if corpo and corpo[-1].strip().isdigit():
                rodape = int(corpo.pop().strip())
                if rodape != len(corpo):
                    rodape_ruim.append(f"{p.name}: rodape {rodape}, linhas {len(corpo)}")
            else:
                sem_rodape.append(p.name)
            for linha in corpo:
                c = linha.split("|")
                if len(c) != 6:
                    lixo.append(f"{p.name}: {linha[:60]!r}")
                    continue
                n += 1
                if c[0] != p.stem[-8:]:
                    data_ruim.append(f"{p.name}: {c[0]}")
                simbolos[(c[1], c[5])] += 1
                try:
                    sv, se, tv = float(c[2]), float(c[3]), float(c[4])
                except ValueError:
                    lixo.append(f"{p.name}: {linha[:60]!r}")
                    continue
                if min(sv, se, tv) < 0:
                    negativos.append(f"{p.name}: {c[1]}")
                if sv > tv + 1e-6:
                    short_maior.append(f"{p.name}: {c[1]} short={sv} total={tv}")
        ds.d["linhas"] += n
        linhas_por_dia.append((n, d_arq))
        sym_dup += [f"{p.name}: {k[0]}/{k[1]}" for k, v in simbolos.items() if v > 1]
    r = checar_datas(ds, "arquivos", datas, uteis=True)
    if linhas_por_dia:
        r["linhas_por_dia_min"] = min(linhas_por_dia)[0]
        r["linhas_por_dia_min_em"] = iso(min(linhas_por_dia)[1])
        r["linhas_por_dia_max"] = max(linhas_por_dia)[0]
        r["linhas_por_dia_mediana"] = statistics.median(x[0] for x in linhas_por_dia)
    periodo(ds, r["inicio"], r["fim"])
    for rot, lst in [("cabecalho diferente do esperado", cab_ruim), ("Date da linha diferente da data do arquivo", data_ruim),
                     ("ShortVolume > TotalVolume", short_maior), ("volume negativo", negativos),
                     ("linha mal formada", lixo), ("mesmo simbolo/mercado repetido no dia", sym_dup),
                     ("rodape com contagem diferente do numero de linhas", rodape_ruim),
                     ("arquivo sem rodape de contagem", sem_rodape)]:
        if lst:
            ds.achado(rot, len(lst), lst)
    if datas_vix and datas:
        tem = set(datas)
        sem_finra = [d for d in datas_vix if datas[0] <= d <= max(datas) and d not in tem]
        r["pregoes_vix_sem_arquivo_finra"] = len(sem_finra)
        if sem_finra:
            ds.achado("dias com linha no VIX_History da Cboe e sem arquivo FINRA (403); conferir se sao feriados "
                      "de bolsa dos EUA", len(sem_finra), [iso(x) for x in sem_finra], "CALCULATED")
    return ds


def v_iana(pasta):
    ds = Dataset("I03_TZDB", "IANA tzdb (tar.gz)")
    for p in sorted(pasta.glob("*.tar.gz")):
        ds.arquivo(p)
        with tarfile.open(p, "r:gz") as tf:
            membros = tf.getmembers()
            perigosos = [m.name for m in membros if m.name.startswith(("/", "..")) or ".." in Path(m.name).parts
                         or m.issym() or m.islnk()]
            versao = None
            for m in membros:
                if m.name == "version" and m.isfile():
                    versao = tf.extractfile(m).read().decode().strip()
        ds.d["checagens"][p.name] = {"membros": len(membros), "version": versao}
        if perigosos:
            ds.achado(f"{p.name}: caminhos absolutos, '..' ou links dentro do tar", len(perigosos), perigosos)
        if versao and versao not in p.name:
            ds.achado(f"{p.name}: arquivo 'version' diz {versao}")
    return ds


MESES = re.compile(r"(?P<sym>[A-Z]+)-(?P<tipo>1m|fundingRate|metrics)-(?P<ano>\d{4})-(?P<mes>\d{2})(?:-(?P<dia>\d{2}))?\.zip")


def _ms(v):
    """timestamp da Binance -> (ms, unidade). Desde 2025 o spot usa microssegundos."""
    v = int(v)
    return (v // 1000, "us") if v > 10 ** 14 else (v, "ms")


def v_binance(pasta):
    ds = Dataset("G01_BINANCE_VISION", "Binance Vision BTC/ETH: klines 1m, funding e metrics (ZIP com CSV)")
    series = collections.defaultdict(lambda: {"arquivos": 0, "linhas": 0, "periodos": [], "unidades": collections.Counter(),
                                              "primeiro_us": None, "faltando": 0, "duplicadas": 0, "ohlc": 0,
                                              "fora_do_periodo": 0, "close_time": 0, "fora_de_ordem": 0,
                                              "sem_linhas": [], "taxa_extrema": 0, "intervalos_h": collections.Counter(),
                                              "bordas": [], "dup_identicas": 0, "dup_conflitantes": 0})
    exemplos = collections.defaultdict(list)
    for p in sorted(pasta.glob("*.zip")):
        rot, resto = p.name.split("_", 1)
        m = MESES.fullmatch(resto)
        if not m:
            ds.achado(f"nome inesperado: {p.name}")
            continue
        ds.arquivo(p)
        s = series[(rot, m["sym"])]
        s["arquivos"] += 1
        ano, mes = int(m["ano"]), int(m["mes"])
        if m["dia"]:
            ini = dt.datetime(ano, mes, int(m["dia"]), tzinfo=dt.timezone.utc)
            fim = ini + dt.timedelta(days=1)
        else:
            ini = dt.datetime(ano, mes, 1, tzinfo=dt.timezone.utc)
            fim = dt.datetime(ano + (mes == 12), mes % 12 + 1, 1, tzinfo=dt.timezone.utc)
        ini_ms, fim_ms = int(ini.timestamp() * 1000), int(fim.timestamp() * 1000)
        s["periodos"].append(ini.date())
        with zipfile.ZipFile(p) as zf:
            with zf.open(zf.namelist()[0]) as f:
                linhas = io.TextIOWrapper(f, encoding="utf-8").read().splitlines()
        if linhas and not linhas[0][:1].isdigit():
            linhas = linhas[1:]
        s["linhas"] += len(linhas)
        ds.d["linhas"] += len(linhas)
        if not linhas:
            s["sem_linhas"].append(p.name)
            continue
        if m["tipo"] == "1m":
            ts = []
            for ln in linhas:
                c = ln.split(",")
                t, un = _ms(c[0])
                s["unidades"][un] += 1
                if un == "us" and (s["primeiro_us"] is None or ini.date() < s["primeiro_us"]):
                    s["primeiro_us"] = ini.date()
                ts.append(t)
                o, h, lo, cl = map(float, c[1:5])
                if lo > min(o, cl) + 1e-9 or h < max(o, cl) - 1e-9:
                    s["ohlc"] += 1
                    exemplos[(rot, m["sym"], "ohlc")].append(f"{p.name}:{t}")
                if _ms(c[6])[0] != t + 59999:
                    s["close_time"] += 1
                    exemplos[(rot, m["sym"], "close_time")].append(f"{p.name}:{t}")
            unicos = set(ts)
            s["fora_de_ordem"] += sum(1 for a, b in zip(ts, ts[1:]) if b < a)
            fora = [t for t in unicos if not ini_ms <= t < fim_ms]
            s["fora_do_periodo"] += len(fora)
            dentro = [t for t in unicos if ini_ms <= t < fim_ms]
            if dentro:
                # minutos faltando entre o 1o e o ultimo, mais as bordas do mes; a borda inicial do
                # 1o arquivo da serie (inicio da listagem) e descontada no fechamento
                falta = (max(dentro) - min(dentro)) // 60000 + 1 - len(dentro)
                b_ini = (min(dentro) - ini_ms) // 60000
                b_fim = (fim_ms - 60000 - max(dentro)) // 60000
                s["bordas"].append((ini.date(), falta, b_ini, b_fim, p.name))
        elif m["tipo"] == "fundingRate":
            ts = []
            for ln in linhas:
                c = ln.split(",")
                t = _ms(c[0])[0]
                ts.append(t)
                s["intervalos_h"][c[1]] += 1
                if abs(float(c[2])) > 0.003:
                    s["taxa_extrema"] += 1
                    exemplos[(rot, m["sym"], "taxa_extrema")].append(f"{p.name}:{t}:{c[2]}")
            s["fora_do_periodo"] += sum(1 for t in ts if not ini_ms <= t < fim_ms)
        else:  # metrics: uma linha a cada 5 min
            ts = []
            for ln in linhas:
                c = ln.split(",")
                t = int(dt.datetime.fromisoformat(c[0]).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
                ts.append(t)
            unicos = set(ts)
            s["fora_do_periodo"] += sum(1 for t in unicos if not ini_ms <= t <= fim_ms)
            falta = 288 - len([t for t in unicos if ini_ms <= t < fim_ms])
            if falta > 0:
                s["faltando"] += falta
                exemplos[(rot, m["sym"], "faltando")].append(f"{p.name}: {falta} de 288")
        # repetidos: linha identica a 1a ocorrencia do mesmo timestamp, ou com valores diferentes
        primeira = {}
        for t, ln in zip(ts, linhas):
            if t not in primeira:
                primeira[t] = ln
            elif primeira[t] == ln:
                s["dup_identicas"] += 1
                if not exemplos[(rot, m["sym"], "dup_identicas")] or exemplos[(rot, m["sym"], "dup_identicas")][-1] != p.name:
                    exemplos[(rot, m["sym"], "dup_identicas")].append(p.name)
            else:
                s["dup_conflitantes"] += 1
                exemplos[(rot, m["sym"], "dup_conflitantes")].append(f"{p.name}:{t}")
    for (rot, sym), s in sorted(series.items()):
        per = sorted(s["periodos"])
        inicio_listagem = None
        for periodo_arq, falta, b_ini, b_fim, nome in sorted(s["bordas"]):
            if periodo_arq == per[0]:
                inicio_listagem = b_ini
                b_ini = 0
            if falta + b_ini + b_fim:
                s["faltando"] += falta + b_ini + b_fim
                exemplos[(rot, sym, "faltando")].append(f"{nome}: {falta} no meio, {b_ini}+{b_fim} nas bordas")
        diario = rot == "ummetrics"
        passo = dt.timedelta(days=1)
        buracos = []
        for a, b in zip(per, per[1:]):
            esperado = a + passo if diario else dt.date(a.year + (a.month == 12), a.month % 12 + 1, 1)
            if b != esperado:
                buracos.append(f"{a} -> {b}")
        chave = f"{rot}_{sym}"
        info = {"arquivos": s["arquivos"], "linhas": s["linhas"], "inicio": iso(per[0]), "ultimo_periodo": iso(per[-1]),
                "periodos_ausentes_entre_arquivos": len(buracos), "unidade_timestamp": dict(s["unidades"]),
                "primeiro_arquivo_em_microssegundos": iso(s["primeiro_us"]),
                "faltando": s["faltando"], "duplicadas_identicas": s["dup_identicas"],
                "duplicadas_conflitantes": s["dup_conflitantes"], "fora_do_periodo": s["fora_do_periodo"],
                "fora_de_ordem": s["fora_de_ordem"]}
        if rot in ("spot1m", "um1m"):
            info.update({"ohlc_inconsistente": s["ohlc"], "close_time_inesperado": s["close_time"],
                         "minutos_antes_do_1o_registro_no_1o_mes": inicio_listagem})
            info["faltando_pct"] = round(100 * s["faltando"] / max(1, s["linhas"] + s["faltando"]), 4)
        if rot == "umfund":
            info.update({"intervalo_funding_h": dict(s["intervalos_h"]), "taxa_abs_maior_0_3pct": s["taxa_extrema"]})
        if rot == "ummetrics":
            info["faltando_pct"] = round(100 * s["faltando"] / max(1, 288 * s["arquivos"]), 4)
        ds.d["checagens"][chave] = info
        periodo(ds, iso(per[0]), iso(per[-1]))
        if buracos:
            ds.achado(f"{chave}: periodos ausentes entre arquivos", len(buracos), buracos, "CALCULATED")
        if s["sem_linhas"]:
            ds.achado(f"{chave}: ZIP sem linhas", len(s["sem_linhas"]), s["sem_linhas"])
        for campo, texto in [("faltando", "registros faltando no periodo de cada arquivo"),
                             ("dup_identicas", "linhas repetidas identicas (mesmo timestamp e mesmos valores); "
                              "exemplos = arquivos afetados"),
                             ("dup_conflitantes", "timestamp repetido com valores DIFERENTES"),
                             ("fora_do_periodo", "timestamp fora do periodo do arquivo"),
                             ("fora_de_ordem", "timestamps fora de ordem"), ("ohlc", "OHLC inconsistente"),
                             ("close_time", "close_time diferente de open_time + 59999 ms"),
                             ("taxa_extrema", "funding com |taxa| > 0,3%")]:
            if s.get(campo):
                ds.achado(f"{chave}: {texto}", s[campo], exemplos.get((rot, sym, campo)),
                          "CALCULATED" if campo == "faltando" else "OBSERVED")
        if len(s["unidades"]) > 1:
            ds.achado(f"{chave}: unidade do timestamp muda de ms para us a partir de {s['primeiro_us']}; "
                      "normalizar antes de juntar", sum(s["unidades"].values()))
    return ds


def v_famafrench(pasta):
    ds = Dataset("B05_FF_LIBRARY", "Kenneth French Data Library (ZIP com CSV em blocos)")
    for p in sorted(pasta.glob("*.zip")):
        ds.arquivo(p)
        with zipfile.ZipFile(p) as zf:
            texto = zf.read(zf.namelist()[0]).decode("latin-1").splitlines()
        i = next(k for k, ln in enumerate(texto) if ln.startswith(","))
        datas, lixo, ausentes = [], [], 0
        for ln in texto[i + 1:]:
            c = [x.strip() for x in ln.split(",")]
            if not c[0].isdigit():
                break
            d = (dt.date(int(c[0][:4]), int(c[0][4:6]), int(c[0][6:])) if len(c[0]) == 8
                 else dt.date(int(c[0][:4]), int(c[0][4:6]), 1))
            datas.append(d)
            try:
                v = [float(x) for x in c[1:]]
                ausentes += sum(1 for x in v if x in (-99.99, -999))
            except ValueError:
                lixo.append(ln)
        ds.d["linhas"] += len(datas)
        r = checar_datas(ds, p.stem, datas, uteis="daily" in p.name, permitir_fds="daily" not in p.name)
        r["colunas"] = texto[i]
        r["linhas_de_nota_antes"] = i
        r["valores_ausentes_-99_99"] = ausentes
        sabados = [x for x in datas if x.weekday() >= 5]
        if sabados and max(sabados) < dt.date(1953, 1, 1):
            ds.achado(f"{p.stem}: todos os {len(sabados)} fins de semana sao anteriores a 1953; a NYSE abria aos "
                      "sabados ate 1952, entao sao pregoes reais", len(sabados), etiqueta="INFERRED")
        periodo(ds, r["inicio"], r["fim"])
        if lixo:
            ds.achado(f"{p.stem}: linhas nao numericas", len(lixo), lixo)
        if ausentes:
            ds.achado(f"{p.stem}: valores -99,99/-999 (codigo de ausente)", ausentes)
        ds.d["nao_validado"].append(f"{p.name}: so o 1o bloco (mensal/diario) foi lido; blocos anuais nao")
    return ds


def v_fja(pasta):
    ds = Dataset("B16_FJA_SP500", "composicao historica do S&P 500 (fja05680, experimental)")
    for nome in ["historical_components_changes.csv", "historical_components_changes_updated.csv"]:
        p = pasta / nome
        if not p.exists():
            continue
        ds.arquivo(p)
        rows = ler_csv(p)
        datas, tamanhos = [], []
        for r in rows[1:]:
            if r:
                datas.append(dt.date.fromisoformat(r[0]))
                tamanhos.append(len([t for t in r[1].split(",") if t]))
        ds.d["linhas"] += len(datas)
        r = checar_datas(ds, p.stem, datas, permitir_fds=False)
        r["tickers_por_data_min"], r["tickers_por_data_max"] = min(tamanhos), max(tamanhos)
        periodo(ds, r["inicio"], r["fim"])
        fora = [f"{d} n={n}" for d, n in zip(datas, tamanhos) if not 480 <= n <= 520]
        if fora:
            ds.achado(f"{p.stem}: datas com contagem de tickers fora de 480..520", len(fora), fora, "CALCULATED")
    p = pasta / "sp500_ticker_start_end.csv"
    if p.exists():
        ds.arquivo(p)
        rows = ler_csv(p)
        inv = [r for r in rows[1:] if r and r[2] and r[2] < r[1]]
        ds.d["checagens"]["sp500_ticker_start_end"] = {"linhas": len(rows) - 1, "fim_antes_do_inicio": len(inv)}
        if inv:
            ds.achado("sp500_ticker_start_end: end_date < start_date", len(inv), inv)
    for nome in ["sp500_changes_since_2019.csv", "sp500.csv", "README.md", "LICENSE.txt"]:
        p = pasta / nome
        if p.exists():
            ds.arquivo(p)
            ds.d["nao_validado"].append(f"{nome}: so registrado (referencia)")
    return ds


# ---------------------------------------------------------------- relatorio
def markdown(res):
    L = [f"# Validação de conteúdo — RAW/*/{res['dia']}", "",
         f"- Rodada: {res['utc_inicio']} → {res['utc_fim']} (UTC). Script `ferramentas/validar.py` sha256 `{res['sha256_validador'][:16]}…`.",
         "- Nada em RAW foi alterado. Nenhuma classe do catálogo foi mudada.",
         "- Etiquetas: OBSERVED = lido no arquivo; CALCULATED = conta sobre o lido; INFERRED = interpretação.", "",
         "| Dataset | Arquivos | Linhas | Período | Veredito | Achados |", "|---|---:|---:|---|---|---:|"]
    for d in res["datasets"]:
        per = " a ".join(x or "?" for x in d["periodo"]) if d["periodo"] else "—"
        L.append(f"| {d['dataset_id']} | {d['arquivos']} | {d['linhas']:,} | {per} | {d['veredito']} | {len(d['achados'])} |"
                 .replace(",", "."))
    for d in res["datasets"]:
        if not d["achados"] and not d["nao_validado"]:
            continue
        L += ["", f"## {d['dataset_id']}", ""]
        for a in d["achados"]:
            n = f" (n={a['n']})" if "n" in a else ""
            ex = f" — ex.: {', '.join(a['exemplos'][:4])}" if a.get("exemplos") else ""
            L.append(f"- [{a['etiqueta']}] {a['texto']}{n}{ex}")
        for x in d["nao_validado"]:
            L.append(f"- [NÃO VALIDADO] {x}")
    return "\n".join(L) + "\n"


def main(argv=None, agora=None):
    ap = argparse.ArgumentParser(prog="validar.py", description=__doc__.splitlines()[0])
    ap.add_argument("dia", help="pasta do dia em RAW/<fonte>/, ex.: 20261029")
    ap.add_argument("--raiz", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--ignorar-janela", action="store_true", help="so para os testes")
    a = ap.parse_args(argv)
    raiz = Path(a.raiz).resolve()
    agora = agora or dt.datetime.now(dt.timezone.utc)
    if not re.fullmatch(r"\d{8}", a.dia):
        print("dia deve ser AAAAMMDD", file=sys.stderr)
        return 2
    if em_janela_captura(agora) and not a.ignorar_janela:
        print(f"RECUSADO: janela de captura Rithmic aberta (NY {hora_ny(agora):%a %H:%M}).", file=sys.stderr)
        return 2
    hoje = agora.date()

    def pasta(fonte):
        return raiz / "RAW" / fonte / a.dia

    inicio = dt.datetime.now(dt.timezone.utc)
    dss = [integridade(raiz, a.dia)]
    cboe, datas_vix = v_cboe(pasta("cboe"))
    dss += [cboe, v_treasury(pasta("treasury")),
            v_gsw(pasta("fed") / "feds200628.csv", "E03_GSW_NOMINAL", "curva nominal Gurkaynak-Sack-Wright"),
            v_gsw(pasta("fed") / "feds200805.csv", "E04_GSW_TIPS", "curva real (TIPS) Gurkaynak-Sack-Wright"),
            v_fomc(pasta("fed")), v_cftc(pasta("cftc")), *v_bcb(pasta("bcb"), hoje),
            v_gpr(pasta("gpr")), v_epu(pasta("epu")), v_finra(pasta("finra"), datas_vix), v_iana(pasta("iana")),
            v_binance(pasta("binance")), v_famafrench(pasta("famafrench")), v_fja(pasta("fja_sp500"))]
    res = {"esquema": ESQUEMA, "dia": a.dia, "utc_inicio": inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "utc_fim": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "sha256_validador": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "datasets": [d.fechar() for d in dss]}
    saida = raiz / "MANIFEST" / "validacao"
    saida.mkdir(parents=True, exist_ok=True)
    carimbo = inicio.strftime("%Y%m%dT%H%M%SZ")
    js = livre(saida / f"validacao_{a.dia}_{carimbo}.json")
    gravar_json_atomico(js, res)
    md = livre(saida / f"validacao_{a.dia}_{carimbo}.md")
    md.write_text(markdown(res), encoding="utf-8")
    for d in res["datasets"]:
        print(f"{d['dataset_id']:<22} {d['veredito']:<14} arquivos={d['arquivos']:<5} achados={len(d['achados'])}")
    print(f"relatorio: {js.relative_to(raiz)}  e  {md.relative_to(raiz)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
