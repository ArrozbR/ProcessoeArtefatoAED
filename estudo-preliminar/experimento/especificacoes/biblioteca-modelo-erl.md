# Documento de Requisitos — Sistema de Empréstimos de Biblioteca

> Modelo **ERL** (Especificação de Requisitos para LLM), versão 1.0.

| Campo | Valor |
|---|---|
| Versão do documento | 1.0 |
| Data | 2026-10-15 |
| Autores | Grupo ProcessoeArtefatoAED |
| Situação | Pronto para geração |

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

Controlar o acervo e os empréstimos de uma biblioteca acadêmica: cadastro de livros e usuários, empréstimo, devolução com multa por atraso, renovação e fila de reservas.

### 1.2 Escopo: o que está incluído

- Cadastro de livros e de usuários.
- Empréstimo, devolução e renovação de exemplares.
- Cálculo, acúmulo e pagamento de multas por atraso.
- Fila de reservas por livro.
- Consultas: exemplares disponíveis, saldo devedor, empréstimos atrasados e dados de um empréstimo.

### 1.3 Fora de escopo

- Persistência: todo o estado fica em memória, dentro de uma instância de `Biblioteca`.
- Interface com o usuário (linha de comando, web ou gráfica), autenticação e permissões.
- Remoção ou edição de livros e usuários.
- Expiração de reservas e cancelamento de reservas.
- Identificação individual de exemplares (só a quantidade importa).
- Leitura do relógio do sistema: toda data chega como parâmetro.

### 1.4 Atores

| Ator | Descrição | O que faz no sistema |
|---|---|---|
| Bibliotecário | Funcionário que opera o sistema | Todos os RF |
| Usuário | Aluno ou professor que pega livros | É o sujeito dos empréstimos, reservas e multas; não chama o sistema diretamente |

---

## 2. Glossário

| Termo | Definição |
|---|---|
| Livro | Título do acervo, identificado pelo ISBN. Um livro tem uma quantidade de exemplares. |
| Exemplar | Cópia física de um livro. Os exemplares de um mesmo livro são indistinguíveis. |
| Empréstimo ativo | Empréstimo cuja `data_devolucao` é `None`. |
| Exemplares disponíveis | `quantidade` do livro menos o número de empréstimos ativos daquele livro. |
| Data prevista | Data limite para devolver um empréstimo sem multa (campo `data_prevista`). |
| Atrasado | Um empréstimo ativo está atrasado numa data `D` quando `D > data_prevista`. Na própria data prevista ele **não** está atrasado. |
| Dias de atraso | `(data_devolucao - data_prevista).days` quando o resultado é positivo; caso contrário, 0. |
| Saldo devedor | Soma das multas geradas para o usuário menos os pagamentos feitos, em centavos. |
| Bloqueado | Situação do usuário definida na RN-05. |
| Fila de reservas | Lista ordenada (primeiro a entrar, primeiro a sair) de usuários esperando um livro. Existe uma fila por livro. |

---

## 3. Restrições técnicas

| ID | Restrição |
|---|---|
| RT-01 | Linguagem: Python 3.10 ou superior. |
| RT-02 | Dependências: somente a biblioteca padrão do Python. |
| RT-03 | Estrutura: um único arquivo chamado `biblioteca.py`. |
| RT-04 | Ponto de entrada: a classe `Biblioteca` e todas as exceções da Seção 5.1 importáveis de `biblioteca` (`from biblioteca import Biblioteca, DadosInvalidos, ...`). O construtor `Biblioteca()` não recebe argumentos. |
| RT-05 | Dinheiro em centavos, como `int`. Datas como `datetime.date`. |
| RT-06 | O código não deve imprimir nada nem ler entrada do usuário. |

---

## 4. Modelo de dados

### 4.1 Livro

| Campo | Tipo | Obrigatório | Restrições | Valor inicial |
|---|---|---|---|---|
| isbn | `str` | Sim | Exatamente 13 caracteres, todos dígitos de 0 a 9. Único. | — |
| titulo | `str` | Sim | Não vazio e não composto só de espaços. | — |
| autor | `str` | Sim | Não vazio e não composto só de espaços. | — |
| quantidade | `int` | Sim | Maior ou igual a 1. | — |

### 4.2 Usuário

| Campo | Tipo | Obrigatório | Restrições | Valor inicial |
|---|---|---|---|---|
| id_usuario | `str` | Sim | Não vazio e não composto só de espaços. Único. | — |
| nome | `str` | Sim | Não vazio e não composto só de espaços. | — |
| tipo | `str` | Sim | Exatamente `"aluno"` ou `"professor"` (minúsculas). | — |
| saldo_devedor | `int` | — | Maior ou igual a 0. | 0 |

### 4.3 Empréstimo

