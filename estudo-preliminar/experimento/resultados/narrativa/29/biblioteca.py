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


class Biblioteca:
    def __init__(self):
        self._livros = {}  # isbn -> dict
        self._usuarios = {}  # id -> dict
        self._emprestimos = {}  # id -> dict
        self._proximo_id = 1
        self._ativos = set()  # ids de empréstimos ativos
        self._ativos_por_usuario = {}  # id_usuario -> set de ids
        self._ativos_por_livro = {}  # isbn -> int
        self._filas = {}  # isbn -> deque de id_usuario

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
        }
        self._ativos_por_livro[isbn] = 0
        self._filas[isbn] = deque()

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
            "id_usuario": id_usuario,
            "nome": nome,
            "tipo": tipo,
            "saldo": 0,
        }
        self._ativos_por_usuario[id_usuario] = set()

    # ------------------------------------------------------------------ auxiliares
    def _bloqueado(self, id_usuario, data) -> bool:
        if self._usuarios[id_usuario]["saldo"] > 0:
            return True
        for id_emp in self._ativos_por_usuario[id_usuario]:
            if self._emprestimos[id_emp]["data_prevista"] < data:
                return True
        return False

    def _disponiveis(self, isbn) -> int:
        return self._livros[isbn]["quantidade"] - self._ativos_por_livro[isbn]

    def _obter_emprestimo(self, id_emprestimo):
        try:
            emp = self._emprestimos.get(id_emprestimo)
        except TypeError:
            emp = None
        if emp is None:
            raise EmprestimoNaoEncontrado("empréstimo não encontrado")
        return emp

    def _obter_usuario(self, id_usuario):
        try:
            usuario = self._usuarios.get(id_usuario)
        except TypeError:
            usuario = None
        if usuario is None:
            raise UsuarioNaoEncontrado("usuário não encontrado")
        return usuario

    def _verificar_livro(self, isbn):
        try:
            existe = isbn in self._livros
        except TypeError:
            existe = False
        if not existe:
            raise LivroNaoEncontrado("livro não encontrado")

    # ------------------------------------------------------------------ operações
    def emprestar(self, isbn, id_usuario, data):
        usuario = self._obter_usuario(id_usuario)
        self._verificar_livro(isbn)
        if self._bloqueado(id_usuario, data):
            raise UsuarioBloqueado("usuário bloqueado")
        tipo = usuario["tipo"]
        if len(self._ativos_por_usuario[id_usuario]) >= _LIMITES[tipo]:
            raise LimiteDeEmprestimosAtingido("limite de empréstimos atingido")
        if self._disponiveis(isbn) <= 0:
            raise ExemplarIndisponivel("sem exemplares disponíveis")
        fila = self._filas[isbn]
        if fila and fila[0] != id_usuario:
            raise ExemplarIndisponivel("exemplar reservado para outro usuário")

        if fila:
            fila.popleft()
        id_emp = self._proximo_id
        self._proximo_id += 1
        self._emprestimos[id_emp] = {
            "id_emprestimo": id_emp,
            "isbn": isbn,
            "id_usuario": id_usuario,
            "data_emprestimo": data,
            "data_prevista": data + datetime.timedelta(days=_PRAZOS[tipo]),
            "data_devolucao": None,
            "renovacoes": 0,
            "multa": 0,
        }
        self._ativos.add(id_emp)
        self._ativos_por_usuario[id_usuario].add(id_emp)
        self._ativos_por_livro[isbn] += 1
        return id_emp

    def devolver(self, id_emprestimo, data):
        emp = self._obter_emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        if data < emp["data_emprestimo"]:
            raise DadosInvalidos("data de devolução anterior ao empréstimo")
        dias_atraso = (data - emp["data_prevista"]).days
        multa = min(max(0, dias_atraso) * _MULTA_DIA, _TETO_MULTA)

        emp["data_devolucao"] = data
        emp["multa"] = multa
        id_emp = emp["id_emprestimo"]
        self._ativos.discard(id_emp)
        self._ativos_por_usuario[emp["id_usuario"]].discard(id_emp)
        self._ativos_por_livro[emp["isbn"]] -= 1
        self._usuarios[emp["id_usuario"]]["saldo"] += multa
        return multa

    def renovar(self, id_emprestimo, data):
        emp = self._obter_emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        if data > emp["data_prevista"]:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emp["renovacoes"] >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if self._filas[emp["isbn"]]:
            raise RenovacaoNaoPermitida("livro com fila de reservas")
        if self._bloqueado(emp["id_usuario"], data):
            raise RenovacaoNaoPermitida("usuário bloqueado")
        tipo = self._usuarios[emp["id_usuario"]]["tipo"]
        emp["data_prevista"] = emp["data_prevista"] + datetime.timedelta(days=_PRAZOS[tipo])
        emp["renovacoes"] += 1
        return emp["data_prevista"]

    def reservar(self, isbn, id_usuario):
        self._obter_usuario(id_usuario)
        self._verificar_livro(isbn)
        fila = self._filas[isbn]
        if self._disponiveis(isbn) > 0 and not fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for id_emp in self._ativos_por_usuario[id_usuario]:
            if self._emprestimos[id_emp]["isbn"] == isbn:
                raise ReservaNaoPermitida("usuário já está com exemplar do livro")
        fila.append(id_usuario)
        return len(fila)

    # ------------------------------------------------------------------ consultas
    def exemplares_disponiveis(self, isbn):
        self._verificar_livro(isbn)
        return self._disponiveis(isbn)

    def saldo_devedor(self, id_usuario):
        return self._obter_usuario(id_usuario)["saldo"]

    def pagar_multa(self, id_usuario, valor):
        usuario = self._obter_usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0 or valor > usuario["saldo"]:
            raise DadosInvalidos("valor de pagamento inválido")
        usuario["saldo"] -= valor
        return usuario["saldo"]

    def emprestimos_atrasados(self, data):
        return sorted(
            id_emp for id_emp in self._ativos
            if self._emprestimos[id_emp]["data_prevista"] < data
        )

    def consultar_emprestimo(self, id_emprestimo):
        emp = self._obter_emprestimo(id_emprestimo)
        return dict(emp)
