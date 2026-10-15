# Especificação de Requisitos de Software — Módulo de Empréstimos da Biblioteca Escolar

## 0. Metadados

| Campo | Valor |
|---|---|
| Identificador | ERS-BIB-001 |
| Versão | 1.0 |
| Status | Em revisão (aguardando a revisão adversarial por LLM — fase F4) |
| Autores | Equipe do projeto (Analista de Requisitos) |
| Revisores | Equipe do projeto (Revisor) |
| Aprovado por (PO) | Coordenação da Biblioteca |
| Data | 2026-10-15 |
| Plataforma-alvo | Python ≥ 3.10 |

## 1. Diretivas para a LLM

Você é a pessoa desenvolvedora responsável por implementar o software descrito neste documento. Siga estas diretivas:

1. Implemente **todos** os requisitos deste documento e **nada além** deles.
2. Respeite **exatamente** o Contrato de Interface (seção 6): nomes de arquivos, classes, funções, parâmetros, tipos de retorno e erros. Não renomeie, não traduza e não acrescente parâmetros obrigatórios.
3. Os Critérios de Aceitação (CA) e de Verificação (CV) são casos de teste: o código DEVE satisfazer cada um deles literalmente, com os valores indicados.
4. Respeite as Restrições (seção 4); não use dependências não autorizadas.
5. As palavras **DEVE**, **NÃO DEVE** e **PODE** têm sentido normativo (obrigatório, proibido, opcional).
6. Use os termos exatamente como definidos no Glossário (seção 3).
7. Se algo não estiver especificado, escolha a alternativa mais simples que não viole nenhum requisito e registre-a no código com um comentário iniciado por `# SUPOSIÇÃO:`.
8. Em caso de conflito aparente entre seções, a precedência é: Restrições (4) > Contrato de Interface (6) > Critérios de Aceitação/Verificação (8 e 9) > Regras de Negócio (7) > textos descritivos.
9. Formato da resposta: um único bloco de código Python contendo o arquivo completo `biblioteca.py`, sem explicações antes ou depois.

## 2. Visão geral e contexto

### 2.1 Propósito

Módulo Python que gerencia o acervo, os usuários, os empréstimos, as devoluções com multa, as renovações e as reservas de livros de uma biblioteca escolar.

### 2.2 Contexto e problema

A biblioteca controla empréstimos em planilhas, o que causa erros no cálculo de multas, empréstimos acima do limite e conflitos de reserva. O módulo centraliza essas regras em uma API em memória que será usada por uma aplicação cliente (fora do escopo).

### 2.3 Necessidades de origem

| ID | Necessidade | Origem |
|---|---|---|
| N-01 | Cadastrar livros pelo ISBN, aceitando o ISBN digitado com hífens ou espaços. | Bibliotecária |
| N-02 | Cadastrar alunos e professores, que têm limites e prazos diferentes. | Coordenação da Biblioteca |
| N-03 | Encontrar livros por parte do título ou do autor. | Bibliotecária |
| N-04 | Registrar empréstimos respeitando limite por usuário e bloqueando quem está em atraso ou devendo multa. | Coordenação da Biblioteca |
| N-05 | Registrar devoluções calculando a multa por atraso, com teto. | Coordenação da Biblioteca |
| N-06 | Permitir renovar empréstimos, no máximo duas vezes, desde que ninguém esteja esperando o livro. | Bibliotecária |
| N-07 | Permitir reservar livros sem exemplar disponível, atendendo por ordem de chegada. | Bibliotecária |
| N-08 | Registrar o pagamento de multas, inclusive parcial. | Tesouraria da escola |
| N-09 | Listar empréstimos em atraso para cobrança. | Bibliotecária |

### 2.4 Escopo — incluído

- Cadastro e consulta de livros e usuários.
- Pesquisa de livros por termo.
- Empréstimo, devolução (com multa), renovação.
- Reserva e fila de reservas por ISBN.
- Pagamento de multas e listagem de empréstimos atrasados.

### 2.5 Escopo — fora do escopo

- Persistência (arquivos, banco de dados): todo o estado fica em memória, em uma instância de `Biblioteca`.
- Interface com usuário, menus, `print`, `input`, logs.
- Edição ou remoção de livros e usuários; cancelamento de reservas.
- Notificações (e-mail, SMS), autenticação, múltiplas bibliotecas.

### 2.6 Atores

| Ator | Descrição | Interação com o sistema |
|---|---|---|
| Aplicação cliente | Software usado pela bibliotecária (fora do escopo). | Instancia `Biblioteca` e chama suas operações. |
| Bibliotecária | Operadora da aplicação cliente. | Indireta. |

## 3. Glossário

| Termo | Definição |
|---|---|
| **ISBN** | Identificador de 13 dígitos de um livro. Forma **normalizada**: o texto recebido sem hífens (`-`) e sem espaços (` `). |
| **Livro** | Título do acervo identificado por um ISBN. Um livro possui um ou mais exemplares. |
| **Exemplar** | Cópia física de um livro. Os exemplares de um mesmo livro são indistinguíveis. |
| **Usuário** | Pessoa cadastrada que pode emprestar e reservar livros. Identificada por `id` (texto). |
| **Tipo de usuário** | `ALUNO` ou `PROFESSOR`. Determina limite e prazo (RN-02). |
| **Empréstimo** | Registro de que um usuário retirou um exemplar de um livro em uma data. Identificado por `id` inteiro. |
| **Empréstimo ativo** | Empréstimo cujo `data_devolucao` é `None`. |
| **Data prevista de devolução** | Data até a qual (inclusive) o exemplar pode ser devolvido sem multa. |
| **Atrasado (na data D)** | Um empréstimo ativo está atrasado na data D se e somente se D > data prevista de devolução. |
| **Dias de atraso** | max(0, (data da devolução − data prevista de devolução) em dias corridos). |
| **Multa** | Valor em reais cobrado na devolução atrasada (RN-05). |
| **Saldo devedor** | Soma das multas de um usuário menos os pagamentos realizados. |
| **Pendência (na data D)** | Situação de um usuário com saldo devedor > 0,00 **ou** com ao menos um empréstimo atrasado na data D (RN-06). |
| **Disponível** | Um livro está disponível quando `exemplares_disponiveis` > 0. |
| **Reserva** | Pedido de um usuário para ser atendido quando um exemplar de um livro estiver disponível. |
| **Fila de reservas** | Sequência, por ordem de chegada, dos `id`s dos usuários que reservaram um ISBN. |
| **Renovação** | Extensão da data prevista de devolução de um empréstimo ativo (RN-07). |

## 4. Restrições técnicas e de ambiente

| ID | Restrição |
|---|---|
| R-01 | Linguagem: Python 3.10 ou superior. |
| R-02 | Dependências: somente a biblioteca padrão do Python. |
| R-03 | Entrega: um único arquivo `biblioteca.py` contendo todas as classes desta especificação. |
| R-04 | O módulo NÃO DEVE ler nem gravar arquivos, acessar rede, chamar `print` ou `input`. |
| R-05 | O módulo NÃO DEVE consultar o relógio do sistema (`date.today()`, `datetime.now()`, `datetime.today()`, `datetime.utcnow()`, `time.time()`). Toda data é recebida por parâmetro. |
| R-06 | Importar o módulo NÃO DEVE produzir nenhuma saída em `stdout` ou `stderr`. |

