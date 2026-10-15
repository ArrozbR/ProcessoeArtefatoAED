"""Sistema de Empréstimos de Biblioteca (em memória)."""

from __future__ import annotations

import datetime
from collections import deque


class ErroBiblioteca(Exception):
    pass


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


_PRAZOS = {"aluno": 14, "professor": 28}
_LIMITES = {"aluno": 3, "professor": 5}
_MULTA_DIA = 100
_MULTA_TETO = 2000
_MAX_RENOVACOES = 2
_DIGITOS = frozenset("0123456789")


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _data_valida(valor) -> bool:
    return isinstance(valor, datetime.date) and not isinstance(valor, datetime.datetime)


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
    __slots__ = ("id_usuario", "nome", "tipo", "saldo", "ativos")

    def __init__(self, id_usuario, nome, tipo):
        self.id_usuario = id_usuario
        self.nome = nome
        self.tipo = tipo
        self.saldo = 0
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
        self._livros: dict[str, _Livro] = {}
        self._usuarios: dict[str, _Usuario] = {}
        self._emprestimos: dict[int, _Emprestimo] = {}
        self._ativos: dict[int, _Emprestimo] = {}
        self._proximo_id = 1

    # ------------------------------------------------------------------ helpers

    def _obter_livro(self, isbn) -> _Livro:
        try:
            livro = self._livros.get(isbn)
        except TypeError:
            livro = None
        if livro is None:
            raise LivroNaoEncontrado(f"Livro não encontrado: {isbn!r}")
        return livro

    def _obter_usuario(self, id_usuario) -> _Usuario:
        try:
            usuario = self._usuarios.get(id_usuario)
        except TypeError:
            usuario = None
        if usuario is None:
            raise UsuarioNaoEncontrado(f"Usuário não encontrado: {id_usuario!r}")
        return usuario

    def _obter_emprestimo(self, id_emprestimo) -> _Emprestimo:
        try:
            emp = self._emprestimos.get(id_emprestimo)
        except TypeError:
            emp = None
        if emp is None or isinstance(id_emprestimo, bool):
            raise EmprestimoNaoEncontrado(f"Empréstimo não encontrado: {id_emprestimo!r}")
        return emp

    @staticmethod
    def _bloqueado(usuario: _Usuario, data) -> bool:
        if usuario.saldo > 0:
            return True
        for emp in usuario.ativos.values():
            if emp.data_prevista < data:
                return True
        return False

    # ---------------------------------------------------------------- cadastros

    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
        if not (isinstance(isbn, str) and len(isbn) == 13 and set(isbn) <= _DIGITOS):
            raise DadosInvalidos("ISBN deve ter exatamente 13 dígitos")
        if not _texto_valido(titulo):
            raise DadosInvalidos("Título inválido")
        if not _texto_valido(autor):
            raise DadosInvalidos("Autor inválido")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("Quantidade deve ser um inteiro maior ou igual a 1")
        if isbn in self._livros:
            raise CadastroDuplicado(f"ISBN já cadastrado: {isbn}")
        self._livros[isbn] = _Livro(isbn, titulo, autor, quantidade)

    def cadastrar_usuario(self, id_usuario, nome, tipo):
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("Id de usuário inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("Nome inválido")
        if not isinstance(tipo, str) or tipo not in _PRAZOS:
            raise DadosInvalidos("Tipo deve ser 'aluno' ou 'professor'")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(f"Usuário já cadastrado: {id_usuario}")
        self._usuarios[id_usuario] = _Usuario(id_usuario, nome, tipo)

    # -------------------------------------------------------------- empréstimos

    def emprestar(self, isbn, id_usuario, data):
        usuario = self._obter_usuario(id_usuario)
        livro = self._obter_livro(isbn)
        if not _data_valida(data):
            raise DadosInvalidos("Data inválida")
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(f"Usuário bloqueado: {id_usuario}")
        if len(usuario.ativos) >= _LIMITES[usuario.tipo]:
            raise LimiteDeEmprestimosAtingido(f"Limite atingido: {id_usuario}")
        if livro.ativos >= livro.quantidade:
            raise ExemplarIndisponivel(f"Sem exemplares disponíveis: {isbn}")
        primeiro_da_fila = False
        if livro.fila:
            if livro.fila[0] != id_usuario:
                raise ExemplarIndisponivel(f"Exemplar reservado para outro usuário: {isbn}")
            primeiro_da_fila = True

        id_emp = self._proximo_id
        prevista = data + datetime.timedelta(days=_PRAZOS[usuario.tipo])
        emp = _Emprestimo(id_emp, isbn, id_usuario, data, prevista)

        if primeiro_da_fila:
            livro.fila.popleft()
        self._proximo_id += 1
        self._emprestimos[id_emp] = emp
        self._ativos[id_emp] = emp
        usuario.ativos[id_emp] = emp
        livro.ativos += 1
        return id_emp

    def devolver(self, id_emprestimo, data):
        emp = self._obter_emprestimo(id_emprestimo)
        if emp.data_devolucao is not None:
            raise EmprestimoJaDevolvido(f"Empréstimo já devolvido: {id_emprestimo}")
        if not _data_valida(data) or data < emp.data_emprestimo:
            raise DadosInvalidos("Data de devolução inválida")

        atraso = (data - emp.data_prevista).days
        multa = min(max(atraso, 0) * _MULTA_DIA, _MULTA_TETO)

        usuario = self._usuarios[emp.id_usuario]
        livro = self._livros[emp.isbn]
        emp.data_devolucao = data
        emp.multa = multa
        usuario.saldo += multa
        del usuario.ativos[emp.id_emprestimo]
        del self._ativos[emp.id_emprestimo]
        livro.ativos -= 1
        return multa

    def renovar(self, id_emprestimo, data):
        emp = self._obter_emprestimo(id_emprestimo)
        if emp.data_devolucao is not None:
            raise EmprestimoJaDevolvido(f"Empréstimo já devolvido: {id_emprestimo}")
        if not _data_valida(data):
            raise DadosInvalidos("Data inválida")
        usuario = self._usuarios[emp.id_usuario]
        livro = self._livros[emp.isbn]
        if emp.data_prevista < data:
            raise RenovacaoNaoPermitida("Empréstimo atrasado")
        if emp.renovacoes >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("Limite de renovações atingido")
        if livro.fila:
            raise RenovacaoNaoPermitida("Livro possui fila de reservas")
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("Usuário bloqueado")

        emp.data_prevista = emp.data_prevista + datetime.timedelta(days=_PRAZOS[usuario.tipo])
        emp.renovacoes += 1
        return emp.data_prevista

    # ----------------------------------------------------------------- reservas

    def reservar(self, isbn, id_usuario):
        usuario = self._obter_usuario(id_usuario)
        livro = self._obter_livro(isbn)
        if livro.ativos < livro.quantidade and not livro.fila:
            raise ReservaNaoPermitida("Há exemplar disponível")
        if id_usuario in livro.fila:
            raise ReservaNaoPermitida("Usuário já está na fila")
        for emp in usuario.ativos.values():
            if emp.isbn == isbn:
                raise ReservaNaoPermitida("Usuário já possui exemplar deste livro")
        livro.fila.append(id_usuario)
        return len(livro.fila)

    # ---------------------------------------------------------------- consultas

    def exemplares_disponiveis(self, isbn):
        livro = self._obter_livro(isbn)
        return livro.quantidade - livro.ativos

    def saldo_devedor(self, id_usuario):
        return self._obter_usuario(id_usuario).saldo

    def pagar_multa(self, id_usuario, valor):
        usuario = self._obter_usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0 or valor > usuario.saldo:
            raise DadosInvalidos("Valor de pagamento inválido")
        usuario.saldo -= valor
        return usuario.saldo

    def emprestimos_atrasados(self, data):
        if not _data_valida(data):
            raise DadosInvalidos("Data inválida")
        return sorted(
            id_emp for id_emp, emp in self._ativos.items() if emp.data_prevista < data
        )

    def consultar_emprestimo(self, id_emprestimo):
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