| Campo | Tipo | Obrigatório | Restrições | Valor inicial |
|---|---|---|---|---|
| id_emprestimo | `int` | — | Gerado pelo sistema (RN-09). | — |
| isbn | `str` | Sim | Livro cadastrado. | — |
| id_usuario | `str` | Sim | Usuário cadastrado. | — |
| data_emprestimo | `date` | Sim | — | data da chamada `emprestar` |
| data_prevista | `date` | — | RN-01 e RN-06. | data_emprestimo + prazo |
| data_devolucao | `date` ou `None` | — | — | `None` |
| renovacoes | `int` | — | De 0 a 2. | 0 |
| multa | `int` | — | Multa gerada na devolução (RN-03, RN-04). | 0 |

---

## 5. Interface pública (contrato)

### 5.1 Exceções

Todas definidas no módulo `biblioteca`.

| Exceção | Herda de | Quando é lançada |
|---|---|---|
| `ErroBiblioteca` | `Exception` | Base de todos os erros do sistema. Nunca é lançada diretamente. |
| `DadosInvalidos` | `ErroBiblioteca` | Parâmetro fora das restrições da Seção 4 ou dos RF. |
| `CadastroDuplicado` | `ErroBiblioteca` | ISBN ou id de usuário já cadastrado. |
| `LivroNaoEncontrado` | `ErroBiblioteca` | ISBN não cadastrado. |
| `UsuarioNaoEncontrado` | `ErroBiblioteca` | id de usuário não cadastrado. |
| `EmprestimoNaoEncontrado` | `ErroBiblioteca` | id de empréstimo inexistente. |
| `EmprestimoJaDevolvido` | `ErroBiblioteca` | Operação sobre empréstimo que não está ativo. |
| `UsuarioBloqueado` | `ErroBiblioteca` | Usuário bloqueado (RN-05) tentando emprestar. |
| `LimiteDeEmprestimosAtingido` | `ErroBiblioteca` | Usuário já tem o máximo de empréstimos ativos (RN-02). |
| `ExemplarIndisponivel` | `ErroBiblioteca` | Não há exemplar que o usuário possa levar (RN-07). |
| `RenovacaoNaoPermitida` | `ErroBiblioteca` | Renovação barrada pelas regras do RF-05. |
| `ReservaNaoPermitida` | `ErroBiblioteca` | Reserva barrada pelas regras do RF-06. |

### 5.2 Operações (métodos de `Biblioteca`)

| Assinatura | Retorno | RF |
|---|---|---|
| `cadastrar_livro(isbn: str, titulo: str, autor: str, quantidade: int) -> None` | — | RF-01 |
| `cadastrar_usuario(id_usuario: str, nome: str, tipo: str) -> None` | — | RF-02 |
| `emprestar(isbn: str, id_usuario: str, data: date) -> int` | id do empréstimo criado | RF-03 |
| `devolver(id_emprestimo: int, data: date) -> int` | multa gerada, em centavos | RF-04 |
| `renovar(id_emprestimo: int, data: date) -> date` | nova data prevista | RF-05 |
| `reservar(isbn: str, id_usuario: str) -> int` | posição do usuário na fila (1 = primeiro) | RF-06 |
| `exemplares_disponiveis(isbn: str) -> int` | quantidade disponível | RF-07 |
| `saldo_devedor(id_usuario: str) -> int` | saldo em centavos | RF-08 |
| `pagar_multa(id_usuario: str, valor: int) -> int` | saldo restante, em centavos | RF-09 |
| `emprestimos_atrasados(data: date) -> list[int]` | ids em ordem crescente | RF-10 |
| `consultar_emprestimo(id_emprestimo: int) -> dict` | dados do empréstimo | RF-11 |

---

## 6. Regras de negócio

| ID | Regra |
|---|---|
| RN-01 | Prazo de empréstimo: aluno 14 dias corridos; professor 28 dias corridos. `data_prevista = data_emprestimo + prazo`. |
| RN-02 | Limite de empréstimos ativos simultâneos: aluno 3; professor 5. |
| RN-03 | Multa por atraso: 100 centavos por dia de atraso, calculada na devolução e somada ao saldo devedor do usuário. |
| RN-04 | A multa de um único empréstimo é limitada a 2000 centavos. |
| RN-05 | Um usuário está bloqueado numa data `D` se o saldo devedor dele for maior que 0 **ou** se ele tiver algum empréstimo ativo atrasado em `D`. |
| RN-06 | Um empréstimo pode ser renovado no máximo 2 vezes. Cada renovação soma o prazo do tipo do usuário (RN-01) à **data prevista atual**, e não à data da renovação. |
| RN-07 | Enquanto a fila de reservas de um livro não estiver vazia, somente o primeiro usuário da fila pode emprestar esse livro. Quando ele empresta, sai da fila. Reservas não alteram `exemplares_disponiveis` e não expiram. |
| RN-08 | É permitido que um usuário tenha mais de um empréstimo ativo do mesmo livro. |
| RN-09 | Os ids de empréstimo começam em 1 e aumentam de 1 em 1 a cada empréstimo **bem-sucedido**. Uma chamada que lança exceção não consome id. |
| RN-10 | Uma operação que lança exceção não altera nenhum estado do sistema. |
| RN-11 | O sistema nunca consulta a data atual do computador. Toda data vem do parâmetro `data`. |

---

## 7. Requisitos funcionais

Nos critérios abaixo, `date` é `datetime.date`, `b` é uma instância nova de `Biblioteca()` e os dados de apoio são:

- `L1 = "9788535914849"`, `L2 = "9788575228050"`, `L3 = "9788576082675"`;
- usuários `"u1"`, `"u2"`, `"u3"`, `"u4"` do tipo `"aluno"` e `"p1"` do tipo `"professor"`.

### RF-01 — Cadastrar livro

- **Descrição:** O sistema deve cadastrar um livro com a quantidade de exemplares informada.
- **Prioridade:** Essencial
- **Operação:** `cadastrar_livro(isbn: str, titulo: str, autor: str, quantidade: int) -> None`
- **Regras aplicadas:** RN-10
- **Pré-condições:** nenhuma.
- **Efeito:** o livro passa a existir, com todos os exemplares disponíveis e fila de reservas vazia.
- **Retorno:** `None`.

**Ordem de validação:**

| # | Condição | Exceção |
|---|---|---|
| 1 | `isbn` não tem exatamente 13 caracteres ou contém algo que não seja dígito | `DadosInvalidos` |
| 2 | `titulo` ou `autor` vazio ou só com espaços | `DadosInvalidos` |
| 3 | `quantidade` não é `int` ou é menor que 1 | `DadosInvalidos` |
| 4 | Já existe livro com esse `isbn` | `CadastroDuplicado` |

**Critérios de aceitação:**

- **CA-RF01-01** — Dado `b`, quando `b.cadastrar_livro(L1, "Dom Casmurro", "Machado de Assis", 2)`, então `b.exemplares_disponiveis(L1) == 2`.
- **CA-RF01-02** — Dado `b`, quando `b.cadastrar_livro("978853591484", "Dom Casmurro", "Machado de Assis", 2)` (12 dígitos), então é lançada `DadosInvalidos`.
- **CA-RF01-03** — Dado `b`, quando `b.cadastrar_livro(L1, "   ", "Machado de Assis", 2)`, então é lançada `DadosInvalidos` e `b.exemplares_disponiveis(L1)` lança `LivroNaoEncontrado`.
- **CA-RF01-04** — Dado `b`, quando `b.cadastrar_livro(L1, "Dom Casmurro", "Machado de Assis", 0)`, então é lançada `DadosInvalidos`.
- **CA-RF01-05** — Dado `L1` cadastrado com quantidade 2, quando `b.cadastrar_livro(L1, "Outro", "Outro Autor", 5)`, então é lançada `CadastroDuplicado` e `b.exemplares_disponiveis(L1) == 2`.

### RF-02 — Cadastrar usuário

- **Descrição:** O sistema deve cadastrar um usuário do tipo aluno ou professor.
- **Prioridade:** Essencial
- **Operação:** `cadastrar_usuario(id_usuario: str, nome: str, tipo: str) -> None`
- **Regras aplicadas:** RN-10
- **Pré-condições:** nenhuma.
- **Efeito:** o usuário passa a existir, com saldo devedor 0.
- **Retorno:** `None`.

**Ordem de validação:**

| # | Condição | Exceção |
|---|---|---|
| 1 | `id_usuario` vazio ou só com espaços | `DadosInvalidos` |
| 2 | `nome` vazio ou só com espaços | `DadosInvalidos` |
| 3 | `tipo` diferente de `"aluno"` e de `"professor"` | `DadosInvalidos` |
| 4 | Já existe usuário com esse `id_usuario` | `CadastroDuplicado` |

**Critérios de aceitação:**

- **CA-RF02-01** — Dado `b`, quando `b.cadastrar_usuario("u1", "Ana", "aluno")`, então `b.saldo_devedor("u1") == 0`.
- **CA-RF02-02** — Dado `b`, quando `b.cadastrar_usuario("u1", "Ana", "Aluno")`, então é lançada `DadosInvalidos`.
- **CA-RF02-03** — Dado `"u1"` cadastrado, quando `b.cadastrar_usuario("u1", "Outra Ana", "professor")`, então é lançada `CadastroDuplicado`.
- **CA-RF02-04** — Dado `b`, quando `b.cadastrar_usuario("u1", "", "aluno")`, então é lançada `DadosInvalidos` e `b.saldo_devedor("u1")` lança `UsuarioNaoEncontrado`.

### RF-03 — Emprestar

- **Descrição:** O sistema deve registrar o empréstimo de um exemplar de um livro para um usuário.
- **Prioridade:** Essencial
- **Operação:** `emprestar(isbn: str, id_usuario: str, data: date) -> int`
- **Regras aplicadas:** RN-01, RN-02, RN-05, RN-07, RN-08, RN-09, RN-10
- **Pré-condições:** nenhuma além das validações.
- **Efeito:** cria um empréstimo ativo com `data_emprestimo = data`, `data_prevista` pela RN-01, `renovacoes = 0` e `multa = 0`; os exemplares disponíveis do livro diminuem em 1; se o usuário era o primeiro da fila de reservas do livro, sai da fila.
- **Retorno:** o `id_emprestimo` criado.

**Ordem de validação:**

