from dataclasses import dataclass, field
from datetime import date, timedelta

PRAZO_DIAS = {"aluno": 14, "professor": 28}
LIMITE_ATIVOS = {"aluno": 3, "professor": 5}
MULTA_POR_DIA = 100
MULTA_MAXIMA = 2000
MAX_RENOVACOES = 2


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


@dataclass
class _Livro:
    isbn: str
    titulo: str
    autor: str
    quantidade: int
    ativos: int = 0
    fila: list = field(default_factory=list)


@dataclass
class _Usuario:
    id_usuario: str
    nome: str
    tipo: str
    saldo: int = 0
    ativos: set = field(default_factory=set)


@dataclass
class _Emprestimo:
    id_emprestimo: int
    isbn: str
    id_usuario: str
    data_emprestimo: date
    data_prevista: date
    data_devolucao: date | None = None
    renovacoes: int = 0
    multa: int = 0


def _texto_valido(valor):
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


class Biblioteca:
    def __init__(self):
        self._livros = {}
        self._usuarios = {}
        self._emprestimos = {}
        self._proximo_id = 1

    def cadastrar_livro(self, isbn, titulo, autor, quantidade):
        if not isinstance(isbn, str) or len(isbn) != 13 or not all(c in "0123456789" for c in isbn):
            raise DadosInvalidos("ISBN deve ter 13 dígitos")
        if not _texto_valido(titulo) or not _texto_valido(autor):
            raise DadosInvalidos("título e autor são obrigatórios")
        if not _inteiro(quantidade) or quantidade < 1:
            raise DadosInvalidos("quantidade deve ser um inteiro maior ou igual a 1")
        if isbn in self._livros:
            raise CadastroDuplicado(isbn)
        self._livros[isbn] = _Livro(isbn, titulo, autor, quantidade)

    def cadastrar_usuario(self, id_usuario, nome, tipo):
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id do usuário é obrigatório")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome é obrigatório")
        if tipo not in PRAZO_DIAS:
            raise DadosInvalidos("tipo deve ser 'aluno' ou 'professor'")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado(id_usuario)
        self._usuarios[id_usuario] = _Usuario(id_usuario, nome, tipo)

    def emprestar(self, isbn, id_usuario, data):
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado(id_usuario)
        if len(usuario.ativos) >= LIMITE_ATIVOS[usuario.tipo]:
            raise LimiteDeEmprestimosAtingido(id_usuario)
        if livro.quantidade - livro.ativos == 0 or (livro.fila and livro.fila[0] != id_usuario):
            raise ExemplarIndisponivel(isbn)
        if livro.fila:
            livro.fila.pop(0)
        emprestimo = _Emprestimo(
            self._proximo_id, isbn, id_usuario, data,
            data + timedelta(days=PRAZO_DIAS[usuario.tipo]),
        )
        self._emprestimos[emprestimo.id_emprestimo] = emprestimo
        self._proximo_id += 1
        livro.ativos += 1
        usuario.ativos.add(emprestimo.id_emprestimo)
        return emprestimo.id_emprestimo

    def devolver(self, id_emprestimo, data):
        emprestimo = self._emprestimo_ativo(id_emprestimo)
        if data < emprestimo.data_emprestimo:
            raise DadosInvalidos("devolução anterior ao empréstimo")
        atraso = max((data - emprestimo.data_prevista).days, 0)
        multa = min(atraso * MULTA_POR_DIA, MULTA_MAXIMA)
        emprestimo.data_devolucao = data
        emprestimo.multa = multa
        self._livros[emprestimo.isbn].ativos -= 1
        usuario = self._usuarios[emprestimo.id_usuario]
        usuario.ativos.discard(id_emprestimo)
        usuario.saldo += multa
        return multa

    def renovar(self, id_emprestimo, data):
        emprestimo = self._emprestimo_ativo(id_emprestimo)
        if data > emprestimo.data_prevista:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emprestimo.renovacoes >= MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if self._livros[emprestimo.isbn].fila:
            raise RenovacaoNaoPermitida("há reservas para o livro")
        usuario = self._usuarios[emprestimo.id_usuario]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")
        emprestimo.data_prevista += timedelta(days=PRAZO_DIAS[usuario.tipo])
        emprestimo.renovacoes += 1
        return emprestimo.data_prevista

    def reservar(self, isbn, id_usuario):
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        if livro.quantidade - livro.ativos > 0 and not livro.fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if id_usuario in livro.fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        if any(self._emprestimos[i].isbn == isbn for i in usuario.ativos):
            raise ReservaNaoPermitida("usuário já está com o livro")
        livro.fila.append(id_usuario)
        return len(livro.fila)

    def exemplares_disponiveis(self, isbn):
        livro = self._livro(isbn)
        return livro.quantidade - livro.ativos

    def saldo_devedor(self, id_usuario):
        return self._usuario(id_usuario).saldo

    def pagar_multa(self, id_usuario, valor):
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0:
            raise DadosInvalidos("valor deve ser um inteiro positivo")
        if valor > usuario.saldo:
            raise DadosInvalidos("valor maior que o saldo devedor")
        usuario.saldo -= valor
        return usuario.saldo

    def emprestimos_atrasados(self, data):
        return sorted(
            e.id_emprestimo
            for e in self._emprestimos.values()
            if e.data_devolucao is None and data > e.data_prevista
        )

    def consultar_emprestimo(self, id_emprestimo):
        e = self._emprestimo(id_emprestimo)
        return {
            "id_emprestimo": e.id_emprestimo,
            "isbn": e.isbn,
            "id_usuario": e.id_usuario,
            "data_emprestimo": e.data_emprestimo,
            "data_prevista": e.data_prevista,
            "data_devolucao": e.data_devolucao,
            "renovacoes": e.renovacoes,
            "multa": e.multa,
        }

    def _usuario(self, id_usuario):
        if id_usuario not in self._usuarios:
            raise UsuarioNaoEncontrado(id_usuario)
        return self._usuarios[id_usuario]

    def _livro(self, isbn):
        if isbn not in self._livros:
            raise LivroNaoEncontrado(isbn)
        return self._livros[isbn]

    def _emprestimo(self, id_emprestimo):
        if id_emprestimo not in self._emprestimos:
            raise EmprestimoNaoEncontrado(id_emprestimo)
        return self._emprestimos[id_emprestimo]

    def _emprestimo_ativo(self, id_emprestimo):
        emprestimo = self._emprestimo(id_emprestimo)
        if emprestimo.data_devolucao is not None:
            raise EmprestimoJaDevolvido(id_emprestimo)
        return emprestimo

    def _bloqueado(self, usuario, data):
        if usuario.saldo > 0:
            return True
        return any(data > self._emprestimos[i].data_prevista for i in usuario.ativos)
