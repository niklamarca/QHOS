"""Gera lote1.json (oficiais, sem cadastro).

Padroes de URL conferidos em 29/10/2026 ~02:45Z com GET de 2 KB nos anos extremos de cada serie
(o 1o ano com conteudo e 2026); os anos do meio seguem o mesmo padrao e sao conferidos pelo download.

Rodar: python -I -B ferramentas\\lotes\\gerar_lote1.py
"""
import json
from pathlib import Path

UA = "QHOS-pesquisa (contato: QHOS)"
ANO_FINAL = 2026

fontes = {
    "cboe": {"intervalo_s": 2.0, "licenca": "Cboe: sem licenca formal; 'furnished without responsibility for accuracy'", "chave": None},
    "treasury": {"intervalo_s": 2.0, "licenca": "US Treasury: dado governamental", "chave": None},
    "fed": {"intervalo_s": 2.0, "licenca": "Federal Reserve: dado do Fed (GSW: 'not an official statistical release')", "chave": None},
    "cftc": {"intervalo_s": 2.0, "licenca": "CFTC: dado governamental (termos nao lidos)", "chave": None},
    "bcb": {"intervalo_s": 2.0, "licenca": "BCB dados abertos: ODbL", "chave": None},
}
itens = []


def item(fonte, id_, url, arquivo, tipo, min_bytes=1, max_bytes=None):
    itens.append({"id": id_, "fonte": fonte, "url": url, "arquivo": arquivo, "tipo": tipo,
                  "min_bytes": min_bytes, "max_bytes": max_bytes, "sha256_esperado": None, "checksum_url": None})


# B01 Cboe
for s in ["VIX", "VIX9D", "VIX3M", "VIX6M", "VVIX", "SKEW", "OVX", "GVZ"]:
    item("cboe", f"B01_{s}", f"https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{s}_History.csv",
         f"{s}_History.csv", "csv", 1000, 50 << 20)

# E01 Treasury: um CSV por ano e por tipo (primeiro ano conferido com conteudo)
TR = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv"
for tipo, ini in [("daily_treasury_yield_curve", 1990), ("daily_treasury_real_yield_curve", 2003),
                  ("daily_treasury_bill_rates", 2002), ("daily_treasury_long_term_rate", 2000),
                  ("daily_treasury_real_long_term", 2000)]:
    for a in range(ini, ANO_FINAL + 1):
        item("treasury", f"E01_{tipo}_{a}",
             f"{TR}/{a}/all?type={tipo}&field_tdr_date_value={a}&page&_format=csv",
             f"{tipo}_{a}.csv", "csv", 100, 20 << 20)

# E03/E04 GSW e D06 FOMC
item("fed", "E03_feds200628", "https://www.federalreserve.gov/data/yield-curve-tables/feds200628.csv",
     "feds200628.csv", "csv", 1 << 20, 200 << 20)
item("fed", "E04_feds200805", "https://www.federalreserve.gov/data/yield-curve-tables/feds200805.csv",
     "feds200805.csv", "csv", 1 << 20, 200 << 20)
for pag in ["fomccalendars", "fomc_historical", "fomc_historical_year"]:
    item("fed", f"D06_{pag}", f"https://www.federalreserve.gov/monetarypolicy/{pag}.htm", f"{pag}.htm", "html", 10000, 20 << 20)

# F01 CFTC COT: historico 1986/2006-2016 + um ZIP por ano desde 2017
H = "https://www.cftc.gov/files/dea/history"
for nome in ["deacot1986_2016", "fut_disagg_txt_hist_2006_2016", "fin_fut_txt_2006_2016"]:
    item("cftc", f"F01_{nome}", f"{H}/{nome}.zip", f"{nome}.zip", "zip", 1000, 500 << 20)
for a in range(2017, ANO_FINAL + 1):
    for pre in ["deacot", "fut_disagg_txt_", "fut_fin_txt_"]:
        nome = f"{pre}{a}"
        item("cftc", f"F01_{nome}", f"{H}/{nome}.zip", f"{nome}.zip", "zip", 1000, 100 << 20)

# D07 BCB SGS (json: a 432 recusa formato=csv; janela maxima de 10 anos em serie diaria)
SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{c}/dados?formato=json&dataInicial={i}&dataFinal={f}"
for cod, blocos in [(11, [(1986, 1995), (1996, 2005), (2006, 2015), (2016, 2025), (2026, 2026)]),
                    (432, [(1999, 2008), (2009, 2018), (2019, 2026)])]:
    for a0, a1 in blocos:
        item("bcb", f"D07_sgs{cod}_{a0}_{a1}", SGS.format(c=cod, i=f"01/01/{a0}", f=f"31/12/{a1}"),
             f"sgs{cod}_{a0}_{a1}.json", "json", 10, 50 << 20)

# C05 PTAX e D08 Focus (Olinda, CSV)
OL = "https://olinda.bcb.gov.br/olinda/servico"
item("bcb", "C05_ptax_dolar",
     f"{OL}/PTAX/versao/v1/odata/CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
     f"?@dataInicial='01-01-1984'&@dataFinalCotacao='12-31-{ANO_FINAL}'&$format=text/csv",
     "ptax_dolar.csv", "csv", 1000, 100 << 20)
for rot, ind in [("ipca", "IPCA"), ("cambio", "C%C3%A2mbio"), ("pib", "PIB%20Total"), ("selic", "Selic")]:
    item("bcb", f"D08_focus_anual_{rot}",
         f"{OL}/Expectativas/versao/v1/odata/ExpectativasMercadoAnuais?$filter=Indicador%20eq%20'{ind}'&$format=text/csv",
         f"focus_anual_{rot}.csv", "csv", 1000, 1 << 30)
item("bcb", "D08_focus_selic_reuniao", f"{OL}/Expectativas/versao/v1/odata/ExpectativasMercadoSelic?$format=text/csv",
     "focus_selic_reuniao.csv", "csv", 1000, 1 << 30)

lote = {"lote": "lote1", "user_agent": UA, "timeout_s": 120, "fontes": fontes, "itens": itens}
destino = Path(__file__).with_name("lote1.json")
destino.write_text(json.dumps(lote, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{destino}: {len(itens)} itens")