| # | Condição | Exceção |
|---|---|---|
| 1 | Usuário não cadastrado | `UsuarioNaoEncontrado` |
| 2 | Livro não cadastrado | `LivroNaoEncontrado` |
| 3 | Usuário bloqueado na data `data` (RN-05) | `UsuarioBloqueado` |
| 4 | Usuário já tem o limite de empréstimos ativos (RN-02) | `LimiteDeEmprestimosAtingido` |
| 5 | Exemplares disponíveis igual a 0, **ou** fila de reservas não vazia e o usuário não é o primeiro dela (RN-07) | `ExemplarIndisponivel` |

**Critérios de aceitação:**

- **CA-RF03-01** — Dado `L1` com quantidade 2 e `"u1"`, quando `b.emprestar(L1, "u1", date(2026, 3, 2))`, então retorna `1`, `b.consultar_emprestimo(1)["data_prevista"] == date(2026, 3, 16)` e `b.exemplares_disponiveis(L1) == 1`.
- **CA-RF03-02** — Dado `L1` com quantidade 2 e `"p1"`, quando `b.emprestar(L1, "p1", date(2026, 3, 2))`, então `b.consultar_emprestimo(1)["data_prevista"] == date(2026, 3, 30)`.
- **CA-RF03-03** — Dado `L1` com quantidade 5 e `"u1"`, quando são feitas, em sequência, `b.emprestar(L1, "u1", date(2026, 3, 2))`, `b.emprestar(L1, "xx", date(2026, 3, 2))` (lança `UsuarioNaoEncontrado`) e `b.emprestar(L1, "u1", date(2026, 3, 2))`, então o primeiro retorno é `1` e o terceiro é `2`.
- **CA-RF03-04** — Dado `L1` com quantidade 5 e `"u1"` com 3 empréstimos ativos de `L1` feitos em `date(2026, 3, 2)`, quando `b.emprestar(L1, "u1", date(2026, 3, 3))`, então é lançada `LimiteDeEmprestimosAtingido` e `b.exemplares_disponiveis(L1) == 2`.
- **CA-RF03-05** — Dado `L1` com quantidade 6 e `"p1"` com 5 empréstimos ativos de `L1` feitos em `date(2026, 3, 2)`, quando `b.emprestar(L1, "p1", date(2026, 3, 3))`, então é lançada `LimiteDeEmprestimosAtingido`.
- **CA-RF03-06** — Dado `L1` com quantidade 1 emprestado a `"u1"` em `date(2026, 3, 2)`, quando `b.emprestar(L1, "u2", date(2026, 3, 3))`, então é lançada `ExemplarIndisponivel`.
- **CA-RF03-07** — Dado `b` sem nenhum cadastro, quando `b.emprestar(L1, "u1", date(2026, 3, 2))`, então é lançada `UsuarioNaoEncontrado` (o usuário é verificado antes do livro).
- **CA-RF03-08** — Dado `"u1"` cadastrado e nenhum livro, quando `b.emprestar(L1, "u1", date(2026, 3, 2))`, então é lançada `LivroNaoEncontrado`.
- **CA-RF03-09** — Dado `L1` e `L2` com quantidade 2, e `"u1"` que pegou `L1` em `date(2026, 3, 2)` e devolveu em `date(2026, 3, 19)` (saldo devedor 300), quando `b.emprestar(L2, "u1", date(2026, 3, 20))`, então é lançada `UsuarioBloqueado`.
- **CA-RF03-10** — Dado `L1` e `L2` com quantidade 2, e `"u1"` com `L1` emprestado em `date(2026, 3, 2)` e não devolvido, quando `b.emprestar(L2, "u1", date(2026, 3, 17))`, então é lançada `UsuarioBloqueado`.
- **CA-RF03-11** — Na mesma situação do CA-RF03-10, quando `b.emprestar(L2, "u1", date(2026, 3, 16))`, então retorna `2` (na data prevista ainda não há atraso).
- **CA-RF03-12** — Dado `L1` com quantidade 5 e `"u1"` com 3 empréstimos ativos de `L1` feitos em `date(2026, 3, 2)`, quando `b.emprestar(L1, "u1", date(2026, 3, 20))`, então é lançada `UsuarioBloqueado`, e não `LimiteDeEmprestimosAtingido` (o bloqueio é verificado antes do limite).

### RF-04 — Devolver

- **Descrição:** O sistema deve registrar a devolução de um empréstimo e calcular a multa por atraso.
- **Prioridade:** Essencial
- **Operação:** `devolver(id_emprestimo: int, data: date) -> int`
- **Regras aplicadas:** RN-03, RN-04, RN-10
- **Pré-condições:** nenhuma além das validações.
- **Efeito:** `data_devolucao = data`; o exemplar volta a ficar disponível; `multa = min(dias de atraso × 100, 2000)`; a multa é somada ao saldo devedor do usuário.
- **Retorno:** a multa gerada, em centavos (0 quando não há atraso).

**Ordem de validação:**

| # | Condição | Exceção |
|---|---|---|
| 1 | Não existe empréstimo com esse id | `EmprestimoNaoEncontrado` |
| 2 | O empréstimo já foi devolvido | `EmprestimoJaDevolvido` |
| 3 | `data` anterior a `data_emprestimo` | `DadosInvalidos` |