## 5. Modelo de dados

As três entidades DEVEM ser declaradas com `@dataclass` (módulo `dataclasses`), no arquivo `biblioteca.py`, com os campos **nesta ordem e com estes nomes**.

### 5.1 `Livro`

| Campo | Tipo | Restrições | Valor inicial |
|---|---|---|---|
| `isbn` | `str` | ISBN normalizado e válido (RN-01) | — |
| `titulo` | `str` | não vazio, sem espaços nas extremidades | — |
| `autor` | `str` | não vazio, sem espaços nas extremidades | — |
| `exemplares_total` | `int` | ≥ 1 | valor de `exemplares` no cadastro |
| `exemplares_disponiveis` | `int` | 0 ≤ valor ≤ `exemplares_total` | igual a `exemplares_total` |

### 5.2 `Usuario`

| Campo | Tipo | Restrições | Valor inicial |
|---|---|---|---|
| `id` | `str` | não vazio, sem espaços nas extremidades, único | — |
| `nome` | `str` | não vazio, sem espaços nas extremidades | — |
| `tipo` | `str` | `"ALUNO"` ou `"PROFESSOR"` | — |
| `saldo_devedor` | `Decimal` | ≥ 0, duas casas decimais | `Decimal("0.00")` |

### 5.3 `Emprestimo`

| Campo | Tipo | Restrições | Valor inicial |
|---|---|---|---|
| `id` | `int` | sequencial a partir de 1 (C-07) | — |
| `isbn` | `str` | ISBN normalizado | — |
| `usuario_id` | `str` | `id` normalizado do usuário | — |
| `data_emprestimo` | `date` | — | data informada em `emprestar` |
| `data_prevista_devolucao` | `date` | — | `data_emprestimo` + prazo (RN-02) |
| `data_devolucao` | `date \| None` | `None` enquanto ativo | `None` |
| `renovacoes` | `int` | 0 ≤ valor ≤ 2 | `0` |
| `multa` | `Decimal` | duas casas decimais | `Decimal("0.00")` |

## 6. Contrato de interface

### 6.1 Arquivos e módulos

| Arquivo | Conteúdo |
|---|---|
| `biblioteca.py` | Classes de dados (seção 5), classes de erro (6.2) e a classe `Biblioteca` (6.3). |

### 6.2 Erros / exceções

| Nome | Herda de | Quando é usado (resumo) |
|---|---|---|
| `BibliotecaErro` | `Exception` | Base de todos os erros do módulo. |
| `DadosInvalidosErro` | `BibliotecaErro` | Dado de entrada inválido. |
| `ISBNInvalidoErro` | `DadosInvalidosErro` | ISBN fora da regra RN-01. |
| `DuplicadoErro` | `BibliotecaErro` | Cadastro ou reserva repetidos. |
| `NaoEncontradoErro` | `BibliotecaErro` | Livro, usuário ou empréstimo inexistente. |
| `IndisponivelErro` | `BibliotecaErro` | Sem exemplar disponível para o usuário. |
| `LimiteExcedidoErro` | `BibliotecaErro` | Usuário atingiu o limite de empréstimos ativos. |
| `PendenciaErro` | `BibliotecaErro` | Usuário com pendência (RN-06). |
| `RenovacaoNegadaErro` | `BibliotecaErro` | Renovação não permitida (RN-07, RN-08). |
| `OperacaoInvalidaErro` | `BibliotecaErro` | Operação incompatível com o estado atual. |

### 6.3 Operações

Classe `Biblioteca`, com construtor sem parâmetros (`Biblioteca()`), que cria uma biblioteca vazia. Tipos: `date` = `datetime.date`; `Decimal` = `decimal.Decimal`.

| Operação (assinatura exata) | Retorno | Requisito |
|---|---|---|
| `cadastrar_livro(self, isbn: str, titulo: str, autor: str, exemplares: int)` | `Livro` | RF-01 |
| `cadastrar_usuario(self, id: str, nome: str, tipo: str)` | `Usuario` | RF-02 |
| `buscar_livro(self, isbn: str)` | `Livro` | RF-03 |
| `buscar_usuario(self, id: str)` | `Usuario` | RF-03 |
| `buscar_emprestimo(self, id: int)` | `Emprestimo` | RF-03 |
| `pesquisar_livros(self, termo: str)` | `list[Livro]` | RF-04 |
| `emprestar(self, isbn: str, usuario_id: str, data: date)` | `Emprestimo` | RF-05 |
| `devolver(self, emprestimo_id: int, data: date)` | `Decimal` (multa) | RF-06 |
| `renovar(self, emprestimo_id: int, data: date)` | `Emprestimo` | RF-07 |
| `reservar(self, isbn: str, usuario_id: str)` | `int` (posição na fila, a partir de 1) | RF-08 |
| `fila_reservas(self, isbn: str)` | `list[str]` (ids dos usuários) | RF-09 |
| `pagar_multa(self, usuario_id: str, valor: Decimal)` | `Decimal` (saldo restante) | RF-10 |
| `listar_atrasados(self, data: date)` | `list[Emprestimo]` | RF-11 |

### 6.4 Convenções gerais

| ID | Convenção |
|---|---|
| C-01 | **Normalização de ISBN:** toda operação que recebe `isbn` DEVE remover todas as ocorrências de hífen (`-`) e de espaço (` `) antes de qualquer validação ou busca. O ISBN armazenado e retornado é sempre o normalizado. |
| C-02 | **Normalização de textos:** `titulo`, `autor`, `nome`, `id` (de usuário) e `usuario_id` DEVEM ter os espaços das extremidades removidos (`str.strip()`) antes de validar, buscar e armazenar. `tipo` DEVE ser normalizado com `strip()` seguido de `upper()`. |
| C-03 | **Valores monetários:** todo valor monetário armazenado ou retornado (`multa`, `saldo_devedor`, retornos de `devolver` e `pagar_multa`) DEVE ser `Decimal` com exatamente duas casas decimais (ex.: `Decimal("0.00")`, `Decimal("6.00")`), quantizado com `ROUND_HALF_UP`. NÃO DEVE ser usado `float`. |
| C-04 | **Datas:** objetos `datetime.date`. Diferenças em dias corridos: `(d2 - d1).days`. Somar prazo: `d + timedelta(days=n)`. |
| C-05 | **Ordem de validação:** cada RF lista suas validações em ordem; elas DEVEM ser verificadas nessa ordem e a primeira condição de falha verdadeira determina o erro lançado. |
| C-06 | **Atomicidade:** se uma operação lançar erro, NENHUM dado DEVE ser alterado (livros, usuários, empréstimos, filas de reserva e contador de ids). |
| C-07 | **Ids de empréstimo:** inteiros sequenciais a partir de 1, na ordem de criação; nunca reutilizados. Uma chamada a `emprestar` que lança erro NÃO DEVE consumir id. |
| C-08 | **Objetos retornados:** as operações PODEM retornar o próprio objeto armazenado ou uma cópia dele. |

