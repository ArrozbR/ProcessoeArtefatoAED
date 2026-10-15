# Documento de Requisitos — {{NOME DO SISTEMA}}

> Modelo **ERL** (Especificação de Requisitos para LLM), versão 1.0.
> Siga `3-instrucoes-preenchimento.md` para preencher este documento. Todo texto entre `{{ }}` deve ser substituído, e toda linha que começa com `>` dentro das seções é orientação: apague-a antes de entregar o documento à LLM.

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
2. Use exatamente os nomes, assinaturas, tipos e exceções da Seção 5. Não renomeie, não traduza, não acrescente parâmetros obrigatórios.
3. Quando mais de um erro se aplicar à mesma chamada, siga a **ordem de validação** de cada requisito.
4. Os **critérios de aceitação** (`CA`) são exemplos obrigatórios: o código deve produzir exatamente os resultados descritos.
5. Não invente regras. Situações não descritas aqui estão na Seção 1.3 (fora de escopo) e não precisam ser tratadas.
6. Entregue a saída no formato da Seção 12.

---

## 1. Visão geral

### 1.1 Objetivo

> Uma a três frases: que problema o sistema resolve e para quem.

{{objetivo}}

### 1.2 Escopo: o que está incluído

> Lista curta das capacidades. Cada item vira um ou mais RF.

- {{capacidade}}

### 1.3 Fora de escopo

> O que o sistema **não** faz. Isso impede a LLM de inventar funcionalidades (persistência, interface gráfica, autenticação, etc.).

- {{item fora de escopo}}

### 1.4 Atores

| Ator | Descrição | O que faz no sistema |
|---|---|---|
| {{ator}} | {{descrição}} | {{RFs que usa}} |

---

## 2. Glossário

> Um termo, uma definição. O mesmo termo deve ser usado no documento inteiro, sem sinônimos.

| Termo | Definição |
|---|---|
| {{termo}} | {{definição precisa}} |

---

## 3. Restrições técnicas

| ID | Restrição |
|---|---|
| RT-01 | Linguagem e versão: {{ex.: Python 3.12}} |
| RT-02 | Dependências permitidas: {{ex.: apenas a biblioteca padrão}} |
| RT-03 | Estrutura de arquivos: {{ex.: um único arquivo `modulo.py`}} |
| RT-04 | Ponto de entrada: {{ex.: classe `X` importável de `modulo`}} |
| RT-05 | Representação de valores especiais: {{ex.: dinheiro em centavos (`int`), datas como `datetime.date`}} |

---

## 4. Modelo de dados

> Uma tabela por entidade. Todo campo com tipo, obrigatoriedade e restrição verificável.

### 4.1 {{Entidade}}

| Campo | Tipo | Obrigatório | Restrições | Valor inicial |
|---|---|---|---|---|
| {{campo}} | {{tipo}} | {{Sim/Não}} | {{regra verificável}} | {{valor ou —}} |

---

## 5. Interface pública (contrato)

> A parte mais importante para a conformidade: é por aqui que o software é testado. Escreva as assinaturas exatamente como devem aparecer no código.

### 5.1 Exceções

| Exceção | Herda de | Quando é lançada |
|---|---|---|
| {{ErroBase}} | `Exception` | Base de todos os erros do sistema |
| {{NomeDoErro}} | {{ErroBase}} | {{situação}} |

### 5.2 Operações

| Assinatura | Retorno | RF |
|---|---|---|
| `{{nome(param: tipo, ...) -> tipo}}` | {{significado do retorno}} | {{RF-xx}} |

---

## 6. Regras de negócio

> Cada regra é uma frase afirmativa, atômica, com valores concretos. Regras que permitem algo também são escritas, porque a ausência de regra é ambígua.

| ID | Regra |
|---|---|
| RN-01 | {{regra com valores}} |

---

## 7. Requisitos funcionais

> Copie o bloco abaixo para cada RF. Um RF descreve uma operação da Seção 5.2.

### RF-{{01}} — {{Nome da operação}}

- **Descrição:** O sistema deve {{ação}}.
- **Prioridade:** {{Essencial / Importante / Desejável}}
- **Operação:** `{{assinatura}}`
- **Regras aplicadas:** {{RN-xx, RN-yy}}
- **Pré-condições:** {{estado exigido antes da chamada}}
- **Efeito (pós-condições):** {{o que muda no estado do sistema}}
- **Retorno:** {{valor e significado}}

**Ordem de validação** (a primeira condição verdadeira define o erro; nada é alterado quando há erro):

| # | Condição | Exceção |
|---|---|---|
| 1 | {{condição}} | `{{Exceção}}` |

**Critérios de aceitação:**

- **CA-RF{{01}}-01** — Dado {{estado inicial concreto}}, quando {{chamada concreta com valores}}, então {{resultado exato}}.
- **CA-RF{{01}}-02** — Dado {{...}}, quando {{...}}, então é lançada `{{Exceção}}` e {{o que não mudou}}.

---

## 8. Requisitos não funcionais

> Só entra RNF com métrica e forma de verificação. "O sistema deve ser rápido" não é RNF.

| ID | Requisito | Métrica / limite | Como verificar |
|---|---|---|---|
| RNF-01 | {{requisito}} | {{valor numérico ou condição}} | {{teste, inspeção}} |

**Critérios de aceitação:**

- **CA-RNF{{01}}-01** — {{critério verificável}}.

---

## 9. Cenário integrado

> Um exemplo ponta a ponta que encadeia vários RF, com o estado esperado após cada passo. Serve para a LLM conferir a interação entre as regras.

| Passo | Chamada | Resultado esperado |
|---|---|---|
| 1 | `{{chamada}}` | {{resultado}} |

---

## 10. Matriz de rastreabilidade

| Requisito | Regras | Operação | Critérios de aceitação |
|---|---|---|---|
| RF-{{01}} | {{RN-xx}} | `{{operação}}` | {{CA-RF01-01 a CA-RF01-0n}} |

---

## 11. Questões em aberto

> Precisa estar **vazia** para a Situação ser "Pronto para geração". Resolva cada questão com o cliente e mova a decisão para a seção certa.

| # | Questão | Responsável | Decisão |
|---|---|---|---|
| — | Nenhuma | — | — |

---

## 12. Formato de entrega

{{ex.: Responda com um único bloco de código contendo o arquivo `modulo.py` completo, sem explicações fora do bloco.}}
