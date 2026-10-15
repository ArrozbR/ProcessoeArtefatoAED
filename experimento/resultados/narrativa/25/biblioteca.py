"""Sistema de Empréstimos de Biblioteca."""

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
_DIGITOS = set("0123456789")


def _texto_valido(valor):
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


def _validar_data(data):
    if not isinstance(data, date):
        raise DadosInvalidos("data inválida")


class _Livro:
    __slots__ = ("isbn", "titulo", "autor", "quantidade", "ativos", "fila")

    def __init__(self, isbn, titulo, autor, quantidade):
        self.isbn = isbn
        self.titulo = titulo
        self.autor = autor
        self.quantidade = quantidade
        self.ativos = 0
        self.fila = deque()


class _Usuario:
    __slots__ = ("id_usuario", "nome", "tipo", "saldo", "ativos")

    def __init__(self, id_usuario, nome, tipo):
        self.id_usuario = id_usuario
        self.nome = nome
        self.tipo = tipo
        self.saldo = 0
        self.ativos = set()


class _Emprestimo:
    __slots__ = (
        "id_emprestimo",
        "isbn",
        "id_usuario",
        "data_emprestimo",
        "data_prevista",
        "data_devolucao",
        "renovacoes",
        "multa",
    )

    def __init__(self, id_emprestimo, isbn, id_usuario, data_emprestimo, data_prevista):
        self.id_emprestimo = id_emprestimo
        self.isbn = isbn
        self.id_usuario = id_usuario
        self.data_emprestimo = data_emprestimo
        self.data_prevista = data_prevista
        self.data_devolucao = None
        self.renovacoes = 0
        self.multa = 0


class Biblioteca:
    def __init__(self):
        self._livros = {}
        self._usuarios = {}
        self._emprestimos = {}
        self._ativos = set()
        self._proximo_id = 1

    # ------------------------------------------------------------ cadastros

    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
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
        self._livros[isbn] = _Livro(isbn, titulo, autor, quantidade)

    def cadastrar_usuario(self, id_usuario, nome, tipo):
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome inválido")
        if not isinstance(tipo, str) or tipo not in _PRAZOS:
            raise DadosInvalidos("tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado("usuário já cadastrado")
        self._usuarios[id_usuario] = _Usuario(id_usuario, nome, tipo)

    # ------------------------------------------------------------ auxiliares

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

    def _bloqueado(self, usuario, data):
        if usuario.saldo > 0:
            return True
        for id_emp in usuario.ativos:
            if self._emprestimos[id_emp].data_prevista < data:
                return True
        return False

    # ------------------------------------------------------------ operações

    def emprestar(self, isbn, id_usuario, data):
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        _validar_data(data)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado("usuário bloqueado")
        if len(usuario.ativos) >= _LIMITES[usuario.tipo]:
            raise LimiteDeEmprestimosAtingido("limite atingido")
        if livro.ativos >= livro.quantidade:
            raise ExemplarIndisponivel("sem exemplar disponível")
        if livro.fila and livro.fila[0] != id_usuario:
            raise ExemplarIndisponivel("exemplar reservado para outro usuário")

        if livro.fila:
            livro.fila.popleft()
        id_emp = self._proximo_id
        self._proximo_id += 1
        prevista = data + timedelta(days=_PRAZOS[usuario.tipo])
        self._emprestimos[id_emp] = _Emprestimo(id_emp, isbn, id_usuario, data, prevista)
        self._ativos.add(id_emp)
        usuario.ativos.add(id_emp)
        livro.ativos += 1
        return id_emp

    def devolver(self, id_emprestimo, data):
        emp = self._emprestimo(id_emprestimo)
        if emp.data_devolucao is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        _validar_data(data)
        if data < emp.data_emprestimo:
            raise DadosInvalidos("data anterior ao empréstimo")
        atraso = (data - emp.data_prevista).days
        multa = min(max(0, atraso) * _MULTA_DIA, _TETO_MULTA)
        usuario = self._usuarios[emp.id_usuario]
        livro = self._livros[emp.isbn]
        emp.data_devolucao = data
        emp.multa = multa
        usuario.saldo += multa
        usuario.ativos.discard(id_emprestimo)
        self._ativos.discard(id_emprestimo)
        livro.ativos -= 1
        return multa

    def renovar(self, id_emprestimo, data):
        emp = self._emprestimo(id_emprestimo)
        if emp.data_devolucao is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        _validar_data(data)
        usuario = self._usuarios[emp.id_usuario]
        livro = self._livros[emp.isbn]
        if data > emp.data_prevista:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emp.renovacoes >= _MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if livro.fila:
            raise RenovacaoNaoPermitida("livro com fila de reservas")
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")
        emp.data_prevista = emp.data_prevista + timedelta(days=_PRAZOS[usuario.tipo])
        emp.renovacoes += 1
        return emp.data_prevista

    def reservar(self, isbn, id_usuario):
        self._usuario(id_usuario)
        livro = self._livro(isbn)
        if livro.ativos < livro.quantidade and not livro.fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in livro.fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for id_emp in self._usuarios[id_usuario].ativos:
            if self._emprestimos[id_emp].isbn == isbn:
                raise ReservaNaoPermitida("usuário já possui exemplar deste livro")
        livro.fila.append(id_usuario)
        return len(livro.fila)

    # ------------------------------------------------------------ consultas

    def exemplares_disponiveis(self, isbn):
        livro = self._livro(isbn)
        return livro.quantidade - livro.ativos

    def saldo_devedor(self, id_usuario):
        return self._usuario(id_usuario).saldo

    def pagar_multa(self, id_usuario, valor):
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0 or valor > usuario.saldo:
            raise DadosInvalidos("valor inválido")
        usuario.saldo -= valor
        return usuario.saldo

    def emprestimos_atrasados(self, data):
        _validar_data(data)
        emps = self._emprestimos
        return sorted(i for i in self._ativos if emps[i].data_prevista < data)

    def consultar_emprestimo(self, id_emprestimo):
        emp = self._emprestimo(id_emprestimo)
        return {
            "id_emprestimo": emp.id_emprestimo,
            "isbn": emp.isbn,
            "id_usuario": emp.id_usuario,
            "data_emprestimo": emp.data_emprestimo,
            "data_prevista": emp.data_prevista,
            "data_devolucao": emp.data_devolucao,
            "renovacoes": emp.renovacoes,
            "multa": emp.multa,
        }
