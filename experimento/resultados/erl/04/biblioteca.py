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
_MULTA_DIA = 100
_MULTA_MAX = 2000
_MAX_RENOVACOES = 2


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


class Biblioteca:
    def __init__(self) -> None:
        self._livros: dict[str, dict] = {}
        self._usuarios: dict[str, dict] = {}
        self._emprestimos: dict[int, dict] = {}
        self._ativos_por_livro: dict[str, set[int]] = {}
        self._ativos_por_usuario: dict[str, set[int]] = {}
        self._filas: dict[str, deque] = {}
        self._proximo_id = 1

    # ---------------------------------------------------------------- auxiliares

    def _usuario(self, id_usuario: str) -> dict:
        if not isinstance(id_usuario, str) or id_usuario not in self._usuarios:
            raise UsuarioNaoEncontrado(id_usuario)
        return self._usuarios[id_usuario]

    def _livro(self, isbn: str) -> dict:
        if not isinstance(isbn, str) or isbn not in self._livros:
            raise LivroNaoEncontrado(isbn)
        return self._livros[isbn]

    def _emprestimo(self, id_emprestimo: int) -> dict:
        try:
            emp = self._emprestimos.get(id_emprestimo)
        except TypeError:
            emp = None
        if emp is None:
            raise EmprestimoNaoEncontrado(id_emprestimo)
        return emp

    def _disponiveis(self, isbn: str) -> int:
        return self._livros[isbn]["quantidade"] - len(self._ativos_por_livro[isbn])

    def _bloqueado(self, id_usuario: str, data: date) -> bool:
        if self._usuarios[id_usuario]["saldo_devedor"] > 0:
            return True
        for id_emp in self._ativos_por_usuario[id_usuario]:
            if data > self._emprestimos[id_emp]["data_prevista"]:
                return True
        return False

    # ---------------------------------------------------------------- RF-01

    def cadastrar_livro(self, isbn: str, titulo: str, autor: str, quantidade: int) -> None:
        if not (isinstance(isbn, str) and len(isbn) == 13
                and all(c in "0123456789" for c in isbn)):
            raise DadosInvalidos("isbn inválido")
        if not _texto_valido(titulo) or not _texto_valido(autor):
            raise DadosInvalidos("titulo ou autor inválido")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("quantidade inválida")
        if isbn in self._livros:
            raise CadastroDuplicado(isbn)
        self._livros[isbn] = {
            "isbn": isbn,
            "titulo": titulo,
            "autor": autor,
            "quantidade": quantidade,
        }
        self._ativos_por_livro[isbn] = set()
        self._filas[isbn] = deque()

    # ---------------------------------------------------------------- RF-02

    def cadastrar_usuario(self, id_usuario: str, nome: str, tipo: str) -> None:
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id_usuario inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome inválido")
        if tipo not in ("aluno", "professor") or not isinstance(tipo, str):
            raise DadosInvalidos("tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(id_usuario)
        self._usuarios[id_usuario] = {
            "id_usuario": id_usuario,
            "nome": nome,
            "tipo": tipo,
            "saldo_devedor": 0,
        }
        self._ativos_por_usuario[id_usuario] = set()

    # ---------------------------------------------------------------- RF-03

    def emprestar(self, isbn: str, id_usuario: str, data: date) -> int:
        usuario = self._usuario(id_usuario)
        self._livro(isbn)
        if self._bloqueado(id_usuario, data):
            raise UsuarioBloqueado(id_usuario)
        tipo = usuario["tipo"]
        if len(self._ativos_por_usuario[id_usuario]) >= _LIMITE[tipo]:
            raise LimiteDeEmprestimosAtingido(id_usuario)
        fila = self._filas[isbn]
        if self._disponiveis(isbn) == 0 or (fila and fila[0] != id_usuario):
            raise ExemplarIndisponivel(isbn)

        id_emp = self._proximo_id
        self._proximo_id += 1
        self._emprestimos[id_emp] = {
            "id_emprestimo": id_emp,
            "isbn": isbn,
            "id_usuario": id_usuario,
            "data_emprestimo": data,
            "data_prevista": data + timedelta(days=_PRAZO[tipo]),
            "data_devolucao": None,
            "renovacoes": 0,
            "multa": 0,
        }
        self._ativos_por_livro[isbn].add(id_emp)
        self._ativos_por_usuario[id_usuario].add(id_emp)
        if fila and fila[0] == id_usuario:
            fila.popleft()
        return id_emp

    # ---------------------------------------------------------------- RF-04

    def devolver(self, id_emprestimo: int, data: date) -> int:
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido(id_emprestimo)
        if not isinstance(data, date) or data < emp["data_emprestimo"]:
            raise DadosInvalidos("data de devolução inválida")

        dias = (data - emp["data_prevista"]).days
        if dias < 0:
            dias = 0
        multa = min(dias * _MULTA_DIA, _MULTA_MAX)

        emp["data_devolucao"] = data
        emp["multa"] = multa
        self._ativos_por_livro[emp["isbn"]].discard(emp["id_emprestimo"])
        self._ativos_por_usuario[emp["id_usuario"]].discard(emp["id_emprestimo"])
        self._usuarios[emp["id_usuario"]]["saldo_devedor"] += multa
        return multa

    # ---------------------------------------------------------------- RF-05

    def renovar(self, id_emprestimo: int, data: date) -> date:
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido(id_emprestimo)
        if data > emp["data_prevista"]:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emp["renovacoes"] >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if self._filas[emp["isbn"]]:
            raise RenovacaoNaoPermitida("há reservas para o livro")
        if self._bloqueado(emp["id_usuario"], data):
            raise RenovacaoNaoPermitida("usuário bloqueado")

        tipo = self._usuarios[emp["id_usuario"]]["tipo"]
        emp["data_prevista"] = emp["data_prevista"] + timedelta(days=_PRAZO[tipo])
        emp["renovacoes"] += 1
        return emp["data_prevista"]

    # ---------------------------------------------------------------- RF-06

    def reservar(self, isbn: str, id_usuario: str) -> int:
        self._usuario(id_usuario)
        self._livro(isbn)
        fila = self._filas[isbn]
        if self._disponiveis(isbn) > 0 and not fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for id_emp in self._ativos_por_usuario[id_usuario]:
            if self._emprestimos[id_emp]["isbn"] == isbn:
                raise ReservaNaoPermitida("usuário tem empréstimo ativo do livro")
        fila.append(id_usuario)
        return len(fila)

    # ---------------------------------------------------------------- RF-07

    def exemplares_disponiveis(self, isbn: str) -> int:
        self._livro(isbn)
        return self._disponiveis(isbn)

    # ---------------------------------------------------------------- RF-08

    def saldo_devedor(self, id_usuario: str) -> int:
        return self._usuario(id_usuario)["saldo_devedor"]

    # ---------------------------------------------------------------- RF-09

    def pagar_multa(self, id_usuario: str, valor: int) -> int:
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0:
            raise DadosInvalidos("valor inválido")
        if valor > usuario["saldo_devedor"]:
            raise DadosInvalidos("valor maior que o saldo devedor")
        usuario["saldo_devedor"] -= valor
        return usuario["saldo_devedor"]

    # ---------------------------------------------------------------- RF-10

    def emprestimos_atrasados(self, data: date) -> list[int]:
        ids = []
        for ativos in self._ativos_por_livro.values():
            for id_emp in ativos:
                if data > self._emprestimos[id_emp]["data_prevista"]:
                    ids.append(id_emp)
        ids.sort()
        return ids

    # ---------------------------------------------------------------- RF-11

    def consultar_emprestimo(self, id_emprestimo: int) -> dict:
        emp = self._emprestimo(id_emprestimo)
        return {
            "id_emprestimo": emp["id_emprestimo"],
            "isbn": emp["isbn"],
            "id_usuario": emp["id_usuario"],
            "data_emprestimo": emp["data_emprestimo"],
            "data_prevista": emp["data_prevista"],
            "data_devolucao": emp["data_devolucao"],
            "renovacoes": emp["renovacoes"],
            "multa": emp["multa"],
        }
