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


PRAZOS = {"aluno": 14, "professor": 28}
LIMITES = {"aluno": 3, "professor": 5}
MULTA_POR_DIA = 100
MULTA_MAXIMA = 2000
MAX_RENOVACOES = 2
_DIGITOS = set("0123456789")


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor) -> bool:
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
        self.ativos = {}  # id_emprestimo -> _Emprestimo


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
        self._ativos = {}  # id_emprestimo -> _Emprestimo (somente ativos)
        self._proximo_id = 1

    # ------------------------------------------------------------ auxiliares

    def _obter_usuario(self, id_usuario) -> _Usuario:
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado(f"Usuário não cadastrado: {id_usuario!r}")

    def _obter_livro(self, isbn) -> _Livro:
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado(f"Livro não cadastrado: {isbn!r}")

    def _obter_emprestimo(self, id_emprestimo) -> _Emprestimo:
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado(f"Empréstimo inexistente: {id_emprestimo!r}")

    @staticmethod
    def _bloqueado(usuario: _Usuario, data: date) -> bool:
        if usuario.saldo_devedor > 0:
            return True
        for emp in usuario.ativos.values():
            if data > emp.data_prevista:
                return True
        return False

    # ------------------------------------------------------------ RF-01

    def cadastrar_livro(self, isbn: str, titulo: str, autor: str, quantidade: int) -> None:
        if not (isinstance(isbn, str) and len(isbn) == 13 and all(c in _DIGITOS for c in isbn)):
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
        if not isinstance(tipo, str) or tipo not in PRAZOS:
            raise DadosInvalidos('tipo deve ser "aluno" ou "professor".')
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(f"Usuário já cadastrado: {id_usuario}")
        self._usuarios[id_usuario] = _Usuario(id_usuario, nome, tipo)

    # ------------------------------------------------------------ RF-03

    def emprestar(self, isbn: str, id_usuario: str, data: date) -> int:
        usuario = self._obter_usuario(id_usuario)
        livro = self._obter_livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(f"Usuário bloqueado: {id_usuario}")
        if len(usuario.ativos) >= LIMITES[usuario.tipo]:
            raise LimiteDeEmprestimosAtingido(f"Limite de empréstimos atingido: {id_usuario}")
        disponiveis = livro.quantidade - livro.ativos
        if disponiveis <= 0 or (livro.fila and livro.fila[0] != id_usuario):
            raise ExemplarIndisponivel(f"Exemplar indisponível: {isbn}")

        id_emprestimo = self._proximo_id
        data_prevista = data + timedelta(days=PRAZOS[usuario.tipo])
        emp = _Emprestimo(id_emprestimo, isbn, id_usuario, data, data_prevista)

        self._proximo_id += 1
        self._emprestimos[id_emprestimo] = emp
        self._ativos[id_emprestimo] = emp
        usuario.ativos[id_emprestimo] = emp
        livro.ativos += 1
        if livro.fila and livro.fila[0] == id_usuario:
            livro.fila.popleft()
        return id_emprestimo

    # ------------------------------------------------------------ RF-04

    def devolver(self, id_emprestimo: int, data: date) -> int:
        emp = self._obter_emprestimo(id_emprestimo)
        if emp.data_devolucao is not None:
            raise EmprestimoJaDevolvido(f"Empréstimo já devolvido: {id_emprestimo}")
        if data < emp.data_emprestimo:
            raise DadosInvalidos("Data de devolução anterior à data do empréstimo.")

        dias_atraso = (data - emp.data_prevista).days
        if dias_atraso < 0:
            dias_atraso = 0
        multa = min(dias_atraso * MULTA_POR_DIA, MULTA_MAXIMA)

        usuario = self._usuarios[emp.id_usuario]
        livro = self._livros[emp.isbn]

        emp.data_devolucao = data
        emp.multa = multa
        del self._ativos[emp.id_emprestimo]
        del usuario.ativos[emp.id_emprestimo]
        livro.ativos -= 1
        usuario.saldo_devedor += multa
        return multa

    # ------------------------------------------------------------ RF-05

    def renovar(self, id_emprestimo: int, data: date) -> date:
        emp = self._obter_emprestimo(id_emprestimo)
        if emp.data_devolucao is not None:
            raise EmprestimoJaDevolvido(f"Empréstimo já devolvido: {id_emprestimo}")
        if data > emp.data_prevista:
            raise RenovacaoNaoPermitida("Empréstimo atrasado não pode ser renovado.")
        if emp.renovacoes >= MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("Limite de renovações atingido.")
        livro = self._livros[emp.isbn]
        if livro.fila:
            raise RenovacaoNaoPermitida("Há reservas para este livro.")
        usuario = self._usuarios[emp.id_usuario]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("Usuário bloqueado.")

        emp.data_prevista = emp.data_prevista + timedelta(days=PRAZOS[usuario.tipo])
        emp.renovacoes += 1
        return emp.data_prevista

    # ------------------------------------------------------------ RF-06

    def reservar(self, isbn: str, id_usuario: str) -> int:
        self._obter_usuario(id_usuario)
        livro = self._obter_livro(isbn)
        if livro.quantidade - livro.ativos > 0 and not livro.fila:
            raise ReservaNaoPermitida("Há exemplar disponível; não é preciso reservar.")
        if id_usuario in livro.fila:
            raise ReservaNaoPermitida("Usuário já está na fila deste livro.")
        usuario = self._usuarios[id_usuario]
        for emp in usuario.ativos.values():
            if emp.isbn == isbn:
                raise ReservaNaoPermitida("Usuário já tem empréstimo ativo deste livro.")
        livro.fila.append(id_usuario)
        return len(livro.fila)

    # ------------------------------------------------------------ RF-07

    def exemplares_disponiveis(self, isbn: str) -> int:
        livro = self._obter_livro(isbn)
        return livro.quantidade - livro.ativos

    # ------------------------------------------------------------ RF-08

    def saldo_devedor(self, id_usuario: str) -> int:
        return self._obter_usuario(id_usuario).saldo_devedor

    # ------------------------------------------------------------ RF-09

    def pagar_multa(self, id_usuario: str, valor: int) -> int:
        usuario = self._obter_usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0:
            raise DadosInvalidos("Valor deve ser um inteiro maior que 0.")
        if valor > usuario.saldo_devedor:
            raise DadosInvalidos("Valor maior que o saldo devedor.")
        usuario.saldo_devedor -= valor
        return usuario.saldo_devedor

    # ------------------------------------------------------------ RF-10

    def emprestimos_atrasados(self, data: date) -> list[int]:
        return sorted(
            id_emp for id_emp, emp in self._ativos.items() if data > emp.data_prevista
        )

    # ------------------------------------------------------------ RF-11

    def consultar_emprestimo(self, id_emprestimo: int) -> dict:
        emp = self._obter_emprestimo(id_emprestimo)
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
