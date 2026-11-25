#!/usr/bin/env bash
# Releitura da captura do dia (a ultima linha de provas.jsonl do agendador), so leitura:
# qhos2.livro, qhos2.inspecao --json e o diagnostico quadro a quadro (onde/quando o livro se perde).
# Saida em ~/qhos-v2-data/derivados/dia_<data>/ e resumo na tela. Rodar DEPOIS das 17:15 BRT.
# Uso: tr -d '\r' < /mnt/c/Users/usuario/Downloads/Rithmic/v2_ferramentas/reler_dia.sh | bash
set -u
PY=/home/usuario/qhos-v2/.venv/bin/python
DATA=/home/usuario/qhos-v2-data
FERR=/mnt/c/Users/usuario/Downloads/Rithmic/v2_ferramentas
PROVA=$(tail -1 "$DATA/agenda/provas.jsonl" 2>/dev/null) || { echo "PARE: sem provas.jsonl"; exit 2; }
ID=$($PY -I -c 'import json,sys;p=json.loads(sys.argv[1]);print(p["captura"] or "")' "$PROVA")
DIA=$($PY -I -c 'import json,sys;p=json.loads(sys.argv[1]);print(p["dia"])' "$PROVA")
echo "prova do dia $DIA: $(echo "$PROVA" | cut -c1-400)"
[[ -n "$ID" ]] || { echo "PARE: a prova do dia nao tem captura"; exit 2; }
C="$DATA/capturas/$ID"
[[ -f "$C/manifest.json" ]] || { echo "PARE: $C sem manifest.json (captura ainda aberta ou nao verificada)"; exit 2; }
OUT="$DATA/derivados/dia_$DIA"; mkdir -p "$OUT"
echo "versao instalada: $($PY -c 'import qhos2;print(qhos2.__version__)') | captura $ID"
echo "== livro =="
nice -n 10 "$PY" -m qhos2.livro "$C" | tee "$OUT/$ID.livro.txt"
echo "== inspecao =="
nice -n 10 "$PY" -m qhos2.inspecao --json "$C" > "$OUT/$ID.inspecao.json" && "$PY" -I -c 'import json,sys;r=json.load(open(sys.argv[1]));print("retrato=",{k:r["retrato"][k] for k in("fim_visto","apos_o_fim","ids_duplicados")});print("durante=",r["ciclo"]["durante_retrato"]);print("vivas_no_fim=",r["ordens_vivas_no_fim"])' "$OUT/$ID.inspecao.json"
echo "== diagnostico (onde e quando o livro se perde; maiores intervalos) =="
tr -d '\r' < "$FERR/diagnostico_livro_captura.py" > /tmp/diag_livro.py
nice -n 10 "$PY" /tmp/diag_livro.py "$C" "$OUT/$ID.diagnostico.json" | grep -vE '^\s+[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z / ' | head -40
echo "== faixas de 15 min com anomalia ou cruzado no fim =="
"$PY" -I -c 'import json,sys;r=json.load(open(sys.argv[1]));[print(f["inicio"],"quadros",f["quadros"],"cruzado_fim",f["cruzado_fim"],"remove_desc",f["remove_desconhecida"],"novo_ja_vivo",f["novo_ja_vivo"],"altera_desc",f["altera_desconhecida"],"spread_fim",f.get("spread_fim")) for f in r["faixas_15min"] if f["cruzado_fim"] or f["remove_desconhecida"] or f["novo_ja_vivo"] or f["altera_desconhecida"]] or print("nenhuma")' "$OUT/$ID.diagnostico.json"
echo "saida em $OUT"