**Critérios de aceitação** (situação inicial de todos: `L1` com quantidade 2, `"u1"` pegou `L1` em `date(2026, 3, 2)`, empréstimo id 1, data prevista `date(2026, 3, 16)`):

- **CA-RF04-01** — Quando `b.devolver(1, date(2026, 3, 16))`, então retorna `0`, `b.exemplares_disponiveis(L1) == 2` e `b.consultar_emprestimo(1)["data_devolucao"] == date(2026, 3, 16)`.
- **CA-RF04-02** — Quando `b.devolver(1, date(2026, 3, 19))`, então retorna `300` e `b.saldo_devedor("u1") == 300`.
- **CA-RF04-03** — Quando `b.devolver(1, date(2026, 4, 30))` (45 dias de atraso), então retorna `2000` e `b.saldo_devedor("u1") == 2000`.
- **CA-RF04-04** — Dado que o empréstimo 1 já foi devolvido, quando `b.devolver(1, date(2026, 3, 17))`, então é lançada `EmprestimoJaDevolvido`.
- **CA-RF04-05** — Quando `b.devolver(99, date(2026, 3, 10))`, então é lançada `EmprestimoNaoEncontrado`.
- **CA-RF04-06** — Quando `b.devolver(1, date(2026, 3, 1))`, então é lançada `DadosInvalidos` e `b.consultar_emprestimo(1)["data_devolucao"] is None`.
- **CA-RF04-07** — Dado também o empréstimo 2 de `L1` para `"u1"` em `date(2026, 3, 2)`, quando `b.devolver(1, date(2026, 3, 18))` e `b.devolver(2, date(2026, 3, 19))`, então os retornos são `200` e `300` e `b.saldo_devedor("u1") == 500`.

### RF-05 — Renovar

- **Descrição:** O sistema deve estender a data prevista de um empréstimo ativo.
- **Prioridade:** Importante
- **Operação:** `renovar(id_emprestimo: int, data: date) -> date`
- **Regras aplicadas:** RN-05, RN-06, RN-07, RN-10
- **Pré-condições:** nenhuma além das validações.
- **Efeito:** `data_prevista` passa a ser `data_prevista + prazo do tipo do usuário`; `renovacoes` aumenta em 1.
- **Retorno:** a nova `data_prevista`.

**Ordem de validação:**

| # | Condição | Exceção |
|---|---|---|
| 1 | Não existe empréstimo com esse id | `EmprestimoNaoEncontrado` |
| 2 | O empréstimo já foi devolvido | `EmprestimoJaDevolvido` |
| 3 | `data > data_prevista` (empréstimo atrasado) | `RenovacaoNaoPermitida` |
| 4 | `renovacoes == 2` | `RenovacaoNaoPermitida` |
| 5 | A fila de reservas do livro não está vazia | `RenovacaoNaoPermitida` |
| 6 | O usuário está bloqueado na data `data` (RN-05) | `RenovacaoNaoPermitida` |

**Critérios de aceitação** (situação inicial, salvo indicação: `L1` com quantidade 1, `"u1"` pegou `L1` em `date(2026, 3, 2)`, empréstimo id 1, data prevista `date(2026, 3, 16)`):

- **CA-RF05-01** — Quando `b.renovar(1, date(2026, 3, 10))`, então retorna `date(2026, 3, 30)` e `b.consultar_emprestimo(1)["renovacoes"] == 1`.
- **CA-RF05-02** — Quando `b.renovar(1, date(2026, 3, 10))` e depois `b.renovar(1, date(2026, 3, 20))`, então os retornos são `date(2026, 3, 30)` e `date(2026, 4, 13)`; uma terceira chamada `b.renovar(1, date(2026, 4, 1))` lança `RenovacaoNaoPermitida` e a data prevista continua `date(2026, 4, 13)`.
- **CA-RF05-03** — Quando `b.renovar(1, date(2026, 3, 17))`, então é lançada `RenovacaoNaoPermitida`.
- **CA-RF05-04** — Quando `b.renovar(1, date(2026, 3, 16))`, então retorna `date(2026, 3, 30)`.
- **CA-RF05-05** — Dado que `"u2"` reservou `L1`, quando `b.renovar(1, date(2026, 3, 10))`, então é lançada `RenovacaoNaoPermitida`.
- **CA-RF05-06** — Em vez da situação inicial: dado `L2` com quantidade 1 e `"p1"` que pegou `L2` em `date(2026, 3, 2)` (empréstimo id 1, data prevista `date(2026, 3, 30)`), quando `b.renovar(1, date(2026, 3, 10))`, então retorna `date(2026, 4, 27)`.
- **CA-RF05-07** — Dado que o empréstimo 1 foi devolvido em `date(2026, 3, 5)`, quando `b.renovar(1, date(2026, 3, 6))`, então é lançada `EmprestimoJaDevolvido`.

### RF-06 — Reservar

