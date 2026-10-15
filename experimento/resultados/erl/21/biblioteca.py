"""Sistema de Empréstimos de Biblioteca."""

from __future__ import annotations

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
        self.fila: list[str] = []


class _Usuario:
    __slots__ = ("id_usuario", "nome", "tipo", "saldo_devedor", "ativos")

    def __init__(self, id_usuario: str, nome: str, tipo: str):
        self.id_usuario = id_usuario
        self.nome = nome
        self.tipo = tipo
        self.saldo_devedor = 0
        self.ativos: set[int] = set()


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
    def __init__(self) -> None:
        self._livros: dict[str, _Livro] = {}
        self._usuarios: dict[str, _Usuario] = {}
        self._emprestimos: dict[int, _Emprestimo] = {}
        self._ativos: set[int] = set()
        self._proximo_id = 1

    # ------------------------------------------------------------ auxiliares
    def _usuario(self, id_usuario) -> _Usuario:
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado(id_usuario) from None

    def _livro(self, isbn) -> _Livro:
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado(isbn) from None

    def _emprestimo(self, id_emprestimo) -> _Emprestimo:
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado(id_emprestimo) from None

    def _bloqueado(self, usuario: _Usuario, data: date) -> bool:
        if usuario.saldo_devedor > 0:
            return True
        for id_emp in usuario.ativos:
            if data > self._emprestimos[id_emp].data_prevista:
                return True
        return False

    @staticmethod
    def _disponiveis(livro: _Livro) -> int:
        return livro.quantidade - livro.ativos

    # ------------------------------------------------------------- RF-01
    def cadastrar_livro(self, isbn: str, titulo: str, autor: str, quantidade: int) -> None:
        if not (
            isinstance(isbn, str)
            and len(isbn) == 13
            and all(c in "0123456789" for c in isbn)
        ):
            raise DadosInvalidos("isbn inválido")
        if not _texto_valido(titulo) or not _texto_valido(autor):
            raise DadosInvalidos("título ou autor inválido")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("quantidade inválida")
        if isbn in self._livros:
            raise CadastroDuplicado(isbn)
        self._livros[isbn] = _Livro(isbn, titulo, autor, quantidade)

    # ------------------------------------------------------------- RF-02
    def cadastrar_usuario(self, id_usuario: str, nome: str, tipo: str) -> None:
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id_usuario inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome inválido")
        if tipo not in ("aluno", "professor") or not isinstance(tipo, str):
            raise DadosInvalidos("tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(id_usuario)
        self._usuarios[id_usuario] = _Usuario(id_usuario, nome, tipo)

    # ------------------------------------------------------------- RF-03
    def emprestar(self, isbn: str, id_usuario: str, data: date) -> int:
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(id_usuario)
        if len(usuario.ativos) >= _LIMITE[usuario.tipo]:
            raise LimiteDeEmprestimosAtingido(id_usuario)
        if self._disponiveis(livro) <= 0 or (
            livro.fila and livro.fila[0] != id_usuario
        ):
            raise ExemplarIndisponivel(isbn)

        id_emp = self._proximo_id
        emp = _Emprestimo(
            id_emp,
            isbn,
            id_usuario,
            data,
            data + timedelta(days=_PRAZO[usuario.tipo]),
        )
        self._proximo_id += 1
        self._emprestimos[id_emp] = emp
        self._ativos.add(id_emp)
        usuario.ativos.add(id_emp)
        livro.ativos += 1
        if livro.fila and livro.fila[0] == id_usuario:
            livro.fila.pop(0)
        return id_emp

    # ------------------------------------------------------------- RF-04
    def devolver(self, id_emprestimo: int, data: date) -> int:
        emp = self._emprestimo(id_emprestimo)
        if emp.data_devolucao is not None:
            raise EmprestimoJaDevolvido(id_emprestimo)
        if data < emp.data_emprestimo:
            raise DadosInvalidos("data anterior ao empréstimo")

        dias = (data - emp.data_prevista).days
        if dias < 0:
            dias = 0
        multa = min(dias * _MULTA_DIA, _MULTA_MAX)

        emp.data_devolucao = data
        emp.multa = multa
        self._ativos.discard(emp.id_emprestimo)
        usuario = self._usuarios[emp.id_usuario]
        usuario.ativos.discard(emp.id_emprestimo)
        usuario.saldo_devedor += multa
        self._livros[emp.isbn].ativos -= 1
        return multa

    # ------------------------------------------------------------- RF-05
    def renovar(self, id_emprestimo: int, data: date) -> date:
        emp = self._emprestimo(id_emprestimo)
        if emp.data_devolucao is not None:
            raise EmprestimoJaDevolvido(id_emprestimo)
        if data > emp.data_prevista:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emp.renovacoes >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if self._livros[emp.isbn].fila:
            raise RenovacaoNaoPermitida("há reservas para o livro")
        usuario = self._usuarios[emp.id_usuario]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")

        emp.data_prevista = emp.data_prevista + timedelta(days=_PRAZO[usuario.tipo])
        emp.renovacoes += 1
        return emp.data_prevista

    # ------------------------------------------------------------- RF-06
    def reservar(self, isbn: str, id_usuario: str) -> int:
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if self._disponiveis(livro) > 0 and not livro.fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in livro.fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for id_emp in usuario.ativos:
            if self._emprestimos[id_emp].isbn == isbn:
                raise ReservaNaoPermitida("usuário tem empréstimo ativo do livro")
        livro.fila.append(id_usuario)
        return len(livro.fila)

    # ------------------------------------------------------------- RF-07
    def exemplares_disponiveis(self, isbn: str) -> int:
        return self._disponiveis(self._livro(isbn))

    # ------------------------------------------------------------- RF-08
    def saldo_devedor(self, id_usuario: str) -> int:
        return self._usuario(id_usuario).saldo_devedor

    # ------------------------------------------------------------- RF-09
    def pagar_multa(self, id_usuario: str, valor: int) -> int:
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0:
            raise DadosInvalidos("valor inválido")
        if valor > usuario.saldo_devedor:
            raise DadosInvalidos("valor maior que o saldo devedor")
        usuario.saldo_devedor -= valor
        return usuario.saldo_devedor

    # ------------------------------------------------------------- RF-10
    def emprestimos_atrasados(self, data: date) -> list[int]:
        return sorted(
            id_emp
            for id_emp in self._ativos
            if data > self._emprestimos[id_emp].data_prevista
        )

    # ------------------------------------------------------------- RF-11
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
