"""Leitor do heartbeat da V2. Somente leitura; roda no WSL com o python3 do sistema.

Chamado pelo heartbeat_6h.ps1 (Windows), que confere o SHA256 deste arquivo antes.
Imprime uma linha JSON. Nunca escreve, nunca abre conexao de rede, nunca le senha.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

DADOS = Path("/home/usuario/qhos-v2-data")
AGENDA = DADOS / "agenda"
CAPTURAS = DADOS / "capturas"
INSTALACAO = Path("/home/usuario/qhos-v2")
COLETA = Path("/home/usuario/qhos-coleta")  # clone do coletor multiativo (qhos2-coleta-multi)
CALENDARIO_CONTINUO = DADOS / "coleta_multi" / "calendario" / "calendario-atual.json"
GUARDA = Path.home() / ".local" / "state" / "qhos2" / "operacao" / "estado.json"
JANELA_S = 60
CAUDA_BYTES = 24 * 1024 ** 2
TETO_GB_PADRAO = 12.0
CATALOGO_MAX_BYTES = 8 * 1024 ** 3  # qhos2.catalogo.CAPTURA_MAX_BYTES: acima disso a captura nao verifica


def _json(caminho):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _unit(nome):
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        saida = subprocess.run(["systemctl", "--user", "show", nome, "-p", "ActiveState", "-p", "SubState",
                                "-p", "NRestarts"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {"estado": "desconhecido", "nrestarts": None}
    campos = dict(linha.split("=", 1) for linha in saida.splitlines() if "=" in linha)
    restarts = campos.get("NRestarts", "")
    return {"estado": f"{campos.get('ActiveState', '?')}/{campos.get('SubState', '?')}",
            "nrestarts": int(restarts) if restarts.isdigit() else None}


def _e_agendador(argv):
    """O do tmux (qhos2.agenda rodar) ou o do systemd (coleta_multi.servico agenda|agenda-continuo, que sobe no
    boot e chama o mesmo qhos2.agenda); o coletor multiativo (coletor|coletor-continuo) nao conta."""
    return (b"qhos2.agenda" in argv and b"rodar" in argv) or \
        (b"coleta_multi.servico" in argv and (b"agenda" in argv or b"agenda-continuo" in argv))


def _agendador():
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if _e_agendador(argv):
            return int(proc.name), [a.decode("utf-8", "replace") for a in argv if a]
    return None, []


def _idade_processo(pid):
    """Segundos desde a partida do processo (campo 22 de /proc/<pid>/stat contra /proc/uptime)."""
    if pid is None:
        return None
    try:
        depois_do_nome = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        return round(uptime - int(depois_do_nome[19]) / os.sysconf("SC_CLK_TCK"), 1)
    except (OSError, ValueError, IndexError):
        return None


def _habilitada(nome):
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        saida = subprocess.run(["systemctl", "--user", "is-enabled", nome], capture_output=True, text=True,
                               timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return saida == "enabled"


def _calendario(argv):
    teto_gb = TETO_GB_PADRAO
    valido_ate = None
    for i, arg in enumerate(argv):
        if arg == "--max-gb" and i + 1 < len(argv):
            try:
                teto_gb = float(argv[i + 1])
            except ValueError:
                pass
        elif arg.startswith("--max-gb="):
            try:
                teto_gb = float(arg.split("=", 1)[1])
            except ValueError:
                pass
        elif arg.endswith(".json"):
            cal = _json(Path(arg))
            if isinstance(cal, dict):
                valido_ate = cal.get("valido_ate")
    if valido_ate is None and "coleta_multi.servico" in argv:
        # o servico monta o calendario sozinho a cada partida; ele nao aparece na linha de comando
        cal = _json(CALENDARIO_CONTINUO)
        if isinstance(cal, dict):
            valido_ate = cal.get("valido_ate")
    return valido_ate, teto_gb


def _provas():
    try:
        linhas = (AGENDA / "provas.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, 0, 0
    provas = []
    for linha in linhas:
        try:
            provas.append(json.loads(linha))
        except ValueError:
            pass
    seguidos = 0
    for prova in reversed(provas):
        if prova.get("resultado") != "capturado" or prova.get("verificada") is not True:
            break
        seguidos += 1
    total = sum(1 for p in provas if p.get("resultado") == "capturado" and p.get("verificada") is True)
    ultima = provas[-1] if provas else None
    if ultima:
        ultima = {k: ultima.get(k) for k in ("dia", "resultado", "verificada", "quadros", "erro", "motivo",
                                              "registrado_utc")}
    return ultima, seguidos, total


def _pastas_de_captura():
    pastas = []
    for pasta in CAPTURAS.iterdir():
        if pasta.is_dir() and not pasta.is_symlink():
            try:
                pastas.append((pasta.stat().st_mtime, pasta))
            except OSError:
                pass
    return [p for _m, p in sorted(pastas, reverse=True)]


def _contrato(pastas):
    simbolo = None
    for pasta in pastas:
        ident = _json(pasta / "capture_identity.json")
        if simbolo is None and isinstance(ident, dict):
            simbolo = ident.get("requested_symbol")
        manifesto = _json(pasta / "manifest.json")
        if not isinstance(manifesto, dict):
            continue
        status = manifesto.get("status") or {}
        ref = (status.get("sessao") or {}).get("referencia") or status.get("referencia") or {}
        if ref.get("expiration_date") and (simbolo is None or status.get("requested_symbol") == simbolo):
            return simbolo or status.get("requested_symbol"), ref.get("expiration_date")
    return simbolo, None


def _ao_vivo(pasta, agora_ns):
    """Estatisticas do ultimo minuto do arquivo em gravacao (le so a cauda)."""
    arquivo = pasta / "market_frames.jsonl"
    try:
        tamanho = arquivo.lstat().st_size
        with open(arquivo, "rb") as f:
            f.seek(max(0, tamanho - CAUDA_BYTES))
            cauda = f.read(CAUDA_BYTES)
    except OSError:
        return {"captura": pasta.name, "erro": "arquivo de quadros ilegivel"}
    linhas = cauda.split(b"\n")
    if tamanho > CAUDA_BYTES:
        linhas = linhas[1:]
    quadros = []
    for linha in linhas:
        if not linha.strip():
            continue
        try:
            q = json.loads(linha)
            quadros.append((int(q["index"]), int(q["template_id"]), int(q["received_ns"]), len(linha) + 1))
        except (ValueError, KeyError, TypeError):
            quadros.append(None)
    invalidos = sum(1 for q in quadros if q is None)
    quadros = [q for q in quadros if q is not None]
    if not quadros:
        return {"captura": pasta.name, "bytes": tamanho, "quadros_janela": 0, "invalidos": invalidos}
    ultimo_ns = quadros[-1][2]
    corte = ultimo_ns - JANELA_S * 10 ** 9
    janela = [q for q in quadros if q[2] >= corte]
    coberto_s = max(1e-3, min(JANELA_S, (ultimo_ns - quadros[0][2]) / 1e9))
    lacunas = duplicados = 0
    maior_pausa_ns = 0
    for a, b in zip(janela, janela[1:]):
        if b[0] <= a[0]:
            duplicados += 1
        elif b[0] > a[0] + 1:
            lacunas += b[0] - a[0] - 1
        maior_pausa_ns = max(maior_pausa_ns, b[2] - a[2])
    por_template = {}
    for q in janela:
        por_template[str(q[1])] = por_template.get(str(q[1]), 0) + 1
    fator = 60.0 / coberto_s
    return {
        "captura": pasta.name,
        "bytes": tamanho,
        "bytes_min": int(sum(q[3] for q in janela) * fator),
        "quadros_min": int(len(janela) * fator),
        "por_template_min": {k: int(v * fator) for k, v in por_template.items()},
        "ultimo_index": quadros[-1][0],
        "ultimo_ns": ultimo_ns,
        "frescor_ms": int((agora_ns - ultimo_ns) / 1e6),
        "lacunas": lacunas,
        "duplicados": duplicados,
        "invalidos": invalidos,
        "maior_pausa_ms": int(maior_pausa_ns / 1e6),
        "janela_s": round(coberto_s, 1),
    }


def _keepalive():
    marcador = _json(DADOS / "run" / "keepalive.json")
    if not isinstance(marcador, dict):
        return False
    try:
        cmd = (Path("/proc") / str(int(marcador["pid"])) / "cmdline").read_bytes()
    except (OSError, KeyError, ValueError, TypeError):
        return False
    return cmd == b"/bin/sleep\0infinity\0"


def _portas_expostas():
    expostas = set()
    # FEFFFF0A = 10.255.255.254, o DNS do proprio WSL, preso na interface lo
    for nome, loopback in (("tcp", ("0100007F", "FEFFFF0A")), ("tcp6", ("00000000000000000000000001000000",))):
        try:
            linhas = Path(f"/proc/net/{nome}").read_text().splitlines()[1:]
        except OSError:
            continue
        for linha in linhas:
            partes = linha.split()
            if len(partes) < 4 or partes[3] != "0A":
                continue
            endereco, porta = partes[1].split(":")
            if endereco in loopback or (nome == "tcp" and endereco.endswith("7F")):
                continue
            expostas.add(int(porta, 16))
    return sorted(expostas)


def _arquivos_de_senha():
    achados = []
    padrao = re.compile(r"(password|passwd|senha)", re.IGNORECASE)
    for raiz in (Path.home() / ".config", DADOS):
        for pasta, subpastas, arquivos in os.walk(raiz):
            subpastas[:] = [s for s in subpastas if not (Path(pasta) == DADOS and s in ("capturas", "arquivo_v1"))]
            achados += [os.path.join(pasta, a) for a in arquivos if padrao.search(a)]
    return achados


def _instalacao():
    release = _json(INSTALACAO / "RELEASE.json") or {}
    dist = sorted(INSTALACAO.glob(".venv/lib/python3*/site-packages/qhos2-*.dist-info"))
    instalada = dist[-1].name[len("qhos2-"):-len(".dist-info")] if dist else None
    return {"commit": release.get("commit"), "versao_release": release.get("version"), "versao_instalada": instalada}


def _commit_da_coleta():
    """Commit em uso pelo coletor multiativo (so le o repositorio local; nada de rede)."""
    try:
        saida = subprocess.run(["git", "-C", str(COLETA), "rev-parse", "HEAD"], capture_output=True, text=True,
                               timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return saida if re.fullmatch(r"[0-9a-f]{40}", saida) else None


def main():
    agora_ns = time.time_ns()
    agenda = _json(AGENDA / "estado.json") or {}
    guarda = _json(GUARDA) or {"estado": "LIVRE"}
    pid, argv = _agendador()
    valido_ate, teto_gb = _calendario(argv)
    ultima, seguidos, total = _provas()
    pastas = _pastas_de_captura()
    simbolo, vencimento = _contrato(pastas)
    ao_vivo = _ao_vivo(pastas[0], agora_ns) if agenda.get("estado") == "CAPTURANDO" and pastas else None
    disco = os.statvfs(DADOS)
    print(json.dumps({
        "wsl_agora_ns": agora_ns,
        "brain": _unit("qhos2-brain.service"),
        "coletor": _unit("qhos2-coletor.service"),
        "agendador_pid": pid,
        "agendador_idade_s": _idade_processo(pid),
        # continuo: o servico do systemd gera calendario e contrato sozinho a cada partida
        "agendador_modo": ("continuo" if "agenda-continuo" in argv else "manual") if pid else None,
        "agenda_unit_habilitada": _habilitada("qhos2-agenda.service"),
        "agenda": {k: agenda.get(k) for k in ("estado", "proxima_janela", "proximo_dia", "dia", "janela_fim",
                                               "motivo", "valido_ate", "atualizado_utc")},
        "calendario_valido_ate": valido_ate,
        "teto_bytes": int(min(teto_gb * 1024 ** 3, CATALOGO_MAX_BYTES)),
        "guarda": {"estado": guarda.get("estado"), "motivo": guarda.get("motivo")},
        "ultima_prova": ultima,
        "pregoes_seguidos": seguidos,
        "pregoes_verificados": total,
        "ao_vivo": ao_vivo,
        "simbolo": simbolo,
        "vencimento": vencimento,
        "capturas": len(pastas),
        "disco_livre_gib": round(disco.f_bavail * disco.f_frsize / 2 ** 30, 1),
        "keepalive": _keepalive(),
        "portas_expostas": _portas_expostas(),
        "arquivos_de_senha": _arquivos_de_senha(),
        "dados_modo": oct(DADOS.stat().st_mode & 0o777),
        "instalacao": _instalacao(),
        "coleta_commit": _commit_da_coleta(),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
