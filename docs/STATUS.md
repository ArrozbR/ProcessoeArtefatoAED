# Status

**Prazo:** 2026-10-20, 23:59

## Checklist

- [x] Processo (`entregaveis/1-processo.md`)
- [x] Modelo do documento (`entregaveis/2-modelo-documento-requisitos.md`)
- [x] Instruções de preenchimento (`entregaveis/3-instrucoes-preenchimento.md`)
- [x] Caso de uso especificado no modelo e versão narrativa de controle
- [x] Oráculo (64 testes) validado: referência 64/64, stub 0/64, 8/8 mutantes
- [x] Scripts de geração (`executar.py`) e análise (`analisar.py`)
- [x] Piloto (5 execuções ERL): 5/5 com 100%, nenhum defeito de especificação; documento congelado
- [x] Coleta principal (30 + 30) por agentes do Claude Code: 60/60 com 100%
- [x] Relatório com resultados e PDF (`entregaveis/4-relatorio-tecnico.pdf`)
- [x] Nomes do grupo no relatório e PDF regerado
- [ ] Revisão do grupo e entrega

## Diário de sessões

### 2026-10-19
- Feito: tentativa de replicar com o Gemini pela API gratuita. Os modelos "pro" não têm cota sem cartão e o `gemini-3.8-flash` respondeu só 503/429; abandonado, e as mudanças nos scripts foram descartadas. A PR `proposta-precisa` (processo PRECISA, sem resultados) foi comparada com a main e fechada.
- Ficou confuso: nada.

### 2026-10-15
- Feito: estrutura do repositório, os quatro entregáveis, especificações, oráculo validado e scripts. Sem orçamento para a API, a geração foi feita com agentes do Claude Code (ADR 0003, adendo). Piloto 5/5 e coleta 60/60 com 100%. H1 aceita (Wilcoxon p = 2,2e-8); H2 indeterminada por efeito teto.
- Ficou confuso: nada pendente; a limitação "mesma família de modelo" está declarada no relatório.

## Próximos passos

1. Revisão do relatório (`entregaveis/4-relatorio-tecnico.pdf`) por pelo menos um membro do grupo; se algo mudar no `.md`, regerar o PDF (comando no `CLAUDE.md`).
2. Confirmar o formato de entrega pedido pelo professor (arquivos, PDF ou link do repositório).
3. Entregar até 2026-10-20, 23:59.

## Bloqueios

- Nenhum.
