"""Sistema de Empréstimos de Biblioteca (em memória, somente biblioteca padrão)."""

from __future__ import annotations

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


def _validar_data(valor) -> None:
    if not isinstance(valor, date):
        raise DadosInvalidos("data inválida")


class Biblioteca:
    def __init__(self):
        self._livros = {}          # isbn -> dict(titulo, autor, quantidade, ativos)
        self._usuarios = {}        # id -> dict(nome, tipo, saldo, ativos:set)
        self._emprestimos = {}     # id -> dict
        self._ativos = set()       # ids de empréstimos ativos
        self._filas = {}           # isbn -> list[id_usuario]
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
            "titulo": titulo,
            "autor": autor,
            "quantidade": quantidade,
            "ativos": 0,
        }
        self._filas[isbn] = []

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
            "nome": nome,
            "tipo": tipo,
            "saldo": 0,
            "ativos": set(),
        }

    # ------------------------------------------------------------------ auxiliares
    def _usuario(self, id_usuario):
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado("usuário não encontrado") from None

    def _livro(self, isbn):
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado("livro não encontrado") from None

    def _emprestimo(self, id_emprestimo):
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado("empréstimo não encontrado") from None

    def _bloqueado(self, usuario, data) -> bool:
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
        _validar_data(data)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado("usuário bloqueado")
        tipo = usuario["tipo"]
        if len(usuario["ativos"]) >= _LIMITES[tipo]:
            raise LimiteDeEmprestimosAtingido("limite de empréstimos atingido")
        if livro["quantidade"] - livro["ativos"] <= 0:
            raise ExemplarIndisponivel("sem exemplares disponíveis")
        fila = self._filas[isbn]
        if fila and fila[0] != id_usuario:
            raise ExemplarIndisponivel("exemplar reservado para outro usuário")

        if fila:
            fila.pop(0)
        id_emp = self._proximo_id
        self._proximo_id += 1
        self._emprestimos[id_emp] = {
            "id_emprestimo": id_emp,
            "isbn": isbn,
            "id_usuario": id_usuario,
            "data_emprestimo": data,
            "data_prevista": data + timedelta(days=_PRAZOS[tipo]),
            "data_devolucao": None,
            "renovacoes": 0,
            "multa": 0,
        }
        livro["ativos"] += 1
        usuario["ativos"].add(id_emp)
        self._ativos.add(id_emp)
        return id_emp

    def devolver(self, id_emprestimo, data):
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        _validar_data(data)
        if data < emp["data_emprestimo"]:
            raise DadosInvalidos("data de devolução anterior ao empréstimo")

        atraso = (data - emp["data_prevista"]).days
        multa = min(max(0, atraso) * _MULTA_DIA, _MULTA_TETO)

        usuario = self._usuarios[emp["id_usuario"]]
        livro = self._livros[emp["isbn"]]
        emp["data_devolucao"] = data
        emp["multa"] = multa
        usuario["saldo"] += multa
        usuario["ativos"].discard(id_emprestimo)
        livro["ativos"] -= 1
        self._ativos.discard(id_emprestimo)
        return multa

    def renovar(self, id_emprestimo, data):
        emp = self._emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        _validar_data(data)
        if emp["data_prevista"] < data:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emp["renovacoes"] >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if self._filas[emp["isbn"]]:
            raise RenovacaoNaoPermitida("livro com fila de reservas")
        usuario = self._usuarios[emp["id_usuario"]]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")

        nova = emp["data_prevista"] + timedelta(days=_PRAZOS[usuario["tipo"]])
        emp["data_prevista"] = nova
        emp["renovacoes"] += 1
        return nova

    def reservar(self, isbn, id_usuario):
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        fila = self._filas[isbn]
        if livro["quantidade"] - livro["ativos"] > 0 and not fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for id_emp in usuario["ativos"]:
            if self._emprestimos[id_emp]["isbn"] == isbn:
                raise ReservaNaoPermitida("usuário já possui exemplar do livro")
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
            raise DadosInvalidos("valor de pagamento inválido")
        usuario["saldo"] -= valor
        return usuario["saldo"]

    def emprestimos_atrasados(self, data):
        _validar_data(data)
        emps = self._emprestimos
        return sorted(i for i in self._ativos if emps[i]["data_prevista"] < data)

    def consultar_emprestimo(self, id_emprestimo):
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
