# Instruções de preenchimento do documento de requisitos ERL (v2 — microsserviços)

Estas instruções acompanham o modelo `2-modelo-documento-requisitos.md`. O exemplo completo de um documento preenchido é o do módulo de logística, em `experimento/especificacoes/ERS-LOG-001.md`.

## 1. Princípio geral

O leitor do documento é uma LLM que **não pode fazer perguntas**. Toda decisão que você deixar aberta, ela vai tomar sozinha, e não necessariamente como você esperava. Antes de cada frase, pergunte-se: *dá para implementar isso de duas maneiras diferentes?* Se der, a frase ainda não está pronta.

Em microsserviços há um risco a mais: cada serviço pode estar "certo" isoladamente e o sistema estar errado, porque dois serviços entenderam o mesmo contrato de jeitos diferentes. Por isso, tudo o que cruza a fronteira de um serviço (rotas, campos, eventos) é escrito **uma vez só**, num lugar só, e os requisitos apenas fazem referência a ele.

## 2. Regras de redação

1. **Uma ideia por frase.** Um requisito ou regra contém um único "deve".
2. **Números e fórmulas, não adjetivos.** Escreva "teto(C × L × A ÷ 6) gramas", não "o peso volumétrico".
3. **Um termo por conceito.** Use exatamente os termos do glossário. Não alterne entre "envio", "remessa" e "entrega".
4. **Nomes de código entre crases e idênticos ao contrato:** `POST /envios`, `destinatario_email`, `"FALHA_ENTREGA"`.
5. **Escreva as permissões.** Se algo é permitido e alguém poderia achar que não é, diga explicitamente (ex.: "a escolha não precisa ser a mais barata").
6. **Escreva as fronteiras.** Diga o que acontece exatamente no limite: "a cotação vale até `valida_ate`, inclusive".
7. **IDs estáveis.** Depois de criado, um ID (`RF-E04`, `RN-12`, `CA-E04-07`) nunca muda de significado nem é reaproveitado. Prefixe os RFs pelo serviço (`RF-T`, `RF-C`, `RF-E`...).

### 2.1 Palavras proibidas

Estas palavras indicam requisito vago. Se aparecerem fora do glossário, reescreva a frase com valores concretos.

| Evite | Por quê | Troque por |
|---|---|---|
| rápido, eficiente, escalável | não é mensurável | limite numérico com carga definida ("100 cotações em menos de 15 s") |
| amigável, adequado, apropriado | é opinião | comportamento observável |
| etc., entre outros | lista aberta | lista fechada |
| normalmente, geralmente, se possível | exceção não definida | regra completa com a exceção escrita |
| notificar, integrar, sincronizar (sem complemento) | não diz como nem com que dados | rota ou evento exato, com os campos |
| e/ou | ambíguo | "e" ou "ou", ou duas regras |
| tratar, gerenciar, processar | verbo genérico | operação específica do contrato |

## 3. Como preencher cada seção

### Cabeçalho

Preencha versão, data e autores. A **Situação** só passa para "Pronto para geração" depois do checklist da Seção 7 deste guia.

### Seção 0 — Instruções para quem implementa

Não altere. Ela diz à LLM como ler o restante do documento.

### Seção 1 — Visão geral

- **1.1 Objetivo:** de uma a três frases, com o problema de negócio.
- **1.2 Escopo:** cada item corresponde a um ou mais RF.
- **1.3 Fora de escopo:** tão importante quanto o escopo. Em microsserviços, liste o que a LLM costuma acrescentar sozinha: banco de dados, filas reais (Kafka, RabbitMQ), retentativas, *circuit breaker*, autenticação, API gateway, logs estruturados. Se o sistema não lê o relógio, diga.
- **1.4 Atores:** quem chama o sistema e quais serviços usa. Sistemas externos (ex.: a transportadora) também são atores.

### Seção 2 — Glossário

Defina todo termo com mais de uma leitura possível, e dê fórmula aos termos calculados ("Peso taxado = maior entre o peso real e o peso cubado"). Um bom teste: se dois membros do grupo explicam o termo de jeitos diferentes, ele entra no glossário.

### Seção 3 — Arquitetura

- **3.1 Serviços:** uma linha por serviço, com a responsabilidade numa frase, o **caminho exato do arquivo** e a **variável de ambiente** com a URL base. Inclua a infraestrutura fornecida pronta (ex.: barramento) marcada como "não deve ser implementado". Escreva a regra de isolamento: um serviço só conhece outro pelo contrato.
- **3.2 Comunicação:** um diagrama e uma tabela com **toda** chamada entre serviços. Para cada uma, diga se é HTTP síncrono (o chamador precisa da resposta) ou evento (um fato que já aconteceu), e quando acontece. Uma chamada que não está na tabela não deve existir no código.

### Seção 4 — Restrições técnicas

