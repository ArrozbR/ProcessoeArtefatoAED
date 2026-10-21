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
_MAX_RENOVACOES = 2


def _texto_valido(valor):
    return isinstance(valor, str) and valor.strip() != ""


def _int_valido(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


class Biblioteca:
    def __init__(self):
        self._livros = {}  # isbn -> dict
        self._usuarios = {}  # id_usuario -> dict
        self._emprestimos = {}  # id -> dict
        self._proximo_id = 1

    # ------------------------------------------------------------------
    # Auxiliares
    # ------------------------------------------------------------------
    def _disponiveis(self, livro):
        return livro["quantidade"] - len(livro["ativos"])

    def _bloqueado(self, usuario, data):
        if usuario["saldo"] > 0:
            return True
        for id_emp in usuario["ativos"]:
            if data > self._emprestimos[id_emp]["data_prevista"]:
                return True
        return False

    # ------------------------------------------------------------------
    # RF-01
    # ------------------------------------------------------------------
    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
        if not isinstance(isbn, str) or len(isbn) != 13 or not all(
            c in "0123456789" for c in isbn
        ):
            raise DadosInvalidos("ISBN inválido")
        if not _texto_valido(titulo) or not _texto_valido(autor):
            raise DadosInvalidos("Título ou autor inválido")
        if not _int_valido(quantidade) or quantidade < 1:
            raise DadosInvalidos("Quantidade inválida")
        if isbn in self._livros:
            raise CadastroDuplicado("Livro já cadastrado")
        self._livros[isbn] = {
            "isbn": isbn,
            "titulo": titulo,
            "autor": autor,
            "quantidade": quantidade,
            "ativos": set(),
            "fila": [],
        }

    # ------------------------------------------------------------------
    # RF-02
    # ------------------------------------------------------------------
    def cadastrar_usuario(self, id_usuario, nome, tipo):
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id de usuário inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("Nome inválido")
        if tipo not in ("aluno", "professor") or not isinstance(tipo, str):
            raise DadosInvalidos("Tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado("Usuário já cadastrado")
        self._usuarios[id_usuario] = {
            "id_usuario": id_usuario,
            "nome": nome,
            "tipo": tipo,
            "saldo": 0,
            "ativos": set(),
        }

    # ------------------------------------------------------------------
    # RF-03
    # ------------------------------------------------------------------
    def emprestar(self, isbn, id_usuario, data):
        usuario = self._usuarios.get(id_usuario)
        if usuario is None:
            raise UsuarioNaoEncontrado("Usuário não cadastrado")
        livro = self._livros.get(isbn)
        if livro is None:
            raise LivroNaoEncontrado("Livro não cadastrado")
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado("Usuário bloqueado")
        if len(usuario["ativos"]) >= _LIMITE[usuario["tipo"]]:
            raise LimiteDeEmprestimosAtingido("Limite de empréstimos atingido")
        fila = livro["fila"]
        if self._disponiveis(livro) == 0 or (fila and fila[0] != id_usuario):
            raise ExemplarIndisponivel("Exemplar indisponível")

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
        livro["ativos"].add(id_emp)
        usuario["ativos"].add(id_emp)
        if fila and fila[0] == id_usuario:
            fila.pop(0)
        return id_emp

    # ------------------------------------------------------------------
    # RF-04
    # ------------------------------------------------------------------
    def devolver(self, id_emprestimo, data):
        emp = self._emprestimos.get(id_emprestimo)
        if emp is None:
            raise EmprestimoNaoEncontrado("Empréstimo inexistente")
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("Empréstimo já devolvido")
        if data < emp["data_emprestimo"]:
            raise DadosInvalidos("Data anterior ao empréstimo")

        dias = (data - emp["data_prevista"]).days
        if dias < 0:
            dias = 0
        multa = min(dias * _MULTA_DIA, _MULTA_MAX)

        emp["data_devolucao"] = data
        emp["multa"] = multa
        self._livros[emp["isbn"]]["ativos"].discard(id_emprestimo)
        usuario = self._usuarios[emp["id_usuario"]]
        usuario["ativos"].discard(id_emprestimo)
        usuario["saldo"] += multa
        return multa

    # ------------------------------------------------------------------
    # RF-05
    # ------------------------------------------------------------------
    def renovar(self, id_emprestimo, data):
        emp = self._emprestimos.get(id_emprestimo)
        if emp is None:
            raise EmprestimoNaoEncontrado("Empréstimo inexistente")
        if emp["data_devolucao"] is not None:
            raise EmprestimoJaDevolvido("Empréstimo já devolvido")
        if data > emp["data_prevista"]:
            raise RenovacaoNaoPermitida("Empréstimo atrasado")
        if emp["renovacoes"] >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("Limite de renovações atingido")
        if self._livros[emp["isbn"]]["fila"]:
            raise RenovacaoNaoPermitida("Há reservas para o livro")
        usuario = self._usuarios[emp["id_usuario"]]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("Usuário bloqueado")

        emp["data_prevista"] = emp["data_prevista"] + timedelta(
            days=_PRAZO[usuario["tipo"]]
        )
        emp["renovacoes"] += 1
        return emp["data_prevista"]

    # ------------------------------------------------------------------
    # RF-06
    # ------------------------------------------------------------------
    def reservar(self, isbn, id_usuario):
        usuario = self._usuarios.get(id_usuario)
        if usuario is None:
            raise UsuarioNaoEncontrado("Usuário não cadastrado")
        livro = self._livros.get(isbn)
        if livro is None:
            raise LivroNaoEncontrado("Livro não cadastrado")
        fila = livro["fila"]
        if self._disponiveis(livro) > 0 and not fila:
            raise ReservaNaoPermitida("Há exemplar disponível")
        if id_usuario in fila:
            raise ReservaNaoPermitida("Usuário já está na fila")
        if usuario["ativos"] & livro["ativos"]:
            raise ReservaNaoPermitida("Usuário já tem empréstimo ativo do livro")
        fila.append(id_usuario)
        return len(fila)

    # ------------------------------------------------------------------
    # RF-07
    # ------------------------------------------------------------------
    def exemplares_disponiveis(self, isbn):
        livro = self._livros.get(isbn)
        if livro is None:
            raise LivroNaoEncontrado("Livro não cadastrado")
        return self._disponiveis(livro)

    # ------------------------------------------------------------------
    # RF-08
    # ------------------------------------------------------------------
    def saldo_devedor(self, id_usuario):
        usuario = self._usuarios.get(id_usuario)
        if usuario is None:
            raise UsuarioNaoEncontrado("Usuário não cadastrado")
        return usuario["saldo"]

    # ------------------------------------------------------------------
    # RF-09
    # ------------------------------------------------------------------
    def pagar_multa(self, id_usuario, valor):
        usuario = self._usuarios.get(id_usuario)
        if usuario is None:
            raise UsuarioNaoEncontrado("Usuário não cadastrado")
        if not _int_valido(valor) or valor <= 0:
            raise DadosInvalidos("Valor inválido")
        if valor > usuario["saldo"]:
            raise DadosInvalidos("Valor maior que o saldo devedor")
        usuario["saldo"] -= valor
        return usuario["saldo"]

    # ------------------------------------------------------------------
    # RF-10
    # ------------------------------------------------------------------
    def emprestimos_atrasados(self, data):
        return sorted(
            id_emp
            for id_emp, emp in self._emprestimos.items()
            if emp["data_devolucao"] is None and data > emp["data_prevista"]
        )

    # ------------------------------------------------------------------
    # RF-11
    # ------------------------------------------------------------------
    def consultar_emprestimo(self, id_emprestimo):
        emp = self._emprestimos.get(id_emprestimo)
        if emp is None:
            raise EmprestimoNaoEncontrado("Empréstimo inexistente")
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
