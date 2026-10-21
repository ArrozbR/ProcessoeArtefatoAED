"""Sistema de Empréstimos de Biblioteca."""

from __future__ import annotations

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
_MULTA_POR_DIA = 100
_MULTA_MAXIMA = 2000
_MAX_RENOVACOES = 2


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


class _Livro:
    __slots__ = ("isbn", "titulo", "autor", "quantidade", "ativos", "fila")

    def __init__(self, isbn: str, titulo: str, autor: str, quantidade: int):
        self.isbn = isbn
        self.titulo = titulo
        self.autor = autor
        self.quantidade = quantidade
        self.ativos = 0
        self.fila: deque[str] = deque()

    def disponiveis(self) -> int:
        return self.quantidade - self.ativos


class _Usuario:
    __slots__ = ("id_usuario", "nome", "tipo", "saldo_devedor", "ativos")

    def __init__(self, id_usuario: str, nome: str, tipo: str):
        self.id_usuario = id_usuario
        self.nome = nome
        self.tipo = tipo
        self.saldo_devedor = 0
        self.ativos: dict[int, "_Emprestimo"] = {}


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

    def ativo(self) -> bool:
        return self.data_devolucao is None


class Biblioteca:
    def __init__(self):
        self._livros: dict[str, _Livro] = {}
        self._usuarios: dict[str, _Usuario] = {}
        self._emprestimos: dict[int, _Emprestimo] = {}
        self._ativos: dict[int, _Emprestimo] = {}
        self._proximo_id = 1

    # ------------------------------------------------------------ auxiliares

    def _usuario(self, id_usuario) -> _Usuario:
        try:
            usuario = self._usuarios.get(id_usuario)
        except TypeError:
            usuario = None
        if usuario is None:
            raise UsuarioNaoEncontrado(f"Usuário não cadastrado: {id_usuario!r}")
        return usuario

    def _livro(self, isbn) -> _Livro:
        try:
            livro = self._livros.get(isbn)
        except TypeError:
            livro = None
        if livro is None:
            raise LivroNaoEncontrado(f"Livro não cadastrado: {isbn!r}")
        return livro

    def _emprestimo(self, id_emprestimo) -> _Emprestimo:
        try:
            emprestimo = self._emprestimos.get(id_emprestimo)
        except TypeError:
            emprestimo = None
        if emprestimo is None:
            raise EmprestimoNaoEncontrado(f"Empréstimo inexistente: {id_emprestimo!r}")
        return emprestimo

    @staticmethod
    def _bloqueado(usuario: _Usuario, data: date) -> bool:
        if usuario.saldo_devedor > 0:
            return True
        return any(data > e.data_prevista for e in usuario.ativos.values())

    # ------------------------------------------------------------ RF-01

    def cadastrar_livro(self, isbn: str, titulo: str, autor: str, quantidade: int) -> None:
        if not (isinstance(isbn, str) and len(isbn) == 13 and all(c in "0123456789" for c in isbn)):
            raise DadosInvalidos("ISBN deve ter exatamente 13 dígitos.")
        if not _texto_valido(titulo) or not _texto_valido(autor):
            raise DadosInvalidos("Título e autor não podem ser vazios.")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("Quantidade deve ser um inteiro maior ou igual a 1.")
        if isbn in self._livros:
            raise CadastroDuplicado(f"Livro já cadastrado: {isbn}")
        self._livros[isbn] = _Livro(isbn, titulo, autor, quantidade)

    # ------------------------------------------------------------ RF-02

    def cadastrar_usuario(self, id_usuario: str, nome: str, tipo: str) -> None:
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id_usuario não pode ser vazio.")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome não pode ser vazio.")
        if not isinstance(tipo, str) or tipo not in _PRAZO:
            raise DadosInvalidos('tipo deve ser "aluno" ou "professor".')
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
            raise LimiteDeEmprestimosAtingido(f"Limite de empréstimos atingido: {id_usuario}")
        if livro.disponiveis() <= 0 or (livro.fila and livro.fila[0] != id_usuario):
            raise ExemplarIndisponivel(f"Exemplar indisponível: {isbn}")

        id_emprestimo = self._proximo_id
        data_prevista = data + timedelta(days=_PRAZO[usuario.tipo])
        emprestimo = _Emprestimo(id_emprestimo, isbn, id_usuario, data, data_prevista)

        self._proximo_id += 1
        self._emprestimos[id_emprestimo] = emprestimo
        self._ativos[id_emprestimo] = emprestimo
        usuario.ativos[id_emprestimo] = emprestimo
        livro.ativos += 1
        if livro.fila and livro.fila[0] == id_usuario:
            livro.fila.popleft()
        return id_emprestimo

    # ------------------------------------------------------------ RF-04

    def devolver(self, id_emprestimo: int, data: date) -> int:
        emprestimo = self._emprestimo(id_emprestimo)
        if not emprestimo.ativo():
            raise EmprestimoJaDevolvido(f"Empréstimo já devolvido: {id_emprestimo}")
        if data < emprestimo.data_emprestimo:
            raise DadosInvalidos("Data de devolução anterior à data do empréstimo.")

        dias_atraso = max((data - emprestimo.data_prevista).days, 0)
        multa = min(dias_atraso * _MULTA_POR_DIA, _MULTA_MAXIMA)

        usuario = self._usuarios[emprestimo.id_usuario]
        livro = self._livros[emprestimo.isbn]

        emprestimo.data_devolucao = data
        emprestimo.multa = multa
        self._ativos.pop(emprestimo.id_emprestimo, None)
        usuario.ativos.pop(emprestimo.id_emprestimo, None)
        livro.ativos -= 1
        usuario.saldo_devedor += multa
        return multa

    # ------------------------------------------------------------ RF-05

    def renovar(self, id_emprestimo: int, data: date) -> date:
        emprestimo = self._emprestimo(id_emprestimo)
        if not emprestimo.ativo():
            raise EmprestimoJaDevolvido(f"Empréstimo já devolvido: {id_emprestimo}")
        if data > emprestimo.data_prevista:
            raise RenovacaoNaoPermitida("Empréstimo atrasado não pode ser renovado.")
        if emprestimo.renovacoes >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("Número máximo de renovações atingido.")
        livro = self._livros[emprestimo.isbn]
        if livro.fila:
            raise RenovacaoNaoPermitida("Há reservas para este livro.")
        usuario = self._usuarios[emprestimo.id_usuario]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("Usuário bloqueado.")

        emprestimo.data_prevista = emprestimo.data_prevista + timedelta(days=_PRAZO[usuario.tipo])
        emprestimo.renovacoes += 1
        return emprestimo.data_prevista

    # ------------------------------------------------------------ RF-06

    def reservar(self, isbn: str, id_usuario: str) -> int:
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if livro.disponiveis() > 0 and not livro.fila:
            raise ReservaNaoPermitida("Há exemplar disponível; não é preciso reservar.")
        if id_usuario in livro.fila:
            raise ReservaNaoPermitida("Usuário já está na fila deste livro.")
        if any(e.isbn == isbn for e in usuario.ativos.values()):
            raise ReservaNaoPermitida("Usuário já tem empréstimo ativo deste livro.")
        livro.fila.append(id_usuario)
        return len(livro.fila)

    # ------------------------------------------------------------ RF-07

    def exemplares_disponiveis(self, isbn: str) -> int:
        return self._livro(isbn).disponiveis()

    # ------------------------------------------------------------ RF-08

    def saldo_devedor(self, id_usuario: str) -> int:
        return self._usuario(id_usuario).saldo_devedor

    # ------------------------------------------------------------ RF-09

    def pagar_multa(self, id_usuario: str, valor: int) -> int:
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0:
            raise DadosInvalidos("Valor deve ser um inteiro positivo.")
        if valor > usuario.saldo_devedor:
            raise DadosInvalidos("Valor maior que o saldo devedor.")
        usuario.saldo_devedor -= valor
        return usuario.saldo_devedor

    # ------------------------------------------------------------ RF-10

    def emprestimos_atrasados(self, data: date) -> list[int]:
        return sorted(
            id_emp for id_emp, e in self._ativos.items() if data > e.data_prevista
        )

    # ------------------------------------------------------------ RF-11

    def consultar_emprestimo(self, id_emprestimo: int) -> dict:
        e = self._emprestimo(id_emprestimo)
        return {
            "id_emprestimo": e.id_emprestimo,
            "isbn": e.isbn,
            "id_usuario": e.id_usuario,
            "data_emprestimo": e.data_emprestimo,
            "data_prevista": e.data_prevista,
            "data_devolucao": e.data_devolucao,
            "renovacoes": e.renovacoes,
            "multa": e.multa,
        }