- **Descrição:** O sistema deve colocar um usuário no fim da fila de reservas de um livro sem exemplar livre.
- **Prioridade:** Importante
- **Operação:** `reservar(isbn: str, id_usuario: str) -> int`
- **Regras aplicadas:** RN-07, RN-10
- **Pré-condições:** nenhuma além das validações.
- **Efeito:** o usuário entra no fim da fila de reservas do livro.
- **Retorno:** a posição do usuário na fila (1 é o primeiro).

**Ordem de validação:**

| # | Condição | Exceção |
|---|---|---|
| 1 | Usuário não cadastrado | `UsuarioNaoEncontrado` |
| 2 | Livro não cadastrado | `LivroNaoEncontrado` |
| 3 | Exemplares disponíveis maior que 0 **e** fila de reservas vazia | `ReservaNaoPermitida` |
| 4 | O usuário já está na fila desse livro | `ReservaNaoPermitida` |
| 5 | O usuário tem empréstimo ativo desse livro | `ReservaNaoPermitida` |

**Critérios de aceitação** (situação inicial, salvo indicação: `L1` com quantidade 1 emprestado a `"u1"` em `date(2026, 3, 2)`, empréstimo id 1):

- **CA-RF06-01** — Quando `b.reservar(L1, "u2")` e depois `b.reservar(L1, "u3")`, então os retornos são `1` e `2`.
- **CA-RF06-02** — Dado `L2` com quantidade 1 e nenhum empréstimo, quando `b.reservar(L2, "u2")`, então é lançada `ReservaNaoPermitida`.
- **CA-RF06-03** — Dado que `"u2"` reservou `L1`, quando `b.reservar(L1, "u2")`, então é lançada `ReservaNaoPermitida`.
- **CA-RF06-04** — Quando `b.reservar(L1, "u1")`, então é lançada `ReservaNaoPermitida`.
- **CA-RF06-05** — Dado que `"u2"` e depois `"u3"` reservaram `L1` e que `b.devolver(1, date(2026, 3, 10))` foi feito, quando `b.emprestar(L1, "u3", date(2026, 3, 11))`, então é lançada `ExemplarIndisponivel`; e quando `b.emprestar(L1, "u2", date(2026, 3, 11))`, então retorna `2`.
- **CA-RF06-06** — Na situação final do CA-RF06-05, quando `"u2"` devolve o empréstimo 2 em `date(2026, 3, 12)` e `b.reservar(L1, "u4")` é chamado, então retorna `2` (`"u2"` saiu da fila ao emprestar; `"u3"` é o primeiro).

### RF-07 — Consultar exemplares disponíveis

- **Descrição:** O sistema deve informar quantos exemplares de um livro não estão emprestados.
- **Prioridade:** Essencial
- **Operação:** `exemplares_disponiveis(isbn: str) -> int`
- **Regras aplicadas:** RN-07
- **Retorno:** `quantidade` menos o número de empréstimos ativos do livro. Reservas não entram na conta.

**Ordem de validação:**

| # | Condição | Exceção |
|---|---|---|
| 1 | Livro não cadastrado | `LivroNaoEncontrado` |

**Critérios de aceitação:**

- **CA-RF07-01** — Dado `L1` com quantidade 3 e dois empréstimos ativos de `L1`, quando `b.exemplares_disponiveis(L1)`, então retorna `1`.
- **CA-RF07-02** — Na situação do CA-RF07-01, depois de devolver um dos empréstimos, `b.exemplares_disponiveis(L1)` retorna `2`.
- **CA-RF07-03** — Dado `b` sem livros, quando `b.exemplares_disponiveis(L1)`, então é lançada `LivroNaoEncontrado`.
- **CA-RF07-04** — Dado `L1` com quantidade 1 emprestado a `"u1"`, `"u2"` na fila de reservas e o empréstimo devolvido, quando `b.exemplares_disponiveis(L1)`, então retorna `1`.

### RF-08 — Consultar saldo devedor

- **Descrição:** O sistema deve informar o saldo devedor de um usuário.
- **Prioridade:** Essencial
- **Operação:** `saldo_devedor(id_usuario: str) -> int`
- **Regras aplicadas:** RN-03, RN-04
- **Retorno:** saldo em centavos.

**Ordem de validação:**

| # | Condição | Exceção |
|---|---|---|
| 1 | Usuário não cadastrado | `UsuarioNaoEncontrado` |

**Critérios de aceitação:**

- **CA-RF08-01** — Dado `"u1"` recém-cadastrado, quando `b.saldo_devedor("u1")`, então retorna `0`.
- **CA-RF08-02** — Dado `b` sem usuários, quando `b.saldo_devedor("u1")`, então é lançada `UsuarioNaoEncontrado`.

### RF-09 — Pagar multa

- **Descrição:** O sistema deve abater um pagamento do saldo devedor de um usuário.
- **Prioridade:** Essencial
- **Operação:** `pagar_multa(id_usuario: str, valor: int) -> int`
- **Regras aplicadas:** RN-05, RN-10
- **Efeito:** o saldo devedor diminui em `valor`.
- **Retorno:** o saldo restante, em centavos.

**Ordem de validação:**

