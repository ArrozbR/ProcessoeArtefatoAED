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
_TETO_MULTA = 2000
_MAX_RENOVACOES = 2
_DIGITOS = frozenset("0123456789")


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
        self.fila: deque = deque()


class _Usuario:
    __slots__ = ("id_usuario", "nome", "tipo", "saldo", "ativos")

    def __init__(self, id_usuario, nome, tipo):
        self.id_usuario = id_usuario
        self.nome = nome
        self.tipo = tipo
        self.saldo = 0
        self.ativos: dict = {}  # id_emprestimo -> _Emprestimo


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
        self._ativos: dict = {}  # id_emprestimo -> _Emprestimo (ordem de inserção crescente)
        self._proximo_id = 1

    # ------------------------------------------------------------------ auxiliares
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

    @staticmethod
    def _bloqueado(usuario: _Usuario, data) -> bool:
        if usuario.saldo > 0:
            return True
        for emp in usuario.ativos.values():
            if emp.data_prevista < data:
                return True
        return False

    @staticmethod
    def _disponiveis(livro: _Livro) -> int:
        return livro.quantidade - livro.ativos

    # ------------------------------------------------------------------ cadastros
    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
        if not (isinstance(isbn, str) and len(isbn) == 13 and all(c in _DIGITOS for c in isbn)):
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

    # ------------------------------------------------------------------ empréstimos
    def emprestar(self, isbn, id_usuario, data):
        usuario = self._obter_usuario(id_usuario)
        livro = self._obter_livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(f"usuário {id_usuario} bloqueado")
        if len(usuario.ativos) >= _LIMITES[usuario.tipo]:
            raise LimiteDeEmprestimosAtingido(f"usuário {id_usuario} atingiu o limite")
        if self._disponiveis(livro) <= 0:
            raise ExemplarIndisponivel(f"nenhum exemplar de {isbn} disponível")
        primeiro_da_fila = False
        if livro.fila:
            if livro.fila[0] != id_usuario:
                raise ExemplarIndisponivel(f"há fila de reservas para {isbn}")
            primeiro_da_fila = True

        prevista = data + datetime.timedelta(days=_PRAZOS[usuario.tipo])
        id_emp = self._proximo_id
        emp = _Emprestimo(id_emp, isbn, id_usuario, data, prevista)

        # efetiva (nada abaixo lança exceção)
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
            raise EmprestimoJaDevolvido(f"empréstimo {id_emprestimo} já devolvido")
        if data < emp.data_emprestimo:
            raise DadosInvalidos("data de devolução anterior à data do empréstimo")
        atraso = (data - emp.data_prevista).days
        multa = min(max(atraso, 0) * _MULTA_DIA, _TETO_MULTA)

        usuario = self._usuarios[emp.id_usuario]
        livro = self._livros[emp.isbn]
        emp.data_devolucao = data
        emp.multa = multa
        usuario.saldo += multa
        usuario.ativos.pop(emp.id_emprestimo, None)
        self._ativos.pop(emp.id_emprestimo, None)
        livro.ativos -= 1
        return multa

    def renovar(self, id_emprestimo, data):
        emp = self._obter_emprestimo(id_emprestimo)
        if emp.data_devolucao is not None:
            raise EmprestimoJaDevolvido(f"empréstimo {id_emprestimo} já devolvido")
        if emp.data_prevista < data:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emp.renovacoes >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        livro = self._livros[emp.isbn]
        if livro.fila:
            raise RenovacaoNaoPermitida("livro com fila de reservas")
        usuario = self._usuarios[emp.id_usuario]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")
        nova = emp.data_prevista + datetime.timedelta(days=_PRAZOS[usuario.tipo])
        emp.data_prevista = nova
        emp.renovacoes += 1
        return nova

    # ------------------------------------------------------------------ reservas
    def reservar(self, isbn, id_usuario):
        usuario = self._obter_usuario(id_usuario)
        livro = self._obter_livro(isbn)
        if self._disponiveis(livro) > 0 and not livro.fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in livro.fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for emp in usuario.ativos.values():
            if emp.isbn == isbn:
                raise ReservaNaoPermitida("usuário já está com um exemplar deste livro")
        livro.fila.append(id_usuario)
        return len(livro.fila)

    # ------------------------------------------------------------------ consultas
    def exemplares_disponiveis(self, isbn):
        livro = self._obter_livro(isbn)
        return self._disponiveis(livro)

    def saldo_devedor(self, id_usuario):
        return self._obter_usuario(id_usuario).saldo

    def pagar_multa(self, id_usuario, valor):
        usuario = self._obter_usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0:
            raise DadosInvalidos("valor deve ser inteiro positivo")
        if valor > usuario.saldo:
            raise DadosInvalidos("valor maior que o saldo devedor")
        usuario.saldo -= valor
        return usuario.saldo

    def emprestimos_atrasados(self, data):
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
