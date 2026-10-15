# 0003. Desenho experimental: ERL contra narrativa, n = 30, testes não paramétricos

- **Status:** Aceito
- **Data:** 2026-10-15

## Contexto

A atividade pede um teste de hipótese mostrando conformidade ≥ 85%. Só testar o limiar (H1) não mostra se o mérito é do modelo ou da LLM.

## Decisão

- H1: Wilcoxon de uma amostra contra 0,85 (unilateral) na condição ERL, com t de Student e IC por bootstrap como complemento.
- H2: Mann-Whitney U (unilateral) ERL contra narrativa, com correlação rank-biserial.
- 30 execuções por condição, ordem sorteada com semente fixa e intercalada entre as condições.
- O controle narrativo tem **todo** o conteúdo do ERL, sem a estrutura.
- Falhas de geração (recusa, truncamento, sem código) contam como 0; nenhuma execução é descartada.
- Modelo `claude-opus-5-5`, esforço `medium`, sem *fallback* de modelo.

## Alternativas consideradas

- **Só H1:** atende ao enunciado, mas não separa o efeito do modelo.
- **Controle com menos informação (enunciado "típico" incompleto):** daria um efeito maior, porém confundido com a quantidade de informação.
- **Teste t como principal:** a conformidade é limitada e assimétrica perto de 1, o que fere a premissa de normalidade.

## Consequências

- **Positivas:** H2 é conservador e mais difícil de rejeitar; um resultado positivo é mais convincente.
- **Negativas:** é possível que H2 não seja significativa mesmo com o modelo sendo útil na prática (o relatório discute isso).
- **Como saber se deu errado:** variância tão grande no piloto que n = 30 não permita decidir H1.

## Adendo (2026-10-15): geração por agentes do Claude Code

Sem orçamento para créditos da API, a geração foi feita por agentes do Claude Code (Claude Opus 5.5), um por execução, sem histórico, instruídos a ler só a especificação e gravar `biblioteca.py`. A avaliação usa `executar.py registrar`. Consequências: não dá para fixar o esforço nem auditar o isolamento dos agentes (verificação indireta por semelhança com a referência). Depois do piloto, com variância zero, foi incluído o teste do sinal como complemento robusto a empates, antes da coleta principal. Resultado: 60/60 com 100%; H2 indeterminada por efeito teto.
