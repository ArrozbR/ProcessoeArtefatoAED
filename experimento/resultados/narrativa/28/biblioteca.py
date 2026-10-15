"""Sistema de Empréstimos de Biblioteca (em memória, somente biblioteca padrão)."""

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


def _eh_data(valor) -> bool:
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
        self._livros: dict[str, _Livro] = {}
        self._usuarios: dict[str, _Usuario] = {}
        self._emprestimos: dict[int, _Emprestimo] = {}
        self._ativos: set[int] = set()
        self._proximo_id = 1

    # ------------------------------------------------------------------ cadastro

    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
        if not (isinstance(isbn, str) and len(isbn) == 13 and set(isbn) <= _DIGITOS):
            raise DadosInvalidos("ISBN inválido")
        if not _texto_valido(titulo):
            raise DadosInvalidos("título inválido")
        if not _texto_valido(autor):
            raise DadosInvalidos("autor inválido")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("quantidade inválida")
        if isbn in self._livros:
            raise CadastroDuplicado("ISBN já cadastrado")
        self._livros[isbn] = _Livro(isbn, titulo, autor, quantidade)

    def cadastrar_usuario(self, id_usuario, nome, tipo):
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome inválido")
        if not isinstance(tipo, str) or tipo not in _PRAZOS:
            raise DadosInvalidos("tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado("usuário já cadastrado")
        self._usuarios[id_usuario] = _Usuario(id_usuario, nome, tipo)

    # ------------------------------------------------------------------ auxiliares

    def _usuario(self, id_usuario) -> _Usuario:
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado("usuário não encontrado") from None

    def _livro(self, isbn) -> _Livro:
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado("livro não encontrado") from None

    def _emprestimo(self, id_emprestimo) -> _Emprestimo:
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado("empréstimo não encontrado") from None

    def _bloqueado(self, usuario: _Usuario, data) -> bool:
        if usuario.saldo > 0:
            return True
        for id_emp in usuario.ativos:
            if self._emprestimos[id_emp].data_prevista < data:
                return True
        return False

    # ------------------------------------------------------------------ operações

    def emprestar(self, isbn, id_usuario, data):
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if not _eh_data(data):
            raise DadosInvalidos("data inválida")
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado("usuário bloqueado")
        if len(usuario.ativos) >= _LIMITES[usuario.tipo]:
            raise LimiteDeEmprestimosAtingido("limite de empréstimos atingido")
        if livro.ativos >= livro.quantidade:
            raise ExemplarIndisponivel("sem exemplares disponíveis")
        primeiro_da_fila = False
        if livro.fila:
            if livro.fila[0] != id_usuario:
                raise ExemplarIndisponivel("exemplar reservado para outro usuário")
            primeiro_da_fila = True

        id_emp = self._proximo_id
        prevista = data + datetime.timedelta(days=_PRAZOS[usuario.tipo])
        emprestimo = _Emprestimo(id_emp, livro.isbn, usuario.id_usuario, data, prevista)
        self._proximo_id += 1
        self._emprestimos[id_emp] = emprestimo
        self._ativos.add(id_emp)
        usuario.ativos.add(id_emp)
        livro.ativos += 1
        if primeiro_da_fila:
            livro.fila.popleft()
        return id_emp

    def devolver(self, id_emprestimo, data):
        emprestimo = self._emprestimo(id_emprestimo)
        if emprestimo.data_devolucao is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        if not _eh_data(data) or data < emprestimo.data_emprestimo:
            raise DadosInvalidos("data de devolução inválida")

        atraso = (data - emprestimo.data_prevista).days
        multa = min(max(0, atraso) * _MULTA_DIA, _MULTA_TETO)

        usuario = self._usuarios[emprestimo.id_usuario]
        livro = self._livros[emprestimo.isbn]
        emprestimo.data_devolucao = data
        emprestimo.multa = multa
        usuario.saldo += multa
        usuario.ativos.discard(emprestimo.id_emprestimo)
        livro.ativos -= 1
        self._ativos.discard(emprestimo.id_emprestimo)
        return multa

    def renovar(self, id_emprestimo, data):
        emprestimo = self._emprestimo(id_emprestimo)
        if emprestimo.data_devolucao is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        if not _eh_data(data):
            raise DadosInvalidos("data inválida")
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

    def reservar(self, isbn, id_usuario):
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if livro.ativos < livro.quantidade and not livro.fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in livro.fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for id_emp in usuario.ativos:
            if self._emprestimos[id_emp].isbn == livro.isbn:
                raise ReservaNaoPermitida("usuário já possui exemplar deste livro")
        livro.fila.append(usuario.id_usuario)
        return len(livro.fila)

    # ------------------------------------------------------------------ consultas

    def exemplares_disponiveis(self, isbn):
        livro = self._livro(isbn)
        return livro.quantidade - livro.ativos

    def saldo_devedor(self, id_usuario):
        return self._usuario(id_usuario).saldo

    def pagar_multa(self, id_usuario, valor):
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0 or valor > usuario.saldo:
            raise DadosInvalidos("valor de pagamento inválido")
        usuario.saldo -= valor
        return usuario.saldo

    def emprestimos_atrasados(self, data):
        if not _eh_data(data):
            raise DadosInvalidos("data inválida")
        emps = self._emprestimos
        return sorted(i for i in self._ativos if emps[i].data_prevista < data)

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
