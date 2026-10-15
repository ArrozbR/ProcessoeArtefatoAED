"""Implementação de referência do ERS-BIB-001, escrita por humanos.

Serve apenas para validar o oráculo de testes (fase F5 do processo PRECISA):
ela DEVE obter 100% de conformidade. Nunca é mostrada à LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

CENTAVOS = Decimal("0.01")
MULTA_DIARIA = Decimal("2.00")
TETO_MULTA = Decimal("50.00")
MAX_RENOVACOES = 2
PARAMETROS = {"ALUNO": (3, 14), "PROFESSOR": (5, 28)}  # tipo: (limite, prazo em dias)


def _dinheiro(valor: Decimal) -> Decimal:
    return Decimal(valor).quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def _normalizar_isbn(isbn: str) -> str:
    return isbn.replace("-", "").replace(" ", "")


def _isbn_valido(isbn: str) -> bool:
    if len(isbn) != 13 or not all(c in "0123456789" for c in isbn):
        return False
    soma = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(isbn[:12]))
    return (10 - soma % 10) % 10 == int(isbn[12])


@dataclass
class Livro:
    isbn: str
    titulo: str
    autor: str
    exemplares_total: int
    exemplares_disponiveis: int


@dataclass
class Usuario:
    id: str
    nome: str
    tipo: str
    saldo_devedor: Decimal = Decimal("0.00")


@dataclass
class Emprestimo:
    id: int
    isbn: str
    usuario_id: str
    data_emprestimo: date
    data_prevista_devolucao: date
    data_devolucao: date | None = None
    renovacoes: int = 0
    multa: Decimal = Decimal("0.00")


class BibliotecaErro(Exception):
    pass


class DadosInvalidosErro(BibliotecaErro):
    pass


class ISBNInvalidoErro(DadosInvalidosErro):
    pass


class DuplicadoErro(BibliotecaErro):
    pass


class NaoEncontradoErro(BibliotecaErro):
    pass


class IndisponivelErro(BibliotecaErro):
    pass


class LimiteExcedidoErro(BibliotecaErro):
    pass


class PendenciaErro(BibliotecaErro):
    pass


class RenovacaoNegadaErro(BibliotecaErro):
    pass


class OperacaoInvalidaErro(BibliotecaErro):
    pass


class Biblioteca:
    """Acervo, usuários, empréstimos e reservas de uma biblioteca, em memória."""

    def __init__(self) -> None:
        self._livros: dict[str, Livro] = {}
        self._usuarios: dict[str, Usuario] = {}
        self._emprestimos: dict[int, Emprestimo] = {}
        self._filas: dict[str, list[str]] = {}
        self._proximo_id = 1

    # ----- auxiliares -------------------------------------------------------

    def _ativos(self, usuario_id: str) -> list[Emprestimo]:
        return [e for e in self._emprestimos.values()
                if e.usuario_id == usuario_id and e.data_devolucao is None]

    def _tem_pendencia(self, usuario: Usuario, data: date) -> bool:
        if usuario.saldo_devedor > Decimal("0.00"):
            return True
        return any(data > e.data_prevista_devolucao for e in self._ativos(usuario.id))

    def _emprestimo(self, emprestimo_id: int) -> Emprestimo:
        if emprestimo_id not in self._emprestimos:
            raise NaoEncontradoErro(f"Empréstimo {emprestimo_id} não encontrado.")
        return self._emprestimos[emprestimo_id]

    # ----- RF-01 / RF-02 ----------------------------------------------------

    def cadastrar_livro(self, isbn: str, titulo: str, autor: str, exemplares: int) -> Livro:
        """Cadastra um livro com todos os exemplares disponíveis (RF-01)."""
        isbn = _normalizar_isbn(isbn)
        if not _isbn_valido(isbn):
            raise ISBNInvalidoErro(f"ISBN inválido: {isbn!r}.")
        titulo, autor = titulo.strip(), autor.strip()
        if not titulo:
            raise DadosInvalidosErro("Título vazio.")
        if not autor:
            raise DadosInvalidosErro("Autor vazio.")
        if type(exemplares) is not int or exemplares < 1:
            raise DadosInvalidosErro("Quantidade de exemplares deve ser inteiro ≥ 1.")
        if isbn in self._livros:
            raise DuplicadoErro(f"Livro {isbn} já cadastrado.")
        livro = Livro(isbn, titulo, autor, exemplares, exemplares)
        self._livros[isbn] = livro
        return livro

    def cadastrar_usuario(self, id: str, nome: str, tipo: str) -> Usuario:
        """Cadastra um usuário com saldo devedor zero (RF-02)."""
        id, nome, tipo = id.strip(), nome.strip(), tipo.strip().upper()
        if not id:
            raise DadosInvalidosErro("Id vazio.")
        if not nome:
            raise DadosInvalidosErro("Nome vazio.")
        if tipo not in PARAMETROS:
            raise DadosInvalidosErro(f"Tipo de usuário inválido: {tipo!r}.")
        if id in self._usuarios:
            raise DuplicadoErro(f"Usuário {id} já cadastrado.")
        usuario = Usuario(id, nome, tipo, Decimal("0.00"))
        self._usuarios[id] = usuario
        return usuario

    # ----- RF-03 / RF-04 ----------------------------------------------------

    def buscar_livro(self, isbn: str) -> Livro:
        """Retorna o livro pelo ISBN (RF-03)."""
        isbn = _normalizar_isbn(isbn)
        if isbn not in self._livros:
            raise NaoEncontradoErro(f"Livro {isbn} não encontrado.")
        return self._livros[isbn]

    def buscar_usuario(self, id: str) -> Usuario:
        """Retorna o usuário pelo id (RF-03)."""
        id = id.strip()
        if id not in self._usuarios:
            raise NaoEncontradoErro(f"Usuário {id} não encontrado.")
        return self._usuarios[id]

    def buscar_emprestimo(self, id: int) -> Emprestimo:
        """Retorna o empréstimo pelo id (RF-03)."""
        return self._emprestimo(id)

    def pesquisar_livros(self, termo: str) -> list[Livro]:
        """Pesquisa livros por trecho do título ou do autor (RF-04)."""
        termo = termo.strip().casefold()
        achados = [l for l in self._livros.values()
                   if not termo or termo in l.titulo.casefold() or termo in l.autor.casefold()]
        return sorted(achados, key=lambda l: (l.titulo.casefold(), l.isbn))

    # ----- RF-05 a RF-07 ----------------------------------------------------

    def emprestar(self, isbn: str, usuario_id: str, data: date) -> Emprestimo:
        """Registra o empréstimo de um exemplar (RF-05, RF-09)."""
        livro = self.buscar_livro(isbn)
        usuario = self.buscar_usuario(usuario_id)
        if self._tem_pendencia(usuario, data):
            raise PendenciaErro(f"Usuário {usuario.id} possui pendência.")
        ativos = self._ativos(usuario.id)
        if any(e.isbn == livro.isbn for e in ativos):
            raise OperacaoInvalidaErro("Usuário já possui empréstimo ativo deste livro.")
        limite, prazo = PARAMETROS[usuario.tipo]
        if len(ativos) >= limite:
            raise LimiteExcedidoErro(f"Limite de {limite} empréstimos atingido.")
        if livro.exemplares_disponiveis == 0:
            raise IndisponivelErro("Nenhum exemplar disponível.")
        fila = self._filas.get(livro.isbn, [])
        if fila and fila[0] != usuario.id:
            raise IndisponivelErro("Exemplar reservado para outro usuário.")

        emprestimo = Emprestimo(self._proximo_id, livro.isbn, usuario.id, data,
                                data + timedelta(days=prazo), None, 0, Decimal("0.00"))
        self._proximo_id += 1
        livro.exemplares_disponiveis -= 1
        if fila:
            fila.pop(0)
        self._emprestimos[emprestimo.id] = emprestimo
        return emprestimo

    def devolver(self, emprestimo_id: int, data: date) -> Decimal:
        """Registra a devolução e retorna a multa (RF-06)."""
        emprestimo = self._emprestimo(emprestimo_id)
        if emprestimo.data_devolucao is not None:
            raise OperacaoInvalidaErro("Empréstimo já devolvido.")
        if data < emprestimo.data_emprestimo:
            raise DadosInvalidosErro("Data de devolução anterior ao empréstimo.")
        dias = max(0, (data - emprestimo.data_prevista_devolucao).days)
        multa = _dinheiro(min(dias * MULTA_DIARIA, TETO_MULTA))
        emprestimo.data_devolucao = data
        emprestimo.multa = multa
        usuario = self._usuarios[emprestimo.usuario_id]
        usuario.saldo_devedor = _dinheiro(usuario.saldo_devedor + multa)
        self._livros[emprestimo.isbn].exemplares_disponiveis += 1
        return multa

    def renovar(self, emprestimo_id: int, data: date) -> Emprestimo:
        """Renova um empréstimo ativo (RF-07)."""
        emprestimo = self._emprestimo(emprestimo_id)
        if emprestimo.data_devolucao is not None:
            raise OperacaoInvalidaErro("Empréstimo já devolvido.")
        if data < emprestimo.data_emprestimo:
            raise DadosInvalidosErro("Data de renovação anterior ao empréstimo.")
        if data > emprestimo.data_prevista_devolucao:
            raise RenovacaoNegadaErro("Empréstimo atrasado.")
        if emprestimo.renovacoes >= MAX_RENOVACOES:
            raise RenovacaoNegadaErro("Limite de renovações atingido.")
        if self._filas.get(emprestimo.isbn):
            raise RenovacaoNegadaErro("Há reservas para este livro.")
        _, prazo = PARAMETROS[self._usuarios[emprestimo.usuario_id].tipo]
        emprestimo.data_prevista_devolucao = data + timedelta(days=prazo)
        emprestimo.renovacoes += 1
        return emprestimo

    # ----- RF-08 / RF-09 ----------------------------------------------------

    def reservar(self, isbn: str, usuario_id: str) -> int:
        """Coloca o usuário na fila de reservas do livro (RF-08)."""
        livro = self.buscar_livro(isbn)
        usuario = self.buscar_usuario(usuario_id)
        if any(e.isbn == livro.isbn for e in self._ativos(usuario.id)):
            raise OperacaoInvalidaErro("Usuário já está com um exemplar deste livro.")
        fila = self._filas.get(livro.isbn, [])
        if usuario.id in fila:
            raise DuplicadoErro("Usuário já está na fila.")
        if livro.exemplares_disponiveis > 0 and not fila:
            raise OperacaoInvalidaErro("Livro disponível; reserva desnecessária.")
        self._filas.setdefault(livro.isbn, []).append(usuario.id)
        return len(self._filas[livro.isbn])

    def fila_reservas(self, isbn: str) -> list[str]:
        """Retorna os ids da fila de reservas do livro, em ordem (RF-09)."""
        livro = self.buscar_livro(isbn)
        return list(self._filas.get(livro.isbn, []))

    # ----- RF-10 / RF-11 ----------------------------------------------------

    def pagar_multa(self, usuario_id: str, valor: Decimal) -> Decimal:
        """Abate um pagamento do saldo devedor e retorna o novo saldo (RF-10)."""
        usuario = self.buscar_usuario(usuario_id)
        if valor <= Decimal("0"):
            raise DadosInvalidosErro("Valor deve ser positivo.")
        if valor > usuario.saldo_devedor:
            raise DadosInvalidosErro("Valor maior que o saldo devedor.")
        usuario.saldo_devedor = _dinheiro(usuario.saldo_devedor - valor)
        return usuario.saldo_devedor

    def listar_atrasados(self, data: date) -> list[Emprestimo]:
        """Lista os empréstimos ativos atrasados na data (RF-11)."""
        atrasados = [e for e in self._emprestimos.values()
                     if e.data_devolucao is None and data > e.data_prevista_devolucao]
        return sorted(atrasados, key=lambda e: (e.data_prevista_devolucao, e.id))