## 7. Regras de negócio

| ID | Regra | Fórmula / valores |
|---|---|---|
| RN-01 | **ISBN válido**: após a normalização (C-01), o ISBN tem exatamente 13 caracteres, todos dígitos de `0` a `9`, e o 13º dígito é o dígito verificador correto. | Pesos alternados 1 e 3 a partir do 1º dígito: `soma = d1×1 + d2×3 + d3×1 + … + d12×3`; `dv = (10 − (soma mod 10)) mod 10`; válido se `dv == d13`. Ex.: `9788533302273` é válido; `9788533302274` não. |
| RN-02 | **Parâmetros por tipo de usuário** | `ALUNO`: limite de **3** empréstimos ativos, prazo de **14** dias. `PROFESSOR`: limite de **5** empréstimos ativos, prazo de **28** dias. |
| RN-03 | **Empréstimo ativo** | `data_devolucao is None`. |
| RN-04 | **Atraso** | Empréstimo ativo atrasado na data D ⇔ `D > data_prevista_devolucao`. Na própria data prevista **não** há atraso. |
| RN-05 | **Multa** | `dias_atraso = max(0, (data_devolucao − data_prevista_devolucao).days)`; `multa = min(dias_atraso × Decimal("2.00"), Decimal("50.00"))`. |
| RN-06 | **Pendência** | Usuário tem pendência na data D ⇔ `saldo_devedor > Decimal("0.00")` **ou** possui ao menos um empréstimo atrasado na data D (RN-04). Pagamento parcial não remove a pendência. |
| RN-07 | **Renovação** | No máximo **2** renovações por empréstimo. Nova data prevista = **data da renovação** + prazo do tipo do usuário (RN-02). A renovação NÃO verifica o saldo devedor do usuário. |
| RN-08 | **Fila de reservas** | Uma fila por ISBN, por ordem de chegada (FIFO). Enquanto a fila de um ISBN não estiver vazia, somente o **primeiro** usuário da fila PODE emprestar esse ISBN; ao emprestar, ele é removido da fila. Enquanto a fila não estiver vazia, nenhum empréstimo desse ISBN PODE ser renovado. |

## 8. Requisitos funcionais

### RF-01 — Cadastrar livro

| Campo | Conteúdo |
|---|---|
| Requisito | QUANDO `cadastrar_livro` for chamada com dados válidos, o sistema DEVE armazenar o livro com todos os exemplares disponíveis e retorná-lo. |
| Prioridade | Must |
| Origem | N-01 |
| Operação | `cadastrar_livro(self, isbn: str, titulo: str, autor: str, exemplares: int) -> Livro` |
| Regras de negócio | RN-01 |

**Validações, em ordem:**

| # | Condição de falha | Erro |
|---|---|---|
| 1 | ISBN normalizado inválido (RN-01) | `ISBNInvalidoErro` |
| 2 | `titulo` vazio após `strip()` | `DadosInvalidosErro` |
| 3 | `autor` vazio após `strip()` | `DadosInvalidosErro` |
| 4 | `exemplares` não é do tipo `int` (valores `bool` não são aceitos) ou `exemplares` < 1 | `DadosInvalidosErro` |
| 5 | Já existe livro com o mesmo ISBN normalizado | `DuplicadoErro` |

**Processamento:**

1. Criar `Livro(isbn=<normalizado>, titulo=<strip>, autor=<strip>, exemplares_total=exemplares, exemplares_disponiveis=exemplares)`.
2. Armazenar e retornar o livro.

**Saída:** o `Livro` criado.

**Critérios de aceitação:**

- **CA-01.1** — **Dado** uma biblioteca vazia, **quando** `cadastrar_livro("978-85-333-0227-3", "  Dom Casmurro ", "Machado de Assis", 2)`, **então** retorna `Livro` com `isbn == "9788533302273"`, `titulo == "Dom Casmurro"`, `autor == "Machado de Assis"`, `exemplares_total == 2` e `exemplares_disponiveis == 2`.
- **CA-01.2** — **Dado** uma biblioteca vazia, **quando** `cadastrar_livro("9788533302274", "Dom Casmurro", "Machado de Assis", 1)` (dígito verificador errado), **então** lança `ISBNInvalidoErro`.
- **CA-01.3** — **Dado** uma biblioteca vazia, **quando** o ISBN for `"978853330227"` (12 dígitos) ou `"978853330227X"` (contém letra), **então** lança `ISBNInvalidoErro` em ambos os casos.
- **CA-01.4** — **Dado** uma biblioteca vazia, **quando** `titulo` for `"   "` ou `autor` for `""`, **então** lança `DadosInvalidosErro`.
- **CA-01.5** — **Dado** uma biblioteca vazia, **quando** `exemplares` for `0` ou `"2"` (texto), **então** lança `DadosInvalidosErro`.
- **CA-01.6** — **Dado** o livro `9788533302273` ("Dom Casmurro") cadastrado, **quando** `cadastrar_livro("978-8533302273", "Outro", "Outro Autor", 1)`, **então** lança `DuplicadoErro` e `buscar_livro("9788533302273").titulo` continua `"Dom Casmurro"`.
- **CA-01.7** — *(ordem de validação)* **Dado** uma biblioteca vazia, **quando** `cadastrar_livro("123", "", "Autor", 1)`, **então** lança `ISBNInvalidoErro`; **e dado** o livro `9788533302273` cadastrado, **quando** `cadastrar_livro("9788533302273", "", "Autor", 1)`, **então** lança `DadosInvalidosErro` (e não `DuplicadoErro`).

### RF-02 — Cadastrar usuário

| Campo | Conteúdo |
|---|---|
| Requisito | QUANDO `cadastrar_usuario` for chamada com dados válidos, o sistema DEVE armazenar o usuário com saldo devedor zero e retorná-lo. |
| Prioridade | Must |
| Origem | N-02 |
| Operação | `cadastrar_usuario(self, id: str, nome: str, tipo: str) -> Usuario` |
| Regras de negócio | RN-02 |

**Validações, em ordem:**

| # | Condição de falha | Erro |
|---|---|---|
| 1 | `id` vazio após `strip()` | `DadosInvalidosErro` |
| 2 | `nome` vazio após `strip()` | `DadosInvalidosErro` |
| 3 | `tipo` normalizado (C-02) diferente de `"ALUNO"` e de `"PROFESSOR"` | `DadosInvalidosErro` |
| 4 | Já existe usuário com o mesmo `id` normalizado | `DuplicadoErro` |

**Processamento:**

1. Criar `Usuario(id=<strip>, nome=<strip>, tipo=<strip+upper>, saldo_devedor=Decimal("0.00"))`.
2. Armazenar e retornar o usuário.

