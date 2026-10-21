"""Sistema de Empréstimos de Biblioteca."""

from collections import deque
from datetime import date, timedelta


class ErroBiblioteca(Exception):
    """Base de todos os erros do sistema."""


class DadosInvalidos(ErroBiblioteca):
    pass


class CadastroDuplicado(ErroBiblioteca):
    pass


class LivroNaoEncontrado(ErroBiblioteca):
    pass


class UsuarioNaoEncontrado(ErroBiblioteca):
    pass


class EmprestimoNaoEncontrado(ErroBiblioteca):
    pass


class EmprestimoJaDevolvido(ErroBiblioteca):
    pass


class UsuarioBloqueado(ErroBiblioteca):
    pass


class LimiteDeEmprestimosAtingido(ErroBiblioteca):
    pass


class ExemplarIndisponivel(ErroBiblioteca):
    pass


class RenovacaoNaoPermitida(ErroBiblioteca):
    pass


class ReservaNaoPermitida(ErroBiblioteca):
    pass


_PRAZO = {"aluno": 14, "professor": 28}
_LIMITE = {"aluno": 3, "professor": 5}
_MULTA_DIA = 100
_MULTA_MAX = 2000
_MAX_RENOVACOES = 2
_DIGITOS = frozenset("0123456789")


def _texto_valido(valor):
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


class _Livro:
    __slots__ = ("isbn", "titulo", "autor", "quantidade", "ativos", "fila")

    def __init__(self, isbn, titulo, autor, quantidade):
        self.isbn = isbn
        self.titulo = titulo
        self.autor = autor
        self.quantidade = quantidade
        self.ativos = 0
        self.fila = deque()


class _Usuario:
    __slots__ = ("id_usuario", "nome", "tipo", "saldo_devedor", "ativos")

    def __init__(self, id_usuario, nome, tipo):
        self.id_usuario = id_usuario
        self.nome = nome
        self.tipo = tipo
        self.saldo_devedor = 0
        self.ativos = set()  # ids de empréstimos ativos


class _Emprestimo:
    __slots__ = (
        "id_emprestimo",
        "isbn",
        "id_usuario",
        "data_emprestimo",
        "data_prevista",
        "data_devolucao",
        "renovacoes",
        "multa",
    )

    def __init__(self, id_emprestimo, isbn, id_usuario, data_emprestimo, data_prevista):
        self.id_emprestimo = id_emprestimo
        self.isbn = isbn
        self.id_usuario = id_usuario
        self.data_emprestimo = data_emprestimo
        self.data_prevista = data_prevista
        self.data_devolucao = None
        self.renovacoes = 0
        self.multa = 0


