# Validação do oráculo de aceitação

> Arquivo gerado por `experimento/avaliacao/validar_oraculo.py`.

**Implementação de referência:** 70/70 critérios atendidos.

## Mutantes

| # | Desvio introduzido | Critérios reprovados | Detectado? |
|---|---|---|---|
| 1 | Prazo do ALUNO de 15 dias (RN-02) | CA-05.1, CA-05.8, CA-05.9, CA-06.2, CA-06.3, CA-06.7, CA-07.1, CA-07.2, CA-07.3, CA-07.4, CA-10.1, CA-10.2, CA-10.3, CA-11.2, CV-07.1, CV-08.1 | sim |
| 2 | Limite do ALUNO de 4 empréstimos (RN-02) | CA-05.4, CA-05.11 | sim |
| 3 | Multa sem teto (RN-05) | CA-06.3 | sim |
| 4 | Pendência com ≥ em vez de > (RN-04) | CA-05.8 | sim |
| 5 | ISBN sem remover hífens (C-01) | CA-01.1, CA-01.6, CA-05.1 | sim |
| 6 | Pesquisa diferencia maiúsculas (RF-04) | CA-04.2 | sim |
| 7 | Indisponível verificado antes do limite (RF-05, C-05) | CA-05.11 | sim |
| 8 | Renovação somada à data prevista anterior (RN-07) | CA-07.1, CA-07.4, CA-07.7 | sim |
| 9 | Até 3 renovações (RN-07) | CA-07.4 | sim |
| 10 | Fila de reservas ignorada no empréstimo (RN-08) | CA-09.2 | sim |
| 11 | Valores monetários em float (C-03) | CA-05.9, CA-06.1, CA-06.2, CA-06.3, CA-06.4, CA-06.7, CA-07.6, CA-09.2, CA-09.3, CA-10.1, CA-10.2, CA-10.3, CA-10.4, CA-11.3, CV-08.1 | sim |
| 12 | Pagamento acima do saldo aceito (RF-10) | CA-10.3, CV-07.1 | sim |
| 13 | Atrasados sem desempate por id (RF-11) | CA-11.4 | sim |
| 14 | print() no cadastro (R-04) | CV-03.2 | sim |
| 15 | Método público sem docstring (RNF-06) | CV-06.1 | sim |
| 16 | Tipo de usuário não convertido para maiúsculas (C-02) | CA-02.1 | sim |
| 17 | Devolução aceita data anterior ao empréstimo (RF-06) | CA-06.5, CV-07.1 | sim |

**Escore de mutação:** 17/17 mutantes detectados.