**Saída:** o `Usuario` criado.

**Critérios de aceitação:**

- **CA-02.1** — **Dado** uma biblioteca vazia, **quando** `cadastrar_usuario(" u1 ", " Ana Lima ", " aluno ")`, **então** retorna `Usuario` com `id == "u1"`, `nome == "Ana Lima"`, `tipo == "ALUNO"` e `saldo_devedor == Decimal("0.00")`.
- **CA-02.2** — **Dado** uma biblioteca vazia, **quando** `cadastrar_usuario("u9", "Caio", "VISITANTE")`, **então** lança `DadosInvalidosErro`.
- **CA-02.3** — **Dado** o usuário `"u1"` ("Ana Lima") cadastrado, **quando** `cadastrar_usuario("u1 ", "Outra Pessoa", "PROFESSOR")`, **então** lança `DuplicadoErro` e `buscar_usuario("u1").nome` continua `"Ana Lima"`.
- **CA-02.4** — **Dado** uma biblioteca vazia, **quando** `id` for `""` ou `nome` for `"  "`, **então** lança `DadosInvalidosErro`.

### RF-03 — Consultar por identificador

| Campo | Conteúdo |
|---|---|
| Requisito | QUANDO for solicitada a consulta de um livro, usuário ou empréstimo por identificador, o sistema DEVE retornar o objeto correspondente; SE ele não existir, ENTÃO o sistema DEVE lançar `NaoEncontradoErro`. |
| Prioridade | Must |
| Origem | N-01, N-02, N-04 |
| Operação | `buscar_livro(self, isbn: str) -> Livro`; `buscar_usuario(self, id: str) -> Usuario`; `buscar_emprestimo(self, id: int) -> Emprestimo` |
| Regras de negócio | — |

**Validações, em ordem:**

| # | Condição de falha | Erro |
|---|---|---|
| 1 | Não existe objeto com o identificador normalizado (C-01 para ISBN, C-02 para `id` de usuário) | `NaoEncontradoErro` |

`buscar_livro` NÃO DEVE validar o dígito verificador: apenas normaliza e procura.

**Saída:** o objeto encontrado.

**Critérios de aceitação:**

- **CA-03.1** — **Dado** o livro `9788533302273` cadastrado, **quando** `buscar_livro("978 85 333 0227 3")`, **então** retorna o livro com `isbn == "9788533302273"`.
- **CA-03.2** — **Dado** uma biblioteca sem empréstimos e sem o livro `9780140449136` nem o usuário `"x"`, **quando** `buscar_livro("9780140449136")`, `buscar_usuario("x")` ou `buscar_emprestimo(1)`, **então** cada chamada lança `NaoEncontradoErro`.
- **CA-03.3** — **Dado** o usuário `"u1"` cadastrado, **quando** `buscar_usuario(" u1 ")`, **então** retorna o usuário com `id == "u1"`.

### RF-04 — Pesquisar livros

| Campo | Conteúdo |
|---|---|
| Requisito | QUANDO `pesquisar_livros` for chamada, o sistema DEVE retornar os livros cujo título ou autor contém o termo, sem diferenciar maiúsculas de minúsculas, ordenados por título. |
| Prioridade | Should |
| Origem | N-03 |
| Operação | `pesquisar_livros(self, termo: str) -> list[Livro]` |
| Regras de negócio | — |

**Validações:** nenhuma (esta operação não lança erros).

**Processamento:**

1. Normalizar o termo com `strip()`.
2. Se o termo normalizado for vazio, selecionar **todos** os livros.
3. Caso contrário, selecionar os livros em que `termo.casefold()` está contido em `titulo.casefold()` **ou** em `autor.casefold()`. A comparação NÃO DEVE ignorar acentos (`"cortico"` não encontra `"Cortiço"`).
4. Ordenar por `titulo.casefold()` crescente; em caso de empate, por `isbn` crescente.

**Saída:** lista (possivelmente vazia) de `Livro`.

**Critérios de aceitação** (dados comuns: livros `9788533302273` "Dom Casmurro" / "Machado de Assis"; `9788575223796` "Memórias Póstumas de Brás Cubas" / "Machado de Assis"; `9788593541131` "O Cortiço" / "Aluísio Azevedo"):

- **CA-04.1** — **Quando** `pesquisar_livros("machado")`, **então** retorna os livros `9788533302273` e `9788575223796`, nessa ordem.
- **CA-04.2** — **Quando** `pesquisar_livros("CORTIÇO")`, **então** retorna somente o livro `9788593541131`.
- **CA-04.3** — **Quando** `pesquisar_livros("cortico")` (sem acento), **então** retorna lista vazia.
- **CA-04.4** — **Quando** `pesquisar_livros("xyz")`, **então** retorna lista vazia.
- **CA-04.5** — **Dado** também o livro `9788535902778` "Dom Casmurro" / "Machado de Assis", **quando** `pesquisar_livros("")` ou `pesquisar_livros("   ")`, **então** retorna os quatro livros na ordem `9788533302273`, `9788535902778`, `9788575223796`, `9788593541131`.

### RF-05 — Emprestar livro

| Campo | Conteúdo |
|---|---|
| Requisito | QUANDO `emprestar` for chamada e todas as validações forem atendidas, o sistema DEVE registrar um empréstimo ativo e reduzir em 1 os exemplares disponíveis do livro. |
| Prioridade | Must |
| Origem | N-04, N-07 |
| Operação | `emprestar(self, isbn: str, usuario_id: str, data: date) -> Emprestimo` |
| Regras de negócio | RN-02, RN-03, RN-04, RN-06, RN-08 |

**Validações, em ordem:**

| # | Condição de falha | Erro |
|---|---|---|
| 1 | Livro não cadastrado (ISBN normalizado) | `NaoEncontradoErro` |
| 2 | Usuário não cadastrado (`usuario_id` normalizado) | `NaoEncontradoErro` |
| 3 | Usuário com pendência na data `data` (RN-06) | `PendenciaErro` |
| 4 | Usuário já possui empréstimo ativo do mesmo ISBN | `OperacaoInvalidaErro` |
| 5 | Quantidade de empréstimos ativos do usuário ≥ limite do seu tipo (RN-02) | `LimiteExcedidoErro` |
| 6 | `exemplares_disponiveis == 0` | `IndisponivelErro` |
| 7 | A fila de reservas do ISBN não está vazia e o usuário não é o primeiro da fila (RN-08) | `IndisponivelErro` |

**Processamento:**

1. Criar `Emprestimo(id=<próximo id>, isbn=<normalizado>, usuario_id=<normalizado>, data_emprestimo=data, data_prevista_devolucao=data + prazo do tipo (RN-02), data_devolucao=None, renovacoes=0, multa=Decimal("0.00"))`.
2. Reduzir `exemplares_disponiveis` do livro em 1.
3. Se o usuário for o primeiro da fila de reservas do ISBN, removê-lo da fila.
4. Armazenar e retornar o empréstimo.

**Saída:** o `Emprestimo` criado.

**Critérios de aceitação:**

