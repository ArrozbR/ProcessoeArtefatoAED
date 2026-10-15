"""Sistema de empréstimos de biblioteca acadêmica (em memória)."""

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


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _data_valida(valor) -> bool:
    return isinstance(valor, datetime.date) and not isinstance(valor, datetime.datetime)


class Biblioteca:
    def __init__(self):
        self._livros: dict[str, dict] = {}
        self._usuarios: dict[str, dict] = {}
        self._emprestimos: dict[int, dict] = {}
        self._ativos: set[int] = set()
        self._proximo_id = 1

    # ------------------------------------------------------------------ cadastro
    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
        if not (isinstance(isbn, str) and len(isbn) == 13
                and all(c in "0123456789" for c in isbn)):
            raise DadosInvalidos("ISBN inválido")
        if not _texto_valido(titulo):
            raise DadosInvalidos("título inválido")
        if not _texto_valido(autor):
            raise DadosInvalidos("autor inválido")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("quantidade inválida")
        if isbn in self._livros:
            raise CadastroDuplicado("ISBN já cadastrado")
        self._livros[isbn] = {
            "isbn": isbn,
            "titulo": titulo,
            "autor": autor,
            "quantidade": quantidade,
            "ativos": 0,
            "fila": deque(),
        }

    def cadastrar_usuario(self, id_usuario, nome, tipo):
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome inválido")
        if not isinstance(tipo, str) or tipo not in _PRAZOS:
            raise DadosInvalidos("tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado("usuário já cadastrado")
        self._usuarios[id_usuario] = {
            "id": id_usuario,
            "nome": nome,
            "tipo": tipo,
            "saldo": 0,
            "ativos": set(),
        }

    # ------------------------------------------------------------------ auxiliares
    def _usuario(self, id_usuario) -> dict:
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado("usuário não encontrado") from None

    def _livro(self, isbn) -> dict:
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado("livro não encontrado") from None

    def _emprestimo(self, id_emprestimo) -> dict:
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado("empréstimo não encontrado") from None

    def _bloqueado(self, usuario: dict, data) -> bool:
        if usuario["saldo"] > 0:
            return True
        for id_emp in usuario["ativos"]:
            if self._emprestimos[id_emp]["data_prevista"] < data:
                return True
        return False

    # ------------------------------------------------------------------ operações
    def emprestar(self, isbn, id_usuario, data):
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if not _data_valida(data):
            raise DadosInvalidos("data inválida")
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado("usuário bloqueado")
        if len(usuario["ativos"]) >= _LIMITES[usuario["tipo"]]:
            raise LimiteDeEmprestimosAtingido("limite atingido")
        if livro["ativos"] >= livro["quantidade"]:
            raise ExemplarIndisponivel("sem exemplares")
        fila = livro["fila"]
        if fila and fila[0] != id_usuario:
            raise ExemplarIndisponivel("exemplar reservado")

        if fila:
            fila.popleft()
        id_emp = self._proximo_id
        self._proximo_id += 1
        prazo = _PRAZOS[usuario["tipo"]]
        self._emprestimos[id_emp] = {
            "id_emprestimo": id_emp,
            "isbn": isbn,
            "id_usuario": id_usuario,
            "data_emprestimo": data,
            "data_prevista": data + datetime.timedelta(days=prazo),
            "data_devolucao": None,
            "renovacoes": 0,
            "multa": 0,
        }
        self._ativos.add(id_emp)
        usuario["ativos"].add(id_emp)
        livro["ativos"] += 1
        return id_emp

    def devolver(self, id_emprestimo, data):
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        if not _data_valida(data) or data < emp["data_emprestimo"]:
            raise DadosInvalidos("data inválida")
        atraso = (data - emp["data_prevista"]).days
        multa = min(max(atraso, 0) * _MULTA_DIA, _TETO_MULTA)

        usuario = self._usuarios[emp["id_usuario"]]
        livro = self._livros[emp["isbn"]]
        emp["data_devolucao"] = data
        emp["multa"] = multa
        usuario["saldo"] += multa
        usuario["ativos"].discard(id_emprestimo)
        self._ativos.discard(id_emprestimo)
        livro["ativos"] -= 1
        return multa

    def renovar(self, id_emprestimo, data):
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        if not _data_valida(data):
            raise DadosInvalidos("data inválida")
        if emp["data_prevista"] < data:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emp["renovacoes"] >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if self._livros[emp["isbn"]]["fila"]:
            raise RenovacaoNaoPermitida("livro com fila de reservas")
        usuario = self._usuarios[emp["id_usuario"]]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")

        nova = emp["data_prevista"] + datetime.timedelta(days=_PRAZOS[usuario["tipo"]])
        emp["data_prevista"] = nova
        emp["renovacoes"] += 1
        return nova

    def reservar(self, isbn, id_usuario):
        self._usuario(id_usuario)
        livro = self._livro(isbn)
        fila = livro["fila"]
        if livro["ativos"] < livro["quantidade"] and not fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        usuario = self._usuarios[id_usuario]
        for id_emp in usuario["ativos"]:
            if self._emprestimos[id_emp]["isbn"] == isbn:
                raise ReservaNaoPermitida("usuário já possui exemplar")
        fila.append(id_usuario)
        return len(fila)

    # ------------------------------------------------------------------ consultas
    def exemplares_disponiveis(self, isbn):
        livro = self._livro(isbn)
        return livro["quantidade"] - livro["ativos"]

    def saldo_devedor(self, id_usuario):
        return self._usuario(id_usuario)["saldo"]

    def pagar_multa(self, id_usuario, valor):
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0 or valor > usuario["saldo"]:
            raise DadosInvalidos("valor inválido")
        usuario["saldo"] -= valor
        return usuario["saldo"]

    def emprestimos_atrasados(self, data):
        if not _data_valida(data):
            raise DadosInvalidos("data inválida")
        return sorted(
            i for i in self._ativos if self._emprestimos[i]["data_prevista"] < data
        )

    def consultar_emprestimo(self, id_emprestimo):
        return dict(self._emprestimo(id_emprestimo))
