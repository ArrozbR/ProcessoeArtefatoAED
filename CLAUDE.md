# ProcessoeArtefatoAED

Atividade "AED — Processo e Artefato": um processo de análise de requisitos que produz um documento em Markdown capaz de levar uma LLM a gerar código com conformidade ≥ 85%, mais um experimento (teste de hipótese) que mede essa conformidade. Entrega: **2026-10-20, 23:59**. Repositório do grupo.

- Entregáveis: `entregaveis/` (1 processo, 2 modelo, 3 instruções, 4 relatório)
- Experimento: `experimento/` (especificações, oráculo, referência, scripts, resultados)
- Decisões (ADRs): `docs/decisoes/`

## Regras absolutas

1. **Nunca executar `git commit`, `git push`** nem nada que publique no remoto (merge, tag, PR). Comandos git de leitura (`status`, `diff`, `log`) são permitidos. **Lembre o autor de commitar a cada checkpoint** (entregável concluído, experimento rodado, fim de sessão): liste os arquivos alterados e dê uma mensagem pronta (Conventional Commits, em português). O autor faz o commit.
2. **Nenhum resultado inventado.** Números do relatório saem de `experimento/resultados/` gerados por uma execução real. Seção sem dados fica marcada **[PENDENTE]**.
3. **Congelamento.** Depois do piloto, `experimento/especificacoes/`, `experimento/testes/` e `experimento/prompt.txt` não mudam durante a coleta principal. Mudança necessária → registrar no relatório (Seção 3.6) e recomeçar a coleta.
4. **Oráculo validado.** Qualquer mudança em `experimento/testes/` ou `experimento/referencia/` exige `python executar.py validar` passando (referência 64/64, stub 0/64, todos os mutantes detectados).
5. **Afirmações sobre a API** (modelos, preços, parâmetros) vêm da documentação oficial, não da memória.

## Comandos

- `/retomar`: início de sessão. Lê `docs/STATUS.md` e propõe o próximo passo.
- `/encerrar`: fim de sessão. Atualiza `docs/STATUS.md` e lista o que o autor precisa commitar.

## Experimento

```bash
cd experimento
py -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python executar.py validar
.venv/Scripts/python executar.py gerar --n 30                    # via API (precisa de ANTHROPIC_API_KEY)
.venv/Scripts/python executar.py registrar                       # avalia código gerado fora da API (agentes)
.venv/Scripts/python analisar.py
```

A coleta de 2026-10-15 foi feita por agentes do Claude Code (sem custo de API): cada agente leu só a especificação e gravou `resultados/<condicao>/<NN>/biblioteca.py`; depois `registrar` + `analisar`. O PDF do relatório é gerado convertendo o Markdown com a biblioteca `markdown` e imprimindo com o Chrome headless (`--print-to-pdf`).

## Estado atual

@docs/STATUS.md
