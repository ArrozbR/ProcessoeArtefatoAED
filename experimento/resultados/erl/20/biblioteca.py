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
_DIGITOS = set("0123456789")


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _int_valido(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


class Biblioteca:
    def __init__(self) -> None:
        self._livros: dict[str, dict] = {}
        self._usuarios: dict[str, dict] = {}
        self._emprestimos: dict[int, dict] = {}
        self._proximo_id = 1

    # ----------------------------------------------------------- auxiliares
    def _usuario(self, id_usuario: str) -> dict:
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado(id_usuario) from None

    def _livro(self, isbn: str) -> dict:
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado(isbn) from None

    def _emprestimo(self, id_emprestimo: int) -> dict:
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado(id_emprestimo) from None

    def _ativos_usuario(self, usuario: dict) -> list[dict]:
        return [
            self._emprestimos[i]
            for i in usuario["emprestimos"]
            if self._emprestimos[i]["data_devolucao"] is None
        ]

    def _bloqueado(self, usuario: dict, data: date) -> bool:
        if usuario["saldo_devedor"] > 0:
            return True
        for emp in self._ativos_usuario(usuario):
            if data > emp["data_prevista"]:
                return True
        return False

    def _disponiveis(self, livro: dict) -> int:
        return livro["quantidade"] - livro["ativos"]

    # ------------------------------------------------------------ RF-01
    def cadastrar_livro(self, isbn: str, titulo: str, autor: str, quantidade: int) -> None:
        if not isinstance(isbn, str) or len(isbn) != 13 or not set(isbn) <= _DIGITOS:
            raise DadosInvalidos("isbn inválido")
        if not _texto_valido(titulo) or not _texto_valido(autor):
            raise DadosInvalidos("título ou autor inválido")
        if not _int_valido(quantidade) or quantidade < 1:
            raise DadosInvalidos("quantidade inválida")
        if isbn in self._livros:
            raise CadastroDuplicado(isbn)
        self._livros[isbn] = {
            "isbn": isbn,
            "titulo": titulo,
            "autor": autor,
            "quantidade": quantidade,
            "ativos": 0,
            "fila": [],
        }

    # ------------------------------------------------------------ RF-02
    def cadastrar_usuario(self, id_usuario: str, nome: str, tipo: str) -> None:
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id_usuario inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome inválido")
        if not isinstance(tipo, str) or tipo not in _PRAZO:
            raise DadosInvalidos("tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(id_usuario)
        self._usuarios[id_usuario] = {
            "id_usuario": id_usuario,
            "nome": nome,
            "tipo": tipo,
            "saldo_devedor": 0,
            "emprestimos": [],
        }

    # ------------------------------------------------------------ RF-03
    def emprestar(self, isbn: str, id_usuario: str, data: date) -> int:
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(id_usuario)
        if len(self._ativos_usuario(usuario)) >= _LIMITE[usuario["tipo"]]:
            raise LimiteDeEmprestimosAtingido(id_usuario)
        fila = livro["fila"]
        if self._disponiveis(livro) == 0 or (fila and fila[0] != id_usuario):
            raise ExemplarIndisponivel(isbn)

        id_emprestimo = self._proximo_id
        self._proximo_id += 1
        self._emprestimos[id_emprestimo] = {
            "id_emprestimo": id_emprestimo,
            "isbn": isbn,
            "id_usuario": id_usuario,
            "data_emprestimo": data,
            "data_prevista": data + timedelta(days=_PRAZO[usuario["tipo"]]),
            "data_devolucao": None,
            "renovacoes": 0,
            "multa": 0,
        }
        usuario["emprestimos"].append(id_emprestimo)
        livro["ativos"] += 1
        if fila and fila[0] == id_usuario:
            fila.pop(0)
        return id_emprestimo

    # ------------------------------------------------------------ RF-04
    def devolver(self, id_emprestimo: int, data: date) -> int:
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido(id_emprestimo)
        if data < emp["data_emprestimo"]:
            raise DadosInvalidos("data anterior ao empréstimo")
        dias = (data - emp["data_prevista"]).days
        if dias < 0:
            dias = 0
        multa = min(dias * _MULTA_DIA, _MULTA_MAX)
        emp["data_devolucao"] = data
        emp["multa"] = multa
        self._livros[emp["isbn"]]["ativos"] -= 1
        self._usuarios[emp["id_usuario"]]["saldo_devedor"] += multa
        return multa

    # ------------------------------------------------------------ RF-05
    def renovar(self, id_emprestimo: int, data: date) -> date:
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido(id_emprestimo)
        if data > emp["data_prevista"]:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emp["renovacoes"] >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if self._livros[emp["isbn"]]["fila"]:
            raise RenovacaoNaoPermitida("há reservas para o livro")
        usuario = self._usuarios[emp["id_usuario"]]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")
        emp["data_prevista"] = emp["data_prevista"] + timedelta(days=_PRAZO[usuario["tipo"]])
        emp["renovacoes"] += 1
        return emp["data_prevista"]

    # ------------------------------------------------------------ RF-06
    def reservar(self, isbn: str, id_usuario: str) -> int:
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        fila = livro["fila"]
        if self._disponiveis(livro) > 0 and not fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for emp in self._ativos_usuario(usuario):
            if emp["isbn"] == isbn:
                raise ReservaNaoPermitida("usuário tem empréstimo ativo do livro")
        fila.append(id_usuario)
        return len(fila)

    # ------------------------------------------------------------ RF-07
    def exemplares_disponiveis(self, isbn: str) -> int:
        return self._disponiveis(self._livro(isbn))

    # ------------------------------------------------------------ RF-08
    def saldo_devedor(self, id_usuario: str) -> int:
        return self._usuario(id_usuario)["saldo_devedor"]

    # ------------------------------------------------------------ RF-09
    def pagar_multa(self, id_usuario: str, valor: int) -> int:
        usuario = self._usuario(id_usuario)
        if not _int_valido(valor) or valor <= 0:
            raise DadosInvalidos("valor inválido")
        if valor > usuario["saldo_devedor"]:
            raise DadosInvalidos("valor maior que o saldo devedor")
        usuario["saldo_devedor"] -= valor
        return usuario["saldo_devedor"]

    # ------------------------------------------------------------ RF-10
    def emprestimos_atrasados(self, data: date) -> list[int]:
        return sorted(
            i
            for i, emp in self._emprestimos.items()
            if emp["data_devolucao"] is None and data > emp["data_prevista"]
        )

    # ------------------------------------------------------------ RF-11
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
