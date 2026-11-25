"""Peca 1 - adquirir.py: baixa um lote aprovado para RAW/ sem alterar os bytes.

Uso (sempre com -I):
  python -I adquirir.py LOTE.json              simulacao: lista o que faria, sem rede
  python -I adquirir.py LOTE.json --executar   baixa de verdade

Regras (PLANO_AQUISICAO_20261028.md):
- so biblioteca padrao; cada arquivo vai para RAW/<fonte>/<AAAAMMDD>/ como veio;
- cada item gera uma linha em MANIFEST/aquisicoes.jsonl (url sem chave, UTC, HTTP, bytes,
  SHA-256, Last-Modified/ETag, licenca); o que falha vai para QUARANTINE/ com o motivo;
- nada e apagado: parciais e invalidos vao para QUARANTINE/, nunca para o lixo;
- limite por fonte, 3 tentativas com espera exponencial, checkpoint para retomar, log;
- chaves so do Gerenciador de Credenciais do Windows ou de variavel de ambiente;
  a chave nunca e gravada: a URL registrada guarda o marcador {CHAVE};
- recusa --executar durante a janela de captura Rithmic (seg-sex 09:00-16:15 em Nova York).
"""
import argparse, datetime as dt, hashlib, http.client, json, logging, os, re, shutil, sys, time
import urllib.error, urllib.parse, urllib.request
from pathlib import Path

ESQUEMA = "qhos_dados.aquisicao.v1"
UA_PADRAO = "QHOS-pesquisa (contato: QHOS)"
TENTATIVAS = 3
HTTP_RETENTAVEL = {429, 500, 502, 503, 504}
ESPERA_MAX_S = 300.0
BLOCO = 1 << 20
TIPOS = {"csv", "texto", "json", "zip", "gz", "html", "binario"}
RE_NOME = re.compile(r"^[A-Za-z0-9_-]+$")
RE_ARQUIVO = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")
RE_HEX64 = re.compile(r"\b([0-9a-fA-F]{64})\b")
LOOPBACK = ("http://127.0.0.1:", "http://127.0.0.1/")

log = logging.getLogger("adquirir")


class ErroLote(Exception):
    """Lote mal formado: nada roda."""


class NaoRetentavel(Exception):
    """Falha que nao melhora tentando de novo."""

    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class Retentavel(Exception):
    """Falha transitoria (rede, 5xx, 429, leitura incompleta)."""

    def __init__(self, msg, status=None, espera=None):
        super().__init__(msg)
        self.status, self.espera = status, espera


# ---------------------------------------------------------------- relogio
def _domingo(ano, mes, n):
    """n-esimo domingo do mes."""
    d = dt.date(ano, mes, 1)
    return d + dt.timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))


def hora_ny(agora_utc):
    """UTC -> hora local de Nova York pela regra dos EUA desde 2007 (sem tzdata)."""
    ano = agora_utc.year
    inicio = dt.datetime.combine(_domingo(ano, 3, 2), dt.time(7), dt.timezone.utc)   # 02:00 EST
    fim = dt.datetime.combine(_domingo(ano, 11, 1), dt.time(6), dt.timezone.utc)     # 02:00 EDT
    desvio = -4 if inicio <= agora_utc < fim else -5
    return (agora_utc + dt.timedelta(hours=desvio)).replace(tzinfo=None)


def em_janela_captura(agora_utc):
    ny = hora_ny(agora_utc)
    return ny.weekday() < 5 and dt.time(9, 0) <= ny.time() < dt.time(16, 15)