Fixe a linguagem, as dependências permitidas (lista fechada), a estrutura de arquivos, como cada serviço é executado, a biblioteca das chamadas entre serviços e o tempo-limite, a representação de dinheiro e datas, a regra sobre o relógio e a saída padrão.

### Seção 5 — Convenções gerais da API

É a seção que mais economiza texto e evita inconsistência: o que vale para todas as rotas fica escrito **aqui**, uma vez.

- **5.2 Erros:** o formato do corpo de erro e a tabela de códigos HTTP. Diga explicitamente que os erros gerados automaticamente pelo framework (ex.: validação do FastAPI) devem ser convertidos para esse formato; sem isso, a LLM deixa o padrão do framework.
- **5.3 Ordem geral de validação:** em geral, a validação do corpo vem antes de tudo. Escreva isso aqui e use as tabelas dos RFs só para o restante.
- **5.4 Atomicidade:** erro não altera estado, não consome identificador e não publica evento.
- **5.5 Identificadores:** formato exato e regra de sequência. Identificadores determinísticos tornam os CAs verificáveis.
- **5.6 Reinício de estado:** uma rota para os testes zerarem o estado do serviço.

### Seção 6 — Eventos

- **6.1 Envelope:** os campos comuns a todo evento.
- **6.2 Barramento:** o contrato de publicação e de entrega (ordem, sincronismo, quem recebe o quê).
- **6.3 Catálogo:** uma linha por tipo de evento, com o publicador, o gatilho e a **lista exata de campos de `dados`**. Prefira eventos autocontidos: se o consumidor precisa de um dado, ele vai no evento.
- **6.4 Consumo:** o que o consumidor faz com envelope inválido, evento repetido (idempotência) e tipo desconhecido.

### Seção 7 — Regras de negócio

Uma regra por linha, com valores e fórmulas. Indique o arredondamento ("sempre para cima, ao centavo"). Tabelas de referência (faixas de CEP, mensagens) entram aqui ou no serviço que as usa. Regras de estado (transições permitidas) são listadas por completo: o que não está listado é inválido.

### Seção 8 — Serviços e requisitos funcionais

- **8.0 Cenário base:** defina uma vez os dados de partida dos CAs (cadastros e requisições de referência com nome, como S0, Q-A e E-1). Os CAs ficam curtos e todos partem do mesmo lugar.
- **Um bloco por serviço,** com o modelo de dados, a tabela de rotas e os RFs.
- **Um RF por rota** (ou por consumo de evento), com descrição, corpo, dependências (chamadas HTTP a outros serviços), efeito (inclusive eventos publicados), resposta, **tabela de ordem de validação** e CAs.

### Seção 9 — Requisitos não funcionais

Só entra RNF com métrica e forma de verificação. Em microsserviços, os típicos são as dependências permitidas, o formato de erro, a configuração por ambiente, o desempenho com carga definida e o silêncio na saída padrão.

### Seção 10 — Cenário integrado

Um fluxo de ponta a ponta que passa por **todos** os serviços e pelos eventos. É o único CA que verifica o sistema como um todo, e ele pega contratos que só "batem" no papel.

### Seção 11 — Matriz de rastreabilidade

Uma linha por RF, com as RN, a rota e o intervalo de CAs. Toda RN deve aparecer em pelo menos uma linha.

### Seção 12 — Questões em aberto

Use durante a análise. Antes da geração, essa tabela precisa estar vazia (só a linha "Nenhuma").

### Seção 13 — Formato de entrega

Diga exatamente como a LLM deve devolver vários arquivos (ex.: um bloco por arquivo, com o caminho na primeira linha). Isso permite extrair o código automaticamente, sem edição manual.

## 4. Como escrever critérios de aceitação

Formato: **CA-Xnn-mm — Dado** *estado inicial concreto*, **quando** *requisição concreta*, **então** *status e corpo exatos*.

Para cada RF, escreva um CA para cada um destes casos:

| Caso | Exemplo |
|---|---|
| Caminho feliz, com o corpo completo da resposta | criar envio com E-1 → 201 com todos os campos |
| Cada linha da ordem de validação | cotação inexistente → 422 `COTACAO_NAO_ENCONTRADA` |
| Conflito entre erros | pedido duplicado **e** cotação inexistente → 409 `ENVIO_DUPLICADO` |
| Cada fronteira numérica, dos dois lados | 10000 g entra (peso máximo); 10001 g não entra |
| Cada arredondamento | 1001 × 1,10 = 1101,1 → 1102 |
| Calendário | cotação de sexta + 1 dia útil = segunda |
| Efeito em outro serviço | depois de criar o envio, o barramento recebeu `envio.criado` com estes dados |
| Idempotência | o mesmo evento duas vezes gera uma única notificação |
| Atomicidade | depois do erro, o estado consultado é o mesmo de antes e o identificador não avançou |

Regras para os CAs:

- Use **valores literais** (`"2026-03-02T10:00:00"`, `1870`), nunca "uma data qualquer".
- Parta do cenário base (Seção 8.0) e diga só o que muda.
- Refaça as contas à mão: um CA errado ensina a regra errada à LLM e reprova a implementação correta.
- Cada CA deve poder virar um teste automático que usa só rotas HTTP e eventos.

### Exemplo: de requisito vago a requisito pronto

> **Vago:** "O frete considera o peso e o tamanho do pacote, com acréscimo para outros estados."
>
> **Pronto:**
> - Glossário — Peso cubado: `teto(C × L × A ÷ 6)` gramas. Peso taxado: o maior entre o peso real e o peso cubado.
> - RN-03: Preço base = `tarifa_base_centavos + tarifa_kg_centavos × teto(peso_taxado_g ÷ 1000)`.
> - RN-04: Se interestadual, preço = `teto(preço_base × (100 + adicional_interestadual_pct) ÷ 100)`; o arredondamento é sempre para cima, ao centavo.
> - CA-C01-03: pacote de 100 g e 10 × 10 × 11 cm → `"peso_taxado_g": 184`.
> - CA-C01-04: preço base 1001 com adicional de 10% → `"preco_centavos": 1102`.

## 5. Erros comuns

| Erro | Consequência na geração | Correção |
|---|---|---|
| Não definir a ordem dos erros | Erro certo por acaso, errado em metade dos casos | Ordem geral (5.3) + tabela de validação por RF |
| Campos do evento descritos de forma solta ("os dados do envio") | Publicador e consumidor usam nomes diferentes | Lista exata de campos no catálogo (6.3) |
| Não falar do formato de erro do framework | A LLM deixa o 422 padrão do FastAPI | Seção 5.2 explícita |
| URL de outro serviço fixa no código | Funciona na máquina de quem gerou, falha nos testes | Variável de ambiente lida na hora da chamada |
| Usar a data do computador | Testes não reproduzíveis | Datas sempre na requisição (RT-06) |
| Prazo em "dias" sem dizer quais | Corridos ou úteis? O primeiro dia conta? | Fórmula e exemplo na RN |
| Escopo negativo vazio | A LLM acrescenta banco, fila real, autenticação | Preencher a Seção 1.3 |
| Questão em aberto não resolvida | A LLM escolhe sozinha | Resolver com o cliente antes da geração |

## 6. Como usar o documento com a LLM

1. Envie o documento **inteiro** num prompt curto e fixo, que não acrescente requisitos. O que usamos está em `experimento/prompt.txt`:
   > Implemente o software descrito no documento de requisitos a seguir. Responda com um bloco de código Python por arquivo, em que a primeira linha de cada bloco é um comentário com o caminho do arquivo.
2. Não corrija o código à mão antes de medir. A medição avalia o documento, não quem conserta o código.
3. Rode o oráculo e calcule a conformidade (fase F7 do processo).

## 7. Checklist de validação (Definição de Pronto)

O revisor marca cada item. O documento só passa para "Pronto para geração" com todos marcados.

**Estrutura**
- [ ] Todas as seções de 0 a 13 estão presentes e sem `{{ }}` nem linhas de orientação `>`.
- [ ] A Seção 12 (questões em aberto) contém só "Nenhuma".

**Clareza**
- [ ] Nenhuma palavra da lista de palavras proibidas fora do glossário.
- [ ] Todo termo de domínio usado nos RF está no glossário, e cada conceito tem um único nome.
- [ ] Todo valor numérico tem unidade, e todo cálculo tem fórmula e regra de arredondamento.

**Arquitetura e contratos**
- [ ] Todo serviço tem arquivo, variável de ambiente e responsabilidade.
- [ ] Toda chamada entre serviços aparece na tabela de comunicação (3.2), e nenhuma chamada fora dela é necessária para cumprir os RFs.
- [ ] Todo evento tem publicador, gatilho e lista exata de campos; todo consumidor tem regra de idempotência e de tipo desconhecido.
- [ ] O formato de erro, a ordem geral de validação, a atomicidade e os identificadores estão na Seção 5.
- [ ] O sistema é testável: URLs por ambiente, datas na requisição, rota de reset, identificadores determinísticos.

**Regras e requisitos**
- [ ] Toda RN é atômica e aparece na matriz de rastreabilidade.
- [ ] Todo RF tem efeito, resposta e tabela de ordem de validação.
- [ ] Todo RNF tem métrica, forma de verificação e pelo menos um CA.

**Critérios de aceitação**
- [ ] Todo RF tem CA de caminho feliz e um CA para cada linha da ordem de validação.
- [ ] Toda fronteira numérica e todo arredondamento têm CA.
- [ ] Todo evento publicado e todo efeito em outro serviço têm CA.
- [ ] Existe um cenário integrado que passa por todos os serviços.
- [ ] Todo CA usa valores literais e foi recalculado à mão pelo revisor.
- [ ] Leitura adversarial feita: para nenhum RF o revisor encontrou duas implementações diferentes que satisfaçam o texto.
