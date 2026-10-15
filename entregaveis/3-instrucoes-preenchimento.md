# Instruções de preenchimento do documento de requisitos ERL

Estas instruções acompanham o modelo `2-modelo-documento-requisitos.md`. O exemplo completo de um documento preenchido está em `experimento/especificacoes/biblioteca-modelo-erl.md`.

## 1. Princípio geral

O leitor do documento é uma LLM que **não pode fazer perguntas**. Toda decisão que você deixar aberta, ela vai tomar sozinha, e não necessariamente como você esperava. Antes de cada frase, pergunte-se: *dá para implementar isso de duas maneiras diferentes?* Se der, a frase ainda não está pronta.

## 2. Regras de redação

1. **Uma ideia por frase.** Um requisito ou regra contém um único "deve".
2. **Números, não adjetivos.** Escreva "14 dias corridos", não "duas semanas"; "menos de 5 segundos", não "rápido".
3. **Um termo por conceito.** Use exatamente os termos do glossário. Não alterne entre "usuário", "leitor" e "cliente".
4. **Nomes de código entre crases e idênticos ao contrato:** `emprestar`, `DadosInvalidos`, `"aluno"`.
5. **Escreva as permissões.** Se algo é permitido e alguém poderia achar que não é, diga explicitamente (ex.: RN-08).
6. **Escreva as fronteiras.** Diga o que acontece exatamente no limite: "na própria data prevista ainda não há atraso".
7. **IDs estáveis.** Depois de criado, um ID (`RF-03`, `RN-05`, `CA-RF03-07`) nunca muda de significado nem é reaproveitado.

### 2.1 Palavras proibidas

Estas palavras indicam requisito vago. Se aparecerem fora do glossário, reescreva a frase com valores concretos.

| Evite | Por quê | Troque por |
|---|---|---|
| rápido, eficiente, performático | não é mensurável | limite numérico de tempo com carga definida |
| amigável, intuitivo, adequado, apropriado | é opinião | comportamento observável |
| etc., entre outros, e assim por diante | lista aberta | lista fechada |
| normalmente, geralmente, se possível | exceção não definida | regra completa com a exceção escrita |
| deve ser possível | não diz quem faz nem como | "o sistema deve..." mais a operação |
| e/ou | ambíguo | "e" ou "ou", ou duas regras |
| suporte a, gerenciar, tratar | verbo genérico | operação específica do contrato |

## 3. Como preencher cada seção

### Cabeçalho

Preencha versão, data e autores. A **Situação** só passa para "Pronto para geração" depois da validação (checklist da Seção 7 deste guia).

### Seção 0 — Instruções para quem implementa

Não altere. Ela diz à LLM como ler o restante do documento. Só ajuste o item 6 se a Seção 12 mudar de número.

### Seção 1 — Visão geral

- **1.1 Objetivo:** de uma a três frases.
- **1.2 Escopo:** cada item corresponde a um ou mais RF.
- **1.3 Fora de escopo:** é tão importante quanto o escopo. Liste o que uma LLM costuma acrescentar sozinha: persistência em arquivo ou banco, menus de linha de comando, autenticação, logs, edição e remoção de registros. Se o sistema não lê o relógio, diga.
- **1.4 Atores:** quem chama o sistema e quem é afetado por ele.

### Seção 2 — Glossário

Defina todo termo do domínio que tenha mais de uma leitura possível. Um bom teste: se dois membros do grupo explicam o termo de jeitos diferentes, ele entra no glossário. Defina termos derivados por fórmula sempre que der ("Exemplares disponíveis = quantidade − empréstimos ativos").

### Seção 3 — Restrições técnicas

Fixe a linguagem e a versão, as dependências permitidas, o nome e a quantidade de arquivos, o ponto de entrada e a representação de valores especiais:

- **Dinheiro:** inteiro em centavos, para evitar erros de arredondamento de `float`.
- **Datas:** o tipo exato (`datetime.date`) e o fato de chegarem como parâmetro. Um sistema que lê o relógio não pode ser testado de forma repetível.
- **Saída:** se o código pode ou não imprimir.

