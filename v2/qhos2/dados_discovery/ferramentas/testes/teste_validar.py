"""Testes da peca 2 (validar.py) com dados sinteticos e defeitos plantados.

  python -I -B ferramentas\\testes\\teste_validar.py
"""
import datetime as dt
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validar as V  # noqa: E402

DIA = "20261029"
FORA = dt.datetime(2026, 10, 29, 9, 0, tzinfo=dt.timezone.utc)     # 05:00 NY
DENTRO = dt.datetime(2026, 10, 29, 15, 0, tzinfo=dt.timezone.utc)  # 11:00 NY


def ms(d):
    return int(d.timestamp() * 1000)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        for sub in ("RAW", "QUARANTINE", "MANIFEST"):
            (self.raiz / sub).mkdir()
        self.registros = []

    def tearDown(self):
        self.tmp.cleanup()

    def arq(self, fonte, nome, dados, registrar=True):
        p = self.raiz / "RAW" / fonte / DIA / nome
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(dados if isinstance(dados, bytes) else dados.encode())
        if registrar:
            self.registros.append({"resultado": "ok", "destino": p.relative_to(self.raiz).as_posix(),
                                   "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
        return p

    def zipar(self, fonte, nome, membro, texto):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as zf:
            zf.writestr(membro, texto)
        return self.arq(fonte, nome, b.getvalue())

    def rodar(self, agora=FORA):
        (self.raiz / "MANIFEST" / "aquisicoes.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in self.registros), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = V.main([DIA, "--raiz", str(self.raiz)], agora=agora)
        self.err = err.getvalue()
        if rc != 0:
            return rc, None
        js = sorted((self.raiz / "MANIFEST" / "validacao").glob("*.json"))[-1]
        res = json.loads(js.read_text(encoding="utf-8"))
        return rc, {d["dataset_id"]: d for d in res["datasets"]}

    def textos(self, d):
        return " | ".join(a["texto"] for a in d["achados"])


class TesteValidar(Base):
    def montar(self):
        # VIX: 4 pregoes + 1 data repetida + 1 sabado + OHLC ruim em 05/01
        self.arq("cboe", "VIX_History.csv",
                 "DATE,OPEN,HIGH,LOW,CLOSE\n"
                 "01/02/2024,10,12,9,11\n01/03/2024,10,12,9,11\n01/04/2024,10,12,9,11\n"
                 "01/04/2024,10,12,9,11\n01/05/2024,10,9,9,11\n01/06/2024,10,12,9,11\n")
        cab = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"
        self.arq("finra", "CNMSshvol20240102.txt", cab + "20240102|AAA|10|0|20|Q\n1\n")
        self.arq("finra", "CNMSshvol20240103.txt", cab + "20240104|AAA|30|0|20|Q\n1\n")  # data errada e short>total
        self.arq("finra", "CNMSshvol20240105.txt", cab + "20240105|AAA|1|0|2|Q\n20240105|BBB|1|0|2|Q\n5\n")  # rodape errado
        # Binance spot: fev/2021 em ms com 1 minuto faltando e 1 repetido; mar/2021 em us completo
        ini = dt.datetime(2021, 2, 1, tzinfo=dt.timezone.utc)
        linhas = []
        for k in range(28 * 1440):
            if k == 100:
                continue
            t = ms(ini) + k * 60000
            linhas.append(f"{t},1,2,0.5,1.5,1,{t + 59999},1,1,1,1,0")
        linhas.append(linhas[5])                       # repetida identica
        linhas.append(linhas[6].replace(",1.5,", ",1.6,", 1))  # mesmo minuto, valor diferente
        self.zipar("binance", "spot1m_BTCUSDT-1m-2021-02.zip", "BTCUSDT-1m-2021-02.csv", "\n".join(linhas))
        ini = dt.datetime(2021, 3, 1, tzinfo=dt.timezone.utc)
        linhas = [f"{(ms(ini) + k * 60000) * 1000},1,2,0.5,1.5,1,{(ms(ini) + k * 60000 + 59999) * 1000 + 999},1,1,1,1,0"
                  for k in range(31 * 1440)]
        self.zipar("binance", "spot1m_BTCUSDT-1m-2021-03.zip", "BTCUSDT-1m-2021-03.csv", "\n".join(linhas))
        # SGS: 432 com data futura; 11 com sobreposicao entre blocos
        self.arq("bcb", "sgs432_2026_2026.json", json.dumps([{"data": "28/10/2026", "valor": "15.00"},
                                                              {"data": "05/11/2026", "valor": "15.00"}]))
        self.arq("bcb", "sgs11_a.json", json.dumps([{"data": "26/10/2026", "valor": "0,05"}]))
        self.arq("bcb", "sgs11_b.json", json.dumps([{"data": "26/10/2026", "valor": "0,05"},
                                                    {"data": "27/10/2026", "valor": "x"}]))
        # CFTC com mercado/data repetido
        self.zipar("cftc", "deacot2026.zip", "annual.txt",
                   '"Market and Exchange Names","As of Date in Form YYYY-MM-DD","CFTC Contract Market Code"\n'
                   '"A",2026-10-20,001\n"A",2026-10-20,001\n"B",2026-10-13,002\n')
        # tar com caminho perigoso
        b = io.BytesIO()
        with tarfile.open(fileobj=b, mode="w:gz") as tf:
            for nome, conteudo in [("version", b"2026f\n"), ("../fora", b"x")]:
                ti = tarfile.TarInfo(nome)
                ti.size = len(conteudo)
                tf.addfile(ti, io.BytesIO(conteudo))
        self.arq("iana", "tzdata2026f.tar.gz", b.getvalue())

    def foto(self):
        return {p.relative_to(self.raiz).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((self.raiz / "RAW").rglob("*")) if p.is_file()}

    def test_defeitos_plantados(self):
        self.montar()
        antes = self.foto()
        rc, ds = self.rodar()
        self.assertEqual(rc, 0)
        self.assertEqual(self.foto(), antes, "RAW nao pode mudar")

        vix = self.textos(ds["B01_CBOE_VOL_IDX"])
        self.assertIn("datas repetidas", vix)
        self.assertIn("sabado/domingo", vix)
        self.assertIn("OHLC inconsistente", vix)

        fin = ds["F08_FINRA_SHVOL"]
        t = self.textos(fin)
        self.assertIn("Date da linha diferente", t)
        self.assertIn("ShortVolume > TotalVolume", t)
        self.assertNotIn("linha mal formada", t)
        rod = next(a for a in fin["achados"] if "rodape com contagem" in a["texto"])
        self.assertEqual(rod["exemplos"], ["CNMSshvol20240105.txt: rodape 5, linhas 2"])
        sem = next(a for a in fin["achados"] if "sem arquivo FINRA" in a["texto"])
        self.assertEqual(sem["exemplos"], ["2024-01-04"])

        bn = ds["G01_BINANCE_VISION"]
        c = bn["checagens"]["spot1m_BTCUSDT"]
        self.assertEqual(c["faltando"], 1)
        self.assertEqual(c["duplicadas_identicas"], 1)
        self.assertEqual(c["duplicadas_conflitantes"], 1)
        self.assertEqual(c["primeiro_arquivo_em_microssegundos"], "2021-03-01")
        self.assertEqual(c["close_time_inesperado"], 0)
        self.assertIn("unidade do timestamp muda", self.textos(bn))

        sgs = self.textos(ds["D07_BCB_SGS"])
        self.assertIn("datas depois de hoje", sgs)
        self.assertIn("sgs11: 1 datas repetidas", sgs)
        self.assertIn("nao numericos", sgs)

        self.assertIn("mesmo mercado e data", self.textos(ds["F01_CFTC_COT_FILES"]))
        self.assertIn("'..'", self.textos(ds["I03_TZDB"]))
        self.assertEqual(ds["INTEGRIDADE"]["achados"], [])
        self.assertEqual(ds["E01_UST_CURVE"]["veredito"], "sem arquivos")

    def test_integridade(self):
        self.montar()
        p = self.raiz / "RAW" / "cboe" / DIA / "VIX_History.csv"
        p.write_text(p.read_text() + "01/08/2024,10,12,9,11\n")
        self.arq("cboe", "SKEW_History.csv", "DATE,SKEW\n01/02/2024,120\n", registrar=False)
        rc, ds = self.rodar()
        t = self.textos(ds["INTEGRIDADE"])
        self.assertIn("SHA-256 diferente", t)
        self.assertIn("sem linha 'ok'", t)

    def test_recusa_na_janela(self):
        self.montar()
        rc, _ = self.rodar(agora=DENTRO)
        self.assertEqual(rc, 2)
        self.assertIn("janela de captura", self.err)
        self.assertFalse((self.raiz / "MANIFEST" / "validacao").exists())

    def test_nao_sobrescreve_relatorio(self):
        self.montar()
        self.rodar()
        self.rodar()
        self.assertEqual(len(list((self.raiz / "MANIFEST" / "validacao").glob("*.json"))), 2)


class TesteUtil(unittest.TestCase):
    def test_num(self):
        self.assertEqual(V.num("0,05"), 0.05)
        self.assertEqual(V.num(" 1.5 "), 1.5)
        self.assertIsNone(V.num("NA"))
        with self.assertRaises(ValueError):
            V.num("x")

    def test_resumo_datas(self):
        d = [dt.date(2024, 1, 2), dt.date(2024, 1, 5), dt.date(2024, 1, 3)]
        r = V.resumo_datas(d, uteis=True)
        self.assertEqual(r["fora_de_ordem"], 1)
        self.assertEqual(r["dias_uteis_sem_dado"], 1)  # 04/01
        self.assertEqual(r["maior_buraco_dias"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
