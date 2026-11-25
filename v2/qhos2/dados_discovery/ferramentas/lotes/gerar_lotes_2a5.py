"""Gera lote2.json .. lote5.json. Conferencias de URL feitas em 29/10/2026 ~03:00Z (GET de 2 KB):

- lote2 (FRED/EIA/BLS): sem chave o FRED da 400, a EIA 403 e o BLS v2 responde (gzip). Formato das URLs
  pela documentacao de cada API; IDs de serie EIA nao conferidos (sem chave) -> o download confere.
- lote3: GPR/EPU 200; FINRA CNMS 403 em 31/07/2018 e 200 em 01/08/2018 (os dias saem dos pregoes do
  VIX baixado no lote 1); tzdb 2026f (data.iana.org/time-zones/tzdb/version). WGC fora: so pagina HTML.
- lote4 (Binance Vision): lista de arquivos lida da listagem S3 publica, cada ZIP com o seu .CHECKSUM.
- lote5: Fama-French 200; fja05680/sp500 pela API do GitHub. SEC FSDS FORA: 403 com o User-Agent
  sem e-mail (a SEC exige contato declarado) -> decisao do autor.

Rodar: python -I -B ferramentas\\lotes\\gerar_lotes_2a5.py   (precisa do VIX_History.csv do lote 1)
"""
import csv
import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

UA = "QHOS-pesquisa (contato: QHOS)"
AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]


def novo(nome, fontes, timeout=120):
    return {"lote": nome, "user_agent": UA, "timeout_s": timeout, "fontes": fontes, "itens": []}


def item(lote, fonte, id_, url, arquivo, tipo, min_bytes=1, max_bytes=None, checksum_url=None):
    lote["itens"].append({"id": id_, "fonte": fonte, "url": url, "arquivo": arquivo, "tipo": tipo,
                          "min_bytes": min_bytes, "max_bytes": max_bytes, "sha256_esperado": None,
                          "checksum_url": checksum_url})


