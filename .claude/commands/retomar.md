---
description: Retoma o trabalho, lendo o estado atual e propondo o próximo passo
allowed-tools: Read, Glob, Grep, Bash(git status:*), Bash(git log:*), Bash(git diff:*)
---

Você está iniciando uma sessão no ProcessoeArtefatoAED. O prazo de entrega é 2026-10-20, 23:59.

1. Leia `docs/STATUS.md` inteiro.
2. Rode `git status` e `git log --oneline -10`. Se houver mudanças não commitadas, avise: o autor pode ter esquecido de commitar a sessão anterior.
3. Se `experimento/resultados/execucoes.csv` existir, diga quantas execuções há por condição.

Responda de forma curta, com:

- **Onde paramos** (2 ou 3 linhas).
- **Pendências:** mudanças não commitadas e bloqueios.
- **Proposta para hoje:** 1 ou 2 tarefas com critério de pronto verificável, considerando os dias que faltam para o prazo.

Termine perguntando o que o autor quer fazer. Não comece antes da resposta.

$ARGUMENTS
