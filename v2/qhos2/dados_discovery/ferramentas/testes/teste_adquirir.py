"""Testes da peca 1 (adquirir.py). So loopback 127.0.0.1, sem rede externa.

  python -I ferramentas\\testes\\teste_adquirir.py
"""
import datetime as dt, hashlib, http.server, io, json, os, sys, tempfile, threading, unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adquirir as A  # noqa: E402

SEGREDO = "SEGREDO-teste-9f3a"
FORA_DA_JANELA = dt.datetime(2026, 10, 28, 22, 0, tzinfo=dt.timezone.utc)   # qua 18:00 NY
DENTRO_DA_JANELA = dt.datetime(2026, 10, 28, 14, 0, tzinfo=dt.timezone.utc)  # qua 10:00 NY
CSV = b"data,valor\n2026-01-02,1.5\n2026-01-05,1.6\n"
ZIP = b"PK\x03\x04" + b"conteudo-zip" * 10


class Servidor(http.server.BaseHTTPRequestHandler):
    contagem = {}

    def log_message(self, *a):
        pass

    def _envia(self, corpo, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Last-Modified", "Tue, 27 Oct 2026 12:00:00 GMT")
        self.send_header("ETag", '"abc123"')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        caminho = self.path.split("?")[0]
        n = Servidor.contagem[caminho] = Servidor.contagem.get(caminho, 0) + 1
        if caminho == "/ok.csv":
            self._envia(CSV)
        elif caminho == "/instavel.csv":
            self._envia(b"ocupado", 503, {"Retry-After": "0"}) if n <= 2 else self._envia(CSV)
        elif caminho == "/sempre503.csv":
            self._envia(b"ocupado", 503)
        elif caminho == "/nao_existe.csv":
            self._envia(b"nao", 404)
        elif caminho == "/html.csv":
            self._envia(b"<!DOCTYPE html><html>login</html>")
        elif caminho == "/grande.bin":
            self._envia(b"x" * 5000)
        elif caminho == "/chave.csv":
            ok = f"api_key={SEGREDO}" in self.path
            self._envia(CSV if ok else b"sem chave", 200 if ok else 403)
        elif caminho == "/a.zip":
            self._envia(ZIP)
        elif caminho == "/a.zip.CHECKSUM":
            self._envia(f"{hashlib.sha256(ZIP).hexdigest()}  a.zip\n".encode())
        elif caminho == "/b.zip.CHECKSUM":
            self._envia(("0" * 64 + "  b.zip\n").encode())
        elif caminho == "/fora":
            self.send_response(302)
            self.send_header("Location", "http://exemplo.invalid/x.csv")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._envia(b"?", 404)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Servidor)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        Servidor.contagem = {}
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        for sub in ("RAW", "QUARANTINE", "MANIFEST"):
            (self.raiz / sub).mkdir()
        self.esperas = []
        os.environ["QHOS_TESTE_CHAVE"] = SEGREDO

    def tearDown(self):
        os.environ.pop("QHOS_TESTE_CHAVE", None)
        self.tmp.cleanup()

    def lote(self, itens, fontes=None, nome="teste"):
        f = fontes or {"loc": {"intervalo_s": 0, "licenca": "teste"},
                       "comchave": {"intervalo_s": 0, "licenca": "teste",
                                    "chave": {"credman": "QHOS_DADOS/__teste_inexistente__",
                                              "env": "QHOS_TESTE_CHAVE"}}}
        p = self.raiz / f"{nome}.json"
        p.write_text(json.dumps({"lote": nome, "fontes": f, "itens": itens}), encoding="utf-8")
        return p

    def rodar(self, lote, *extra, agora=FORA_DA_JANELA, relogio=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = A.main([str(lote), "--raiz", str(self.raiz), "--permitir-loopback",
                         "--espera-base", "0.01", *extra],
                        agora=agora, dormir=self.esperas.append, relogio=relogio)
        return rc, out.getvalue(), err.getvalue()

    def jsonl(self):
        p = self.raiz / "MANIFEST" / "aquisicoes.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


class TesteRelogio(unittest.TestCase):
    def caso(self, iso, esperado):
        t = dt.datetime.fromisoformat(iso).replace(tzinfo=dt.timezone.utc)
        self.assertEqual(A.em_janela_captura(t), esperado, iso)

    def test_janela(self):
        self.caso("2026-10-28T13:30:00", True)    # qua 09:30 EDT
        self.caso("2026-10-28T12:59:00", False)   # 08:59 EDT
        self.caso("2026-10-28T20:14:59", True)    # 16:14 EDT
        self.caso("2026-10-28T20:15:00", False)   # 16:15 EDT
        self.caso("2026-10-31T15:00:00", False)   # sabado
        self.caso("2026-11-02T14:05:00", True)    # seg 09:05 EST (horario de verao acabou 01/11)
        self.caso("2026-11-02T13:30:00", False)   # 08:30 EST
        self.caso("2026-03-09T13:05:00", True)    # seg 09:05 EDT (comecou 08/03)
        self.caso("2026-03-06T13:05:00", False)   # sex 08:05 EST

    def test_domingos(self):
        self.assertEqual(A._domingo(2026, 3, 2), dt.date(2026, 3, 8))
        self.assertEqual(A._domingo(2026, 11, 1), dt.date(2026, 11, 1))


class TesteLote(Base):
    def erro(self, itens, **kw):
        with self.assertRaises(A.ErroLote) as c:
            A.carregar_lote(self.lote(itens, **kw), loopback=True)
        return str(c.exception)

    def test_recusas(self):
        u = self.url + "/ok.csv"
        self.assertIn("nome simples", self.erro([{"id": "x", "fonte": "loc", "url": u, "arquivo": "../x.csv"}]))
        self.assertIn("nome simples", self.erro([{"id": "x", "fonte": "loc", "url": u, "arquivo": "a\\b.csv"}]))
        self.assertIn("https://", self.erro([{"id": "x", "fonte": "loc", "url": "http://exemplo.com/a", "arquivo": "a"}]))
        self.assertIn("id repetido", self.erro([{"id": "x", "fonte": "loc", "url": u, "arquivo": "a"},
                                                {"id": "x", "fonte": "loc", "url": u, "arquivo": "b"}]))
        self.assertIn("{CHAVE}", self.erro([{"id": "x", "fonte": "comchave", "url": u, "arquivo": "a"}]))
        self.assertIn("sem licenca", self.erro([{"id": "x", "fonte": "f", "url": u, "arquivo": "a"}],
                                               fontes={"f": {"intervalo_s": 0}}))

    def test_loopback_so_com_opcao(self):
        p = self.lote([{"id": "x", "fonte": "loc", "url": self.url + "/ok.csv", "arquivo": "a.csv"}])
        with self.assertRaises(A.ErroLote):
            A.carregar_lote(p, loopback=False)


class TesteExecucao(Base):
    def itens(self):
        u = self.url
        return [
            {"id": "ok", "fonte": "loc", "url": u + "/ok.csv", "arquivo": "ok.csv", "tipo": "csv"},
            {"id": "instavel", "fonte": "loc", "url": u + "/instavel.csv", "arquivo": "instavel.csv", "tipo": "csv"},
            {"id": "sempre503", "fonte": "loc", "url": u + "/sempre503.csv", "arquivo": "s.csv"},
            {"id": "nao_existe", "fonte": "loc", "url": u + "/nao_existe.csv", "arquivo": "n.csv"},
            {"id": "html", "fonte": "loc", "url": u + "/html.csv", "arquivo": "html.csv", "tipo": "csv"},
            {"id": "grande", "fonte": "loc", "url": u + "/grande.bin", "arquivo": "g.bin", "max_bytes": 1000},
            {"id": "chave", "fonte": "comchave", "url": u + "/chave.csv?api_key={CHAVE}", "arquivo": "k.csv", "tipo": "csv"},
            {"id": "zip_ok", "fonte": "loc", "url": u + "/a.zip", "arquivo": "a.zip", "tipo": "zip",
             "checksum_url": u + "/a.zip.CHECKSUM"},
            {"id": "zip_ruim", "fonte": "loc", "url": u + "/a.zip", "arquivo": "b.zip", "tipo": "zip",
             "checksum_url": u + "/b.zip.CHECKSUM"},
            {"id": "sha_errado", "fonte": "loc", "url": u + "/ok.csv", "arquivo": "e.csv", "sha256_esperado": "1" * 64},
            {"id": "redir", "fonte": "loc", "url": u + "/fora", "arquivo": "r.csv"},
        ]

    def test_simulacao_nao_toca_rede(self):
        rc, out, _ = self.rodar(self.lote(self.itens()))
        self.assertEqual(rc, 0)
        self.assertIn("SIMULACAO", out)
        self.assertIn("encontrada (env)", out)
        self.assertEqual(Servidor.contagem, {})
        self.assertEqual(list((self.raiz / "RAW").iterdir()), [])
        self.assertNotIn(SEGREDO, out)

    def test_recusa_na_janela(self):
        rc, _, err = self.rodar(self.lote(self.itens()), "--executar", agora=DENTRO_DA_JANELA)
        self.assertEqual(rc, 2)
        self.assertIn("janela de captura", err)
        self.assertEqual(Servidor.contagem, {})

    def test_recusa_sem_chave(self):
        os.environ.pop("QHOS_TESTE_CHAVE")
        rc, _, err = self.rodar(self.lote(self.itens()), "--executar")
        self.assertEqual(rc, 2)
        self.assertIn("chave ausente", err)
        self.assertEqual(Servidor.contagem, {})

    def test_para_quando_a_janela_abre_no_meio(self):
        u = self.url + "/ok.csv"
        itens = [{"id": f"i{n}", "fonte": "loc", "url": u, "arquivo": f"{n}.csv", "tipo": "csv"} for n in range(3)]
        lote = self.lote(itens)
        horas = iter([FORA_DA_JANELA, DENTRO_DA_JANELA])
        rc, out, _ = self.rodar(lote, "--executar", relogio=lambda: next(horas))
        self.assertEqual(rc, 3)
        self.assertIn("INTERROMPIDO", out)
        self.assertEqual([r["id"] for r in self.jsonl()], ["i0"])
        self.assertFalse((self.raiz / "MANIFEST" / "adquirir.lock").exists())
        # fora da janela de novo: retoma do checkpoint
        rc, _, _ = self.rodar(lote, "--executar")
        self.assertEqual(rc, 0)
        self.assertEqual([r["id"] for r in self.jsonl()], ["i0", "i1", "i2"])

    def test_trava(self):
        (self.raiz / "MANIFEST" / "adquirir.lock").write_text("outro")
        rc, _, err = self.rodar(self.lote(self.itens()), "--executar")
        self.assertEqual(rc, 2)
        self.assertIn("outra execucao", err)

    def test_execucao_completa(self):
        lote = self.lote(self.itens())
        rc, out, _ = self.rodar(lote, "--executar")
        self.assertEqual(rc, 1)
        regs = {r["id"]: r for r in self.jsonl()}
        self.assertEqual(len(regs), 11)
        dia = FORA_DA_JANELA.strftime("%Y%m%d")

        r = regs["ok"]
        self.assertEqual(r["resultado"], "ok")
        self.assertEqual(r["sha256"], hashlib.sha256(CSV).hexdigest())
        self.assertEqual(r["bytes"], len(CSV))
        self.assertEqual(r["last_modified"], "Tue, 27 Oct 2026 12:00:00 GMT")
        self.assertEqual(r["etag"], '"abc123"')
        self.assertEqual(r["licenca"], "teste")
        self.assertEqual((self.raiz / "RAW" / "loc" / dia / "ok.csv").read_bytes(), CSV)

        self.assertEqual((regs["instavel"]["resultado"], regs["instavel"]["tentativas"]), ("ok", 3))
        self.assertEqual((regs["sempre503"]["resultado"], regs["sempre503"]["tentativas"]), ("falha", 3))
        self.assertEqual(regs["sempre503"]["status_http"], 503)
        self.assertEqual(Servidor.contagem["/sempre503.csv"], 3)
        self.assertIn(0.01, self.esperas)   # sem Retry-After: 0.01 e depois 0.02
        self.assertIn(0.02, self.esperas)
        self.assertIn(0.0, self.esperas)    # instavel manda Retry-After: 0
        self.assertEqual((regs["nao_existe"]["resultado"], regs["nao_existe"]["tentativas"]), ("falha", 1))
        self.assertEqual(regs["nao_existe"]["status_http"], 404)
        self.assertEqual(Servidor.contagem["/nao_existe.csv"], 1)
        self.assertTrue(regs["nao_existe"]["destino"].endswith(".motivo.json"))

        for i in ("html", "grande", "zip_ruim", "sha_errado"):
            self.assertEqual(regs[i]["resultado"], "quarentena", i)
            self.assertTrue((self.raiz / regs[i]["destino"]).exists(), i)
            self.assertTrue(regs[i]["destino"].startswith("QUARANTINE/"), i)
        self.assertIn("HTML", " ".join(regs["html"]["motivos"]))
        self.assertIn("max_bytes", " ".join(regs["grande"]["motivos"]))
        self.assertIn("publicado", " ".join(regs["zip_ruim"]["motivos"]))
        self.assertEqual(regs["zip_ok"]["resultado"], "ok")
        self.assertEqual(regs["zip_ok"]["sha256_publicado"], hashlib.sha256(ZIP).hexdigest())
        self.assertEqual(regs["redir"]["resultado"], "falha")
        self.assertIn("redirecionamento recusado", " ".join(regs["redir"]["motivos"]))

        r = regs["chave"]
        self.assertEqual(r["resultado"], "ok")
        self.assertIn("{CHAVE}", r["url"])
        self.assertIn("{CHAVE}", r["url_final"])

        # nenhum .part sobrou e nada em RAW alem dos ok
        self.assertEqual(list(self.raiz.rglob("*.part")), [])
        em_raw = sorted(p.name for p in (self.raiz / "RAW").rglob("*") if p.is_file())
        self.assertEqual(em_raw, ["a.zip", "instavel.csv", "k.csv", "ok.csv"])
        self.assertFalse((self.raiz / "MANIFEST" / "adquirir.lock").exists())

        # a chave nao aparece em nenhum arquivo gravado nem na saida
        self.assertNotIn(SEGREDO, out)
        for p in self.raiz.rglob("*"):
            if p.is_file():
                self.assertNotIn(SEGREDO.encode(), p.read_bytes(), str(p))

        # retomada: so os que falharam rodam de novo; nada e sobrescrito
        Servidor.contagem = {}
        rc2, out2, _ = self.rodar(lote, "--executar")
        self.assertEqual(rc2, 1)
        self.assertEqual(Servidor.contagem.get("/ok.csv"), 1)  # so o sha_errado; o item ok foi pulado
        novos = self.jsonl()[11:]
        self.assertEqual({r["id"] for r in novos},
                         {"sempre503", "nao_existe", "html", "grande", "zip_ruim", "sha_errado", "redir"})
        self.assertIn("pulado (checkpoint", out2)
        # quarentena repetida nao sobrescreve a anterior
        self.assertTrue(any(p.name.startswith("html_1") for p in (self.raiz / "QUARANTINE").rglob("*")))

    def test_nao_sobrescreve_raw(self):
        dia = FORA_DA_JANELA.strftime("%Y%m%d")
        pasta = self.raiz / "RAW" / "loc" / dia
        pasta.mkdir(parents=True)
        (pasta / "ok.csv").write_bytes(b"antigo")
        rc, _, _ = self.rodar(self.lote([{"id": "ok", "fonte": "loc", "url": self.url + "/ok.csv",
                                          "arquivo": "ok.csv"}]), "--executar")
        self.assertEqual(rc, 1)
        self.assertEqual((pasta / "ok.csv").read_bytes(), b"antigo")
        self.assertIn("ja existe", " ".join(self.jsonl()[0]["motivos"]))


class TesteCredman(unittest.TestCase):
    def test_alvo_inexistente(self):
        self.assertIsNone(A.ler_credman("QHOS_DADOS/__teste_inexistente__"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