- **CA-05.1** — **Dado** o livro `9788533302273` com 2 exemplares e o usuário ALUNO `"u1"`, **quando** `emprestar("978-85-333-0227-3", "u1", date(2026, 3, 2))`, **então** retorna `Emprestimo` com `id == 1`, `isbn == "9788533302273"`, `usuario_id == "u1"`, `data_emprestimo == date(2026, 3, 2)`, `data_prevista_devolucao == date(2026, 3, 16)`, `data_devolucao is None`, `renovacoes == 0`, `multa == Decimal("0.00")`; e `buscar_livro("9788533302273").exemplares_disponiveis == 1`.
- **CA-05.2** — **Dado** o usuário PROFESSOR `"p1"` e um livro disponível, **quando** `p1` empresta em `date(2026, 3, 2)`, **então** `data_prevista_devolucao == date(2026, 3, 30)`.
- **CA-05.3** — *(ids)* **Dado** o empréstimo de id 1 já criado, **quando** uma chamada a `emprestar` falha com `NaoEncontradoErro` e, em seguida, um novo empréstimo válido é feito, **então** o novo empréstimo tem `id == 2`.
- **CA-05.4** — **Dado** o ALUNO `"u1"` com 3 empréstimos ativos de 3 livros diferentes, **quando** `u1` tenta emprestar um 4º livro disponível, **então** lança `LimiteExcedidoErro` e os exemplares disponíveis do 4º livro não mudam.
- **CA-05.5** — **Dado** o PROFESSOR `"p1"` sem empréstimos e 6 livros disponíveis, **quando** `p1` empresta 5 livros, **então** os 5 empréstimos são criados; **e quando** tenta o 6º, **então** lança `LimiteExcedidoErro`.
- **CA-05.6** — **Dado** um livro com 1 exemplar emprestado a `"u1"`, **quando** `"u2"` tenta emprestá-lo, **então** lança `IndisponivelErro`.
- **CA-05.7** — **Dado** um livro com 2 exemplares e `"u1"` com empréstimo ativo dele, **quando** `"u1"` tenta emprestá-lo novamente, **então** lança `OperacaoInvalidaErro` e `exemplares_disponiveis` continua 1.
- **CA-05.8** — *(pendência por atraso; valor-limite)* **Dado** que `"u1"` (ALUNO) emprestou o livro A em `date(2026, 3, 2)` (prevista 2026-03-16), **quando** empresta o livro B em `date(2026, 3, 16)`, **então** o empréstimo é criado; **e quando** tenta emprestar o livro C em `date(2026, 3, 17)`, **então** lança `PendenciaErro`.
- **CA-05.9** — *(pendência por saldo)* **Dado** o usuário `"u1"` com `saldo_devedor == Decimal("2.00")` e sem empréstimos ativos, **quando** tenta emprestar um livro disponível, **então** lança `PendenciaErro`.
- **CA-05.10** — **Quando** `emprestar` é chamada com ISBN não cadastrado, **ou** com `usuario_id` não cadastrado, **então** lança `NaoEncontradoErro`.
- **CA-05.11** — *(ordem de validação)* **Dado** o ALUNO `"u1"` com 3 empréstimos ativos e um livro com 0 exemplares disponíveis, **quando** `"u1"` tenta emprestar esse livro, **então** lança `LimiteExcedidoErro` (e não `IndisponivelErro`).

### RF-06 — Devolver livro

| Campo | Conteúdo |
|---|---|
| Requisito | QUANDO `devolver` for chamada para um empréstimo ativo, o sistema DEVE encerrar o empréstimo, calcular a multa, somá-la ao saldo devedor do usuário, devolver o exemplar ao acervo e retornar a multa. |
| Prioridade | Must |
| Origem | N-05 |
| Operação | `devolver(self, emprestimo_id: int, data: date) -> Decimal` |
| Regras de negócio | RN-04, RN-05 |

**Validações, em ordem:**

| # | Condição de falha | Erro |
|---|---|---|
| 1 | Empréstimo inexistente | `NaoEncontradoErro` |
| 2 | Empréstimo já devolvido (`data_devolucao` não é `None`) | `OperacaoInvalidaErro` |
| 3 | `data` < `data_emprestimo` | `DadosInvalidosErro` |

**Processamento:**

1. `data_devolucao = data`.
2. Calcular a multa (RN-05) e gravá-la em `multa` do empréstimo.
3. Somar a multa ao `saldo_devedor` do usuário.
4. Aumentar em 1 os `exemplares_disponiveis` do livro.

**Saída:** a multa (`Decimal`, duas casas; `Decimal("0.00")` quando não há atraso).

**Critérios de aceitação** (dados comuns: ALUNO `"u1"` emprestou o livro `9788533302273`, de 2 exemplares, em `date(2026, 3, 2)`, prevista 2026-03-16, empréstimo id 1):

- **CA-06.1** — **Quando** `devolver(1, date(2026, 3, 16))`, **então** retorna `Decimal("0.00")`, o empréstimo fica com `data_devolucao == date(2026, 3, 16)`, o livro volta a ter 2 exemplares disponíveis e o saldo de `"u1"` continua `Decimal("0.00")`.
- **CA-06.2** — **Quando** `devolver(1, date(2026, 3, 19))` (3 dias de atraso), **então** retorna `Decimal("6.00")`, `buscar_emprestimo(1).multa == Decimal("6.00")` e `buscar_usuario("u1").saldo_devedor == Decimal("6.00")`.
- **CA-06.3** — *(teto; valor-limite)* **Dado** dois empréstimos de usuários diferentes com prevista 2026-03-16, **quando** um é devolvido em `date(2026, 4, 9)` (24 dias de atraso) e o outro em `date(2026, 4, 11)` (26 dias), **então** as multas são `Decimal("48.00")` e `Decimal("50.00")`, respectivamente.
- **CA-06.4** — **Dado** o empréstimo 1 já devolvido, **quando** `devolver(1, date(2026, 3, 20))`, **então** lança `OperacaoInvalidaErro` e o saldo do usuário e os exemplares disponíveis não mudam.
- **CA-06.5** — **Quando** `devolver(1, date(2026, 3, 1))` (antes do empréstimo), **então** lança `DadosInvalidosErro`, o empréstimo continua ativo e o livro continua com 1 exemplar disponível.
- **CA-06.6** — **Quando** `devolver(99, date(2026, 3, 16))`, **então** lança `NaoEncontradoErro`.
- **CA-06.7** — *(acúmulo)* **Dado** que `"u1"` tem dois empréstimos com prevista 2026-03-16, **quando** devolve um em `date(2026, 3, 17)` e o outro em `date(2026, 3, 18)`, **então** o `saldo_devedor` de `"u1"` é `Decimal("6.00")`.

### RF-07 — Renovar empréstimo