class Biblioteca:
    def __init__(self):
        self._livros = {}
        self._usuarios = {}
        self._emprestimos = {}
        self._proximo_id = 1

    # ------------------------------------------------------------ auxiliares

    def _livro(self, isbn):
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado(f"Livro não encontrado: {isbn!r}") from None

    def _usuario(self, id_usuario):
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado(f"Usuário não encontrado: {id_usuario!r}") from None

    def _emprestimo(self, id_emprestimo):
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado(
                f"Empréstimo não encontrado: {id_emprestimo!r}"
            ) from None

    def _bloqueado(self, usuario, data):
        if usuario.saldo_devedor > 0:
            return True
        for id_emp in usuario.ativos:
            if data > self._emprestimos[id_emp].data_prevista:
                return True
        return False

    # ------------------------------------------------------------ RF-01

    def cadastrar_livro(self, isbn: str, titulo: str, autor: str, quantidade: int) -> None:
        if not isinstance(isbn, str) or len(isbn) != 13 or not set(isbn) <= _DIGITOS:
            raise DadosInvalidos("ISBN deve ter exatamente 13 dígitos")
        if not _texto_valido(titulo) or not _texto_valido(autor):
            raise DadosInvalidos("Título e autor são obrigatórios")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("Quantidade deve ser um inteiro maior ou igual a 1")
        if isbn in self._livros:
            raise CadastroDuplicado(f"Livro já cadastrado: {isbn}")
        self._livros[isbn] = _Livro(isbn, titulo, autor, quantidade)

    # ------------------------------------------------------------ RF-02

    def cadastrar_usuario(self, id_usuario: str, nome: str, tipo: str) -> None:
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id_usuario é obrigatório")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome é obrigatório")
        if tipo not in _PRAZO or not isinstance(tipo, str):
            raise DadosInvalidos('tipo deve ser "aluno" ou "professor"')
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(f"Usuário já cadastrado: {id_usuario}")
        self._usuarios[id_usuario] = _Usuario(id_usuario, nome, tipo)

    # ------------------------------------------------------------ RF-03

    def emprestar(self, isbn: str, id_usuario: str, data: date) -> int:
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(f"Usuário bloqueado: {id_usuario}")
        if len(usuario.ativos) >= _LIMITE[usuario.tipo]:
            raise LimiteDeEmprestimosAtingido(
                f"Usuário atingiu o limite de empréstimos: {id_usuario}"
            )
        disponiveis = livro.quantidade - livro.ativos
        if disponiveis <= 0 or (livro.fila and livro.fila[0] != id_usuario):
            raise ExemplarIndisponivel(f"Nenhum exemplar disponível para {id_usuario}")

        id_emp = self._proximo_id
        prevista = data + timedelta(days=_PRAZO[usuario.tipo])
        emp = _Emprestimo(id_emp, isbn, id_usuario, data, prevista)
        self._emprestimos[id_emp] = emp
        self._proximo_id += 1
        usuario.ativos.add(id_emp)
        livro.ativos += 1
        if livro.fila and livro.fila[0] == id_usuario:
            livro.fila.popleft()
        return id_emp

    # ------------------------------------------------------------ RF-04

    def devolver(self, id_emprestimo: int, data: date) -> int:
        emp = self._emprestimo(id_emprestimo)
        if emp.data_devolucao is not None:
            raise EmprestimoJaDevolvido(f"Empréstimo já devolvido: {id_emprestimo}")
        if data < emp.data_emprestimo:
            raise DadosInvalidos("Data de devolução anterior à data de empréstimo")

        dias = (data - emp.data_prevista).days
        if dias < 0:
            dias = 0
        multa = min(dias * _MULTA_DIA, _MULTA_MAX)

        emp.data_devolucao = data
        emp.multa = multa
        usuario = self._usuarios[emp.id_usuario]
        usuario.saldo_devedor += multa
        usuario.ativos.discard(emp.id_emprestimo)
        self._livros[emp.isbn].ativos -= 1
        return multa

    # ------------------------------------------------------------ RF-05

    def renovar(self, id_emprestimo: int, data: date) -> date:
        emp = self._emprestimo(id_emprestimo)
        if emp.data_devolucao is not None:
            raise EmprestimoJaDevolvido(f"Empréstimo já devolvido: {id_emprestimo}")
        if data > emp.data_prevista:
            raise RenovacaoNaoPermitida("Empréstimo atrasado")
        if emp.renovacoes >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("Limite de renovações atingido")
        if self._livros[emp.isbn].fila:
            raise RenovacaoNaoPermitida("Há reservas para este livro")
        usuario = self._usuarios[emp.id_usuario]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("Usuário bloqueado")

        emp.data_prevista = emp.data_prevista + timedelta(days=_PRAZO[usuario.tipo])
        emp.renovacoes += 1
        return emp.data_prevista

    # ------------------------------------------------------------ RF-06

    def reservar(self, isbn: str, id_usuario: str) -> int:
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if livro.quantidade - livro.ativos > 0 and not livro.fila:
            raise ReservaNaoPermitida("Há exemplar disponível")
        if id_usuario in livro.fila:
            raise ReservaNaoPermitida("Usuário já está na fila")
        for id_emp in usuario.ativos:
            if self._emprestimos[id_emp].isbn == isbn:
                raise ReservaNaoPermitida("Usuário já tem empréstimo ativo deste livro")
        livro.fila.append(id_usuario)
        return len(livro.fila)

    # ------------------------------------------------------------ RF-07

    def exemplares_disponiveis(self, isbn: str) -> int:
        livro = self._livro(isbn)
        return livro.quantidade - livro.ativos

    # ------------------------------------------------------------ RF-08

    def saldo_devedor(self, id_usuario: str) -> int:
        return self._usuario(id_usuario).saldo_devedor

    # ------------------------------------------------------------ RF-09

    def pagar_multa(self, id_usuario: str, valor: int) -> int:
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0:
            raise DadosInvalidos("Valor deve ser um inteiro positivo")
        if valor > usuario.saldo_devedor:
            raise DadosInvalidos("Valor maior que o saldo devedor")
        usuario.saldo_devedor -= valor
        return usuario.saldo_devedor

    # ------------------------------------------------------------ RF-10

    def emprestimos_atrasados(self, data: date) -> list[int]:
        return sorted(
            emp.id_emprestimo
            for emp in self._emprestimos.values()
            if emp.data_devolucao is None and data > emp.data_prevista
        )

    # ------------------------------------------------------------ RF-11

    def consultar_emprestimo(self, id_emprestimo: int) -> dict:
        emp = self._emprestimo(id_emprestimo)
        return {
            "id_emprestimo": emp.id_emprestimo,
            "isbn": emp.isbn,
            "id_usuario": emp.id_usuario,
            "data_emprestimo": emp.data_emprestimo,
            "data_prevista": emp.data_prevista,
            "data_devolucao": emp.data_devolucao,
            "renovacoes": emp.renovacoes,
            "multa": emp.multa,
        }
