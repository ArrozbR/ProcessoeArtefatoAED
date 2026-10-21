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


_PRAZO = {"aluno": 14, "professor": 28}
_LIMITE = {"aluno": 3, "professor": 5}
_MULTA_DIA = 100
_MULTA_TETO = 2000
_MAX_RENOVACOES = 2


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _data_valida(valor) -> bool:
    return isinstance(valor, datetime.date)


class Biblioteca:
    def __init__(self):
        self._livros = {}          # isbn -> dict(titulo, autor, quantidade)
        self._usuarios = {}        # id -> dict(nome, tipo, saldo)
        self._emprestimos = {}     # id -> dict
        self._ativos_usuario = {}  # id_usuario -> set(id_emprestimo)
        self._ativos_livro = {}    # isbn -> set(id_emprestimo)
        self._ativos = set()       # ids de empréstimos ativos
        self._filas = {}           # isbn -> deque(id_usuario)
        self._proximo_id = 1

    # ------------------------------------------------------------------ util
    def _usuario(self, id_usuario):
        try:
            u = self._usuarios.get(id_usuario)
        except TypeError:
            u = None
        if u is None:
            raise UsuarioNaoEncontrado(f"usuário {id_usuario!r} não encontrado")
        return u

    def _livro(self, isbn):
        try:
            l = self._livros.get(isbn)
        except TypeError:
            l = None
        if l is None:
            raise LivroNaoEncontrado(f"livro {isbn!r} não encontrado")
        return l

    def _emprestimo(self, id_emprestimo):
        try:
            e = self._emprestimos.get(id_emprestimo)
        except TypeError:
            e = None
        if e is None or isinstance(id_emprestimo, bool):
            raise EmprestimoNaoEncontrado(f"empréstimo {id_emprestimo!r} não encontrado")
        return e

    def _bloqueado(self, id_usuario, data) -> bool:
        if self._usuarios[id_usuario]["saldo"] > 0:
            return True
        for id_emp in self._ativos_usuario[id_usuario]:
            if self._emprestimos[id_emp]["data_prevista"] < data:
                return True
        return False

    def _disponiveis(self, isbn) -> int:
        return self._livros[isbn]["quantidade"] - len(self._ativos_livro[isbn])

    # ------------------------------------------------------------- cadastros
    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
        if not (isinstance(isbn, str) and len(isbn) == 13
                and all(c in "0123456789" for c in isbn)):
            raise DadosInvalidos("ISBN deve ter exatamente 13 dígitos")
        if not _texto_valido(titulo):
            raise DadosInvalidos("título inválido")
        if not _texto_valido(autor):
            raise DadosInvalidos("autor inválido")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("quantidade inválida")
        if isbn in self._livros:
            raise CadastroDuplicado(f"ISBN {isbn} já cadastrado")
        self._livros[isbn] = {"titulo": titulo, "autor": autor, "quantidade": quantidade}
        self._ativos_livro[isbn] = set()
        self._filas[isbn] = deque()

    def cadastrar_usuario(self, id_usuario, nome, tipo):
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id de usuário inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome inválido")
        if not isinstance(tipo, str) or tipo not in _PRAZO:
            raise DadosInvalidos("tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(f"usuário {id_usuario} já cadastrado")
        self._usuarios[id_usuario] = {"nome": nome, "tipo": tipo, "saldo": 0}
        self._ativos_usuario[id_usuario] = set()

    # ------------------------------------------------------------ empréstimo
    def emprestar(self, isbn, id_usuario, data):
        usuario = self._usuario(id_usuario)
        self._livro(isbn)
        if not _data_valida(data):
            raise DadosInvalidos("data inválida")
        if self._bloqueado(id_usuario, data):
            raise UsuarioBloqueado(f"usuário {id_usuario} bloqueado")
        tipo = usuario["tipo"]
        if len(self._ativos_usuario[id_usuario]) >= _LIMITE[tipo]:
            raise LimiteDeEmprestimosAtingido(f"limite de empréstimos de {id_usuario}")
        fila = self._filas[isbn]
        if self._disponiveis(isbn) <= 0:
            raise ExemplarIndisponivel("todos os exemplares estão emprestados")
        if fila and fila[0] != id_usuario:
            raise ExemplarIndisponivel("há fila de reservas para o livro")

        if fila and fila[0] == id_usuario:
            fila.popleft()
        id_emp = self._proximo_id
        self._proximo_id += 1
        self._emprestimos[id_emp] = {
            "id_emprestimo": id_emp,
            "isbn": isbn,
            "id_usuario": id_usuario,
            "data_emprestimo": data,
            "data_prevista": data + datetime.timedelta(days=_PRAZO[tipo]),
            "data_devolucao": None,
            "renovacoes": 0,
            "multa": 0,
        }
        self._ativos.add(id_emp)
        self._ativos_usuario[id_usuario].add(id_emp)
        self._ativos_livro[isbn].add(id_emp)
        return id_emp

    def devolver(self, id_emprestimo, data):
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido(f"empréstimo {id_emprestimo} já devolvido")
        if not _data_valida(data) or data < emp["data_emprestimo"]:
            raise DadosInvalidos("data de devolução inválida")
        atraso = (data - emp["data_prevista"]).days
        multa = min(max(0, atraso) * _MULTA_DIA, _MULTA_TETO)

        emp["data_devolucao"] = data
        emp["multa"] = multa
        self._usuarios[emp["id_usuario"]]["saldo"] += multa
        self._ativos.discard(id_emprestimo)
        self._ativos_usuario[emp["id_usuario"]].discard(id_emprestimo)
        self._ativos_livro[emp["isbn"]].discard(id_emprestimo)
        return multa

    def renovar(self, id_emprestimo, data):
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido(f"empréstimo {id_emprestimo} já devolvido")
        if not _data_valida(data):
            raise DadosInvalidos("data inválida")
        if emp["data_prevista"] < data:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emp["renovacoes"] >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if self._filas[emp["isbn"]]:
            raise RenovacaoNaoPermitida("livro com fila de reservas")
        if self._bloqueado(emp["id_usuario"], data):
            raise RenovacaoNaoPermitida("usuário bloqueado")
        tipo = self._usuarios[emp["id_usuario"]]["tipo"]
        nova = emp["data_prevista"] + datetime.timedelta(days=_PRAZO[tipo])
        emp["data_prevista"] = nova
        emp["renovacoes"] += 1
        return nova

    # -------------------------------------------------------------- reservas
    def reservar(self, isbn, id_usuario):
        self._usuario(id_usuario)
        self._livro(isbn)
        fila = self._filas[isbn]
        if self._disponiveis(isbn) > 0 and not fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for id_emp in self._ativos_usuario[id_usuario]:
            if self._emprestimos[id_emp]["isbn"] == isbn:
                raise ReservaNaoPermitida("usuário já está com um exemplar do livro")
        fila.append(id_usuario)
        return len(fila)

    # -------------------------------------------------------------- consultas
    def exemplares_disponiveis(self, isbn):
        self._livro(isbn)
        return self._disponiveis(isbn)

    def saldo_devedor(self, id_usuario):
        return self._usuario(id_usuario)["saldo"]

    def pagar_multa(self, id_usuario, valor):
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0 or valor > usuario["saldo"]:
            raise DadosInvalidos("valor de pagamento inválido")
        usuario["saldo"] -= valor
        return usuario["saldo"]

    def emprestimos_atrasados(self, data):
        if not _data_valida(data):
            raise DadosInvalidos("data inválida")
        return sorted(
            i for i in self._ativos if self._emprestimos[i]["data_prevista"] < data
        )

    def consultar_emprestimo(self, id_emprestimo):
        emp = self._emprestimo(id_emprestimo)
        return dict(emp)
