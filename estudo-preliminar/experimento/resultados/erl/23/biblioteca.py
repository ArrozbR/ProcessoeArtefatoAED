"""Sistema de Empréstimos de Biblioteca."""

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
_DIGITOS = set("0123456789")


def _texto_valido(valor):
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


class Biblioteca:
    def __init__(self):
        self._livros = {}  # isbn -> {"titulo", "autor", "quantidade", "ativos"}
        self._usuarios = {}  # id -> {"nome", "tipo", "saldo"}
        self._emprestimos = {}  # id -> dict
        self._ativos_usuario = {}  # id_usuario -> set de ids de empréstimo ativos
        self._ativos = set()  # ids de empréstimos ativos
        self._filas = {}  # isbn -> list de id_usuario
        self._proximo_id = 1

    # ------------------------------------------------------------------ auxiliares

    def _disponiveis(self, isbn):
        livro = self._livros[isbn]
        return livro["quantidade"] - livro["ativos"]

    def _bloqueado(self, id_usuario, data):
        if self._usuarios[id_usuario]["saldo"] > 0:
            return True
        for id_emp in self._ativos_usuario[id_usuario]:
            if data > self._emprestimos[id_emp]["data_prevista"]:
                return True
        return False

    def _obter_emprestimo(self, id_emprestimo):
        try:
            emp = self._emprestimos.get(id_emprestimo)
        except TypeError:
            emp = None
        if emp is None:
            raise EmprestimoNaoEncontrado(f"Empréstimo {id_emprestimo!r} não encontrado")
        return emp

    def _verificar_usuario(self, id_usuario):
        try:
            existe = id_usuario in self._usuarios
        except TypeError:
            existe = False
        if not existe:
            raise UsuarioNaoEncontrado(f"Usuário {id_usuario!r} não encontrado")

    def _verificar_livro(self, isbn):
        try:
            existe = isbn in self._livros
        except TypeError:
            existe = False
        if not existe:
            raise LivroNaoEncontrado(f"Livro {isbn!r} não encontrado")

    # ------------------------------------------------------------------ RF-01

    def cadastrar_livro(self, isbn: str, titulo: str, autor: str, quantidade: int) -> None:
        if not isinstance(isbn, str) or len(isbn) != 13 or not set(isbn) <= _DIGITOS:
            raise DadosInvalidos("ISBN inválido")
        if not _texto_valido(titulo) or not _texto_valido(autor):
            raise DadosInvalidos("Título ou autor inválido")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("Quantidade inválida")
        if isbn in self._livros:
            raise CadastroDuplicado(f"Livro {isbn} já cadastrado")
        self._livros[isbn] = {
            "titulo": titulo,
            "autor": autor,
            "quantidade": quantidade,
            "ativos": 0,
        }
        self._filas[isbn] = []

    # ------------------------------------------------------------------ RF-02

    def cadastrar_usuario(self, id_usuario: str, nome: str, tipo: str) -> None:
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id_usuario inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("Nome inválido")
        if not isinstance(tipo, str) or tipo not in _PRAZO:
            raise DadosInvalidos("Tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(f"Usuário {id_usuario} já cadastrado")
        self._usuarios[id_usuario] = {"nome": nome, "tipo": tipo, "saldo": 0}
        self._ativos_usuario[id_usuario] = set()

    # ------------------------------------------------------------------ RF-03

    def emprestar(self, isbn: str, id_usuario: str, data: date) -> int:
        self._verificar_usuario(id_usuario)
        self._verificar_livro(isbn)
        if self._bloqueado(id_usuario, data):
            raise UsuarioBloqueado(f"Usuário {id_usuario} bloqueado")
        usuario = self._usuarios[id_usuario]
        if len(self._ativos_usuario[id_usuario]) >= _LIMITE[usuario["tipo"]]:
            raise LimiteDeEmprestimosAtingido(f"Usuário {id_usuario} atingiu o limite")
        fila = self._filas[isbn]
        if self._disponiveis(isbn) == 0 or (fila and fila[0] != id_usuario):
            raise ExemplarIndisponivel(f"Sem exemplar disponível de {isbn}")

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
        self._livros[isbn]["ativos"] += 1
        self._ativos_usuario[id_usuario].add(id_emp)
        self._ativos.add(id_emp)
        if fila and fila[0] == id_usuario:
            fila.pop(0)
        return id_emp

    # ------------------------------------------------------------------ RF-04

    def devolver(self, id_emprestimo: int, data: date) -> int:
        emp = self._obter_emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido(f"Empréstimo {id_emprestimo} já devolvido")
        if data < emp["data_emprestimo"]:
            raise DadosInvalidos("Data de devolução anterior à data de empréstimo")

        dias = (data - emp["data_prevista"]).days
        if dias < 0:
            dias = 0
        multa = min(dias * _MULTA_DIA, _MULTA_MAX)

        emp["data_devolucao"] = data
        emp["multa"] = multa
        self._livros[emp["isbn"]]["ativos"] -= 1
        self._ativos_usuario[emp["id_usuario"]].discard(emp["id_emprestimo"])
        self._ativos.discard(emp["id_emprestimo"])
        self._usuarios[emp["id_usuario"]]["saldo"] += multa
        return multa

    # ------------------------------------------------------------------ RF-05

    def renovar(self, id_emprestimo: int, data: date) -> date:
        emp = self._obter_emprestimo(id_emprestimo)
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido(f"Empréstimo {id_emprestimo} já devolvido")
        if data > emp["data_prevista"]:
            raise RenovacaoNaoPermitida("Empréstimo atrasado")
        if emp["renovacoes"] >= 2:
            raise RenovacaoNaoPermitida("Limite de renovações atingido")
        if self._filas[emp["isbn"]]:
            raise RenovacaoNaoPermitida("Há reservas para o livro")
        if self._bloqueado(emp["id_usuario"], data):
            raise RenovacaoNaoPermitida("Usuário bloqueado")

        tipo = self._usuarios[emp["id_usuario"]]["tipo"]
        emp["data_prevista"] = emp["data_prevista"] + timedelta(days=_PRAZO[tipo])
        emp["renovacoes"] += 1
        return emp["data_prevista"]

    # ------------------------------------------------------------------ RF-06

    def reservar(self, isbn: str, id_usuario: str) -> int:
        self._verificar_usuario(id_usuario)
        self._verificar_livro(isbn)
        fila = self._filas[isbn]
        if self._disponiveis(isbn) > 0 and not fila:
            raise ReservaNaoPermitida("Há exemplar disponível")
        if id_usuario in fila:
            raise ReservaNaoPermitida("Usuário já está na fila")
        for id_emp in self._ativos_usuario[id_usuario]:
            if self._emprestimos[id_emp]["isbn"] == isbn:
                raise ReservaNaoPermitida("Usuário já tem empréstimo ativo desse livro")
        fila.append(id_usuario)
        return len(fila)

    # ------------------------------------------------------------------ RF-07

    def exemplares_disponiveis(self, isbn: str) -> int:
        self._verificar_livro(isbn)
        return self._disponiveis(isbn)

    # ------------------------------------------------------------------ RF-08

    def saldo_devedor(self, id_usuario: str) -> int:
        self._verificar_usuario(id_usuario)
        return self._usuarios[id_usuario]["saldo"]

    # ------------------------------------------------------------------ RF-09

    def pagar_multa(self, id_usuario: str, valor: int) -> int:
        self._verificar_usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0:
            raise DadosInvalidos("Valor inválido")
        usuario = self._usuarios[id_usuario]
        if valor > usuario["saldo"]:
            raise DadosInvalidos("Valor maior que o saldo devedor")
        usuario["saldo"] -= valor
        return usuario["saldo"]

    # ------------------------------------------------------------------ RF-10

    def emprestimos_atrasados(self, data: date) -> list:
        return sorted(
            id_emp
            for id_emp in self._ativos
            if data > self._emprestimos[id_emp]["data_prevista"]
        )

    # ------------------------------------------------------------------ RF-11

    def consultar_emprestimo(self, id_emprestimo: int) -> dict:
        emp = self._obter_emprestimo(id_emprestimo)
        return dict(emp)
