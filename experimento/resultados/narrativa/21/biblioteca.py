"""Sistema de Empréstimos de Biblioteca (em memória, somente biblioteca padrão)."""

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
_TETO_MULTA = 2000
_MAX_RENOVACOES = 2
_DIGITOS = frozenset("0123456789")


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


class Biblioteca:
    def __init__(self) -> None:
        self._livros: dict[str, dict] = {}
        self._usuarios: dict[str, dict] = {}
        self._emprestimos: dict[int, dict] = {}
        self._ativos: set[int] = set()
        self._ativos_por_usuario: dict[str, set[int]] = {}
        self._ativos_por_livro: dict[str, int] = {}
        self._filas: dict[str, deque] = {}
        self._proximo_id = 1

    # ------------------------------------------------------------ cadastros
    def cadastrar_livro(self, isbn, titulo, autor, quantidade) -> None:
        if not (isinstance(isbn, str) and len(isbn) == 13 and set(isbn) <= _DIGITOS):
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

    def cadastrar_usuario(self, id_usuario, nome, tipo) -> None:
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

    def _bloqueado(self, id_usuario, data) -> bool:
        if self._usuarios[id_usuario]["saldo"] > 0:
            return True
        for eid in self._ativos_por_usuario[id_usuario]:
            if self._emprestimos[eid]["data_prevista"] < data:
                return True
        return False

    def _disponiveis(self, isbn) -> int:
        return self._livros[isbn]["quantidade"] - self._ativos_por_livro[isbn]

    # ------------------------------------------------------------ operações
    def emprestar(self, isbn, id_usuario, data) -> int:
        usuario = self._usuario(id_usuario)
        self._livro(isbn)
        if not isinstance(data, date):
            raise DadosInvalidos("data inválida")
        if self._bloqueado(id_usuario, data):
            raise UsuarioBloqueado("usuário bloqueado")
        tipo = usuario["tipo"]
        if len(self._ativos_por_usuario[id_usuario]) >= _LIMITES[tipo]:
            raise LimiteDeEmprestimosAtingido("limite de empréstimos atingido")
        if self._disponiveis(isbn) <= 0:
            raise ExemplarIndisponivel("nenhum exemplar disponível")
        fila = self._filas[isbn]
        if fila and fila[0] != id_usuario:
            raise ExemplarIndisponivel("exemplar reservado para outro usuário")

        if fila and fila[0] == id_usuario:
            fila.popleft()
        eid = self._proximo_id
        self._proximo_id += 1
        self._emprestimos[eid] = {
            "id_emprestimo": eid,
            "isbn": isbn,
            "id_usuario": id_usuario,
            "data_emprestimo": data,
            "data_prevista": data + timedelta(days=_PRAZOS[tipo]),
            "data_devolucao": None,
            "renovacoes": 0,
            "multa": 0,
        }
        self._ativos.add(eid)
        self._ativos_por_usuario[id_usuario].add(eid)
        self._ativos_por_livro[isbn] += 1
        return eid

    def devolver(self, id_emprestimo, data) -> int:
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        if not isinstance(data, date) or data < emp["data_emprestimo"]:
            raise DadosInvalidos("data de devolução inválida")

        atraso = (data - emp["data_prevista"]).days
        multa = min(max(atraso, 0) * _MULTA_DIA, _TETO_MULTA)
        emp["data_devolucao"] = data
        emp["multa"] = multa
        self._usuarios[emp["id_usuario"]]["saldo"] += multa
        self._ativos.discard(emp["id_emprestimo"])
        self._ativos_por_usuario[emp["id_usuario"]].discard(emp["id_emprestimo"])
        self._ativos_por_livro[emp["isbn"]] -= 1
        return multa

    def renovar(self, id_emprestimo, data) -> date:
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        if not isinstance(data, date):
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
        emp["data_prevista"] = emp["data_prevista"] + timedelta(days=_PRAZOS[tipo])
        emp["renovacoes"] += 1
        return emp["data_prevista"]

    def reservar(self, isbn, id_usuario) -> int:
        self._usuario(id_usuario)
        self._livro(isbn)
        fila = self._filas[isbn]
        if self._disponiveis(isbn) > 0 and not fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for eid in self._ativos_por_usuario[id_usuario]:
            if self._emprestimos[eid]["isbn"] == isbn:
                raise ReservaNaoPermitida("usuário já possui exemplar deste livro")
        fila.append(id_usuario)
        return len(fila)

    # ------------------------------------------------------------ consultas
    def exemplares_disponiveis(self, isbn) -> int:
        self._livro(isbn)
        return self._disponiveis(isbn)

    def saldo_devedor(self, id_usuario) -> int:
        return self._usuario(id_usuario)["saldo"]

    def pagar_multa(self, id_usuario, valor) -> int:
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0 or valor > usuario["saldo"]:
            raise DadosInvalidos("valor de pagamento inválido")
        usuario["saldo"] -= valor
        return usuario["saldo"]

    def emprestimos_atrasados(self, data) -> list[int]:
        emps = self._emprestimos
        return sorted(eid for eid in self._ativos if emps[eid]["data_prevista"] < data)

    def consultar_emprestimo(self, id_emprestimo) -> dict:
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