### Seção 4 — Modelo de dados

Uma tabela por entidade. Toda restrição tem que ser verificável por código: "exatamente 13 caracteres, todos dígitos de 0 a 9" em vez de "ISBN válido". Informe o valor inicial dos campos que o sistema gera.

### Seção 5 — Interface pública (contrato)

É a seção que mais influencia a conformidade, porque é por ela que os testes enxergam o software.

- **5.1 Exceções:** uma por tipo de erro de negócio, todas herdando de uma base comum. O nome da exceção é parte do contrato.
- **5.2 Operações:** a assinatura completa, com tipos, e o significado do retorno. Se o retorno é uma estrutura (`dict`, objeto), liste as chaves ou atributos **exatos** no RF correspondente.

Se o sistema for uma API HTTP, troque a tabela de operações por método, rota, corpo de requisição, corpo de resposta e códigos de status. O princípio é o mesmo: nada que o teste vá usar pode ficar implícito.

### Seção 6 — Regras de negócio

Uma regra por linha, com valores. Indique a fórmula quando houver cálculo (`data_prevista = data_emprestimo + prazo`). Inclua as regras gerais que valem para tudo (atomicidade: "uma operação que falha não altera o estado"; geração de ids: "começam em 1 e só contam operações bem-sucedidas").

### Seção 7 — Requisitos funcionais

Um RF para cada operação da Seção 5.2. Para cada RF:

1. **Descrição** no formato "O sistema deve...".
2. **Prioridade:** Essencial, Importante ou Desejável.
3. **Regras aplicadas:** os ids das RN que o RF usa. É isso que alimenta a matriz de rastreabilidade.
4. **Efeito:** tudo o que muda no estado do sistema.
5. **Ordem de validação:** uma tabela numerada com cada condição de erro e a exceção correspondente. A primeira condição verdadeira define o erro. É o que resolve o caso em que dois erros se aplicam ao mesmo tempo.
6. **Critérios de aceitação** (ver a Seção 4 deste guia).

### Seção 8 — Requisitos não funcionais

Só entra RNF que tenha métrica e forma de verificação. Cada RNF ganha pelo menos um CA, testado como os demais.

### Seção 9 — Cenário integrado

Uma sequência de 8 a 12 chamadas que atravessa vários RF e passa pelas interações mais delicadas entre as regras (ex.: reserva + bloqueio + pagamento). Mostre o resultado esperado em cada passo.

### Seção 10 — Matriz de rastreabilidade

Uma linha por RF, com as RN, a operação e o intervalo de CAs. Toda RN deve aparecer em pelo menos uma linha. Se não aparece, ou a regra não é necessária ou falta um RF ou CA.

### Seção 11 — Questões em aberto

Use durante a análise. Antes da geração, essa tabela precisa estar vazia (só a linha "Nenhuma").

### Seção 12 — Formato de entrega

Diga exatamente o que a LLM deve devolver (ex.: um único bloco de código com o arquivo completo). Isso permite extrair o código automaticamente, sem edição manual.

## 4. Como escrever critérios de aceitação

Formato: **CA-RFxx-yy — Dado** *estado inicial concreto*, **quando** *chamada concreta*, **então** *resultado exato*.

Para cada RF, escreva um CA para cada um destes casos:

| Caso | Exemplo |
|---|---|
| Caminho feliz | empréstimo de aluno retorna id 1 e data prevista em 14 dias |
| Cada linha da ordem de validação | usuário inexistente → `UsuarioNaoEncontrado` |
| Cada fronteira numérica, dos dois lados | devolução na data prevista (0) e um dia depois (100) |
| Cada teto ou limite | 45 dias de atraso → 2000, e não 4500 |
| Conflito entre erros | bloqueado **e** no limite → `UsuarioBloqueado` |
| Atomicidade | depois do erro, o estado consultado é o mesmo de antes |

Regras para os CAs:

