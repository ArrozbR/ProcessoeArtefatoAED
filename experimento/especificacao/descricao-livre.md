# Sistema de empréstimos da biblioteca escolar

Precisamos de um módulo Python (`biblioteca.py`, só biblioteca padrão, Python 3.10+) para controlar os empréstimos da biblioteca da escola. Tudo fica em memória, numa classe `Biblioteca`.

**Livros.** Cada livro é identificado pelo ISBN-13 (as pessoas costumam digitar com hífens) e tem título, autor e quantidade de exemplares. O ISBN precisa ser válido (dígito verificador do ISBN-13). Não pode haver dois livros com o mesmo ISBN.

**Usuários.** Os usuários têm um id, nome e tipo, que pode ser aluno ou professor. Aluno pode ficar com até 3 livros ao mesmo tempo, por 14 dias; professor, até 5 livros, por 28 dias.

**Empréstimos.** Para emprestar, o livro precisa ter exemplar disponível e o usuário não pode estar com livro atrasado nem devendo multa. Ninguém pode pegar dois exemplares do mesmo livro. Na devolução, cobra-se multa de R$ 2,00 por dia de atraso, limitada a R$ 50,00; a multa vai para o saldo devedor do usuário, que pode pagar aos poucos.

**Renovação.** O usuário pode renovar o empréstimo até 2 vezes, se não estiver atrasado e se ninguém tiver reservado o livro. O novo prazo conta a partir do dia da renovação.

**Reservas.** Quando não há exemplar disponível, o usuário pode entrar numa fila de reserva do livro. Quando o livro voltar, quem está na frente da fila tem prioridade para pegá-lo.

**Consultas.** A bibliotecária precisa pesquisar livros por parte do título ou do autor e ver a lista de empréstimos atrasados em uma data, para fazer a cobrança.

## Interface

Classes de dados (dataclasses):

- `Livro(isbn, titulo, autor, exemplares_total, exemplares_disponiveis)`
- `Usuario(id, nome, tipo, saldo_devedor)`
- `Emprestimo(id, isbn, usuario_id, data_emprestimo, data_prevista_devolucao, data_devolucao, renovacoes, multa)`

Exceções: `BibliotecaErro` (base), `DadosInvalidosErro`, `ISBNInvalidoErro` (um tipo de dado inválido), `DuplicadoErro`, `NaoEncontradoErro`, `IndisponivelErro`, `LimiteExcedidoErro`, `PendenciaErro`, `RenovacaoNegadaErro`, `OperacaoInvalidaErro`.

Métodos da classe `Biblioteca`:

- `cadastrar_livro(isbn, titulo, autor, exemplares)` → `Livro`
- `cadastrar_usuario(id, nome, tipo)` → `Usuario`
- `buscar_livro(isbn)`, `buscar_usuario(id)`, `buscar_emprestimo(id)`
- `pesquisar_livros(termo)` → lista de livros
- `emprestar(isbn, usuario_id, data)` → `Emprestimo`
- `devolver(emprestimo_id, data)` → valor da multa
- `renovar(emprestimo_id, data)` → `Emprestimo`
- `reservar(isbn, usuario_id)` → posição na fila
- `fila_reservas(isbn)` → lista de ids dos usuários na fila
- `pagar_multa(usuario_id, valor)` → saldo restante
- `listar_atrasados(data)` → lista de empréstimos

Use `Decimal` para dinheiro e `date` para datas.
