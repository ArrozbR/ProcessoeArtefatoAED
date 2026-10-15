<!--
  MODELO PRECISA v1.0 — Documento de Especificação de Requisitos (ERS)
  Como usar: copie este arquivo, substitua todo texto entre < > e remova os comentários HTML.
  Instruções detalhadas: docs/3-instrucoes-preenchimento.md
  Exemplo preenchido: experimento/especificacao/ERS-BIB-001.md
  Convenção: seções marcadas (OBRIGATÓRIA) não podem ser removidas; se não se aplicam, escreva "Não se aplica — <motivo>".
-->

# Especificação de Requisitos de Software — <Nome do Sistema>

## 0. Metadados (OBRIGATÓRIA)

| Campo | Valor |
|---|---|
| Identificador | ERS-<SIGLA>-<NNN> |
| Versão | <x.y> |
| Status | <Rascunho \| Em revisão \| Aprovado> |
| Autores | <nomes> |
| Revisores | <nomes — diferentes dos autores> |
| Aprovado por (PO) | <nome> |
| Data | <AAAA-MM-DD> |
| Plataforma-alvo | <linguagem e versão, ex.: Python ≥ 3.10> |

## 1. Diretivas para a LLM (OBRIGATÓRIA)

<!-- Texto padrão: mantenha-o. Ajuste apenas os trechos entre < >. -->

Você é a pessoa desenvolvedora responsável por implementar o software descrito neste documento. Siga estas diretivas:

1. Implemente **todos** os requisitos deste documento e **nada além** deles.
2. Respeite **exatamente** o Contrato de Interface (seção 6): nomes de arquivos, classes, funções, parâmetros, tipos de retorno e erros. Não renomeie, não traduza e não acrescente parâmetros obrigatórios.
3. Os Critérios de Aceitação (CA) e de Verificação (CV) são casos de teste: o código DEVE satisfazer cada um deles literalmente, com os valores indicados.
4. Respeite as Restrições (seção 4); não use dependências não autorizadas.
5. As palavras **DEVE**, **NÃO DEVE** e **PODE** têm sentido normativo (obrigatório, proibido, opcional).
6. Use os termos exatamente como definidos no Glossário (seção 3).
7. Se algo não estiver especificado, escolha a alternativa mais simples que não viole nenhum requisito e registre-a no código com um comentário iniciado por `<marcador de suposição, ex.: # SUPOSIÇÃO:>`.
8. Em caso de conflito aparente entre seções, a precedência é: Restrições (4) > Contrato de Interface (6) > Critérios de Aceitação/Verificação (8 e 9) > Regras de Negócio (7) > textos descritivos.
9. Formato da resposta: <ex.: um único bloco de código contendo o arquivo completo `nome.ext`, sem explicações antes ou depois>.

## 2. Visão geral e contexto (OBRIGATÓRIA)

### 2.1 Propósito

<Uma ou duas frases: o que o software faz e para quem.>

### 2.2 Contexto e problema

<Situação atual, problema a ser resolvido e benefício esperado. Máximo de um parágrafo.>

### 2.3 Necessidades de origem

| ID | Necessidade | Origem (quem pediu) |
|---|---|---|
| N-01 | <necessidade expressa pela parte interessada> | <pessoa/papel> |

### 2.4 Escopo — incluído

- <funcionalidade incluída>

### 2.5 Escopo — fora do escopo

<!-- Liste o que a LLM poderia "achar útil" acrescentar, mas NÃO deve: persistência, interface gráfica, autenticação, logs etc. -->

- <item explicitamente excluído>

### 2.6 Atores

| Ator | Descrição | Interação com o sistema |
|---|---|---|
| <ator> | <quem é> | <o que faz no sistema> |

## 3. Glossário (OBRIGATÓRIA)

<!-- Um termo = um significado. Defina todo termo de domínio usado nos requisitos. Não use sinônimos no restante do documento. -->

| Termo | Definição |
|---|---|
| <Termo> | <Definição precisa, preferencialmente operacional (como se calcula / como se reconhece).> |

## 4. Restrições técnicas e de ambiente (OBRIGATÓRIA)

| ID | Restrição |
|---|---|
| R-01 | Linguagem/plataforma: <...> |
| R-02 | Dependências permitidas: <ex.: somente a biblioteca padrão> |
| R-03 | Estrutura de entrega: <arquivos/pastas exatos> |
| R-04 | <Proibições: ex.: não ler/gravar arquivos, não acessar rede, não usar relógio do sistema> |

## 5. Modelo de dados (OBRIGATÓRIA se houver dados estruturados)

<!-- Uma tabela por entidade. Ordem dos campos = ordem de declaração esperada no código. -->

### 5.1 <Entidade>

<Como a entidade deve ser representada no código, ex.: "dataclass `Nome` (módulo `x`)".>

| Campo | Tipo | Restrições | Valor inicial/padrão |
|---|---|---|---|
| <campo> | <tipo> | <ex.: não vazio; 0 ≤ x ≤ y> | <valor ou "—"> |

## 6. Contrato de interface (OBRIGATÓRIA)

### 6.1 Arquivos e módulos

| Arquivo | Conteúdo |
|---|---|
| <arquivo> | <o que deve conter> |

### 6.2 Erros / exceções

<!-- Defina a hierarquia completa. Cada condição de erro dos RFs aponta para um destes nomes. -->

| Nome | Herda de | Quando é usado (resumo) |
|---|---|---|
| <NomeErro> | <Base> | <situação> |

