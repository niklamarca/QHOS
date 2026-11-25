# Matriz de leitura de arquivos e JSON no Brain (QHOS-AUD-315)

Levantamento feito no lote l3c-brain-ferramentas (base 36f0e1f), por leitura do código. Cada linha é um
contexto diferente; políticas diferentes **não foram unificadas** neste lote — só a que era claramente
um defeito isolado (NaN aceito no estado próprio das investigações) foi corrigida. "Não verificado"
significa que o lote não executou o caso; não é afirmação de que esteja certo ou errado.

| Leitor | Arquivo | Link / junção | Limite de bytes | NaN / Infinity | Contexto e observação |
|---|---|---|---|---|---|
| `assistant.read_repair_history` | `repair_history.jsonl` | Recusa: `lstat` de todo o caminho léxico + `O_NOFOLLOW` + identidade (`st_dev`,`st_ino`) antes e depois de abrir | 24 000 B (cauda; primeira linha parcial descartada) | Não rejeitado no `json.loads` por linha; `timestamp` exige `isfinite` | Projeção de UI de um log de reparo. Política mais estrita do pacote. Caracterizado em `tests/test_brain_file_reading_policies.py::test_repair_history_nao_segue_link`. |
| `assistant.read_regression_audit` | `regression_audit.json` | **Segue link** (`Path.open`) | 64 KiB (`report_too_large`) | Não rejeitado no parse; `generated_at` exige `isfinite`, contagens exigem `int` | Relatório **externo** colocado pelo operador; se o link é intencional (relatório gerado fora do `state_dir`) só o autor pode dizer. Não alterado. |
| `investigation.InvestigationEngine.__init__` | `runtime_investigations.json` | Segue link | 4 MiB (`incidents_too_large`) | **Rejeitado desde este lote** (`parse_constant`); estado volta a vazio | Estado próprio do Brain. Alterado: `investigation.py`. |
| `verification.read_evidence` | evidências de regressão, `request.json`, `worker-error.json`, `regression-results.json` | O `open` segue link, mas os chamadores checam antes (`regression._verify_saved`: `is_symlink` + `resolve().parent`; `_owned_mirror`; `_prune_evidence`) | 4 MiB (`evidence_too_large`) | Rejeitado (`json.dumps(allow_nan=False)`) | Evidência própria. Caracterizado em `test_read_evidence_rejeita_nan_e_documento_grande`. |
| `tester.SoftwareTester.__init__` | `tester_state.json` | Segue link (`read_text`) | Sem limite | Aceito | Estado próprio; qualquer erro vira estado inicial (ver QHOS-AUD-295, fora deste lote). Não alterado. |
| `tester.SoftwareTester.__init__` | `smoke_evidence.json` | Recusa: `is_symlink` + `resolve().parent` | 64 KiB (`evidence_too_large`) | Rejeitado (`allow_nan=False` no digest) | Evidência própria. |
| `knowledge.SourceBoundary.read_source` | fontes do pacote observado | Recusa link/junção em cada componente do caminho (`knowledge.py:135-146, 209-218`) | Limites próprios do índice | n/a (texto) | Perímetro de leitura de fonte. Não alterado. |
| `incident_memory.IncidentMemory._load` | `incident_memory.json` | Recusa: `is_symlink` | `MAX_STATE_BYTES` | Não verificado neste lote | Estado próprio. |
| `linux_actions.LinuxActionLog._load` | `linux_actions.json` | Recusa: `is_symlink` + `S_ISREG` | 128 KiB | Não rejeitado no parse; `timestamp` não finito vira `0.0` | Trilha de auditoria (ver QHOS-AUD-278, fora deste lote). |
| `observer._read_json` | arquivos do coletor | Recusa: `is_symlink`, exige `is_file` | 4 000 000 B | Não verificado neste lote | Lote 3b. |
| `codex_chat` (`:21`, `:78`) | estado do chat | Recusa link/junção nos pais (`_no_links`) | Não verificado | Não verificado | Lote 3a/3b (QHOS-AUD-283 sobre `is_junction`). |
| `supervisor.QhosSupervisor._load_state` | `supervisor_state.json` | Segue link (`read_text`) | Sem limite | `restart_attempts` filtrados por `_finite_timestamp`; demais campos não | Código legado do supervisor V1; `run_command` recusa religar na V2. |
| `file_browser.FileBrowser.read` | arquivo nomeado pelo usuário | Resolve com `strict=True`; recusa fora da raiz e credenciais no caminho léxico e no resolvido (QHOS-AUD-301) | 65 536 B por bloco | n/a (texto; binário só metadado) | Perímetro de leitura sob demanda. |

## O que foi alterado neste lote

- `src/brain_service/investigation.py`: `json.loads(raw, parse_constant=_reject_non_finite)`.

## O que fica para o autor

- `read_regression_audit` seguir link: depende de onde o relatório externo é gerado.
- Unificar `read_evidence` com checagem de link dentro da própria função (hoje é dever de cada chamador).
- `tester_state.json` sem limite de tamanho e com NaN aceito.
