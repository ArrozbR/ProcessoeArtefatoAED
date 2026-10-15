"""Sistema de empréstimos de biblioteca acadêmica (em memória)."""

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
        self._livros: dict[str, dict] = {}
        self._usuarios: dict[str, dict] = {}
        self._emprestimos: dict[int, dict] = {}
        self._ativos: dict[int, dict] = {}
        self._ativos_por_usuario: dict[str, set[int]] = {}
        self._ativos_por_livro: dict[str, int] = {}
        self._filas: dict[str, deque] = {}
        self._proximo_id = 1

    # ------------------------------------------------------------ cadastros

    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
        if not (
            isinstance(isbn, str)
            and len(isbn) == 13
            and all(c in "0123456789" for c in isbn)
        ):
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

    # ------------------------------------------------------------ auxiliares

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

    def _bloqueado(self, id_usuario, data: date) -> bool:
        if self._usuarios[id_usuario]["saldo"] > 0:
            return True
        for id_emp in self._ativos_por_usuario[id_usuario]:
            if self._emprestimos[id_emp]["data_prevista"] < data:
                return True
        return False

    def _disponiveis(self, isbn) -> int:
        return self._livros[isbn]["quantidade"] - self._ativos_por_livro[isbn]

    # ------------------------------------------------------------ operações

    def emprestar(self, isbn, id_usuario, data):
        usuario = self._usuario(id_usuario)
        self._livro(isbn)
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
        emprestimo = {
            "id_emprestimo": id_emp,
            "isbn": isbn,
            "id_usuario": id_usuario,
            "data_emprestimo": data,
            "data_prevista": data + timedelta(days=_PRAZOS[tipo]),
            "data_devolucao": None,
            "renovacoes": 0,
            "multa": 0,
        }
        self._emprestimos[id_emp] = emprestimo
        self._ativos[id_emp] = emprestimo
        self._ativos_por_usuario[id_usuario].add(id_emp)
        self._ativos_por_livro[isbn] += 1
        return id_emp

    def devolver(self, id_emprestimo, data):
        emprestimo = self._emprestimo(id_emprestimo)
        if emprestimo["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        if not isinstance(data, date) or data < emprestimo["data_emprestimo"]:
            raise DadosInvalidos("data de devolução inválida")

        atraso = (data - emprestimo["data_prevista"]).days
        multa = min(max(atraso, 0) * _MULTA_DIA, _MULTA_TETO)

        emprestimo["data_devolucao"] = data
        emprestimo["multa"] = multa
        id_usuario = emprestimo["id_usuario"]
        self._usuarios[id_usuario]["saldo"] += multa
        del self._ativos[id_emprestimo]
        self._ativos_por_usuario[id_usuario].discard(id_emprestimo)
        self._ativos_por_livro[emprestimo["isbn"]] -= 1
        return multa

    def renovar(self, id_emprestimo, data):
        emprestimo = self._emprestimo(id_emprestimo)
        if emprestimo["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        if emprestimo["data_prevista"] < data:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emprestimo["renovacoes"] >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if self._filas[emprestimo["isbn"]]:
            raise RenovacaoNaoPermitida("livro com fila de reservas")
        id_usuario = emprestimo["id_usuario"]
        if self._bloqueado(id_usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")

        tipo = self._usuarios[id_usuario]["tipo"]
        emprestimo["data_prevista"] = emprestimo["data_prevista"] + timedelta(
            days=_PRAZOS[tipo]
        )
        emprestimo["renovacoes"] += 1
        return emprestimo["data_prevista"]

    def reservar(self, isbn, id_usuario):
        self._usuario(id_usuario)
        self._livro(isbn)
        fila = self._filas[isbn]
        if self._disponiveis(isbn) > 0 and not fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for id_emp in self._ativos_por_usuario[id_usuario]:
            if self._emprestimos[id_emp]["isbn"] == isbn:
                raise ReservaNaoPermitida("usuário já está com este livro")
        fila.append(id_usuario)
        return len(fila)

    # ------------------------------------------------------------ consultas

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
        return sorted(
            id_emp
            for id_emp, emp in self._ativos.items()
            if emp["data_prevista"] < data
        )

    def consultar_emprestimo(self, id_emprestimo):
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
