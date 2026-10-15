"""Sistema de Empréstimos de Biblioteca."""

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


_PRAZO = {"aluno": 14, "professor": 28}
_LIMITE = {"aluno": 3, "professor": 5}
_MULTA_DIA = 100
_MULTA_MAX = 2000
_MAX_RENOVACOES = 2


def _texto_valido(valor):
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


class Biblioteca:
    def __init__(self):
        self._livros = {}        # isbn -> dict(titulo, autor, quantidade, ativos, fila)
        self._usuarios = {}      # id -> dict(nome, tipo, saldo, ativos:set)
        self._emprestimos = {}   # id -> dict
        self._proximo_id = 1

    # ---------------- auxiliares ----------------

    def _usuario(self, id_usuario):
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado(id_usuario)

    def _livro(self, isbn):
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado(isbn)

    def _emprestimo(self, id_emprestimo):
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado(id_emprestimo)

    def _bloqueado(self, usuario, data):
        if usuario["saldo"] > 0:
            return True
        for id_emp in usuario["ativos"]:
            if data > self._emprestimos[id_emp]["data_prevista"]:
                return True
        return False

    # ---------------- RF-01 ----------------

    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
        if not (isinstance(isbn, str) and len(isbn) == 13
                and all(c in "0123456789" for c in isbn)):
            raise DadosInvalidos("isbn inválido")
        if not _texto_valido(titulo) or not _texto_valido(autor):
            raise DadosInvalidos("titulo ou autor inválido")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("quantidade inválida")
        if isbn in self._livros:
            raise CadastroDuplicado(isbn)
        self._livros[isbn] = {
            "titulo": titulo,
            "autor": autor,
            "quantidade": quantidade,
            "ativos": 0,
            "fila": [],
        }

    # ---------------- RF-02 ----------------

    def cadastrar_usuario(self, id_usuario, nome, tipo):
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id_usuario inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome inválido")
        if tipo not in ("aluno", "professor") or not isinstance(tipo, str):
            raise DadosInvalidos("tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(id_usuario)
        self._usuarios[id_usuario] = {
            "nome": nome,
            "tipo": tipo,
            "saldo": 0,
            "ativos": set(),
        }

    # ---------------- RF-03 ----------------

    def emprestar(self, isbn, id_usuario, data):
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(id_usuario)
        if len(usuario["ativos"]) >= _LIMITE[usuario["tipo"]]:
            raise LimiteDeEmprestimosAtingido(id_usuario)
        disponiveis = livro["quantidade"] - livro["ativos"]
        fila = livro["fila"]
        if disponiveis <= 0 or (fila and fila[0] != id_usuario):
            raise ExemplarIndisponivel(isbn)

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
            fila.pop(0)
        return id_emp

    # ---------------- RF-04 ----------------

    def devolver(self, id_emprestimo, data):
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
        usuario = self._usuarios[emp["id_usuario"]]
        usuario["ativos"].discard(id_emprestimo)
        usuario["saldo"] += multa
        return multa

    # ---------------- RF-05 ----------------

    def renovar(self, id_emprestimo, data):
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

        nova = emp["data_prevista"] + timedelta(days=_PRAZO[usuario["tipo"]])
        emp["data_prevista"] = nova
        emp["renovacoes"] += 1
        return nova

    # ---------------- RF-06 ----------------

    def reservar(self, isbn, id_usuario):
        self._usuario(id_usuario)
        livro = self._livro(isbn)
        fila = livro["fila"]
        disponiveis = livro["quantidade"] - livro["ativos"]
        if disponiveis > 0 and not fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for id_emp in self._usuarios[id_usuario]["ativos"]:
            if self._emprestimos[id_emp]["isbn"] == isbn:
                raise ReservaNaoPermitida("usuário já tem empréstimo ativo do livro")
        fila.append(id_usuario)
        return len(fila)

    # ---------------- RF-07 ----------------

    def exemplares_disponiveis(self, isbn):
        livro = self._livro(isbn)
        return livro["quantidade"] - livro["ativos"]

    # ---------------- RF-08 ----------------

    def saldo_devedor(self, id_usuario):
        return self._usuario(id_usuario)["saldo"]

    # ---------------- RF-09 ----------------

    def pagar_multa(self, id_usuario, valor):
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0:
            raise DadosInvalidos("valor inválido")
        if valor > usuario["saldo"]:
            raise DadosInvalidos("valor maior que o saldo devedor")
        usuario["saldo"] -= valor
        return usuario["saldo"]

    # ---------------- RF-10 ----------------

    def emprestimos_atrasados(self, data):
        return sorted(
            id_emp
            for id_emp, emp in self._emprestimos.items()
            if emp["data_devolucao"] is None and data > emp["data_prevista"]
        )

    # ---------------- RF-11 ----------------

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
