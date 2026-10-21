"""Sistema de Empréstimos de Biblioteca (em memória, somente biblioteca padrão)."""

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
_MULTA_TETO = 2000
_MAX_RENOVACOES = 2
_DIGITOS = frozenset("0123456789")


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


class Biblioteca:
    def __init__(self):
        self._livros = {}        # isbn -> dict(titulo, autor, quantidade, ativos)
        self._usuarios = {}      # id -> dict(nome, tipo, saldo, ativos:set)
        self._emprestimos = {}   # id -> dict
        self._ativos = set()     # ids de empréstimos ativos
        self._filas = {}         # isbn -> deque de id_usuario
        self._proximo_id = 1

    # ------------------------------------------------------------ auxiliares
    def _usuario(self, id_usuario):
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado(f"usuário {id_usuario!r} não encontrado")

    def _livro(self, isbn):
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado(f"livro {isbn!r} não encontrado")

    def _emprestimo(self, id_emprestimo):
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado(
                f"empréstimo {id_emprestimo!r} não encontrado")

    def _bloqueado(self, usuario, data) -> bool:
        if usuario["saldo"] > 0:
            return True
        for id_emp in usuario["ativos"]:
            if self._emprestimos[id_emp]["data_prevista"] < data:
                return True
        return False

    def _disponiveis(self, livro) -> int:
        return livro["quantidade"] - livro["ativos"]

    # ------------------------------------------------------------ cadastros
    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
        if not (isinstance(isbn, str) and len(isbn) == 13
                and all(c in _DIGITOS for c in isbn)):
            raise DadosInvalidos("ISBN deve ter exatamente 13 dígitos")
        if not _texto_valido(titulo):
            raise DadosInvalidos("título inválido")
        if not _texto_valido(autor):
            raise DadosInvalidos("autor inválido")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("quantidade inválida")
        if isbn in self._livros:
            raise CadastroDuplicado(f"ISBN {isbn} já cadastrado")
        self._livros[isbn] = {
            "titulo": titulo,
            "autor": autor,
            "quantidade": quantidade,
            "ativos": 0,
        }

    def cadastrar_usuario(self, id_usuario, nome, tipo):
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id de usuário inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome inválido")
        if not isinstance(tipo, str) or tipo not in _PRAZOS:
            raise DadosInvalidos("tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(f"usuário {id_usuario} já cadastrado")
        self._usuarios[id_usuario] = {
            "nome": nome,
            "tipo": tipo,
            "saldo": 0,
            "ativos": set(),
        }

    # ------------------------------------------------------------ empréstimos
    def emprestar(self, isbn, id_usuario, data):
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(f"usuário {id_usuario} bloqueado")
        if len(usuario["ativos"]) >= _LIMITES[usuario["tipo"]]:
            raise LimiteDeEmprestimosAtingido(
                f"usuário {id_usuario} atingiu o limite de empréstimos")
        if self._disponiveis(livro) <= 0:
            raise ExemplarIndisponivel("todos os exemplares estão emprestados")
        fila = self._filas.get(isbn)
        primeiro_da_fila = False
        if fila:
            if fila[0] != id_usuario:
                raise ExemplarIndisponivel(
                    "há fila de reservas e o usuário não é o primeiro")
            primeiro_da_fila = True

        id_emp = self._proximo_id
        prevista = data + datetime.timedelta(days=_PRAZOS[usuario["tipo"]])
        self._emprestimos[id_emp] = {
            "id_emprestimo": id_emp,
            "isbn": isbn,
            "id_usuario": id_usuario,
            "data_emprestimo": data,
            "data_prevista": prevista,
            "data_devolucao": None,
            "renovacoes": 0,
            "multa": 0,
        }
        self._proximo_id += 1
        self._ativos.add(id_emp)
        usuario["ativos"].add(id_emp)
        livro["ativos"] += 1
        if primeiro_da_fila:
            fila.popleft()
            if not fila:
                del self._filas[isbn]
        return id_emp

    def devolver(self, id_emprestimo, data):
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido(
                f"empréstimo {id_emprestimo} já devolvido")
        if data < emp["data_emprestimo"]:
            raise DadosInvalidos(
                "data de devolução anterior à data do empréstimo")
        atraso = (data - emp["data_prevista"]).days
        multa = min(max(0, atraso) * _MULTA_DIA, _MULTA_TETO)

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
            raise EmprestimoJaDevolvido(
                f"empréstimo {id_emprestimo} já devolvido")
        if emp["data_prevista"] < data:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emp["renovacoes"] >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if self._filas.get(emp["isbn"]):
            raise RenovacaoNaoPermitida("livro com fila de reservas")
        usuario = self._usuarios[emp["id_usuario"]]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")
        nova = emp["data_prevista"] + datetime.timedelta(
            days=_PRAZOS[usuario["tipo"]])
        emp["data_prevista"] = nova
        emp["renovacoes"] += 1
        return nova

    # ------------------------------------------------------------ reservas
    def reservar(self, isbn, id_usuario):
        self._usuario(id_usuario)
        livro = self._livro(isbn)
        fila = self._filas.get(isbn)
        if self._disponiveis(livro) > 0 and not fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if fila and id_usuario in fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for id_emp in self._usuarios[id_usuario]["ativos"]:
            if self._emprestimos[id_emp]["isbn"] == isbn:
                raise ReservaNaoPermitida(
                    "usuário já está com um exemplar desse livro")
        if fila is None:
            fila = deque()
            self._filas[isbn] = fila
        fila.append(id_usuario)
        return len(fila)

    # ------------------------------------------------------------ consultas
    def exemplares_disponiveis(self, isbn):
        return self._disponiveis(self._livro(isbn))

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
            id_emp for id_emp in self._ativos
            if self._emprestimos[id_emp]["data_prevista"] < data
        )

    def consultar_emprestimo(self, id_emprestimo):
        return dict(self._emprestimo(id_emprestimo))
