"""Sistema de Empréstimos de Biblioteca."""

from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class _Livro:
    isbn: str
    titulo: str
    autor: str
    quantidade: int
    ativos: int = 0
    fila: list = field(default_factory=list)


@dataclass
class _Usuario:
    id_usuario: str
    nome: str
    tipo: str
    saldo_devedor: int = 0
    ativos: set = field(default_factory=set)


@dataclass
class _Emprestimo:
    id_emprestimo: int
    isbn: str
    id_usuario: str
    data_emprestimo: date
    data_prevista: date
    data_devolucao: date | None = None
    renovacoes: int = 0
    multa: int = 0


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


class Biblioteca:
    def __init__(self) -> None:
        self._livros: dict[str, _Livro] = {}
        self._usuarios: dict[str, _Usuario] = {}
        self._emprestimos: dict[int, _Emprestimo] = {}
        self._ativos: dict[int, _Emprestimo] = {}
        self._proximo_id = 1

    # ----------------------------------------------------------- auxiliares
    def _obter_usuario(self, id_usuario: str) -> _Usuario:
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado(id_usuario) from None

    def _obter_livro(self, isbn: str) -> _Livro:
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado(isbn) from None

    def _obter_emprestimo(self, id_emprestimo: int) -> _Emprestimo:
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

    def _tem_ativo_do_livro(self, usuario: _Usuario, isbn: str) -> bool:
        return any(self._emprestimos[i].isbn == isbn for i in usuario.ativos)

    # ------------------------------------------------------------- RF-01
    def cadastrar_livro(self, isbn: str, titulo: str, autor: str, quantidade: int) -> None:
        if not (isinstance(isbn, str) and len(isbn) == 13
                and all(c in "0123456789" for c in isbn)):
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
        usuario = self._obter_usuario(id_usuario)
        livro = self._obter_livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(id_usuario)
        if len(usuario.ativos) >= LIMITES[usuario.tipo]:
            raise LimiteDeEmprestimosAtingido(id_usuario)
        disponiveis = livro.quantidade - livro.ativos
        if disponiveis <= 0 or (livro.fila and livro.fila[0] != id_usuario):
            raise ExemplarIndisponivel(isbn)

        id_emp = self._proximo_id
        emprestimo = _Emprestimo(
            id_emprestimo=id_emp,
            isbn=isbn,
            id_usuario=id_usuario,
            data_emprestimo=data,
            data_prevista=data + timedelta(days=PRAZOS[usuario.tipo]),
        )
        self._proximo_id += 1
        self._emprestimos[id_emp] = emprestimo
        self._ativos[id_emp] = emprestimo
        usuario.ativos.add(id_emp)
        livro.ativos += 1
        if livro.fila and livro.fila[0] == id_usuario:
            livro.fila.pop(0)
        return id_emp

    # ------------------------------------------------------------- RF-04
    def devolver(self, id_emprestimo: int, data: date) -> int:
        emprestimo = self._obter_emprestimo(id_emprestimo)
        if emprestimo.data_devolucao is not None:
            raise EmprestimoJaDevolvido(id_emprestimo)
        if data < emprestimo.data_emprestimo:
            raise DadosInvalidos("data anterior ao empréstimo")

        dias = (data - emprestimo.data_prevista).days
        multa = min(max(dias, 0) * MULTA_POR_DIA, MULTA_MAXIMA)

        emprestimo.data_devolucao = data
        emprestimo.multa = multa
        del self._ativos[id_emprestimo]
        usuario = self._usuarios[emprestimo.id_usuario]
        usuario.ativos.discard(id_emprestimo)
        usuario.saldo_devedor += multa
        self._livros[emprestimo.isbn].ativos -= 1
        return multa

    # ------------------------------------------------------------- RF-05
    def renovar(self, id_emprestimo: int, data: date) -> date:
        emprestimo = self._obter_emprestimo(id_emprestimo)
        if emprestimo.data_devolucao is not None:
            raise EmprestimoJaDevolvido(id_emprestimo)
        if data > emprestimo.data_prevista:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emprestimo.renovacoes >= MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if self._livros[emprestimo.isbn].fila:
            raise RenovacaoNaoPermitida("há reservas para o livro")
        usuario = self._usuarios[emprestimo.id_usuario]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")

        emprestimo.data_prevista = emprestimo.data_prevista + timedelta(
            days=PRAZOS[usuario.tipo]
        )
        emprestimo.renovacoes += 1
        return emprestimo.data_prevista

    # ------------------------------------------------------------- RF-06
    def reservar(self, isbn: str, id_usuario: str) -> int:
        usuario = self._obter_usuario(id_usuario)
        livro = self._obter_livro(isbn)
        if livro.quantidade - livro.ativos > 0 and not livro.fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in livro.fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        if self._tem_ativo_do_livro(usuario, isbn):
            raise ReservaNaoPermitida("usuário já tem empréstimo ativo do livro")
        livro.fila.append(id_usuario)
        return len(livro.fila)

    # ------------------------------------------------------------- RF-07
    def exemplares_disponiveis(self, isbn: str) -> int:
        livro = self._obter_livro(isbn)
        return livro.quantidade - livro.ativos

    # ------------------------------------------------------------- RF-08
    def saldo_devedor(self, id_usuario: str) -> int:
        return self._obter_usuario(id_usuario).saldo_devedor

    # ------------------------------------------------------------- RF-09
    def pagar_multa(self, id_usuario: str, valor: int) -> int:
        usuario = self._obter_usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0:
            raise DadosInvalidos("valor inválido")
        if valor > usuario.saldo_devedor:
            raise DadosInvalidos("valor maior que o saldo devedor")
        usuario.saldo_devedor -= valor
        return usuario.saldo_devedor

    # ------------------------------------------------------------- RF-10
    def emprestimos_atrasados(self, data: date) -> list[int]:
        return sorted(
            id_emp for id_emp, emp in self._ativos.items() if data > emp.data_prevista
        )

    # ------------------------------------------------------------- RF-11
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
