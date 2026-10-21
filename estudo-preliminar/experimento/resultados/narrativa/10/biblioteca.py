"""Sistema de empréstimos de biblioteca acadêmica (em memória)."""

from __future__ import annotations

import datetime
from collections import deque

__all__ = [
    "Biblioteca",
    "ErroBiblioteca",
    "DadosInvalidos",
    "CadastroDuplicado",
    "LivroNaoEncontrado",
    "UsuarioNaoEncontrado",
    "EmprestimoNaoEncontrado",
    "EmprestimoJaDevolvido",
    "UsuarioBloqueado",
    "LimiteDeEmprestimosAtingido",
    "ExemplarIndisponivel",
    "RenovacaoNaoPermitida",
    "ReservaNaoPermitida",
]


class ErroBiblioteca(Exception):
    """Exceção base do sistema."""


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
_TETO_MULTA = 2000
_MAX_RENOVACOES = 2
_DIGITOS = frozenset("0123456789")


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _validar_data(data) -> None:
    if not isinstance(data, datetime.date):
        raise DadosInvalidos("data deve ser datetime.date")


class _Livro:
    __slots__ = ("isbn", "titulo", "autor", "quantidade", "ativos", "fila")

    def __init__(self, isbn, titulo, autor, quantidade):
        self.isbn = isbn
        self.titulo = titulo
        self.autor = autor
        self.quantidade = quantidade
        self.ativos = 0
        self.fila: deque = deque()


