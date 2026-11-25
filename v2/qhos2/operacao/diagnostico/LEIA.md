# Diagnóstico da captura (ferramentas avulsas, só leitura)

Ferramentas que eram usadas a partir de `C:\Users\usuario\Downloads\Rithmic\v2_ferramentas\` (28/10/2026) e não
tinham cópia no git. Foram trazidas byte a byte; não são instaladas em `~/qhos-v2` e não entram na
`identidade_codigo` (que cobre só `src/qhos2`). Nenhuma delas grava na captura de origem.

| Arquivo | sha256 | O que faz |
|---|---|---|
| `diagnostico_livro_captura.py` | `e1128acb5321dddb236e3710412b13837de365792ca6cd4ae80a4b228e90a9e2` | Etapa 2, passo 7: percorre a captura quadro a quadro com o `Livro` da 0.2.5 e grava em JSON onde e quando o livro deixa de ser confiável (fim do retrato, primeiras anomalias, livro cruzado, faixas de 15 min, maiores intervalos entre quadros). Uso: `python diagnostico_livro_captura.py <pasta da captura> <saida.json>`. |
| `reler_dia.sh` | `4792c1dd5553318f0ed07cdbcbcb8481d766f95d5544ae5feaa52f2a8c4e0849` | Releitura da captura do dia (última linha de `agenda/provas.jsonl`): `qhos2.livro`, `qhos2.inspecao --json` e o diagnóstico acima; saída em `~/qhos-v2-data/derivados/dia_<data>/`. Rodar depois das 17:15 BRT. |
| `teto_local.py` | `4f57f91003a96aeb67655a2514c7c93b0f10d96066993d96ab155074568aeb4c` | Teto local de quadros por segundo: repassa os quadros brutos de uma captura pelo caminho de recepção (decodificar + `Gravador`) numa raiz temporária, que apaga no fim. Uso: `python teto_local.py <pasta da captura> <raiz temporaria fora do git> [max_quadros]`. |

Todas importam `qhos2` e devem rodar com o Python da instalação (`/home/usuario/qhos-v2/.venv/bin/python`).

`reler_dia.sh` ainda fixa `FERR` na pasta antiga em `Downloads` (a variável é definida dentro do script, então não
adianta passá-la pelo ambiente). Para usar a cópia do repositório, troque essa linha pelo caminho desta pasta (no WSL:
`/mnt/c/Users/usuario/OneDrive/Desktop/QHOSv2/qhos2/operacao/diagnostico`). O arquivo foi mantido como estava para que o
hash bata com a cópia original.
