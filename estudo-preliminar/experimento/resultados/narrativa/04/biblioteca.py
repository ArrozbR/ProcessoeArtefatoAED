"""Sistema de Empréstimos de Biblioteca (em memória)."""

from __future__ import annotations

import datetime
from datetime import timedelta


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
_DIGITOS = set("0123456789")


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


class Biblioteca:
    def __init__(self) -> None:
        self._livros: dict[str, dict] = {}
        self._usuarios: dict[str, dict] = {}
        self._emprestimos: dict[int, dict] = {}
        self._ativos: dict[int, dict] = {}
        self._proximo_id = 1

    # ----------------------------------------------------------- auxiliares
    def _livro(self, isbn) -> dict:
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado(f"livro inexistente: {isbn!r}") from None

    def _usuario(self, id_usuario) -> dict:
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado(f"usuário inexistente: {id_usuario!r}") from None

    def _emprestimo(self, id_emprestimo) -> dict:
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado(
                f"empréstimo inexistente: {id_emprestimo!r}"
            ) from None

    def _bloqueado(self, usuario: dict, data) -> bool:
        if usuario["saldo"] > 0:
            return True
        for id_emp in usuario["ativos"]:
            if self._emprestimos[id_emp]["data_prevista"] < data:
                return True
        return False

    def _disponiveis(self, livro: dict) -> int:
        return livro["quantidade"] - livro["ativos"]

    # ------------------------------------------------------------ cadastros
    def cadastrar_livro(self, isbn, titulo, autor, quantidade) -> None:
        if not (isinstance(isbn, str) and len(isbn) == 13 and set(isbn) <= _DIGITOS):
            raise DadosInvalidos("ISBN deve ter exatamente 13 dígitos")
        if not _texto_valido(titulo):
            raise DadosInvalidos("título inválido")
        if not _texto_valido(autor):
            raise DadosInvalidos("autor inválido")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("quantidade inválida")
        if isbn in self._livros:
            raise CadastroDuplicado(f"ISBN já cadastrado: {isbn}")
        self._livros[isbn] = {
            "isbn": isbn,
            "titulo": titulo,
            "autor": autor,
            "quantidade": quantidade,
            "ativos": 0,
            "fila": [],
        }

    def cadastrar_usuario(self, id_usuario, nome, tipo) -> None:
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id de usuário inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome inválido")
        if not isinstance(tipo, str) or tipo not in _PRAZOS:
            raise DadosInvalidos("tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(f"usuário já cadastrado: {id_usuario}")
        self._usuarios[id_usuario] = {
            "id": id_usuario,
            "nome": nome,
            "tipo": tipo,
            "saldo": 0,
            "ativos": set(),
        }

    # ---------------------------------------------------------- empréstimos
    def emprestar(self, isbn, id_usuario, data) -> int:
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(f"usuário bloqueado: {id_usuario}")
        if len(usuario["ativos"]) >= _LIMITES[usuario["tipo"]]:
            raise LimiteDeEmprestimosAtingido(f"limite atingido: {id_usuario}")
        if self._disponiveis(livro) <= 0:
            raise ExemplarIndisponivel(f"sem exemplar disponível: {isbn}")
        fila = livro["fila"]
        if fila and fila[0] != id_usuario:
            raise ExemplarIndisponivel(f"exemplar reservado: {isbn}")

        id_emp = self._proximo_id
        prevista = data + timedelta(days=_PRAZOS[usuario["tipo"]])
        emprestimo = {
            "id_emprestimo": id_emp,
            "isbn": isbn,
            "id_usuario": id_usuario,
            "data_emprestimo": data,
            "data_prevista": prevista,
            "data_devolucao": None,
            "renovacoes": 0,
            "multa": 0,
        }
        if fila:
            fila.pop(0)
        self._proximo_id += 1
        self._emprestimos[id_emp] = emprestimo
        self._ativos[id_emp] = emprestimo
        usuario["ativos"].add(id_emp)
        livro["ativos"] += 1
        return id_emp

    def devolver(self, id_emprestimo, data) -> int:
        emprestimo = self._emprestimo(id_emprestimo)
        if emprestimo["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido(f"empréstimo já devolvido: {id_emprestimo}")
        if not isinstance(data, datetime.date) or data < emprestimo["data_emprestimo"]:
            raise DadosInvalidos("data de devolução inválida")

        atraso = (data - emprestimo["data_prevista"]).days
        multa = min(max(atraso, 0) * _MULTA_DIA, _TETO_MULTA)

        usuario = self._usuarios[emprestimo["id_usuario"]]
        livro = self._livros[emprestimo["isbn"]]
        emprestimo["data_devolucao"] = data
        emprestimo["multa"] = multa
        usuario["saldo"] += multa
        usuario["ativos"].discard(emprestimo["id_emprestimo"])
        self._ativos.pop(emprestimo["id_emprestimo"], None)
        livro["ativos"] -= 1
        return multa

    def renovar(self, id_emprestimo, data):
        emprestimo = self._emprestimo(id_emprestimo)
        if emprestimo["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido(f"empréstimo já devolvido: {id_emprestimo}")
        if data > emprestimo["data_prevista"]:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emprestimo["renovacoes"] >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        livro = self._livros[emprestimo["isbn"]]
        if livro["fila"]:
            raise RenovacaoNaoPermitida("livro com fila de reservas")
        usuario = self._usuarios[emprestimo["id_usuario"]]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")

        nova = emprestimo["data_prevista"] + timedelta(days=_PRAZOS[usuario["tipo"]])
        emprestimo["data_prevista"] = nova
        emprestimo["renovacoes"] += 1
        return nova

    # -------------------------------------------------------------- reservas
    def reservar(self, isbn, id_usuario) -> int:
        self._usuario(id_usuario)
        livro = self._livro(isbn)
        fila = livro["fila"]
        if self._disponiveis(livro) > 0 and not fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for id_emp in self._usuarios[id_usuario]["ativos"]:
            if self._emprestimos[id_emp]["isbn"] == isbn:
                raise ReservaNaoPermitida("usuário já está com exemplar do livro")
        fila.append(id_usuario)
        return len(fila)

    # ------------------------------------------------------------- consultas
    def exemplares_disponiveis(self, isbn) -> int:
        return self._disponiveis(self._livro(isbn))

    def saldo_devedor(self, id_usuario) -> int:
        return self._usuario(id_usuario)["saldo"]

    def pagar_multa(self, id_usuario, valor) -> int:
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0 or valor > usuario["saldo"]:
            raise DadosInvalidos("valor de pagamento inválido")
        usuario["saldo"] -= valor
        return usuario["saldo"]

    def emprestimos_atrasados(self, data) -> list[int]:
        return sorted(
            id_emp
            for id_emp, emp in self._ativos.items()
            if emp["data_prevista"] < data
        )

    def consultar_emprestimo(self, id_emprestimo) -> dict:
        emprestimo = self._emprestimo(id_emprestimo)
        return {
            "id_emprestimo": emprestimo["id_emprestimo"],
            "isbn": emprestimo["isbn"],
            "id_usuario": emprestimo["id_usuario"],
            "data_emprestimo": emprestimo["data_emprestimo"],
            "data_prevista": emprestimo["data_prevista"],
            "data_devolucao": emprestimo["data_devolucao"],
            "renovacoes": emprestimo["renovacoes"],
            "multa": emprestimo["multa"],
        }