class _Usuario:
    __slots__ = ("id_usuario", "nome", "tipo", "saldo", "ativos")

    def __init__(self, id_usuario, nome, tipo):
        self.id_usuario = id_usuario
        self.nome = nome
        self.tipo = tipo
        self.saldo = 0
        self.ativos: set = set()


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
        self._livros: dict = {}
        self._usuarios: dict = {}
        self._emprestimos: dict = {}
        self._ativos: set = set()
        self._proximo_id = 1

    # ------------------------------------------------------------------
    # Auxiliares
    # ------------------------------------------------------------------
    def _obter_usuario(self, id_usuario) -> _Usuario:
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado(f"usuário {id_usuario!r} não encontrado") from None

    def _obter_livro(self, isbn) -> _Livro:
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado(f"livro {isbn!r} não encontrado") from None

    def _obter_emprestimo(self, id_emprestimo) -> _Emprestimo:
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado(
                f"empréstimo {id_emprestimo!r} não encontrado"
            ) from None

    def _bloqueado(self, usuario: _Usuario, data) -> bool:
        if usuario.saldo > 0:
            return True
        for id_emp in usuario.ativos:
            if self._emprestimos[id_emp].data_prevista < data:
                return True
        return False

    @staticmethod
    def _disponiveis(livro: _Livro) -> int:
        return livro.quantidade - livro.ativos

    # ------------------------------------------------------------------
    # Cadastros
    # ------------------------------------------------------------------
    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
        if not (isinstance(isbn, str) and len(isbn) == 13 and set(isbn) <= _DIGITOS):
            raise DadosInvalidos("ISBN deve ter exatamente 13 dígitos")
        if not _texto_valido(titulo):
            raise DadosInvalidos("título inválido")
        if not _texto_valido(autor):
            raise DadosInvalidos("autor inválido")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("quantidade deve ser inteiro >= 1")
        if isbn in self._livros:
            raise CadastroDuplicado(f"ISBN {isbn} já cadastrado")
        self._livros[isbn] = _Livro(isbn, titulo, autor, quantidade)

    def cadastrar_usuario(self, id_usuario, nome, tipo):
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id de usuário inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome inválido")
        if not isinstance(tipo, str) or tipo not in _PRAZOS:
            raise DadosInvalidos("tipo deve ser 'aluno' ou 'professor'")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(f"usuário {id_usuario} já cadastrado")
        self._usuarios[id_usuario] = _Usuario(id_usuario, nome, tipo)

    # ------------------------------------------------------------------
    # Empréstimo, devolução, renovação
    # ------------------------------------------------------------------
    def emprestar(self, isbn, id_usuario, data):
        _validar_data(data)
        usuario = self._obter_usuario(id_usuario)
        livro = self._obter_livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(f"usuário {id_usuario} bloqueado")
        if len(usuario.ativos) >= _LIMITES[usuario.tipo]:
            raise LimiteDeEmprestimosAtingido(f"usuário {id_usuario} no limite")
        if self._disponiveis(livro) <= 0:
            raise ExemplarIndisponivel(f"sem exemplares de {isbn}")
        primeiro_da_fila = False
        if livro.fila:
            if livro.fila[0] != id_usuario:
                raise ExemplarIndisponivel(f"livro {isbn} reservado para outro usuário")
            primeiro_da_fila = True

        id_emp = self._proximo_id
        prevista = data + datetime.timedelta(days=_PRAZOS[usuario.tipo])
        emprestimo = _Emprestimo(id_emp, isbn, id_usuario, data, prevista)

        if primeiro_da_fila:
            livro.fila.popleft()
        self._proximo_id += 1
        self._emprestimos[id_emp] = emprestimo
        self._ativos.add(id_emp)
        usuario.ativos.add(id_emp)
        livro.ativos += 1
        return id_emp

    def devolver(self, id_emprestimo, data):
        emprestimo = self._obter_emprestimo(id_emprestimo)
        if emprestimo.data_devolucao is not None:
            raise EmprestimoJaDevolvido(f"empréstimo {id_emprestimo} já devolvido")
        _validar_data(data)
        if data < emprestimo.data_emprestimo:
            raise DadosInvalidos("data de devolução anterior à data do empréstimo")

        atraso = (data - emprestimo.data_prevista).days
        multa = min(max(atraso, 0) * _MULTA_DIA, _TETO_MULTA)

        usuario = self._usuarios[emprestimo.id_usuario]
        livro = self._livros[emprestimo.isbn]
        emprestimo.data_devolucao = data
        emprestimo.multa = multa
        usuario.saldo += multa
        usuario.ativos.discard(emprestimo.id_emprestimo)
        self._ativos.discard(emprestimo.id_emprestimo)
        livro.ativos -= 1
        return multa

    def renovar(self, id_emprestimo, data):
        emprestimo = self._obter_emprestimo(id_emprestimo)
        if emprestimo.data_devolucao is not None:
            raise EmprestimoJaDevolvido(f"empréstimo {id_emprestimo} já devolvido")
        _validar_data(data)
        usuario = self._usuarios[emprestimo.id_usuario]
        livro = self._livros[emprestimo.isbn]
        if emprestimo.data_prevista < data:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emprestimo.renovacoes >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if livro.fila:
            raise RenovacaoNaoPermitida("livro com fila de reservas")
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")

        nova = emprestimo.data_prevista + datetime.timedelta(days=_PRAZOS[usuario.tipo])
        emprestimo.data_prevista = nova
        emprestimo.renovacoes += 1
        return nova

    # ------------------------------------------------------------------
    # Reservas
    # ------------------------------------------------------------------
    def reservar(self, isbn, id_usuario):
        usuario = self._obter_usuario(id_usuario)
        livro = self._obter_livro(isbn)
        if self._disponiveis(livro) > 0 and not livro.fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in livro.fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for id_emp in usuario.ativos:
            if self._emprestimos[id_emp].isbn == isbn:
                raise ReservaNaoPermitida("usuário já possui exemplar emprestado")
        livro.fila.append(id_usuario)
        return len(livro.fila)

    # ------------------------------------------------------------------
    # Consultas e pagamentos
    # ------------------------------------------------------------------
    def exemplares_disponiveis(self, isbn):
        return self._disponiveis(self._obter_livro(isbn))

    def saldo_devedor(self, id_usuario):
        return self._obter_usuario(id_usuario).saldo

    def pagar_multa(self, id_usuario, valor):
        usuario = self._obter_usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0 or valor > usuario.saldo:
            raise DadosInvalidos("valor de pagamento inválido")
        usuario.saldo -= valor
        return usuario.saldo

    def emprestimos_atrasados(self, data):
        _validar_data(data)
        emprestimos = self._emprestimos
        return sorted(i for i in self._ativos if emprestimos[i].data_prevista < data)

    def consultar_emprestimo(self, id_emprestimo):
        e = self._obter_emprestimo(id_emprestimo)
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