| # | Condição | Exceção |
|---|---|---|
| 1 | Usuário não cadastrado | `UsuarioNaoEncontrado` |
| 2 | `valor` não é `int` ou é menor ou igual a 0 | `DadosInvalidos` |
| 3 | `valor` maior que o saldo devedor | `DadosInvalidos` |

**Critérios de aceitação** (situação inicial: `L1` com quantidade 2, `"u1"` pegou `L1` em `date(2026, 3, 2)` e devolveu em `date(2026, 3, 19)`, saldo devedor 300):

- **CA-RF09-01** — Quando `b.pagar_multa("u1", 100)`, então retorna `200` e `b.saldo_devedor("u1") == 200`.
- **CA-RF09-02** — Quando `b.pagar_multa("u1", 500)`, então é lançada `DadosInvalidos` e `b.saldo_devedor("u1") == 300`.
- **CA-RF09-03** — Quando `b.pagar_multa("u1", 0)`, então é lançada `DadosInvalidos`.
- **CA-RF09-04** — Quando `b.pagar_multa("u1", 300)` e depois `b.emprestar(L1, "u1", date(2026, 3, 20))`, então os retornos são `0` e `2` (o usuário deixou de estar bloqueado).

### RF-10 — Listar empréstimos atrasados

- **Descrição:** O sistema deve listar os empréstimos ativos atrasados numa data.
- **Prioridade:** Importante
- **Operação:** `emprestimos_atrasados(data: date) -> list[int]`
- **Regras aplicadas:** definição de "Atrasado" (Seção 2)
- **Retorno:** lista dos `id_emprestimo` dos empréstimos ativos com `data > data_prevista`, em ordem crescente. Lista vazia quando não há nenhum.

**Critérios de aceitação** (situação inicial: `L1` com quantidade 2; empréstimo 1 de `L1` para `"u1"` em `date(2026, 3, 2)`, data prevista `date(2026, 3, 16)`; empréstimo 2 de `L1` para `"p1"` em `date(2026, 3, 2)`, data prevista `date(2026, 3, 30)`):

- **CA-RF10-01** — Quando `b.emprestimos_atrasados(date(2026, 3, 20))`, então retorna `[1]`; e `b.emprestimos_atrasados(date(2026, 4, 1))` retorna `[1, 2]`.
- **CA-RF10-02** — Dado que o empréstimo 1 foi devolvido em `date(2026, 3, 25)`, quando `b.emprestimos_atrasados(date(2026, 4, 1))`, então retorna `[2]`.
- **CA-RF10-03** — Quando `b.emprestimos_atrasados(date(2026, 3, 16))`, então retorna `[]`.
- **CA-RF10-04** — Dado `b` sem empréstimos, quando `b.emprestimos_atrasados(date(2026, 3, 16))`, então retorna `[]`.

### RF-11 — Consultar empréstimo

- **Descrição:** O sistema deve devolver os dados de um empréstimo.
- **Prioridade:** Importante
- **Operação:** `consultar_emprestimo(id_emprestimo: int) -> dict`
- **Retorno:** um `dict` novo com **exatamente** as chaves `"id_emprestimo"`, `"isbn"`, `"id_usuario"`, `"data_emprestimo"`, `"data_prevista"`, `"data_devolucao"`, `"renovacoes"` e `"multa"`, com os valores e tipos da Seção 4.3. Alterar o `dict` retornado não altera o sistema.

**Ordem de validação:**

| # | Condição | Exceção |
|---|---|---|
| 1 | Não existe empréstimo com esse id | `EmprestimoNaoEncontrado` |

**Critérios de aceitação** (situação inicial: `L1` com quantidade 2, `"u1"` pegou `L1` em `date(2026, 3, 2)`, empréstimo id 1):

- **CA-RF11-01** — Quando `b.consultar_emprestimo(1)`, então retorna `{"id_emprestimo": 1, "isbn": L1, "id_usuario": "u1", "data_emprestimo": date(2026, 3, 2), "data_prevista": date(2026, 3, 16), "data_devolucao": None, "renovacoes": 0, "multa": 0}`.
- **CA-RF11-02** — Dado que o empréstimo 1 foi devolvido em `date(2026, 3, 18)`, quando `b.consultar_emprestimo(1)`, então `"data_devolucao"` é `date(2026, 3, 18)` e `"multa"` é `200`.
- **CA-RF11-03** — Quando `b.consultar_emprestimo(7)`, então é lançada `EmprestimoNaoEncontrado`.
- **CA-RF11-04** — Quando `d = b.consultar_emprestimo(1)`, `d["renovacoes"] = 99` e `b.consultar_emprestimo(1)` é chamado de novo, então a segunda consulta tem `"renovacoes"` igual a `0`.

---

## 8. Requisitos não funcionais

