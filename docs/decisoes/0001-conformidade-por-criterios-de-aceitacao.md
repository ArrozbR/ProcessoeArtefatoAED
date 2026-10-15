# 0001. Conformidade medida por critérios de aceitação com oráculo automático

- **Status:** Aceito
- **Data:** 2026-10-15

## Contexto

A atividade exige conformidade ≥ 85% entre documento e software, mas não define como medir. A métrica precisa ser objetiva, repetível e aplicável a dezenas de execuções.

## Decisão

Conformidade = CAs aprovados ÷ total de CAs do documento. Cada CA tem exatamente um teste automático com o mesmo id. O oráculo só vale depois de validado: referência 100%, stub vazio 0% e todos os mutantes detectados.

## Alternativas consideradas

- **Avaliação manual por checklist de requisitos:** subjetiva, lenta para 60 execuções e sujeita a viés de quem avalia.
- **Pontuar por RF (aprovado só se todos os CAs passam):** grosseira demais; um erro de fronteira zeraria um RF inteiro.
- **LLM como avaliadora:** introduz uma segunda fonte de variação e é difícil de validar.

## Consequências

- **Positivas:** medição automática, rastreável até o requisito, com validade do instrumento demonstrada.
- **Negativas:** mede só o que os CAs cobrem; os CAs têm pesos iguais, embora as regras tenham alcances diferentes.
- **Como saber se deu errado:** código que passa em todos os CAs mas viola uma regra do documento.
