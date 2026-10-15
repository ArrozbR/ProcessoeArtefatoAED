# Entregável 3 — Instruções de Preenchimento do Modelo PRECISA

Este guia explica **como preencher** o [modelo de documento de requisitos](2-modelo-documento-requisitos.md). Ele pressupõe que as fases F1 (Elicitação) e F2 (Análise) do [processo](1-processo.md) já foram realizadas. Um exemplo completo e aprovado está em [ERS-BIB-001](../experimento/especificacao/ERS-BIB-001.md) — consulte-o sempre que estiver em dúvida.

> **Regra de ouro:** escreva para um leitor que **não pode fazer perguntas** e que, diante de qualquer dúvida, vai **chutar**. Esse leitor é a LLM.

---

## 1. Antes de começar

1. Copie o arquivo do modelo para `ERS-<SIGLA>-<NNN>.md`.
2. Preencha as seções **na ordem**: 0 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14. A seção 1 (Diretivas) só tem dois campos a ajustar.
   - Motivo da ordem: glossário e modelo de dados (3, 5) fixam o vocabulário; o contrato (6) fixa os nomes; só então os requisitos (8) podem referenciá-los sem ambiguidade.
3. Remova todos os comentários `<!-- -->` e todos os marcadores `< >` ao final. Um `<` esquecido é um requisito indefinido.

## 2. Regras gerais de redação

| # | Regra | Errado | Certo |
|---|---|---|---|
| 1 | Use **DEVE / NÃO DEVE / PODE** (maiúsculas) para obrigatório / proibido / opcional. Não use "deveria", "pode ser que", "idealmente". | "O sistema deveria validar o ISBN." | "O sistema DEVE validar o ISBN conforme RN-01." |
| 2 | **Uma obrigação por frase.** Se a frase tem "e" ligando duas ações, divida. | "O sistema DEVE registrar o empréstimo e avisar o usuário e atualizar o estoque." | Três frases ou uma lista numerada de passos. |
| 3 | **Sujeito explícito** ("o sistema", "a operação X"). Evite voz passiva sem agente. | "A multa é calculada." | "A operação `devolver` DEVE calcular a multa conforme RN-05." |
| 4 | **Valores concretos com unidade.** | "prazo curto", "multa pequena" | "14 dias corridos", "R$ 2,00 por dia" |
| 5 | **Termos do glossário, sempre iguais.** Escolha "usuário" *ou* "leitor" *ou* "cliente" — nunca alterne. | "usuário" na seção 5 e "leitor" na seção 8 | "usuário" em todo o documento |
| 6 | **Nomes de código em `crase`** e copiados do contrato (seção 6). | "a função de empréstimo" | "`emprestar(isbn, usuario_id, data)`" |
| 7 | **Diga também o que NÃO fazer** quando houver uma interpretação "tentadora" errada. | (silêncio sobre acentos) | "A comparação NÃO DEVE ignorar acentos: `cortico` não encontra `Cortiço`." |
| 8 | **Datas em AAAA-MM-DD**, dinheiro com duas casas e separador explícito. | "2/3", "R$2" | "2026-03-02", "R$ 2,00 (`Decimal("2.00")`)" |
| 9 | **Limites com operador matemático** (`<`, `≤`, `>`, `≥`). | "até 3 livros", "depois do prazo" | "no máximo 3 (≤ 3)", "data > data prevista" |

### 2.1 Palavras e expressões proibidas

Se uma destas aparecer, substitua por um valor ou critério verificável:

> rápido, lento, fácil, amigável, intuitivo, adequado, apropriado, eficiente, robusto, flexível, moderno, simples, seguro (sem métrica), etc., e/ou, entre outros, normalmente, geralmente, se possível, quando necessário, alguns, vários, muitos, poucos, pequeno, grande, suficiente, razoável, o mais rápido possível, e assim por diante, similar, semelhante, TBD, a definir.

## 3. Padrões EARS (em português)

Todo requisito da coluna **Requisito** dos RFs e RNFs deve seguir um destes padrões (Mavin et al., 2009):

