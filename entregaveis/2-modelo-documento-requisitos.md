# Documento de Requisitos — {{NOME DO SISTEMA}} ({{CÓDIGO, ex.: ERS-XXX-001}})

> Modelo **ERL** (Especificação de Requisitos para LLM), versão 2.0, para sistemas de **microsserviços**.
> Siga `3-instrucoes-preenchimento.md`. Todo texto entre `{{ }}` deve ser substituído, e toda linha que começa com `>` dentro das seções é orientação: apague-a antes de entregar o documento à LLM.
> Exemplo completo preenchido: `experimento/especificacoes/ERS-LOG-001.md`.

| Campo | Valor |
|---|---|
| Versão do documento | {{1.0}} |
| Data | {{AAAA-MM-DD}} |
| Autores | {{nomes}} |
| Situação | {{Rascunho / Pronto para geração}} |

---

## 0. Instruções para quem implementa

Este documento é a única fonte de verdade. Ao implementar:

1. Implemente **todos** os requisitos `RF`, `RN` e `RNF` deste documento, e **somente** eles.
2. Use exatamente os caminhos de arquivo, rotas HTTP, nomes de campos JSON, códigos de status e códigos de erro deste documento. Não renomeie, não traduza, não acrescente campos obrigatórios.
3. Quando mais de um erro se aplicar à mesma requisição, siga a **ordem de validação** de cada requisito.
4. Os **critérios de aceitação** (`CA`) são exemplos obrigatórios: o sistema deve produzir exatamente os resultados descritos.
5. Não invente regras. O que não está descrito aqui está na Seção 1.3 (fora de escopo).
6. Entregue a saída no formato da Seção 13.

---

## 1. Visão geral

### 1.1 Objetivo

> De uma a três frases: que problema o sistema resolve e para quem.

{{objetivo}}

### 1.2 Escopo: o que está incluído

- {{capacidade}}

### 1.3 Fora de escopo

> Liste o que uma LLM costuma acrescentar sozinha: banco de dados, autenticação, interface, filas reais, retentativas, leitura do relógio, edição/remoção de registros.

- {{item fora de escopo}}

### 1.4 Atores

| Ator | Descrição | Serviços que usa |
|---|---|---|
| {{ator}} | {{descrição}} | {{serviços}} |

---

## 2. Glossário

> Um termo, uma definição, sem sinônimos. Termos calculados ganham fórmula.

| Termo | Definição |
|---|---|
| {{termo}} | {{definição precisa}} |

---

## 3. Arquitetura

### 3.1 Serviços

> Uma linha por serviço. A coluna "Arquivo" fixa onde o código vai ficar; a coluna "Variável" fixa como os outros serviços o encontram.

| Serviço | Responsabilidade | Arquivo | Variável com a URL base |
|---|---|---|---|
| {{Nome}} | {{responsabilidade}} | `{{servicos/<nome>/main.py}}` | `{{URL_NOME}}` |
| {{Infraestrutura fornecida, ex.: barramento}} | {{responsabilidade}} | não deve ser implementado | `{{URL_...}}` |

> Escreva a regra de isolamento: um serviço não lê nem altera dados de outro diretamente.

### 3.2 Comunicação

> Diagrama (Mermaid) e uma tabela com toda chamada entre serviços. Para cada uma: síncrona (HTTP) ou por evento, e quando acontece.

| De | Para | Forma | Quando |
|---|---|---|---|
| {{serviço}} | {{serviço}} | {{HTTP `MÉTODO rota` / evento `tipo`}} | {{gatilho}} |

---

## 4. Restrições técnicas

| ID | Restrição |
|---|---|
| RT-01 | Linguagem e versão: {{ex.: Python 3.10+}} |
| RT-02 | Dependências permitidas: {{lista fechada}} |
| RT-03 | Estrutura: {{um arquivo por serviço, o que cada arquivo expõe e como é executado}} |
| RT-04 | Chamadas entre serviços: {{biblioteca, síncrona/assíncrona, tempo-limite}} |
| RT-05 | Representação: {{dinheiro, peso, datas, data e hora}} |
| RT-06 | Relógio e aleatoriedade: {{ex.: datas sempre na requisição}} |
| RT-07 | Saída padrão: {{ex.: o código não imprime nada}} |

---

## 5. Convenções gerais da API

> Tudo o que vale para todas as rotas de todos os serviços. É aqui que se evita repetir a mesma regra em cada RF.

### 5.1 Formato

{{JSON, nomes de campo, campos extras}}

### 5.2 Erros

```json
{"erro": "CODIGO_DO_ERRO", "mensagem": "texto"}
```