- Use **valores literais** (`date(2026, 3, 2)`, `300`), nunca "uma data qualquer".
- Diga o estado inicial completo ou remeta a uma situação inicial comum escrita no início da seção.
- Refaça as contas à mão. Um CA errado ensina a regra errada à LLM e reprova a implementação correta.
- Cada CA deve poder virar um teste automático usando só a Seção 5.

### Exemplo: de requisito vago a requisito pronto

> **Vago:** "O usuário que atrasar a devolução paga multa, com um limite."
>
> **Pronto:**
> - RN-03: Multa por atraso: 100 centavos por dia de atraso, calculada na devolução e somada ao saldo devedor do usuário.
> - RN-04: A multa de um único empréstimo é limitada a 2000 centavos.
> - Glossário — Dias de atraso: `(data_devolucao - data_prevista).days` quando positivo; caso contrário, 0.
> - CA-RF04-01: devolução em `date(2026, 3, 16)` (a data prevista) retorna `0`.
> - CA-RF04-02: devolução em `date(2026, 3, 19)` retorna `300`.
> - CA-RF04-03: devolução em `date(2026, 4, 30)` retorna `2000`.

## 5. Erros comuns

| Erro | Consequência na geração | Correção |
|---|---|---|
| Não definir a ordem dos erros | Exceção certa por acaso, errada em metade dos casos | Tabela de ordem de validação |
| Retorno descrito como "os dados do empréstimo" | Chaves com outros nomes; objeto em vez de `dict` | Listar as chaves exatas |
| Prazo em "semanas" ou "um mês" | 30 dias? Mês de calendário? | Dias corridos |
| Regra só no CA, não na RN | A LLM generaliza o exemplo de outra forma | Regra na Seção 6 e exemplo no CA |
| Escopo negativo vazio | A LLM acrescenta menus, arquivos, `print` | Preencher a Seção 1.3 e a RT de saída |
| Questão em aberto não resolvida | A LLM escolhe sozinha | Resolver com o cliente antes da geração |

## 6. Como usar o documento com a LLM

1. Envie o documento **inteiro** num prompt curto e fixo, que não acrescente requisitos. O que usamos está em `experimento/prompt.txt`:
   > Implemente o software descrito no documento de requisitos a seguir. Responda apenas com um único bloco de código Python contendo o arquivo biblioteca.py completo.
2. Não corrija o código à mão antes de medir. A medição avalia o documento, não quem conserta o código.
3. Rode o oráculo e calcule a conformidade (fase F7 do processo).

## 7. Checklist de validação (Definição de Pronto)

O revisor marca cada item. O documento só passa para "Pronto para geração" com todos marcados.

**Estrutura**
- [ ] Todas as seções de 0 a 12 estão presentes e sem `{{ }}` nem linhas de orientação `>`.
- [ ] A Seção 11 (questões em aberto) contém só "Nenhuma".

**Clareza**
- [ ] Nenhuma palavra da lista de palavras proibidas fora do glossário.
- [ ] Todo termo de domínio usado nos RF está no glossário, e cada conceito tem um único nome.
- [ ] Todo valor numérico tem unidade (dias corridos, centavos, segundos).

**Contrato**
- [ ] Toda operação da Seção 5.2 tem assinatura com tipos e um RF correspondente.
- [ ] Toda exceção da Seção 5.1 aparece em pelo menos uma tabela de ordem de validação.
- [ ] Todo retorno estruturado tem as chaves ou atributos listados.

**Regras e requisitos**
- [ ] Toda RN é atômica e aparece na matriz de rastreabilidade.
- [ ] Todo RF tem efeito, retorno e ordem de validação.
- [ ] Todo RNF tem métrica, forma de verificação e pelo menos um CA.

**Critérios de aceitação**
- [ ] Todo RF tem CA de caminho feliz e um CA para cada linha da ordem de validação.
- [ ] Toda fronteira numérica tem CA dos dois lados.
- [ ] Todo CA usa valores literais e foi recalculado à mão pelo revisor.
- [ ] Leitura adversarial feita: para nenhum RF o revisor encontrou duas implementações diferentes que satisfaçam o texto.
