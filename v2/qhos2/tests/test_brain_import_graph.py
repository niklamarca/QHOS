"""Grafo estatico de imports de src/ (QHOS-AUD-008): o ciclo nao pode crescer.

Mesmo metodo da auditoria (ast, sem importar nada). Os cortes que desfazem o ciclo de 23 modulos
e api<->runtime dependem da janela J2 (07_organizacao/PROPOSTA_ORGANIZACAO.md 3.1-3.2); ate la este
teste so impede que um modulo novo entre num ciclo ou que um ciclo novo apareca.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

CICLO_BRAIN = {
    "brain.architecture", "brain.attest", "brain.config_contract", "brain.context", "brain.control_side",
    "brain.diagnose", "brain.discovery.engine", "brain.discovery.fingerprint", "brain.discovery.improvement",
    "brain.discovery.observation", "brain.discovery.policy_g1", "brain.discovery.policy_g11",
    "brain.discovery.revision", "brain.discovery.score", "brain.eval_paths", "brain.fable.effort",
    "brain.fable.scratch", "brain.fable.strategy", "brain.local_config", "brain.providers", "brain.reasoner",
    "brain.sandbox", "brain.verification",
}
CICLO_API_RUNTIME = {"brain.api", "brain.runtime"}


def grafo_de_imports(src=SRC):
    mods = {}
    for p in src.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(src).with_suffix("")
        mods[".".join(rel.parts[:-1] if rel.name == "__init__" else rel.parts)] = p

    def resolver(alvo, nivel, atual):
        if nivel:
            base = atual.split(".")
            if mods[atual].name != "__init__.py":
                base = base[:-1]
            base = base[: len(base) - (nivel - 1)]
            alvo = ".".join(base + ([alvo] if alvo else []))
        return alvo

    grafo = {}
    for nome, p in mods.items():
        arestas = set()
        for no in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(no, ast.Import):
                arestas.update(a.name for a in no.names)
            elif isinstance(no, ast.ImportFrom):
                alvo = resolver(no.module or "", no.level, nome)
                arestas.add(alvo)
                arestas.update(alvo + "." + a.name for a in no.names if alvo + "." + a.name in mods)
        internos = set()
        for a in arestas:
            cand = a
            while cand and cand not in mods:
                cand = cand.rpartition(".")[0]
            if cand:
                internos.add(cand)
        internos.discard(nome)
        grafo[nome] = sorted(internos)
    return grafo


def componentes_fortemente_conexas(grafo):
    indice, baixo, pilha, em_pilha, sccs, contador = {}, {}, [], set(), [], [0]

    def visitar(v):
        indice[v] = baixo[v] = contador[0]
        contador[0] += 1
        pilha.append(v)
        em_pilha.add(v)
        for w in grafo.get(v, []):
            if w not in indice:
                visitar(w)
                baixo[v] = min(baixo[v], baixo[w])
            elif w in em_pilha:
                baixo[v] = min(baixo[v], indice[w])
        if baixo[v] == indice[v]:
            comp = set()
            while True:
                w = pilha.pop()
                em_pilha.discard(w)
                comp.add(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(comp)

    import sys
    anterior = sys.getrecursionlimit()
    sys.setrecursionlimit(max(anterior, 10000))
    try:
        for v in sorted(grafo):
            if v not in indice:
                visitar(v)
    finally:
        sys.setrecursionlimit(anterior)
    return sccs


def test_nenhum_modulo_novo_entra_num_ciclo_de_imports():
    sccs = componentes_fortemente_conexas(grafo_de_imports())
    conhecidos = (CICLO_BRAIN, CICLO_API_RUNTIME)
    # Cada SCC tem de estar contida num ciclo conhecido: partir um ciclo em pedacos menores passa, ampliar nao.
    for ciclo in sccs:
        assert any(ciclo <= k for k in conhecidos), f"ciclo novo ou ampliado: {sorted(ciclo)}"


def test_qhos2_nao_importa_o_brain():
    grafo = grafo_de_imports()
    violacoes = [(m, d) for m, deps in grafo.items() if m.split(".")[0] == "qhos2"
                 for d in deps if d.split(".")[0] in {"brain", "brain_service", "voice_service"}]
    assert violacoes == []