| HTTP | `erro` | Quando |
|---|---|---|
| 422 | `DADOS_INVALIDOS` | {{validação; diga explicitamente se inclui a validação automática do framework}} |
| 404 | {{código}} | {{recurso da rota não existe}} |
| 409 | {{código}} | {{conflito com o estado}} |
| 503 | {{código}} | {{serviço chamado indisponível}} |

### 5.3 Ordem geral de validação

{{ex.: validação do corpo antes de qualquer outra verificação}}

### 5.4 Atomicidade

{{ex.: requisição com erro não altera estado, não consome id, não publica evento}}

### 5.5 Identificadores

| Recurso | Formato | Regra |
|---|---|---|
| {{recurso}} | {{`PREFIXO-000001`}} | {{sequencial a partir de 1, por criação bem-sucedida}} |

### 5.6 Reinício de estado

{{rota de reset para os testes, ex.: `POST /admin/reset` → 204}}

---

## 6. Eventos

### 6.1 Envelope

```json
{"id": "{{...}}", "tipo": "{{...}}", "ocorrido_em": "{{...}}", "dados": {}}
```

### 6.2 Barramento

> Contrato do barramento (fornecido ou a implementar): como publicar, como os assinantes recebem, ordem e garantia de entrega.

### 6.3 Catálogo de eventos

| Tipo | Publicado por | Quando | `dados` |
|---|---|---|---|
| `{{dominio.fato}}` | {{serviço}} | {{gatilho}} | {{campos}} |

### 6.4 Consumo de eventos

> Envelope inválido, evento repetido (idempotência), tipo desconhecido e resposta de sucesso.

---

## 7. Regras de negócio

> Cada regra é uma frase afirmativa, atômica, com valores e fórmulas. Escreva também as permissões. Tabelas de referência (ex.: faixas de CEP) entram aqui.

| ID | Regra |
|---|---|
| RN-01 | {{regra}} |

---

## 8. Serviços e requisitos funcionais

### 8.0 Cenário base dos critérios de aceitação

> Defina uma vez os dados de partida (cadastros e requisições de referência com nome, ex.: S0, Q-A, E-1) e reutilize-os nos CAs. Isso deixa cada CA curto e sem ambiguidade.

### 8.{{n}} Serviço {{Nome}}

**Modelo de dados — {{Entidade}}**

| Campo | Tipo | Restrições | Valor inicial |
|---|---|---|---|
| `{{campo}}` | {{tipo}} | {{restrição verificável}} | {{valor ou —}} |

**Rotas**

| Método e rota | Sucesso | RF |
|---|---|---|
| `{{MÉTODO /rota}}` | {{status, corpo}} | {{RF-X01}} |

#### RF-{{X01}} — {{Nome da operação}}

- **Descrição:** O sistema deve {{ação}}.
- **Corpo / parâmetros:** {{campos e restrições, ou referência à tabela}}
- **Dependência:** {{chamadas HTTP feitas a outros serviços}}
- **Efeito:** {{o que muda no estado; eventos publicados}}
- **Resposta:** {{status e corpo}}

| # | Condição | HTTP | `erro` |
|---|---|---|---|
| 1 | {{condição}} | {{status}} | `{{CODIGO}}` |

- **CA-{{X01}}-01** — Dado {{estado inicial concreto}}, quando {{requisição concreta}}, então {{status e corpo exatos}}.

---

## 9. Requisitos não funcionais

| ID | Requisito | Métrica / limite | Como verificar |
|---|---|---|---|
| RNF-01 | {{requisito}} | {{valor numérico ou condição}} | {{teste, inspeção}} |

- **CA-RNF{{01}}-01** — {{critério verificável}}.

---

## 10. Cenário integrado

> Um fluxo de ponta a ponta que atravessa todos os serviços, com o resultado de cada passo.

| Passo | Ator | Requisição | Resultado esperado |
|---|---|---|---|
| 1 | {{ator}} | {{requisição}} | {{resultado}} |

- **CA-INT-01** — O cenário integrado produz exatamente os resultados da tabela.

---

## 11. Matriz de rastreabilidade

| Requisito | Regras | Rota | Critérios de aceitação |
|---|---|---|---|
| RF-{{X01}} | {{RN-xx}} | `{{rota}}` | {{CA-X01-01 a 0n}} |

---

## 12. Questões em aberto

> Precisa estar vazia para a Situação ser "Pronto para geração".

| # | Questão | Responsável | Decisão |
|---|---|---|---|
| — | Nenhuma | — | — |

---

## 13. Formato de entrega

{{ex.: um bloco de código por arquivo, com a primeira linha `# arquivo: <caminho>`; nada fora dos blocos}}