| Campo | Conteúdo |
|---|---|
| Requisito | QUANDO `renovar` for chamada para um empréstimo ativo, não atrasado, com menos de 2 renovações e sem fila de reservas para o ISBN, o sistema DEVE definir a nova data prevista como a data da renovação mais o prazo do tipo do usuário e incrementar `renovacoes`. |
| Prioridade | Must |
| Origem | N-06 |
| Operação | `renovar(self, emprestimo_id: int, data: date) -> Emprestimo` |
| Regras de negócio | RN-02, RN-04, RN-07, RN-08 |

**Validações, em ordem:**

| # | Condição de falha | Erro |
|---|---|---|
| 1 | Empréstimo inexistente | `NaoEncontradoErro` |
| 2 | Empréstimo já devolvido | `OperacaoInvalidaErro` |
| 3 | `data` < `data_emprestimo` | `DadosInvalidosErro` |
| 4 | Empréstimo atrasado na data `data` (RN-04) | `RenovacaoNegadaErro` |
| 5 | `renovacoes` ≥ 2 | `RenovacaoNegadaErro` |
| 6 | A fila de reservas do ISBN não está vazia | `RenovacaoNegadaErro` |

**Processamento:**

1. `data_prevista_devolucao = data + prazo do tipo do usuário (RN-02)`.
2. `renovacoes = renovacoes + 1`.

**Saída:** o `Emprestimo` atualizado.

**Critérios de aceitação** (dados comuns: ALUNO `"u1"` emprestou o livro `9788533302273` em `date(2026, 3, 2)`, prevista 2026-03-16, empréstimo id 1):

- **CA-07.1** — **Quando** `renovar(1, date(2026, 3, 10))`, **então** retorna o empréstimo com `data_prevista_devolucao == date(2026, 3, 24)` e `renovacoes == 1`.
- **CA-07.2** — *(valor-limite)* **Quando** `renovar(1, date(2026, 3, 16))` (na data prevista), **então** a renovação é aceita e `data_prevista_devolucao == date(2026, 3, 30)`.
- **CA-07.3** — **Quando** `renovar(1, date(2026, 3, 17))` (atrasado), **então** lança `RenovacaoNegadaErro`, e o empréstimo mantém `data_prevista_devolucao == date(2026, 3, 16)` e `renovacoes == 0`.
- **CA-07.4** — **Dado** as renovações em `date(2026, 3, 10)` (prevista → 2026-03-24) e `date(2026, 3, 20)` (prevista → 2026-04-03), **quando** `renovar(1, date(2026, 3, 25))`, **então** lança `RenovacaoNegadaErro` e `renovacoes == 2`.
- **CA-07.5** — **Dado** que o livro tem 1 exemplar (emprestado a `"u1"`) e `"u2"` está na fila de reservas, **quando** `renovar(1, date(2026, 3, 10))`, **então** lança `RenovacaoNegadaErro`.
- **CA-07.6** — **Quando** `renovar(99, date(2026, 3, 10))`, **então** lança `NaoEncontradoErro`; **e dado** o empréstimo 1 já devolvido, **quando** `renovar(1, date(2026, 3, 10))`, **então** lança `OperacaoInvalidaErro`.
- **CA-07.7** — **Dado** o PROFESSOR `"p1"` com empréstimo feito em `date(2026, 3, 2)`, **quando** renova em `date(2026, 3, 10)`, **então** `data_prevista_devolucao == date(2026, 4, 7)`.

### RF-08 — Reservar livro

| Campo | Conteúdo |
|---|---|
| Requisito | QUANDO `reservar` for chamada para um livro sem exemplar disponível para o usuário, o sistema DEVE colocar o usuário no fim da fila de reservas do ISBN e retornar sua posição. |
| Prioridade | Should |
| Origem | N-07 |
| Operação | `reservar(self, isbn: str, usuario_id: str) -> int` |
| Regras de negócio | RN-08 |

**Validações, em ordem:**

| # | Condição de falha | Erro |
|---|---|---|
| 1 | Livro não cadastrado | `NaoEncontradoErro` |
| 2 | Usuário não cadastrado | `NaoEncontradoErro` |
| 3 | Usuário possui empréstimo ativo do mesmo ISBN | `OperacaoInvalidaErro` |
| 4 | Usuário já está na fila de reservas do ISBN | `DuplicadoErro` |
| 5 | `exemplares_disponiveis > 0` **e** a fila de reservas do ISBN está vazia | `OperacaoInvalidaErro` |

A reserva NÃO verifica pendência nem limite de empréstimos.

**Processamento:**

1. Acrescentar o `usuario_id` normalizado ao fim da fila do ISBN.

**Saída:** a posição do usuário na fila (`int`, a primeira posição é 1).

**Critérios de aceitação** (dados comuns: livro `9788533302273` com 1 exemplar, emprestado a `"u1"`; usuários `"u2"` e `"u3"` cadastrados):

- **CA-08.1** — **Quando** `reservar("9788533302273", "u2")` e depois `reservar("9788533302273", "u3")`, **então** retornam `1` e `2`, e `fila_reservas("9788533302273") == ["u2", "u3"]`.
- **CA-08.2** — **Dado** o livro `9788575223796` com exemplares disponíveis e sem fila, **quando** `reservar("9788575223796", "u2")`, **então** lança `OperacaoInvalidaErro` e a fila desse livro continua vazia.
- **CA-08.3** — **Dado** `"u2"` já na fila, **quando** `reservar("9788533302273", "u2")`, **então** lança `DuplicadoErro` e a fila continua `["u2"]`.
- **CA-08.4** — **Quando** `reservar("9788533302273", "u1")` (quem está com o exemplar), **então** lança `OperacaoInvalidaErro`.
- **CA-08.5** — **Quando** `reservar` é chamada com ISBN não cadastrado, **ou** com usuário não cadastrado, **então** lança `NaoEncontradoErro`.

### RF-09 — Atender a fila de reservas

| Campo | Conteúdo |
|---|---|
| Requisito | ENQUANTO houver fila de reservas para um ISBN, o sistema DEVE permitir o empréstimo desse ISBN apenas ao primeiro da fila e, QUANDO ele emprestar, DEVE removê-lo da fila. O sistema DEVE permitir consultar a fila. |
| Prioridade | Should |
| Origem | N-07 |
| Operação | `fila_reservas(self, isbn: str) -> list[str]` (consulta) e `emprestar` (efeito, RF-05 validação 7 e processamento 3) |
| Regras de negócio | RN-08 |

**Validações de `fila_reservas`, em ordem:**

| # | Condição de falha | Erro |
|---|---|---|
| 1 | Livro não cadastrado | `NaoEncontradoErro` |

**Saída de `fila_reservas`:** lista com os `id`s dos usuários na ordem da fila; lista vazia se não houver reservas.

**Critérios de aceitação** (dados comuns: livro `9788533302273` com 1 exemplar; ALUNOs `"u1"`, `"u2"`, `"u3"`):