def gravar(lote):
    p = AQUI / f"{lote['lote']}.json"
    p.write_text(json.dumps(lote, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{p}: {len(lote['itens'])} itens")


# ------------------------------------------------------------------ lote 2 (chaves do autor)
def lote2():
    def ch(n):
        return {"credman": f"QHOS_DADOS/{n}", "env": f"QHOS_{n}_KEY"}
    L = novo("lote2", {
        "fred": {"intervalo_s": 1.0, "licenca": "FRED/ALFRED: termos FRED; series de terceiros podem exigir permissao", "chave": ch("FRED")},
        "eia": {"intervalo_s": 1.0, "licenca": "EIA: termos EIA (reuso permitido)", "chave": ch("EIA")},
        "bls": {"intervalo_s": 2.0, "licenca": "BLS: dado governamental", "chave": ch("BLS")},
    })
    F = "https://api.stlouisfed.org/fred"
    for s in ["CPIAUCSL", "PAYEMS", "UNRATE", "GDP", "PCEPI", "DFII10", "DGS2", "DGS10", "T10YIE", "T5YIFR",
              "DTWEXBGS", "DCOILWTICO", "DCOILBRENTEU", "DHHNGSP", "NFCI", "STLFSI4", "SOFR", "EFFR"]:
        base = f"series_id={s}&api_key={{CHAVE}}&file_type=json"
        item(L, "fred", f"D02_{s}_primeira", f"{F}/series/observations?{base}&output_type=4"
             "&realtime_start=1776-07-04&realtime_end=9999-12-31", f"{s}_primeira_divulgacao.json", "json", 50, 200 << 20)
        item(L, "fred", f"D01_{s}_atual", f"{F}/series/observations?{base}", f"{s}_atual.json", "json", 50, 200 << 20)
        item(L, "fred", f"D02_{s}_vintages", f"{F}/series/vintagedates?{base}&limit=10000",
             f"{s}_vintagedates.json", "json", 50, 50 << 20)
    for s in ["PET.WCESTUS1.W", "PET.WCSSTUS1.W", "PET.WGTSTUS1.W", "PET.WDISTUS1.W",
              "PET.W_EPC0_SAX_YCUOK_MBBL.W", "PET.WCRFPUS2.W", "NG.NW2_EPG0_SWO_R48_BCF.W"]:
        item(L, "eia", f"I05_{s.replace('.', '_')}", f"https://api.eia.gov/v2/seriesid/{s}?api_key={{CHAVE}}",
             f"{s}.json", "json", 50, 100 << 20)
    for s in ["CUUR0000SA0", "CUSR0000SA0", "CUSR0000SA0L1E", "CES0000000001", "LNS14000000"]:
        for a0, a1 in [(1987, 2006), (2007, 2026)]:
            # o BLS responde em gzip mesmo sem Accept-Encoding (sonda de 29/10): guardado como veio
            item(L, "bls", f"D04_{s}_{a0}_{a1}", f"https://api.bls.gov/publicAPI/v2/timeseries/data/{s}"
                 f"?registrationkey={{CHAVE}}&startyear={a0}&endyear={a1}", f"{s}_{a0}_{a1}.json.gz", "gz", 50, 50 << 20)
    return L


# ------------------------------------------------------------------ lote 3
def pregoes_vix(desde):
    achados = sorted((RAIZ / "RAW" / "cboe").glob("*/VIX_History.csv"))
    if not achados:
        sys.exit("falta RAW/cboe/*/VIX_History.csv (rode o lote 1 antes)")
    with open(achados[-1], newline="", encoding="utf-8") as f:
        dias = [dt.datetime.strptime(r["DATE"], "%m/%d/%Y").date() for r in csv.DictReader(f)]
    return achados[-1], [d for d in dias if d >= desde]


def lote3():
    L = novo("lote3", {
        "gpr": {"intervalo_s": 2.0, "licenca": "GPR (Caldara-Iacoviello): uso livre com citacao", "chave": None},
        "epu": {"intervalo_s": 2.0, "licenca": "EPU (Baker-Bloom-Davis): CC BY 4.0", "chave": None},
        "finra": {"intervalo_s": 0.5, "licenca": "FINRA: Terms of Use gerais (nao lidos)", "chave": None},
        "iana": {"intervalo_s": 2.0, "licenca": "IANA tzdb: dominio publico (INFERRED)", "chave": None},
    })
    for n in ["data_gpr_export.xls", "data_gpr_daily_recent.xls"]:
        item(L, "gpr", f"I09_{n.split('.')[0]}", f"https://www.matteoiacoviello.com/gpr_files/{n}", n, "binario", 10000, 100 << 20)
    for n, t in [("US_Policy_Uncertainty_Data.xlsx", "zip"), ("All_Daily_Policy_Data.csv", "csv"),
                 ("Global_Policy_Uncertainty_Data.xlsx", "zip")]:
        item(L, "epu", f"I10_{n.split('.')[0]}", f"https://www.policyuncertainty.com/media/{n}", n, t, 1000, 100 << 20)
    vix, dias = pregoes_vix(dt.date(2018, 8, 1))
    print(f"  FINRA: {len(dias)} pregoes de {dias[0]} a {dias[-1]} (de {vix.relative_to(RAIZ)})")
    for d in dias:
        n = f"CNMSshvol{d:%Y%m%d}.txt"
        item(L, "finra", f"F08_{d:%Y%m%d}", f"https://cdn.finra.org/equity/regsho/daily/{n}", n, "texto", 1000, 50 << 20)
    for n in ["tzdata2026f.tar.gz", "tzcode2026f.tar.gz"]:
        item(L, "iana", f"I03_{n.split('.')[0]}", f"https://data.iana.org/time-zones/releases/{n}", n, "gz", 10000, 50 << 20)
    return L


# ------------------------------------------------------------------ lote 4 (Binance Vision)
def listar_s3(prefixo):
    chaves, marcador = [], ""
    while True:
        u = ("https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix="
             + urllib.parse.quote(prefixo) + (f"&marker={urllib.parse.quote(marcador)}" if marcador else ""))
        x = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=60).read().decode()
        ks = re.findall(r"<Key>([^<]+)</Key>", x)
        chaves += ks
        if "<IsTruncated>true</IsTruncated>" not in x or not ks:
            return [k for k in chaves if k.endswith(".zip")]
        marcador = ks[-1]


def lote4():
    L = novo("lote4", {"binance": {"intervalo_s": 0.5, "chave": None,
                                   "licenca": "Binance Vision: CC BY-NC-SA 4.0; so pesquisa nao comercial; nao usar em trading ao vivo"}})
    for sym in ["BTCUSDT", "ETHUSDT"]:
        for rot, pre in [("spot1m", f"data/spot/monthly/klines/{sym}/1m/"),
                         ("um1m", f"data/futures/um/monthly/klines/{sym}/1m/"),
                         ("umfund", f"data/futures/um/monthly/fundingRate/{sym}/"),
                         ("ummetrics", f"data/futures/um/daily/metrics/{sym}/")]:
            ks = listar_s3(pre)
            print(f"  {pre}: {len(ks)} zips {ks[0].rsplit('/', 1)[-1] if ks else ''} .. {ks[-1].rsplit('/', 1)[-1] if ks else ''}")
            for k in ks:
                n = k.rsplit("/", 1)[-1]
                u = f"https://data.binance.vision/{k}"
                # o mesmo nome existe em spot e em um: o prefixo do rotulo separa
                item(L, "binance", f"G01_{rot}_{n[:-4]}".replace(".", "_"), u, f"{rot}_{n}", "zip", 100, 500 << 20,
                     checksum_url=u + ".CHECKSUM")
    return L


# ------------------------------------------------------------------ lote 5
def lote5():
    L = novo("lote5", {
        "famafrench": {"intervalo_s": 2.0, "chave": None, "licenca": "Kenneth R. French Data Library: copyright Fama-French; termos explicitos nao encontrados"},
        "fja_sp500": {"intervalo_s": 2.0, "chave": None, "licenca": "fja05680/sp500: MIT no repositorio; conteudo do indice e da S&P (INFERRED); experimental"},
    })
    FF = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
    for n in ["F-F_Research_Data_Factors_CSV.zip", "F-F_Research_Data_Factors_daily_CSV.zip",
              "F-F_Research_Data_5_Factors_2x3_CSV.zip", "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
              "F-F_Momentum_Factor_CSV.zip", "F-F_Momentum_Factor_daily_CSV.zip"]:
        item(L, "famafrench", f"B05_{n[:-4]}".replace(".", "_"), f"{FF}/{n}", n, "zip", 1000, 50 << 20)
    G = "https://raw.githubusercontent.com/fja05680/sp500/master"
    for rot, caminho in [("historical_components_changes", "S%26P%20500%20Historical%20Components%20%26%20Changes.csv"),
                         ("historical_components_changes_updated", "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"),
                         ("sp500_ticker_start_end", "sp500_ticker_start_end.csv"),
                         ("sp500_changes_since_2019", "sp500_changes_since_2019.csv"),
                         ("sp500", "sp500.csv"), ("README", "README.md"), ("LICENSE", "LICENSE")]:
        t = "texto" if rot in ("README", "LICENSE") else "csv"
        item(L, "fja_sp500", f"B16_{rot}", f"{G}/{caminho}", f"{rot}.{'md' if rot == 'README' else 'txt' if rot == 'LICENSE' else 'csv'}",
             t, 100, 100 << 20)
    return L


if __name__ == "__main__":
    for f in (lote2, lote3, lote4, lote5):
        gravar(f())