### 6.3 Operações

<!-- Uma linha por operação pública. Assinatura copiável, com tipos. -->

| Operação (assinatura exata) | Retorno | Requisito |
|---|---|---|
| `<nome(param: tipo, ...)>` | `<tipo>` | RF-<xx> |

### 6.4 Convenções gerais

<!-- Regras transversais que valem para TODAS as operações: normalização de entrada, arredondamento, formato de datas, ordenação padrão, ordem de validação, atomicidade, mutabilidade dos objetos retornados. -->

| ID | Convenção |
|---|---|
| C-01 | <ex.: textos recebidos têm espaços das extremidades removidos antes de validar e armazenar> |
| C-02 | <ex.: as verificações de erro são feitas na ordem listada em cada RF; a primeira que falhar determina o erro> |
| C-03 | <ex.: se uma operação lança erro, nenhum dado é alterado> |

## 7. Regras de negócio (OBRIGATÓRIA)

<!-- Regras atômicas, com números, fórmulas e unidades. Referenciadas pelos RFs. -->

| ID | Regra | Fórmula / valores |
|---|---|---|
| RN-01 | <regra> | <ex.: multa = min(dias × 2,00; 50,00)> |

## 8. Requisitos funcionais (OBRIGATÓRIA)

<!-- Repita o bloco abaixo para cada RF. -->

### RF-<xx> — <Título curto no infinitivo>

| Campo | Conteúdo |
|---|---|
| Requisito | <Frase EARS. Ex.: "QUANDO o usuário solicitar X, o sistema DEVE Y."> |
| Prioridade | <Must \| Should \| Could> |
| Origem | <N-xx> |
| Operação | `<assinatura da seção 6.3>` |
| Regras de negócio | <RN-xx, RN-yy> |

**Pré-condições:** <estado necessário antes da operação, ou "nenhuma">.

**Validações, em ordem** (a primeira que falhar determina o erro; nada é alterado):

| # | Condição de falha | Erro |
|---|---|---|
| 1 | <condição> | `<NomeErro>` |

**Processamento (caso de sucesso):**

1. <passo>
2. <passo>

**Saída:** <o que é retornado, com tipo e formato>.

**Critérios de aceitação:**

<!-- Dado/Quando/Então com valores concretos. Cubra: caminho feliz, cada erro da tabela, cada valor-limite, a ordem de validação quando houver duas falhas possíveis. -->

- **CA-<xx>.1** — **Dado** <estado inicial concreto>, **quando** <ação com valores>, **então** <resultado observável com valores>.
- **CA-<xx>.2** — **Dado** <...>, **quando** <...>, **então** <...>.

## 9. Requisitos não funcionais (OBRIGATÓRIA)

<!-- Somente requisitos mensuráveis. Categoria conforme ISO/IEC 25010. -->

| ID | Categoria | Requisito | Critério de verificação |
|---|---|---|---|
| RNF-01 | <ex.: Portabilidade> | <requisito com métrica> | **CV-01.1** — <como verificar e limite de aceitação> |

## 10. Cenário de ponta a ponta (RECOMENDADA)

<!-- Uma sequência curta de chamadas reais com os resultados esperados, mostrando as operações trabalhando juntas. -->

```text
<chamada 1>  →  <resultado>
<chamada 2>  →  <resultado>
```

## 11. Premissas, decisões e questões em aberto (OBRIGATÓRIA)

| ID | Tipo | Descrição | Situação |
|---|---|---|---|
| P-01 | <Premissa \| Decisão \| Questão> | <texto> | <Resolvida em AAAA-MM-DD por ... \| Aberta> |

> Um documento com status **Aprovado** NÃO PODE ter questões com situação **Aberta**.

## 12. Matriz de rastreabilidade (OBRIGATÓRIA)

| Necessidade | Requisito | Regras | Operação | Critérios |
|---|---|---|---|---|
| N-<xx> | RF-<xx> | RN-<xx> | `<operação>` | CA-<xx>.1 … CA-<xx>.n |

## 13. Checklist de prontidão — Definition of Ready (OBRIGATÓRIA)

- [ ] Todas as seções obrigatórias estão preenchidas (ou marcadas "Não se aplica — motivo").
- [ ] Todo termo de domínio usado está no Glossário e é usado sempre com o mesmo nome.
- [ ] Toda operação tem assinatura completa com tipos e todos os erros possíveis listados.
- [ ] Toda operação com mais de uma validação tem a ordem de validação definida.
- [ ] Toda regra de negócio tem valores concretos (números, unidades, fórmulas, arredondamento).
- [ ] Todo RF tem ao menos um CA de caminho feliz, um CA por erro e CAs para os valores-limite.
- [ ] Todo RNF tem métrica e critério de verificação (CV).
- [ ] Toda listagem tem critério de ordenação e de desempate.
- [ ] O que está fora do escopo está explícito.
- [ ] Nenhuma palavra vaga (ver lista nas instruções) permanece no texto.
- [ ] Não há questões abertas (seção 11).
- [ ] A revisão adversarial por LLM foi feita e todos os achados foram tratados.
- [ ] A matriz de rastreabilidade cobre 100% dos RF, RN e critérios.

## 14. Histórico de revisões (OBRIGATÓRIA)

| Versão | Data | Autor | Descrição da mudança |
|---|---|---|---|
| <0.1> | <AAAA-MM-DD> | <nome> | <Versão inicial> |