- **CA-09.1** — **Dado** o livro sem reservas, **quando** `fila_reservas("9788533302273")`, **então** retorna `[]`; **e quando** `fila_reservas("9780140449136")` (não cadastrado), **então** lança `NaoEncontradoErro`.
- **CA-09.2** — **Dado** que `"u1"` emprestou o livro em `date(2026, 3, 2)`, `"u2"` e `"u3"` reservaram (nessa ordem) e `"u1"` devolveu em `date(2026, 3, 5)`, **quando** `"u3"` tenta emprestar em `date(2026, 3, 5)`, **então** lança `IndisponivelErro`; **e quando** `"u2"` empresta em `date(2026, 3, 5)`, **então** o empréstimo é criado e `fila_reservas("9788533302273") == ["u3"]`.
- **CA-09.3** — **Dado** que `"u1"` emprestou o livro, `"u2"` reservou, `"u1"` devolveu, `"u2"` emprestou e depois devolveu (tudo sem atraso), **quando** `"u3"` (que não reservou) empresta o livro, **então** o empréstimo é criado.

### RF-10 — Pagar multa

| Campo | Conteúdo |
|---|---|
| Requisito | QUANDO `pagar_multa` for chamada com valor positivo não superior ao saldo devedor, o sistema DEVE subtrair o valor do saldo devedor do usuário e retornar o novo saldo. |
| Prioridade | Must |
| Origem | N-08 |
| Operação | `pagar_multa(self, usuario_id: str, valor: Decimal) -> Decimal` |
| Regras de negócio | RN-06 |

**Validações, em ordem:**

| # | Condição de falha | Erro |
|---|---|---|
| 1 | Usuário não cadastrado | `NaoEncontradoErro` |
| 2 | `valor` ≤ `Decimal("0")` | `DadosInvalidosErro` |
| 3 | `valor` > `saldo_devedor` do usuário | `DadosInvalidosErro` |

**Processamento:**

1. `saldo_devedor = saldo_devedor − valor` (duas casas, C-03).

**Saída:** o novo `saldo_devedor`.

**Critérios de aceitação** (dados comuns: ALUNO `"u1"` com `saldo_devedor == Decimal("6.00")` e sem empréstimos ativos):

- **CA-10.1** — **Quando** `pagar_multa("u1", Decimal("4.00"))`, **então** retorna `Decimal("2.00")` e `buscar_usuario("u1").saldo_devedor == Decimal("2.00")`.
- **CA-10.2** — **Dado** o pagamento anterior, **quando** `pagar_multa("u1", Decimal("2.00"))`, **então** retorna `Decimal("0.00")`; **e** `"u1"` consegue emprestar um livro disponível.
- **CA-10.3** — **Quando** `valor` for `Decimal("0")`, `Decimal("-1.00")` ou `Decimal("6.01")`, **então** lança `DadosInvalidosErro` e o saldo continua `Decimal("6.00")`.
- **CA-10.4** — **Quando** `pagar_multa("x", Decimal("1.00"))` (usuário inexistente), **então** lança `NaoEncontradoErro`.

### RF-11 — Listar empréstimos atrasados

| Campo | Conteúdo |
|---|---|
| Requisito | QUANDO `listar_atrasados` for chamada com uma data D, o sistema DEVE retornar os empréstimos ativos atrasados em D (RN-04), ordenados por data prevista de devolução crescente e, em empate, por `id` crescente. |
| Prioridade | Should |
| Origem | N-09 |
| Operação | `listar_atrasados(self, data: date) -> list[Emprestimo]` |
| Regras de negócio | RN-03, RN-04 |

**Validações:** nenhuma (esta operação não lança erros).

**Saída:** lista (possivelmente vazia) de `Emprestimo`. Empréstimos devolvidos nunca aparecem.

**Critérios de aceitação** (dados comuns: empréstimo id 1 — ALUNO `"u1"` em `date(2026, 3, 2)`, prevista 2026-03-16; id 2 — ALUNO `"u2"` em `date(2026, 3, 1)`, prevista 2026-03-15; id 3 — PROFESSOR `"p1"` em `date(2026, 3, 2)`, prevista 2026-03-30; livros distintos):

- **CA-11.1** — **Quando** `listar_atrasados(date(2026, 3, 20))`, **então** retorna os empréstimos de ids `[2, 1]`, nessa ordem.
- **CA-11.2** — *(valor-limite)* **Quando** `listar_atrasados(date(2026, 3, 16))`, **então** retorna somente o empréstimo de id `2`.
- **CA-11.3** — **Dado** que o empréstimo 2 foi devolvido em `date(2026, 3, 18)`, **quando** `listar_atrasados(date(2026, 3, 20))`, **então** retorna somente o empréstimo de id `1`.
- **CA-11.4** — *(desempate)* **Dado** também o empréstimo id 4 — ALUNO `"u3"` em `date(2026, 3, 1)`, prevista 2026-03-15, **quando** `listar_atrasados(date(2026, 3, 20))`, **então** retorna os ids `[2, 4, 1]`.

## 9. Requisitos não funcionais

| ID | Categoria | Requisito | Critério de verificação |
|---|---|---|---|
| RNF-01 | Portabilidade | O módulo DEVE usar somente a biblioteca padrão do Python (R-01, R-02). | **CV-01.1** — Todo `import`/`from … import` de `biblioteca.py` refere-se a um módulo de `sys.stdlib_module_names` (ou `__future__`). |
| RNF-02 | Confiabilidade (determinismo) | O módulo NÃO DEVE consultar o relógio do sistema (R-05). | **CV-02.1** — Não há, em `biblioteca.py`, chamadas a `.today()`, `.now()`, `.utcnow()` nem a `time.time()`. |
| RNF-03 | Compatibilidade | O módulo NÃO DEVE ter efeitos colaterais de entrada/saída (R-04, R-06). | **CV-03.1** — Importar `biblioteca` em um processo novo produz `stdout` e `stderr` vazios. **CV-03.2** — Não há, em `biblioteca.py`, chamadas a `print`, `input` ou `open`. |
| RNF-04 | Desempenho | O módulo DEVE suportar o volume de uma biblioteca escolar. | **CV-04.1** — Cadastrar 2.000 livros e 2.000 usuários, registrar 2.000 empréstimos e executar `listar_atrasados` e `pesquisar_livros("")` uma vez cada DEVE levar, no total, menos de 5 segundos. |
| RNF-05 | Manutenibilidade (conformidade de interface) | O módulo DEVE declarar os erros e as classes de dados exatamente como nas seções 5 e 6.2. | **CV-05.1** — Todas as classes de erro da seção 6.2 existem com as heranças indicadas. **CV-05.2** — `Livro`, `Usuario` e `Emprestimo` são *dataclasses* com os campos da seção 5, na mesma ordem. |
| RNF-06 | Manutenibilidade (documentação) | A classe `Biblioteca` e cada um de seus métodos públicos DEVEM ter *docstring*. | **CV-06.1** — `Biblioteca` e todos os seus métodos cujo nome não começa com `_` têm *docstring* não vazia. |
| RNF-07 | Confiabilidade (atomicidade) | Uma operação que lança erro NÃO DEVE alterar nenhum dado (C-06, C-07). | **CV-07.1** — Após uma sequência de operações que falham (cadastro duplicado, empréstimo indisponível, devolução inválida, renovação negada, reserva inválida, pagamento inválido), livros, usuários, empréstimos, filas e o próximo id de empréstimo permanecem como antes. |
| RNF-08 | Confiabilidade (exatidão monetária) | Valores monetários DEVEM ser `Decimal` com duas casas (C-03). | **CV-08.1** — `saldo_devedor` inicial, a `multa` de um empréstimo novo, o retorno de `devolver` e o retorno de `pagar_multa` são instâncias de `Decimal` cuja representação textual tem exatamente duas casas decimais (ex.: `"0.00"`, `"6.00"`). |