| Padrão | Estrutura | Exemplo |
|---|---|---|
| **Ubíquo** (sempre vale) | O sistema DEVE `<resposta>`. | O sistema DEVE armazenar o ISBN sem hífens. |
| **Orientado a evento** | QUANDO `<gatilho>`, o sistema DEVE `<resposta>`. | QUANDO um empréstimo for registrado, o sistema DEVE reduzir em 1 os exemplares disponíveis. |
| **Orientado a estado** | ENQUANTO `<estado>`, o sistema DEVE `<resposta>`. | ENQUANTO houver fila de reserva para um ISBN, o sistema DEVE permitir o empréstimo apenas ao primeiro da fila. |
| **Comportamento indesejado** | SE `<condição indesejada>`, ENTÃO o sistema DEVE `<resposta>`. | SE o ISBN for inválido, ENTÃO o sistema DEVE lançar `ISBNInvalidoErro`. |
| **Opcional** | ONDE `<característica presente>`, o sistema DEVE `<resposta>`. | ONDE o usuário for do tipo PROFESSOR, o sistema DEVE usar o prazo de 28 dias. |
| **Complexo** | Combinação dos anteriores. | ENQUANTO o empréstimo estiver ativo, QUANDO for solicitada a renovação, o sistema DEVE… |

## 4. Como escrever critérios de aceitação

Formato obrigatório:

> **CA-xx.n** — **Dado** `<estado inicial concreto>`, **quando** `<ação com valores reais>`, **então** `<resultado observável com valores reais>`.

**Regras:**

1. **Valores reais, nunca genéricos.** ❌ "Dado um livro válido" → ✅ "Dado o livro ISBN `9788533302273` com 2 exemplares".
2. **Resultado observável pela interface.** O "então" fala do retorno, do erro lançado ou do estado consultável por outra operação pública — nunca de variáveis internas.
3. **Um comportamento por critério.** Se o "então" tem dois resultados independentes, pense em dividir (exceção: o resultado e a verificação de que nada mais mudou podem ficar juntos).
4. **Cobertura mínima por RF:**
   - 1 CA de **caminho feliz**;
   - 1 CA **para cada linha da tabela de validações** (cada erro);
   - CAs nos **valores-limite** de cada regra numérica (exatamente no limite, um abaixo, um acima — ex.: devolução *no* dia previsto e *um dia depois*);
   - 1 CA de **ordem de validação** quando duas falhas podem ocorrer juntas;
   - 1 CA de **atomicidade** (após um erro, o estado não mudou) para operações que alteram dados.
5. **Todo CA deve ser derivável do texto.** Se o CA revela uma regra que não está escrita em nenhuma RN ou RF, escreva a regra — o CA é exemplo, não fonte escondida.

**Critérios de verificação (CV) para RNF** seguem a mesma ideia, mas descrevem o *método* e o *limite*: "**CV-04.1** — Cadastrar 2.000 livros, 2.000 usuários e registrar 2.000 empréstimos DEVE levar menos de 5 s (medido com `time.perf_counter`)".

## 5. Instruções seção a seção

### Seção 0 — Metadados
- **Identificador:** `ERS-<SIGLA DO SISTEMA>-<número com 3 dígitos>`.
- **Status:** só passa a **Aprovado** depois da fase F4 (validação) e com o checklist da seção 13 completo.
- **Revisores** devem ser diferentes dos **Autores**.

### Seção 1 — Diretivas para a LLM
- **Não reescreva** as diretivas; elas foram testadas no experimento. Ajuste apenas: o marcador de suposição (ex.: `# SUPOSIÇÃO:` em Python, `// SUPOSIÇÃO:` em Java/JS) e o **formato da resposta** (quantos arquivos, em quais blocos).
- Pedir "um único bloco de código, sem explicações" facilita extrair o código sem edição humana.

### Seção 2 — Visão geral e contexto
- **2.1 Propósito:** no máximo duas frases.
- **2.3 Necessidades:** copie as necessidades N-xx da elicitação (F1) com a origem. Elas ancoram a rastreabilidade.
- **2.5 Fora do escopo:** é tão importante quanto o escopo. LLMs tendem a "agregar valor" adicionando persistência em arquivo, menus, `print`, logs, validações extras. Liste tudo o que **não** deve existir.

### Seção 3 — Glossário
- Defina **operacionalmente**: diga como reconhecer ou calcular. ❌ "Atraso: quando o livro está atrasado." ✅ "Atraso: um empréstimo ativo está atrasado na data D se D > data prevista de devolução."
- Inclua termos que parecem óbvios mas têm regra própria (ex.: "ativo", "disponível", "pendência").

### Seção 4 — Restrições técnicas
- Linguagem **e versão mínima**; dependências permitidas (prefira "somente biblioteca padrão" para eliminar variação de ambiente); estrutura exata de arquivos; proibições (I/O, rede, relógio do sistema, variáveis globais).
- Se o software depende de data/hora, **exija que a data seja recebida por parâmetro** — isso torna o comportamento testável e determinístico.

