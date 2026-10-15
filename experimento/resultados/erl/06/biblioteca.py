"""Sistema de Empréstimos de Biblioteca (estado em memória)."""

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
_MULTA_POR_DIA = 100
_MULTA_MAXIMA = 2000
_MAX_RENOVACOES = 2


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _int_valido(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


class Biblioteca:
    def __init__(self):
        self._livros = {}        # isbn -> dict(titulo, autor, quantidade, ativos, fila)
        self._usuarios = {}      # id_usuario -> dict(nome, tipo, saldo, ativos: set de ids)
        self._emprestimos = {}   # id_emprestimo -> dict
        self._proximo_id = 1

    # ------------------------------------------------------------------ apoio
    def _usuario(self, id_usuario):
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado(f"Usuário não cadastrado: {id_usuario!r}")

    def _livro(self, isbn):
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado(f"Livro não cadastrado: {isbn!r}")

    def _emprestimo(self, id_emprestimo):
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado(
                f"Empréstimo inexistente: {id_emprestimo!r}")

    def _bloqueado(self, usuario, data) -> bool:
        if usuario["saldo"] > 0:
            return True
        for id_emp in usuario["ativos"]:
            if data > self._emprestimos[id_emp]["data_prevista"]:
                return True
        return False

    # ------------------------------------------------------------------ RF-01
    def cadastrar_livro(self, isbn: str, titulo: str, autor: str,
                        quantidade: int) -> None:
        if not (isinstance(isbn, str) and len(isbn) == 13
                and all(c in "0123456789" for c in isbn)):
            raise DadosInvalidos("ISBN deve ter exatamente 13 dígitos.")
        if not _texto_valido(titulo) or not _texto_valido(autor):
            raise DadosInvalidos("Título e autor são obrigatórios.")
        if not _int_valido(quantidade) or quantidade < 1:
            raise DadosInvalidos("Quantidade deve ser um inteiro maior ou igual a 1.")
        if isbn in self._livros:
            raise CadastroDuplicado(f"Livro já cadastrado: {isbn}")
        self._livros[isbn] = {
            "titulo": titulo,
            "autor": autor,
            "quantidade": quantidade,
            "ativos": 0,
            "fila": deque(),
        }

    # ------------------------------------------------------------------ RF-02
    def cadastrar_usuario(self, id_usuario: str, nome: str, tipo: str) -> None:
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id_usuario é obrigatório.")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome é obrigatório.")
        if not isinstance(tipo, str) or tipo not in _PRAZO:
            raise DadosInvalidos('tipo deve ser "aluno" ou "professor".')
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(f"Usuário já cadastrado: {id_usuario}")
        self._usuarios[id_usuario] = {
            "nome": nome,
            "tipo": tipo,
            "saldo": 0,
            "ativos": set(),
        }

    # ------------------------------------------------------------------ RF-03
    def emprestar(self, isbn: str, id_usuario: str, data: date) -> int:
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(f"Usuário bloqueado: {id_usuario}")
        if len(usuario["ativos"]) >= _LIMITE[usuario["tipo"]]:
            raise LimiteDeEmprestimosAtingido(
                f"Limite de empréstimos atingido: {id_usuario}")
        disponiveis = livro["quantidade"] - livro["ativos"]
        fila = livro["fila"]
        if disponiveis == 0 or (fila and fila[0] != id_usuario):
            raise ExemplarIndisponivel(f"Sem exemplar disponível: {isbn}")

        id_emp = self._proximo_id
        self._proximo_id += 1
        self._emprestimos[id_emp] = {
            "id_emprestimo": id_emp,
            "isbn": isbn,
            "id_usuario": id_usuario,
            "data_emprestimo": data,
            "data_prevista": data + timedelta(days=_PRAZO[usuario["tipo"]]),
            "data_devolucao": None,
            "renovacoes": 0,
            "multa": 0,
        }
        livro["ativos"] += 1
        usuario["ativos"].add(id_emp)
        if fila and fila[0] == id_usuario:
            fila.popleft()
        return id_emp

    # ------------------------------------------------------------------ RF-04
    def devolver(self, id_emprestimo: int, data: date) -> int:
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido(
                f"Empréstimo já devolvido: {id_emprestimo}")
        if data < emp["data_emprestimo"]:
            raise DadosInvalidos(
                "Data de devolução anterior à data de empréstimo.")

        dias_atraso = (data - emp["data_prevista"]).days
        if dias_atraso < 0:
            dias_atraso = 0
        multa = min(dias_atraso * _MULTA_POR_DIA, _MULTA_MAXIMA)

        emp["data_devolucao"] = data
        emp["multa"] = multa
        self._livros[emp["isbn"]]["ativos"] -= 1
        usuario = self._usuarios[emp["id_usuario"]]
        usuario["ativos"].discard(id_emprestimo)
        usuario["saldo"] += multa
        return multa

    # ------------------------------------------------------------------ RF-05
    def renovar(self, id_emprestimo: int, data: date) -> date:
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido(
                f"Empréstimo já devolvido: {id_emprestimo}")
        if data > emp["data_prevista"]:
            raise RenovacaoNaoPermitida("Empréstimo atrasado.")
        if emp["renovacoes"] >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("Limite de renovações atingido.")
        if self._livros[emp["isbn"]]["fila"]:
            raise RenovacaoNaoPermitida("Há reservas para este livro.")
        usuario = self._usuarios[emp["id_usuario"]]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("Usuário bloqueado.")

        nova = emp["data_prevista"] + timedelta(days=_PRAZO[usuario["tipo"]])
        emp["data_prevista"] = nova
        emp["renovacoes"] += 1
        return nova

    # ------------------------------------------------------------------ RF-06
    def reservar(self, isbn: str, id_usuario: str) -> int:
        self._usuario(id_usuario)
        livro = self._livro(isbn)
        fila = livro["fila"]
        disponiveis = livro["quantidade"] - livro["ativos"]
        if disponiveis > 0 and not fila:
            raise ReservaNaoPermitida("Há exemplar disponível; não é preciso reservar.")
        if id_usuario in fila:
            raise ReservaNaoPermitida("Usuário já está na fila deste livro.")
        for id_emp in self._usuarios[id_usuario]["ativos"]:
            if self._emprestimos[id_emp]["isbn"] == isbn:
                raise ReservaNaoPermitida(
                    "Usuário tem empréstimo ativo deste livro.")
        fila.append(id_usuario)
        return len(fila)

    # ------------------------------------------------------------------ RF-07
    def exemplares_disponiveis(self, isbn: str) -> int:
        livro = self._livro(isbn)
        return livro["quantidade"] - livro["ativos"]

    # ------------------------------------------------------------------ RF-08
    def saldo_devedor(self, id_usuario: str) -> int:
        return self._usuario(id_usuario)["saldo"]

    # ------------------------------------------------------------------ RF-09
    def pagar_multa(self, id_usuario: str, valor: int) -> int:
        usuario = self._usuario(id_usuario)
        if not _int_valido(valor) or valor <= 0:
            raise DadosInvalidos("Valor deve ser um inteiro positivo.")
        if valor > usuario["saldo"]:
            raise DadosInvalidos("Valor maior que o saldo devedor.")
        usuario["saldo"] -= valor
        return usuario["saldo"]

    # ------------------------------------------------------------------ RF-10
    def emprestimos_atrasados(self, data: date) -> list[int]:
        return sorted(
            id_emp for id_emp, emp in self._emprestimos.items()
            if emp["data_devolucao"] is None and data > emp["data_prevista"]
        )

    # ------------------------------------------------------------------ RF-11
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