| ID | Requisito | Métrica / limite | Como verificar |
|---|---|---|---|
| RNF-01 | Usar apenas a biblioteca padrão | 0 importações de módulos fora da biblioteca padrão | Inspeção automática dos `import` do arquivo |
| RNF-02 | Hierarquia de exceções | 11 exceções, todas subclasses de `ErroBiblioteca` | Teste automático |
| RNF-03 | Desempenho | Cadastrar 2000 livros e 2000 usuários, fazer 2000 empréstimos e chamar `emprestimos_atrasados` 100 vezes em menos de 5 segundos | Teste automático cronometrado |
| RNF-04 | Isolamento entre instâncias | Duas instâncias de `Biblioteca` não compartilham estado | Teste automático |
| RNF-05 | Silêncio | Nenhuma operação escreve na saída padrão | Teste automático capturando a saída |

**Critérios de aceitação:**

- **CA-RNF01-01** — Todo módulo importado por `biblioteca.py` pertence à biblioteca padrão do Python.
- **CA-RNF02-01** — `DadosInvalidos`, `CadastroDuplicado`, `LivroNaoEncontrado`, `UsuarioNaoEncontrado`, `EmprestimoNaoEncontrado`, `EmprestimoJaDevolvido`, `UsuarioBloqueado`, `LimiteDeEmprestimosAtingido`, `ExemplarIndisponivel`, `RenovacaoNaoPermitida` e `ReservaNaoPermitida` são subclasses de `ErroBiblioteca`, que é subclasse de `Exception`.
- **CA-RNF03-01** — A carga do RNF-03 (para `i` de 1 a 2000: livro de ISBN `f"{i:013d}"` com quantidade 1, usuário `f"a{i}"` do tipo aluno, e empréstimo desse livro para esse usuário em `date(2026, 3, 2)`) termina em menos de 5 segundos, e `emprestimos_atrasados(date(2026, 3, 20))` retorna os ids de 1 a 2000.
- **CA-RNF04-01** — Dadas `b1 = Biblioteca()` e `b2 = Biblioteca()`, quando `b1.cadastrar_livro(L1, "Dom Casmurro", "Machado de Assis", 1)`, então `b2.exemplares_disponiveis(L1)` lança `LivroNaoEncontrado`.
- **CA-RNF05-01** — O cenário integrado da Seção 9 não escreve nada na saída padrão.

---

## 9. Cenário integrado

| Passo | Chamada | Resultado esperado |
|---|---|---|
| 1 | `b.cadastrar_livro(L1, "Dom Casmurro", "Machado de Assis", 1)` | `None` |
| 2 | `b.cadastrar_usuario("u1", "Ana", "aluno")` e `b.cadastrar_usuario("u2", "Bruno", "aluno")` | `None` |
| 3 | `b.emprestar(L1, "u1", date(2026, 3, 2))` | `1` |
| 4 | `b.reservar(L1, "u2")` | `1` |
| 5 | `b.renovar(1, date(2026, 3, 10))` | lança `RenovacaoNaoPermitida` (há fila) |
| 6 | `b.devolver(1, date(2026, 3, 18))` | `200` |
| 7 | `b.emprestar(L1, "u1", date(2026, 3, 18))` | lança `UsuarioBloqueado` (saldo 200) |
| 8 | `b.pagar_multa("u1", 200)` | `0` |
| 9 | `b.emprestar(L1, "u1", date(2026, 3, 18))` | lança `ExemplarIndisponivel` (`"u2"` é o primeiro da fila) |
| 10 | `b.emprestar(L1, "u2", date(2026, 3, 18))` | `2`; data prevista `date(2026, 4, 1)` |
| 11 | `b.emprestimos_atrasados(date(2026, 4, 2))` | `[2]` |

---

## 10. Matriz de rastreabilidade

| Requisito | Regras | Operação | Critérios de aceitação |
|---|---|---|---|
| RF-01 | RN-10 | `cadastrar_livro` | CA-RF01-01 a 05 |
| RF-02 | RN-10 | `cadastrar_usuario` | CA-RF02-01 a 04 |
| RF-03 | RN-01, RN-02, RN-05, RN-07, RN-08, RN-09, RN-10 | `emprestar` | CA-RF03-01 a 12 |
| RF-04 | RN-03, RN-04, RN-10 | `devolver` | CA-RF04-01 a 07 |
| RF-05 | RN-05, RN-06, RN-07, RN-10 | `renovar` | CA-RF05-01 a 07 |
| RF-06 | RN-07, RN-10 | `reservar` | CA-RF06-01 a 06 |
| RF-07 | RN-07 | `exemplares_disponiveis` | CA-RF07-01 a 04 |
| RF-08 | RN-03, RN-04 | `saldo_devedor` | CA-RF08-01 a 02 |
| RF-09 | RN-05, RN-10 | `pagar_multa` | CA-RF09-01 a 04 |
| RF-10 | — | `emprestimos_atrasados` | CA-RF10-01 a 04 |
| RF-11 | — | `consultar_emprestimo` | CA-RF11-01 a 04 |
| RNF-01 a 05 | RN-11 | — | CA-RNF01-01 a CA-RNF05-01 |

---

## 11. Questões em aberto

| # | Questão | Responsável | Decisão |
|---|---|---|---|
| — | Nenhuma | — | — |

---

## 12. Formato de entrega

Responda com um único bloco de código Python contendo o arquivo `biblioteca.py` completo. Não escreva nada fora do bloco.