### Seção 5 — Modelo de dados
- Uma tabela por entidade, **na ordem** em que os campos devem ser declarados.
- Tipo preciso (`str`, `int`, `Decimal`, `date`, `list[str]`, `X | None`), restrições e valor inicial.
- Diga **como** a entidade é representada (ex.: `@dataclass`), para que a LLM não invente `dict`, tupla ou classe com *getters*.

### Seção 6 — Contrato de interface
- **6.1 Arquivos:** nomes exatos.
- **6.2 Erros:** hierarquia completa (nome, classe-base). Todo erro citado nos RFs deve estar aqui e vice-versa.
- **6.3 Operações:** assinatura **copiável**, com nomes e tipos dos parâmetros e do retorno. É o item de maior impacto na conformidade: sem ele, nenhum teste automatizado consegue nem chamar o código.
- **6.4 Convenções gerais:** regras transversais escritas **uma vez** e que valem para todas as operações — normalização de entrada (`strip`, remoção de hífens, maiúsculas), arredondamento monetário, ordem de validação, **atomicidade** (erro ⇒ nada muda), se objetos retornados são cópias ou referências.

### Seção 7 — Regras de negócio
- **Atômicas** (uma regra por linha) e com **fórmula**: `multa = min(dias_atraso × 2,00; 50,00)`.
- Valores parametrizados por tipo (ex.: limite e prazo por tipo de usuário) ficam melhor em uma **tabela** dentro da regra.
- Toda RN deve ser citada por ao menos um RF (verifique na matriz da seção 12).

### Seção 8 — Requisitos funcionais
Para cada RF:
1. **Requisito:** uma frase EARS que resume o comportamento.
2. **Prioridade:** MoSCoW (Must/Should/Could).
3. **Operação:** a assinatura da seção 6.3.
4. **Validações, em ordem:** tabela "condição de falha → erro", **na ordem em que devem ser verificadas**. A ordem só importa quando duas falhas podem ocorrer juntas — mas, como a LLM não sabe quando isso acontece, **sempre** informe a ordem.
5. **Processamento:** passos numerados do caso de sucesso, incluindo **todos os efeitos colaterais** (o que muda em outras entidades).
6. **Saída:** tipo e formato do retorno.
7. **Critérios de aceitação:** conforme a seção 4 deste guia.

### Seção 9 — Requisitos não funcionais
- Só entram RNFs **mensuráveis**. "O sistema deve ser rápido" não é RNF; "2.000 empréstimos em < 5 s" é.
- Categorias úteis (ISO/IEC 25010): Desempenho, Confiabilidade, Manutenibilidade, Portabilidade, Segurança, Usabilidade.
- Cada RNF tem ao menos um **CV** com método e limite.

### Seção 10 — Cenário de ponta a ponta
- Uma sequência curta de chamadas mostrando as operações juntas, com os resultados esperados. Funciona como "exemplo resolvido" para a LLM e frequentemente revela inconsistências entre RFs.

### Seção 11 — Premissas, decisões e questões em aberto
- Registre as decisões tomadas com o PO na análise (ex.: "a busca não ignora acentos — decidido em 2026-03-01").
- Documento **Aprovado** não pode ter questões **Abertas**.

### Seção 12 — Matriz de rastreabilidade
- Uma linha por RF (e RNF), ligando necessidade → requisito → regras → operação → critérios.
- Use-a para conferir: toda N-xx tem RF? Toda RN é usada? Todo CA pertence a um RF?

### Seção 13 — Checklist de prontidão
- O autor marca na autoverificação (F3); o revisor confirma na validação (F4). Só com 100% marcado o status vira **Aprovado**.

### Seção 14 — Histórico de revisões
- Uma linha por versão, inclusive as mudanças motivadas pela validação (F4) e pela análise de causa-raiz (F7).

## 6. Identificadores

| Elemento | Formato | Exemplo |
|---|---|---|
| Necessidade | `N-nn` | N-03 |
| Restrição | `R-nn` | R-02 |
| Convenção geral | `C-nn` | C-04 |
| Regra de negócio | `RN-nn` | RN-05 |
| Requisito funcional | `RF-nn` | RF-06 |
| Critério de aceitação (do RF-nn) | `CA-nn.m` | CA-06.3 |
| Requisito não funcional | `RNF-nn` | RNF-04 |
| Critério de verificação (do RNF-nn) | `CV-nn.m` | CV-04.1 |
| Premissa/decisão/questão | `P-nn` | P-02 |

