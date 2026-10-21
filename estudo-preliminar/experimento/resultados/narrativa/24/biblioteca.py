"""Sistema de Empréstimos de Biblioteca (em memória)."""

from __future__ import annotations

from collections import deque
from datetime import date, timedelta


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


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


class Biblioteca:
    def __init__(self):
        self._livros = {}          # isbn -> dict(titulo, autor, quantidade, ativos)
        self._usuarios = {}        # id -> dict(nome, tipo, saldo, ativos:set)
        self._emprestimos = {}     # id -> dict
        self._ativos = {}          # id -> None (ordem de inserção = ordem crescente)
        self._filas = {}           # isbn -> deque de ids de usuário
        self._proximo_id = 1

    # ------------------------------------------------------------ cadastros
    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
        if not (isinstance(isbn, str) and len(isbn) == 13
                and all(c in "0123456789" for c in isbn)):
            raise DadosInvalidos("ISBN inválido")
        if not _texto_valido(titulo):
            raise DadosInvalidos("Título inválido")
        if not _texto_valido(autor):
            raise DadosInvalidos("Autor inválido")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("Quantidade inválida")
        if isbn in self._livros:
            raise CadastroDuplicado("ISBN já cadastrado")
        self._livros[isbn] = {
            "titulo": titulo,
            "autor": autor,
            "quantidade": quantidade,
            "ativos": 0,
        }

    def cadastrar_usuario(self, id_usuario, nome, tipo):
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("Id inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("Nome inválido")
        if not isinstance(tipo, str) or tipo not in _PRAZOS:
            raise DadosInvalidos("Tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado("Usuário já cadastrado")
        self._usuarios[id_usuario] = {
            "nome": nome,
            "tipo": tipo,
            "saldo": 0,
            "ativos": set(),
        }

    # ------------------------------------------------------------ auxiliares
    def _usuario(self, id_usuario):
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado("Usuário não encontrado") from None

    def _livro(self, isbn):
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado("Livro não encontrado") from None

    def _emprestimo(self, id_emprestimo):
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado("Empréstimo não encontrado") from None

    def _bloqueado(self, usuario, data) -> bool:
        if usuario["saldo"] > 0:
            return True
        for id_emp in usuario["ativos"]:
            if self._emprestimos[id_emp]["data_prevista"] < data:
                return True
        return False

    # ------------------------------------------------------------ operações
    def emprestar(self, isbn, id_usuario, data):
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado("Usuário bloqueado")
        if len(usuario["ativos"]) >= _LIMITES[usuario["tipo"]]:
            raise LimiteDeEmprestimosAtingido("Limite de empréstimos atingido")
        if livro["ativos"] >= livro["quantidade"]:
            raise ExemplarIndisponivel("Não há exemplar disponível")
        fila = self._filas.get(isbn)
        if fila and fila[0] != id_usuario:
            raise ExemplarIndisponivel("Exemplar reservado para outro usuário")

        if fila:
            fila.popleft()
            if not fila:
                del self._filas[isbn]

        id_emp = self._proximo_id
        self._proximo_id += 1
        self._emprestimos[id_emp] = {
            "id_emprestimo": id_emp,
            "isbn": isbn,
            "id_usuario": id_usuario,
            "data_emprestimo": data,
            "data_prevista": data + timedelta(days=_PRAZOS[usuario["tipo"]]),
            "data_devolucao": None,
            "renovacoes": 0,
            "multa": 0,
        }
        self._ativos[id_emp] = None
        usuario["ativos"].add(id_emp)
        livro["ativos"] += 1
        return id_emp

    def devolver(self, id_emprestimo, data):
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("Empréstimo já devolvido")
        if not isinstance(data, date) or data < emp["data_emprestimo"]:
            raise DadosInvalidos("Data de devolução inválida")

        atraso = (data - emp["data_prevista"]).days
        multa = min(max(0, atraso) * _MULTA_DIA, _MULTA_TETO)

        usuario = self._usuarios[emp["id_usuario"]]
        emp["data_devolucao"] = data
        emp["multa"] = multa
        usuario["saldo"] += multa
        usuario["ativos"].discard(id_emprestimo)
        self._livros[emp["isbn"]]["ativos"] -= 1
        self._ativos.pop(id_emprestimo, None)
        return multa

    def renovar(self, id_emprestimo, data):
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("Empréstimo já devolvido")
        if data > emp["data_prevista"]:
            raise RenovacaoNaoPermitida("Empréstimo atrasado")
        if emp["renovacoes"] >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("Limite de renovações atingido")
        if self._filas.get(emp["isbn"]):
            raise RenovacaoNaoPermitida("Livro com fila de reservas")
        usuario = self._usuarios[emp["id_usuario"]]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("Usuário bloqueado")

        emp["data_prevista"] = emp["data_prevista"] + timedelta(
            days=_PRAZOS[usuario["tipo"]])
        emp["renovacoes"] += 1
        return emp["data_prevista"]

    def reservar(self, isbn, id_usuario):
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        fila = self._filas.get(isbn)
        if not fila and livro["ativos"] < livro["quantidade"]:
            raise ReservaNaoPermitida("Há exemplar disponível")
        if fila and id_usuario in fila:
            raise ReservaNaoPermitida("Usuário já está na fila")
        for id_emp in usuario["ativos"]:
            if self._emprestimos[id_emp]["isbn"] == isbn:
                raise ReservaNaoPermitida("Usuário já possui exemplar do livro")

        if fila is None:
            fila = deque()
            self._filas[isbn] = fila
        fila.append(id_usuario)
        return len(fila)

    # ------------------------------------------------------------ consultas
    def exemplares_disponiveis(self, isbn):
        livro = self._livro(isbn)
        return livro["quantidade"] - livro["ativos"]

    def saldo_devedor(self, id_usuario):
        return self._usuario(id_usuario)["saldo"]

    def pagar_multa(self, id_usuario, valor):
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0 or valor > usuario["saldo"]:
            raise DadosInvalidos("Valor de pagamento inválido")
        usuario["saldo"] -= valor
        return usuario["saldo"]

    def emprestimos_atrasados(self, data):
        emps = self._emprestimos
        return sorted(i for i in self._ativos if emps[i]["data_prevista"] < data)

    def consultar_emprestimo(self, id_emprestimo):
        emp = self._emprestimo(id_emprestimo)
        return dict(emp)