## 10. Cenário de ponta a ponta

```text
b = Biblioteca()
b.cadastrar_livro("978-85-333-0227-3", "Dom Casmurro", "Machado de Assis", 1)
    → Livro(isbn='9788533302273', titulo='Dom Casmurro', autor='Machado de Assis', exemplares_total=1, exemplares_disponiveis=1)
b.cadastrar_usuario("u1", "Ana Lima", "aluno")
    → Usuario(id='u1', nome='Ana Lima', tipo='ALUNO', saldo_devedor=Decimal('0.00'))
b.cadastrar_usuario("p1", "Bruno Reis", "PROFESSOR")
    → Usuario(id='p1', nome='Bruno Reis', tipo='PROFESSOR', saldo_devedor=Decimal('0.00'))
b.emprestar("9788533302273", "u1", date(2026, 3, 2))
    → Emprestimo(id=1, isbn='9788533302273', usuario_id='u1', data_emprestimo=date(2026, 3, 2),
                 data_prevista_devolucao=date(2026, 3, 16), data_devolucao=None, renovacoes=0, multa=Decimal('0.00'))
b.emprestar("9788533302273", "p1", date(2026, 3, 3))       → IndisponivelErro
b.reservar("9788533302273", "p1")                          → 1
b.renovar(1, date(2026, 3, 10))                            → RenovacaoNegadaErro   (há fila)
b.devolver(1, date(2026, 3, 19))                           → Decimal('6.00')        (3 dias × 2,00)
b.emprestar("9788533302273", "p1", date(2026, 3, 19))      → Emprestimo(id=2, ..., data_prevista_devolucao=date(2026, 4, 16), ...)
b.fila_reservas("9788533302273")                           → []
b.buscar_usuario("u1").saldo_devedor                       → Decimal('6.00')
b.pagar_multa("u1", Decimal("6.00"))                       → Decimal('0.00')
```

## 11. Premissas, decisões e questões em aberto

| ID | Tipo | Descrição | Situação |
|---|---|---|---|
| P-01 | Decisão | A pesquisa de livros não ignora acentos. | Resolvida em 2026-10-15 pela Coordenação da Biblioteca |
| P-02 | Decisão | Só é possível reservar um livro sem exemplar disponível ou que já tenha fila. | Resolvida em 2026-10-15 pela Bibliotecária |
| P-03 | Decisão | A renovação não verifica saldo devedor; o empréstimo verifica. | Resolvida em 2026-10-15 pela Coordenação da Biblioteca |
| P-04 | Decisão | Pagamento parcial é permitido e não remove a pendência. | Resolvida em 2026-10-15 pela Tesouraria |
| P-05 | Premissa | As datas recebidas são objetos `datetime.date` válidos; o módulo não valida o tipo das datas. | Aceita |
| P-06 | Decisão | Cancelamento de reserva e edição/remoção de cadastros ficam para uma versão futura. | Resolvida em 2026-10-15 pela Coordenação da Biblioteca |

## 12. Matriz de rastreabilidade

| Necessidade | Requisito | Regras | Operação | Critérios |
|---|---|---|---|---|
| N-01 | RF-01 | RN-01 | `cadastrar_livro` | CA-01.1 … CA-01.7 |
| N-02 | RF-02 | RN-02 | `cadastrar_usuario` | CA-02.1 … CA-02.4 |
| N-01, N-02, N-04 | RF-03 | — | `buscar_livro`, `buscar_usuario`, `buscar_emprestimo` | CA-03.1 … CA-03.3 |
| N-03 | RF-04 | — | `pesquisar_livros` | CA-04.1 … CA-04.5 |
| N-04, N-07 | RF-05 | RN-02, RN-03, RN-04, RN-06, RN-08 | `emprestar` | CA-05.1 … CA-05.11 |
| N-05 | RF-06 | RN-04, RN-05 | `devolver` | CA-06.1 … CA-06.7 |
| N-06 | RF-07 | RN-02, RN-04, RN-07, RN-08 | `renovar` | CA-07.1 … CA-07.7 |
| N-07 | RF-08 | RN-08 | `reservar` | CA-08.1 … CA-08.5 |
| N-07 | RF-09 | RN-08 | `fila_reservas`, `emprestar` | CA-09.1 … CA-09.3 |
| N-08 | RF-10 | RN-06 | `pagar_multa` | CA-10.1 … CA-10.4 |
| N-09 | RF-11 | RN-03, RN-04 | `listar_atrasados` | CA-11.1 … CA-11.4 |
| — | RNF-01 … RNF-08 | — | módulo inteiro | CV-01.1, CV-02.1, CV-03.1, CV-03.2, CV-04.1, CV-05.1, CV-05.2, CV-06.1, CV-07.1, CV-08.1 |

**Total de critérios verificáveis:** 60 CA + 10 CV = **70**.

## 13. Checklist de prontidão — Definition of Ready

- [x] Todas as seções obrigatórias estão preenchidas (ou marcadas "Não se aplica — motivo").
- [x] Todo termo de domínio usado está no Glossário e é usado sempre com o mesmo nome.
- [x] Toda operação tem assinatura completa com tipos e todos os erros possíveis listados.
- [x] Toda operação com mais de uma validação tem a ordem de validação definida.
- [x] Toda regra de negócio tem valores concretos (números, unidades, fórmulas, arredondamento).
- [x] Todo RF tem ao menos um CA de caminho feliz, um CA por erro e CAs para os valores-limite.
- [x] Todo RNF tem métrica e critério de verificação (CV).
- [x] Toda listagem tem critério de ordenação e de desempate.
- [x] O que está fora do escopo está explícito.
- [x] Nenhuma palavra vaga permanece no texto.
- [x] Não há questões abertas (seção 11).
- [ ] A revisão adversarial por LLM foi feita e todos os achados foram tratados.
- [x] A matriz de rastreabilidade cobre 100% dos RF, RN e critérios.

## 14. Histórico de revisões

| Versão | Data | Autor | Descrição da mudança |
|---|---|---|---|
| 0.1 | 2026-10-15 | Analista de Requisitos | Versão inicial a partir da elicitação (N-01 a N-09). |
| 1.0 | 2026-10-15 | Analista de Requisitos | Versão candidata, com DoR autoverificado pelo autor. Pendentes (F4): inspeção por revisor com o checklist e revisão adversarial por LLM. Após tratar os achados, atualizar o status para Aprovado e congelar o documento antes do experimento. |