IDs **nunca são reutilizados**: um requisito removido mantém o ID marcado como "Removido" no histórico.

## 7. Checklist de qualidade (inspeção na fase F4)

Baseado nas características de requisitos bem formados da ISO/IEC/IEEE 29148. O revisor responde **para cada RF/RNF**:

| Característica | Pergunta de inspeção |
|---|---|
| **Necessário** | Está ligado a uma necessidade (N-xx)? Se removido, alguém sentiria falta? |
| **Inequívoco** | Duas pessoas diferentes implementariam do mesmo jeito? Há palavra da lista proibida? |
| **Completo** | Entradas, saídas, erros, ordem de validação, efeitos colaterais e limites estão todos definidos? |
| **Singular** | Trata de uma única capacidade? |
| **Consistente** | Não contradiz outro requisito, RN, convenção ou CA? Usa os termos do glossário? |
| **Verificável** | Existe CA/CV com valores concretos que prove o atendimento? |
| **Factível** | Pode ser implementado com as restrições da seção 4? |
| **Rastreável** | Tem ID único e está na matriz? |
| **Conforme a interface** | Todos os nomes de código citados existem na seção 6, com a mesma grafia? |

Classifique cada achado em **Alta** (a LLM provavelmente gerará comportamento diferente), **Média** (pode gerar) ou **Baixa** (redação). Achados de severidade alta bloqueiam a aprovação.

## 8. Revisão adversarial por LLM (fase F4)

Use uma sessão **nova** de LLM e envie o prompt abaixo seguido do documento completo:

```text
Você é um revisor de requisitos rigoroso. A seguir está um documento de especificação
que será entregue a uma LLM para gerar o código sem possibilidade de perguntas.

Sua tarefa NÃO é implementar. Liste, em uma tabela com as colunas
[ID | Seção | Trecho | Tipo | Problema | Pergunta ao autor | Suposição que você faria],
todos os pontos em que:
  - Ambiguidade: o texto admite mais de uma interpretação;
  - Omissão: falta informação para implementar ou testar (tipo, limite, ordem, erro, formato);
  - Contradição: duas partes do documento discordam;
  - Inconsistência de termo: o mesmo conceito aparece com nomes diferentes;
  - Não verificável: requisito sem critério de aceitação concreto.
Ao final, liste as 5 suposições mais arriscadas que você faria se tivesse de implementar agora.
Não invente problemas: se uma seção estiver clara, não a cite.
```

Para cada linha retornada: se for procedente, corrija o documento (a "suposição que você faria" indica exatamente o que precisa ficar escrito); se não for, registre a justificativa na seção 11. Repita até a LLM não apontar achados de severidade alta.

## 9. Erros frequentes (antes → depois)

| Erro | Antes | Depois |
|---|---|---|
| Limite sem operador | "O aluno pode pegar até 3 livros." | "SE o usuário ALUNO já tiver 3 empréstimos ativos (≥ 3), ENTÃO `emprestar` DEVE lançar `LimiteExcedidoErro`." |
| Fronteira indefinida | "Há multa se devolver atrasado." | "dias_atraso = max(0, (data_devolução − data_prevista) em dias). Devolver **na** data prevista não gera multa." |
| Arredondamento implícito | "Calcular a multa." | "Valor `Decimal` quantizado em 2 casas (ROUND_HALF_UP), ex.: `Decimal("6.00")`." |
| Ordenação sem desempate | "Listar os atrasados." | "Ordenar por `data_prevista_devolucao` crescente; empate por `id` crescente." |
| Erro sem nome | "Mostrar erro se o livro não existir." | "lançar `NaoEncontradoErro`" (definido na seção 6.2). |
| Efeito colateral oculto | "Registrar a devolução." | Passos: 1) definir `data_devolucao`; 2) calcular multa; 3) somar ao `saldo_devedor`; 4) incrementar `exemplares_disponiveis`. |
| Normalização implícita | "Informar o ISBN." | "C-01: toda operação que recebe ISBN DEVE remover hífens e espaços antes de validar e buscar." |
| Escopo aberto | (nada sobre persistência) | "Fora do escopo: persistência em arquivo/banco, interface com usuário, `print`/`input`." |
| Interpretação "tentadora" não negada | "Busca sem diferenciar maiúsculas." | "… e NÃO DEVE ignorar acentos." |
