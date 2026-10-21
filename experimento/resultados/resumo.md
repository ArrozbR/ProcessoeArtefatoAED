# Resumo estatístico

Modelo: `claude-opus-5-5 (agente Claude Code)` · CAs por execução: 77

## Estatística descritiva

| Condição | n | Média | DP | Mediana | Mín | Máx | IC 95% da média (bootstrap) | Execuções ≥ 85% |
|---|---|---|---|---|---|---|---|---|
| erl | 30 | 100.0% | 0.0% | 100.0% | 100.0% | 100.0% | [100.0%; 100.0%] | 30/30 |
| narrativa | 30 | 100.0% | 0.0% | 100.0% | 100.0% | 100.0% | [100.0%; 100.0%] | 30/30 |

Situação das execuções: erl/ok: 30, narrativa/ok: 30

## H1 — conformidade da condição ERL acima de 85%

H0: mediana (e média) da conformidade ≤ 0,85. H1: > 0,85. Teste unilateral, α = 0,05.

| Teste | Estatística | p-valor | Decisão |
|---|---|---|---|
| Wilcoxon de postos sinalizados (principal) | W = 465.0 | 2.16e-08 | rejeita H0 |
| t de Student para uma amostra (complementar) | indefinido: variância zero | — | — |
| Sinal (binomial, robusto a empates) | 30/30 acima | 9.31e-10 | rejeita H0 |

## H2 — ERL contra especificação narrativa

H0: a conformidade da ERL não é maior que a da narrativa. H1: é maior. Teste unilateral, α = 0,05.

| Teste | Estatística | p-valor | Tamanho de efeito | Decisão |
|---|---|---|---|---|
| Mann-Whitney U | indefinido: todas as execuções têm o mesmo valor | — | r = 0 | não rejeita H0 |

## Conformidade por serviço

| Serviço | CAs | média em erl | média em narrativa |
|---|---|---|---|
| Transportadoras | 13 | 100.0% | 100.0% |
| Cotação | 14 | 100.0% | 100.0% |
| Envios | 29 | 100.0% | 100.0% |
| Rastreamento | 8 | 100.0% | 100.0% |
| Notificações | 7 | 100.0% | 100.0% |
| Transversais (reset, RNF, integrado) | 6 | 100.0% | 100.0% |

Linhas de código geradas (média): erl: 790, narrativa: 763

## Critérios de aceitação mais reprovados

| CA | reprovações em erl | reprovações em narrativa |
|---|---|---|
| — | 0 | 0 |
