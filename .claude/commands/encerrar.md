---
description: Encerra a sessão, atualiza docs/STATUS.md e lista o que o autor precisa commitar
allowed-tools: Read, Edit, Write, Glob, Grep, Bash(git status:*), Bash(git diff:*), Bash(git log:*)
---

Você está encerrando uma sessão no ProcessoeArtefatoAED. **Nunca execute `git add`, `git commit` ou `git push`.** Quem commita é o autor.

1. Rode `git status` e `git diff --stat`.
2. Atualize `docs/STATUS.md`: checklist (marque só o que tem critério de pronto verificado), uma entrada nova no topo do diário de sessões, próximos passos (no máximo 3) e bloqueios.
3. Se uma decisão foi tomada na sessão, proponha um ADR em `docs/decisoes/` a partir de `docs/decisoes/TEMPLATE.md`.
4. Responda com:
   - resumo da sessão (até 3 linhas);
   - **arquivos para commitar**, agrupados por assunto;
   - **mensagem de commit sugerida** (Conventional Commits, em português).

$ARGUMENTS
