"""Sistema de Empréstimos de Biblioteca (estado em memória)."""

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
_DIGITOS = set("0123456789")


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

    # ------------------------------------------------------------------ apoio

    def _usuario(self, id_usuario):
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado(id_usuario) from None

    def _livro(self, isbn):
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado(isbn) from None

    def _emprestimo(self, id_emprestimo):
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado(id_emprestimo) from None

    def _bloqueado(self, usuario, data):
        if usuario.saldo_devedor > 0:
            return True
        for id_emp in usuario.ativos:
            if data > self._emprestimos[id_emp].data_prevista:
                return True
        return False

    # ------------------------------------------------------------------ RF-01

    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
        if not isinstance(isbn, str) or len(isbn) != 13 or not set(isbn) <= _DIGITOS:
            raise DadosInvalidos("isbn inválido")
        if not _texto_valido(titulo) or not _texto_valido(autor):
            raise DadosInvalidos("título ou autor inválido")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("quantidade inválida")
        if isbn in self._livros:
            raise CadastroDuplicado(isbn)
        self._livros[isbn] = _Livro(isbn, titulo, autor, quantidade)

    # ------------------------------------------------------------------ RF-02

    def cadastrar_usuario(self, id_usuario, nome, tipo):
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id de usuário inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome inválido")
        if tipo not in ("aluno", "professor") or not isinstance(tipo, str):
            raise DadosInvalidos("tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(id_usuario)
        self._usuarios[id_usuario] = _Usuario(id_usuario, nome, tipo)

    # ------------------------------------------------------------------ RF-03

    def emprestar(self, isbn, id_usuario, data):
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(id_usuario)
        if len(usuario.ativos) >= _LIMITE[usuario.tipo]:
            raise LimiteDeEmprestimosAtingido(id_usuario)
        disponiveis = livro.quantidade - livro.ativos
        if disponiveis <= 0 or (livro.fila and livro.fila[0] != id_usuario):
            raise ExemplarIndisponivel(isbn)

        id_emp = self._proximo_id
        prevista = data + timedelta(days=_PRAZO[usuario.tipo])
        emprestimo = _Emprestimo(id_emp, isbn, id_usuario, data, prevista)
        self._emprestimos[id_emp] = emprestimo
        self._proximo_id += 1
        livro.ativos += 1
        usuario.ativos.add(id_emp)
        if livro.fila and livro.fila[0] == id_usuario:
            livro.fila.popleft()
        return id_emp

    # ------------------------------------------------------------------ RF-04

    def devolver(self, id_emprestimo, data):
        emprestimo = self._emprestimo(id_emprestimo)
        if emprestimo.data_devolucao is not None:
            raise EmprestimoJaDevolvido(id_emprestimo)
        if data < emprestimo.data_emprestimo:
            raise DadosInvalidos("data anterior à data do empréstimo")

        dias = (data - emprestimo.data_prevista).days
        if dias < 0:
            dias = 0
        multa = min(dias * _MULTA_DIA, _MULTA_MAX)

        emprestimo.data_devolucao = data
        emprestimo.multa = multa
        self._livros[emprestimo.isbn].ativos -= 1
        usuario = self._usuarios[emprestimo.id_usuario]
        usuario.ativos.discard(id_emprestimo)
        usuario.saldo_devedor += multa
        return multa

    # ------------------------------------------------------------------ RF-05

    def renovar(self, id_emprestimo, data):
        emprestimo = self._emprestimo(id_emprestimo)
        if emprestimo.data_devolucao is not None:
            raise EmprestimoJaDevolvido(id_emprestimo)
        if data > emprestimo.data_prevista:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emprestimo.renovacoes >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if self._livros[emprestimo.isbn].fila:
            raise RenovacaoNaoPermitida("há reservas para o livro")
        usuario = self._usuarios[emprestimo.id_usuario]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")

        emprestimo.data_prevista = emprestimo.data_prevista + timedelta(
            days=_PRAZO[usuario.tipo]
        )
        emprestimo.renovacoes += 1
        return emprestimo.data_prevista

    # ------------------------------------------------------------------ RF-06

    def reservar(self, isbn, id_usuario):
        self._usuario(id_usuario)
        livro = self._livro(isbn)
        if livro.quantidade - livro.ativos > 0 and not livro.fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in livro.fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for id_emp in self._usuarios[id_usuario].ativos:
            if self._emprestimos[id_emp].isbn == isbn:
                raise ReservaNaoPermitida("usuário já tem empréstimo ativo do livro")
        livro.fila.append(id_usuario)
        return len(livro.fila)

    # ------------------------------------------------------------------ RF-07

    def exemplares_disponiveis(self, isbn):
        livro = self._livro(isbn)
        return livro.quantidade - livro.ativos

    # ------------------------------------------------------------------ RF-08

    def saldo_devedor(self, id_usuario):
        return self._usuario(id_usuario).saldo_devedor

    # ------------------------------------------------------------------ RF-09

    def pagar_multa(self, id_usuario, valor):
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0:
            raise DadosInvalidos("valor inválido")
        if valor > usuario.saldo_devedor:
            raise DadosInvalidos("valor maior que o saldo devedor")
        usuario.saldo_devedor -= valor
        return usuario.saldo_devedor

    # ------------------------------------------------------------------ RF-10

    def emprestimos_atrasados(self, data):
        return sorted(
            e.id_emprestimo
            for e in self._emprestimos.values()
            if e.data_devolucao is None and data > e.data_prevista
        )

    # ------------------------------------------------------------------ RF-11

    def consultar_emprestimo(self, id_emprestimo):
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
