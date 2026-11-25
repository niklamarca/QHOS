# QHOS Event Data Plane

## Estado atual

O plano de eventos existe, mas nasce desativado e isolado da pesquisa diaria.
Nenhum evento Databento alimenta features, estrategias, validacao ou execucao
enquanto `EVENT_PLANE_ENABLED` nao for explicitamente habilitado e a comparacao
shadow nao tiver sido aprovada.

## Invariantes

1. Toda requisicao historica tem `start` e `end`; `end` e exclusivo.
2. O custo e estimado e comparado ao teto local antes do download.
3. O DBN recebido e preservado sem transformacao em `events_native/`.
4. O Parquet analitico nasce dos mesmos bytes do DBN arquivado.
5. Particoes existentes nunca sao sobrescritas.
6. Repetir a mesma requisicao e idempotente.
7. Eventos sem timestamp valido falham fechados.
8. O lake diario e o event lake possuem linhagens separadas.
9. A existencia de eventos nao autoriza seu uso em pesquisa ou execucao.
10. Na fase shadow, toda requisicao exige `limit` e respeita o teto local de
    registros em memoria.
11. Precos Parquet sao preservados como inteiros fixos em unidades de 1e-9;
    conversao para float pertence a uma camada derivada, nunca ao bronze.

## Layout

```text
data_lake/
  events_native/
    provider=databento/dataset=.../schema=.../request=.../*.dbn.zst
  events/
    provider=databento/dataset=.../schema=.../
      symbol=.../date=YYYY-MM-DD/part-*.parquet
    _catalog/
      requests/*.json
      checkpoints/provider=.../dataset=.../schema=.../symbol=....json
```

## Fases de ativacao

- `disabled`: apenas codigo, testes e observabilidade local.
- `shadow`: downloads pequenos; comparacao contra a fonte atual; zero consumidores.
- `research`: datasets derivados possuem linhagem propria e podem alimentar
  experimentos pre-registrados.
- `execution`: futuro; exige live feed, replay, reconexao, controle de sequencia,
  latencia medida e autorizacao de governanca independente.

## Ativacao da conta

Depois de contratar o Databento, execute `DATABENTO_SETUP.bat` e cole a chave
quando solicitado. O instalador nao mostra a chave, atualiza `.env` de forma
atomica, confirma dataset/schema, estima o custo e executa um unico probe shadow
limitado. Repetir o setup para a mesma sessao nao refaz o download pago.

No boot seguinte, o QHOS detecta a chave. Se o probe da ultima sessao ja estiver
no catalogo `COMMITTED`, ele apenas reutiliza o resultado.

## O que ainda nao esta implementado

- cliente live e recuperacao apos desconexao;
- reconstrucao de livro MBO/MBP;
- compactacao de particoes e consulta out-of-core para volumes extremos;
- conversao chunked de pedidos acima do teto shadow;
- security master e mapa point-in-time completo;
- pipeline silver de barras, spreads e microstructure features;
- reconciliacao shadow Databento versus fontes atuais;
- politica de backup incremental para DBN, que nao deve ser copiado integralmente
  pelo backup diario do lake de barras.