def utc_iso(t=None):
    t = t or dt.datetime.now(dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------- chaves
def ler_credman(alvo):
    """Le a senha de uma credencial generica do Windows (cmdkey /generic:ALVO). None se nao houver."""
    if os.name != "nt" or not alvo:
        return None
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                    ("LastWritten", FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                    ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
    adv.CredReadW.restype = wintypes.BOOL
    adv.CredFree.argtypes = [ctypes.c_void_p]
    p = ctypes.POINTER(CREDENTIALW)()
    if not adv.CredReadW(alvo, 1, 0, ctypes.byref(p)):  # 1 = CRED_TYPE_GENERIC
        return None
    try:
        c = p.contents
        blob = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
    finally:
        adv.CredFree(p)
    try:
        valor = blob.decode("utf-16-le")
    except UnicodeDecodeError:
        valor = blob.decode("utf-8", "replace")
    return valor.strip() or None


def obter_chave(cfg):
    """cfg = {"credman": "QHOS_DADOS/FRED", "env": "QHOS_FRED_KEY"}; credman primeiro."""
    valor = ler_credman(cfg.get("credman"))
    if valor:
        return valor, "credman"
    valor = (os.environ.get(cfg.get("env") or "") or "").strip()
    return (valor, "env") if valor else (None, None)


class Mascara:
    """Tira segredos de qualquer texto antes de log, manifesto ou quarentena."""

    def __init__(self):
        self.segredos = set()

    def add(self, s):
        if s:
            self.segredos.update({s, urllib.parse.quote(s, safe="")})

    def __call__(self, texto):
        texto = str(texto)
        for s in sorted(self.segredos, key=len, reverse=True):
            texto = texto.replace(s, "{CHAVE}")
        return texto


# ---------------------------------------------------------------- lote
def url_permitida(url, loopback):
    return url.startswith("https://") or (loopback and url.startswith(LOOPBACK))


def carregar_lote(caminho, loopback=False):
    try:
        lote = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ErroLote(f"nao consegui ler o lote: {e}")
    erros = []
    if not RE_NOME.match(str(lote.get("lote", ""))):
        erros.append("campo 'lote' ausente ou com caracteres invalidos")
    fontes = lote.get("fontes")
    if not isinstance(fontes, dict) or not fontes:
        erros.append("'fontes' vazio")
        fontes = {}
    for nome, f in fontes.items():
        if not RE_NOME.match(nome):
            erros.append(f"fonte '{nome}': nome invalido")
        if not str(f.get("licenca", "")).strip():
            erros.append(f"fonte '{nome}': sem licenca")
        if float(f.get("intervalo_s", 1.0)) < 0:
            erros.append(f"fonte '{nome}': intervalo_s negativo")
        ch = f.get("chave")
        if ch is not None and not (ch.get("credman") or ch.get("env")):
            erros.append(f"fonte '{nome}': chave sem credman nem env")
    itens = lote.get("itens")
    if not isinstance(itens, list) or not itens:
        erros.append("'itens' vazio")
        itens = []
    vistos, destinos = set(), set()
    for i, it in enumerate(itens):
        rot = it.get("id", f"#{i}")
        if not RE_NOME.match(str(it.get("id", ""))):
            erros.append(f"item {rot}: id invalido")
        if rot in vistos:
            erros.append(f"item {rot}: id repetido")
        vistos.add(rot)
        f = fontes.get(it.get("fonte"))
        if f is None:
            erros.append(f"item {rot}: fonte desconhecida")
            continue
        arq = str(it.get("arquivo", ""))
        if not RE_ARQUIVO.match(arq) or arq.endswith(".part"):
            erros.append(f"item {rot}: arquivo deve ser um nome simples, sem pasta")
        if (it["fonte"], arq) in destinos:
            erros.append(f"item {rot}: arquivo repetido na mesma fonte")
        destinos.add((it["fonte"], arq))
        for campo in ("url", "checksum_url"):
            u = it.get(campo)
            if (campo == "url" or u) and not url_permitida(str(u), loopback):
                erros.append(f"item {rot}: {campo} precisa ser https://")
        tem_marcador = "{CHAVE}" in str(it.get("url", "")) + str(it.get("checksum_url", ""))
        if f.get("chave") and not tem_marcador:
            erros.append(f"item {rot}: a fonte usa chave, mas a url nao tem {{CHAVE}}")
        if tem_marcador and not f.get("chave"):
            erros.append(f"item {rot}: url com {{CHAVE}} numa fonte sem chave")
        if it.get("tipo", "binario") not in TIPOS:
            erros.append(f"item {rot}: tipo fora de {sorted(TIPOS)}")
        esp = it.get("sha256_esperado")
        if esp and not re.fullmatch(r"[0-9a-fA-F]{64}", esp):
            erros.append(f"item {rot}: sha256_esperado invalido")
        for campo in ("min_bytes", "max_bytes"):
            if campo in it and (not isinstance(it[campo], int) or it[campo] < 0):
                erros.append(f"item {rot}: {campo} deve ser inteiro >= 0")
    if erros:
        raise ErroLote("lote invalido:\n  - " + "\n  - ".join(erros))
    return lote


# ---------------------------------------------------------------- rede
class _Redirecionamento(urllib.request.HTTPRedirectHandler):
    def __init__(self, loopback):
        self.loopback = loopback

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not url_permitida(newurl, self.loopback):
            raise NaoRetentavel(f"redirecionamento recusado para {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Limitador:
    """Intervalo minimo entre requisicoes da mesma fonte."""

    def __init__(self, dormir=time.sleep, relogio=time.monotonic):
        self.ultimo, self.dormir, self.relogio = {}, dormir, relogio

    def esperar(self, fonte, intervalo):
        falta = self.ultimo.get(fonte, -1e18) + intervalo - self.relogio()
        if falta > 0:
            self.dormir(falta)
        self.ultimo[fonte] = self.relogio()


def _retry_after(valor):
    try:
        return min(float(valor), ESPERA_MAX_S)
    except (TypeError, ValueError):
        return None


def abrir_http(abrir, req, timeout):
    """Abre a requisicao e traduz os erros em Retentavel/NaoRetentavel."""
    try:
        return abrir(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        e.close()
        if e.code in HTTP_RETENTAVEL:
            raise Retentavel(f"HTTP {e.code}", e.code, _retry_after(e.headers.get("Retry-After")))
        raise NaoRetentavel(f"HTTP {e.code}", e.code) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
        raise Retentavel(f"rede: {getattr(e, 'reason', e)}")


def baixar_uma_vez(abrir, url, part, ua, timeout, max_bytes):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
    r = abrir_http(abrir, req, timeout)
    with r:
        h = r.headers
        meta = {"status_http": r.status, "url_final": r.geturl(),
                "last_modified": h.get("Last-Modified"), "etag": h.get("ETag"),
                "content_type": h.get("Content-Type"), "content_length": h.get("Content-Length")}
        sha, n = hashlib.sha256(), 0
        try:
            with open(part, "wb") as f:
                while True:
                    b = r.read(BLOCO)
                    if not b:
                        break
                    n += len(b)
                    if max_bytes and n > max_bytes:
                        raise NaoRetentavel(f"passou de max_bytes={max_bytes}")
                    sha.update(b)
                    f.write(b)
                f.flush()
                os.fsync(f.fileno())
        except (http.client.HTTPException, TimeoutError, ConnectionError) as e:
            # erro de disco (OSError comum) nao entra aqui: sobe e para o lote
            raise Retentavel(f"leitura interrompida apos {n} bytes: {e}")
    cl = meta["content_length"]
    if cl and cl.isdigit() and int(cl) != n:
        raise Retentavel(f"leitura incompleta: {n} de {cl} bytes")
    meta["bytes"], meta["sha256"] = n, sha.hexdigest()
    return meta


def com_tentativas(fn, limitador, fonte, intervalo, espera_base, dormir, mascara):
    """Roda fn ate TENTATIVAS vezes; devolve (resultado, tentativas, ultimo_erro)."""
    erro = None
    for t in range(1, TENTATIVAS + 1):
        limitador.esperar(fonte, intervalo)
        try:
            return fn(), t, None
        except Retentavel as e:
            erro = e
            if t == TENTATIVAS:
                break
            espera = e.espera if e.espera is not None else espera_base * 2 ** (t - 1)
            log.warning("  tentativa %d falhou (%s); nova em %.1f s", t, mascara(e), espera)
            dormir(espera)
        except NaoRetentavel as e:
            return None, t, e
    return None, TENTATIVAS, erro


# ---------------------------------------------------------------- validacao
def validar_conteudo(part, it, n, sha):
    motivos = []
    if n < it.get("min_bytes", 1):
        motivos.append(f"{n} bytes, abaixo de min_bytes={it.get('min_bytes', 1)}")
    with open(part, "rb") as f:
        cab = f.read(512)
    ini = cab.lstrip()[:15].lower()
    tipo = it.get("tipo", "binario")
    if tipo in ("csv", "texto", "json") and ini.startswith(b"<"):
        motivos.append(f"tipo {tipo} esperado, mas o conteudo comeca como HTML/XML")
    if tipo == "json" and ini[:1] not in (b"{", b"["):
        motivos.append("tipo json esperado, mas nao comeca com { ou [")
    if tipo == "zip" and not cab.startswith(b"PK\x03\x04"):
        motivos.append("tipo zip esperado, assinatura PK ausente")
    if tipo == "gz" and not cab.startswith(b"\x1f\x8b"):
        motivos.append("tipo gz esperado, assinatura gzip ausente")
    esp = it.get("sha256_esperado")
    if esp and esp.lower() != sha:
        motivos.append(f"sha256 {sha} difere do esperado {esp.lower()}")
    return motivos


# ---------------------------------------------------------------- disco
def livre(caminho):
    """Primeiro nome que nao existe: nunca sobrescreve."""
    if not caminho.exists():
        return caminho
    for k in range(1, 10000):
        c = caminho.with_name(f"{caminho.stem}_{k}{caminho.suffix}")
        if not c.exists():
            return c
    raise OSError(f"sem nome livre para {caminho}")


def gravar_json_atomico(caminho, dados):
    tmp = caminho.with_name(caminho.name + ".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, caminho)


def quarentena(raiz, fonte, dia, arquivo, part, motivos, registro):
    pasta = raiz / "QUARANTINE" / fonte / dia
    pasta.mkdir(parents=True, exist_ok=True)
    destino = livre(pasta / arquivo)
    if part is not None and part.exists():
        os.replace(part, destino)
    guardado = part is not None and destino.exists()
    nota = livre(destino.with_name(destino.name + ".motivo.json"))
    gravar_json_atomico(nota, {"motivos": motivos, "registro": registro, "arquivo_guardado": guardado})
    return destino if guardado else nota


# ---------------------------------------------------------------- execucao
class Contexto:
    def __init__(self, raiz, lote, dia, abrir, espera_base, dormir, mascara, chaves):
        self.raiz, self.lote, self.dia = raiz, lote, dia
        self.abrir, self.espera_base, self.dormir = abrir, espera_base, dormir
        self.mascara, self.chaves = mascara, chaves
        self.limitador = Limitador(dormir=dormir)
        self.ua = lote.get("user_agent") or UA_PADRAO
        self.timeout = float(lote.get("timeout_s", 60))


def montar(url, chave):
    return url.replace("{CHAVE}", urllib.parse.quote(chave, safe="")) if chave else url


def processar(it, ctx):
    fonte = ctx.lote["fontes"][it["fonte"]]
    intervalo = float(fonte.get("intervalo_s", 1.0))
    chave = ctx.chaves.get(it["fonte"])
    m = ctx.mascara
    pasta = ctx.raiz / "RAW" / it["fonte"] / ctx.dia
    pasta.mkdir(parents=True, exist_ok=True)
    part = pasta / (it["arquivo"] + ".part")
    reg = {"esquema": ESQUEMA, "lote": ctx.lote["lote"], "id": it["id"], "fonte": it["fonte"],
           "url": it["url"], "licenca": it.get("licenca") or fonte["licenca"],
           "utc_inicio": utc_iso(), "user_agent": ctx.ua}

    meta, tent, erro = com_tentativas(
        lambda: baixar_uma_vez(ctx.abrir, montar(it["url"], chave), part, ctx.ua, ctx.timeout,
                               it.get("max_bytes")),
        ctx.limitador, it["fonte"], intervalo, ctx.espera_base, ctx.dormir, m)
    reg["tentativas"] = tent
    if meta:
        reg.update({k: (m(v) if isinstance(v, str) else v) for k, v in meta.items()})
    motivos = [m(f"download: {erro}")] if erro else []
    if erro and getattr(erro, "status", None):
        reg["status_http"] = erro.status
    if not erro and it.get("checksum_url"):
        texto, _, erro_ck = com_tentativas(
            lambda: _ler_pequeno(ctx, montar(it["checksum_url"], chave)),
            ctx.limitador, it["fonte"], intervalo, ctx.espera_base, ctx.dormir, m)
        achado = RE_HEX64.search(texto or "")
        if erro_ck or not achado:
            motivos.append(m(f"checksum publicado indisponivel: {erro_ck or 'sem hash de 64 hex'}"))
        else:
            reg["sha256_publicado"] = achado.group(1).lower()
            if reg["sha256_publicado"] != meta["sha256"]:
                motivos.append(f"sha256 {meta['sha256']} difere do publicado {reg['sha256_publicado']}")
    if not erro:
        motivos += validar_conteudo(part, it, meta["bytes"], meta["sha256"])
    final = pasta / it["arquivo"]
    if not motivos and final.exists():
        motivos.append("destino ja existe em RAW; nada sobrescrito")
    reg["utc_fim"] = utc_iso()
    if motivos:
        guardado = part.exists()
        reg["resultado"] = "quarentena" if guardado else "falha"
        reg["motivos"] = motivos
        limpo = json.loads(m(json.dumps(reg, ensure_ascii=False)))
        reg["destino"] = quarentena(ctx.raiz, it["fonte"], ctx.dia, it["arquivo"],
                                    part if guardado else None, motivos, limpo).relative_to(ctx.raiz).as_posix()
    else:
        os.replace(part, final)
        reg["resultado"] = "ok"
        reg["destino"] = final.relative_to(ctx.raiz).as_posix()
    return reg


def _ler_pequeno(ctx, url):
    req = urllib.request.Request(url, headers={"User-Agent": ctx.ua})
    with abrir_http(ctx.abrir, req, ctx.timeout) as r:
        try:
            return r.read(1 << 16).decode("utf-8", "replace")
        except (http.client.HTTPException, TimeoutError, ConnectionError) as e:
            raise Retentavel(f"leitura do checksum interrompida: {e}")


def configurar_log(raiz, lote, mascara, carimbo):
    pasta = raiz / "MANIFEST" / "logs"
    pasta.mkdir(parents=True, exist_ok=True)

    class Filtro(logging.Filter):
        def filter(self, rec):
            rec.msg, rec.args = mascara(rec.getMessage()), ()
            return True

    log.handlers.clear()
    log.setLevel(logging.INFO)
    log.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fmt.converter = time.gmtime
    for h in (logging.FileHandler(pasta / f"adquirir_{lote}_{carimbo}.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        h.addFilter(Filtro())
        log.addHandler(h)


def main(argv=None, agora=None, abrir=None, dormir=time.sleep, relogio=None):
    ap = argparse.ArgumentParser(prog="adquirir.py", description=__doc__.splitlines()[0])
    ap.add_argument("lote", help="arquivo JSON do lote aprovado")
    ap.add_argument("--executar", action="store_true", help="baixa de verdade (sem isto: simulacao)")
    ap.add_argument("--raiz", default=str(Path(__file__).resolve().parents[1]),
                    help="pasta QHOS_DADOS (padrao: a pasta acima de ferramentas/)")
    ap.add_argument("--espera-base", type=float, default=2.0, help="segundos; dobra a cada tentativa")
    ap.add_argument("--permitir-loopback", action="store_true", help="so para os testes: aceita http://127.0.0.1")
    a = ap.parse_args(argv)
    raiz = Path(a.raiz).resolve()
    if relogio is None:  # relogio() e consultado antes de cada item
        relogio = (lambda: agora) if agora else (lambda: dt.datetime.now(dt.timezone.utc))
    agora = agora or relogio()

    try:
        lote = carregar_lote(a.lote, a.permitir_loopback)
    except ErroLote as e:
        print(e, file=sys.stderr)
        return 2
    for sub in ("RAW", "QUARANTINE", "MANIFEST"):
        if not (raiz / sub).is_dir():
            print(f"raiz sem {sub}/: {raiz}", file=sys.stderr)
            return 2

    mascara, chaves, origem = Mascara(), {}, {}
    for nome, f in lote["fontes"].items():
        if f.get("chave"):
            valor, de_onde = obter_chave(f["chave"])
            chaves[nome], origem[nome] = valor, de_onde
            mascara.add(valor)

    ckpt_arq = raiz / "MANIFEST" / "checkpoints" / f"{lote['lote']}.json"
    feitos = json.loads(ckpt_arq.read_text(encoding="utf-8")) if ckpt_arq.exists() else {}
    dia = agora.strftime("%Y%m%d")

    if not a.executar:
        print(f"SIMULACAO do lote {lote['lote']} (nada sera baixado; use --executar)")
        print(f"raiz: {raiz}   pasta do dia: {dia}   janela de captura agora: "
              f"{'SIM (--executar seria recusado)' if em_janela_captura(agora) else 'nao'}")
        for nome in chaves:
            print(f"chave da fonte {nome}: {'encontrada (' + origem[nome] + ')' if chaves[nome] else 'AUSENTE'}")
        for it in lote["itens"]:
            st = "ja feito (checkpoint)" if it["id"] in feitos else "baixaria"
            print(f"  {it['id']:<28} {st:<22} RAW/{it['fonte']}/{dia}/{it['arquivo']}  <- {it['url']}")
        return 0

    if em_janela_captura(agora):
        print(f"RECUSADO: janela de captura Rithmic aberta (NY {hora_ny(agora):%a %H:%M}). "
              "Rode depois das 16:15 de Nova York.", file=sys.stderr)
        return 2
    faltando = [n for n, v in chaves.items() if not v]
    if faltando:
        print(f"RECUSADO: chave ausente para {faltando} (credman ou variavel de ambiente).", file=sys.stderr)
        return 2

    trava = raiz / "MANIFEST" / "adquirir.lock"
    try:
        fd = os.open(trava, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(f"RECUSADO: outra execucao em andamento (ou trava velha): {trava}", file=sys.stderr)
        return 2
    os.write(fd, f"{os.getpid()} {utc_iso()}\n".encode())
    os.close(fd)
    try:
        carimbo = agora.strftime("%Y%m%dT%H%M%SZ")
        configurar_log(raiz, lote["lote"], mascara, carimbo)
        ckpt_arq.parent.mkdir(parents=True, exist_ok=True)
        opener = abrir or urllib.request.build_opener(_Redirecionamento(a.permitir_loopback)).open
        ctx = Contexto(raiz, lote, dia, opener, a.espera_base, dormir, mascara, chaves)
        log.info("lote %s: %d itens, %d no checkpoint", lote["lote"], len(lote["itens"]), len(feitos))
        cont = {"ok": 0, "quarentena": 0, "falha": 0, "pulado": 0}
        interrompido = False
        with open(raiz / "MANIFEST" / "aquisicoes.jsonl", "a", encoding="utf-8") as jl:
            for it in lote["itens"]:
                if it["id"] in feitos:
                    cont["pulado"] += 1
                    log.info("%s: pulado (checkpoint %s)", it["id"], feitos[it["id"]]["utc"])
                    continue
                agora_item = relogio()
                if em_janela_captura(agora_item):
                    log.warning("janela de captura abriu (NY %s): lote interrompido antes de %s; "
                                "rode de novo depois das 16:15 de Nova York (o checkpoint retoma)",
                                f"{hora_ny(agora_item):%a %H:%M}", it["id"])
                    interrompido = True
                    break
                log.info("%s: %s", it["id"], it["url"])
                try:
                    reg = processar(it, ctx)
                except OSError as e:  # disco: para tudo, o checkpoint permite retomar
                    log.error("%s: erro de disco, lote interrompido: %s", it["id"], e)
                    raise
                jl.write(mascara(json.dumps(reg, ensure_ascii=False)) + "\n")
                jl.flush()
                os.fsync(jl.fileno())
                cont[reg["resultado"]] += 1
                if reg["resultado"] == "ok":
                    feitos[it["id"]] = {"sha256": reg["sha256"], "destino": reg["destino"], "utc": reg["utc_fim"]}
                    gravar_json_atomico(ckpt_arq, feitos)
                    log.info("  ok %d bytes sha256=%s -> %s", reg["bytes"], reg["sha256"], reg["destino"])
                else:
                    log.warning("  %s: %s -> %s", reg["resultado"], "; ".join(reg["motivos"]), reg["destino"])
        log.info("fim: %s%s", cont, " (INTERROMPIDO pela janela de captura)" if interrompido else "")
        if interrompido:
            return 3
        return 0 if cont["quarentena"] == cont["falha"] == 0 else 1
    finally:
        trava.unlink(missing_ok=True)
        for h in list(log.handlers):
            h.close()
            log.removeHandler(h)


if __name__ == "__main__":
    sys.exit(main())
