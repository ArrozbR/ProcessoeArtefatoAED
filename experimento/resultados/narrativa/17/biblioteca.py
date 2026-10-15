"""Sistema de Empréstimos de Biblioteca (em memória, somente biblioteca padrão)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
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


PRAZOS = {"aluno": 14, "professor": 28}
LIMITES = {"aluno": 3, "professor": 5}
MULTA_POR_DIA = 100
TETO_MULTA = 2000
MAX_RENOVACOES = 2
DIGITOS = set("0123456789")


@dataclass
class _Livro:
    isbn: str
    titulo: str
    autor: str
    quantidade: int
    emprestados: int = 0


@dataclass
class _Usuario:
    id_usuario: str
    nome: str
    tipo: str
    saldo: int = 0


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


def _texto_valido(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _validar_data(valor) -> None:
    if not isinstance(valor, date):
        raise DadosInvalidos("data inválida")


class Biblioteca:
    def __init__(self) -> None:
        self._livros: dict[str, _Livro] = {}
        self._usuarios: dict[str, _Usuario] = {}
        self._emprestimos: dict[int, _Emprestimo] = {}
        self._ativos: dict[int, _Emprestimo] = {}
        self._ativos_por_usuario: dict[str, dict[int, _Emprestimo]] = {}
        self._filas: dict[str, deque] = {}
        self._proximo_id = 1

    # ------------------------------------------------------------------ cadastro
    def cadastrar_livro(self, isbn, titulo, autor, quantidade) -> None:
        if not (isinstance(isbn, str) and len(isbn) == 13 and set(isbn) <= DIGITOS):
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
        self._filas[isbn] = deque()

    def cadastrar_usuario(self, id_usuario, nome, tipo) -> None:
        if not _texto_valido(id_usuario):
            raise DadosInvalidos("id inválido")
        if not _texto_valido(nome):
            raise DadosInvalidos("nome inválido")
        if not isinstance(tipo, str) or tipo not in PRAZOS:
            raise DadosInvalidos("tipo inválido")
        if id_usuario in self._usuarios:
            raise CadastroDuplicado("usuário já cadastrado")
        self._usuarios[id_usuario] = _Usuario(id_usuario, nome, tipo)
        self._ativos_por_usuario[id_usuario] = {}

    # ------------------------------------------------------------------ auxiliares
    def _usuario(self, id_usuario) -> _Usuario:
        try:
            return self._usuarios[id_usuario]
        except (KeyError, TypeError):
            raise UsuarioNaoEncontrado("usuário não encontrado") from None

    def _livro(self, isbn) -> _Livro:
        try:
            return self._livros[isbn]
        except (KeyError, TypeError):
            raise LivroNaoEncontrado("livro não encontrado") from None

    def _emprestimo(self, id_emprestimo) -> _Emprestimo:
        try:
            return self._emprestimos[id_emprestimo]
        except (KeyError, TypeError):
            raise EmprestimoNaoEncontrado("empréstimo não encontrado") from None

    def _bloqueado(self, usuario: _Usuario, data: date) -> bool:
        if usuario.saldo > 0:
            return True
        for emp in self._ativos_por_usuario[usuario.id_usuario].values():
            if emp.data_prevista < data:
                return True
        return False

    # ------------------------------------------------------------------ operações
    def emprestar(self, isbn, id_usuario, data) -> int:
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        _validar_data(data)
        if self._bloqueado(usuario, data):
            raise UsuarioBloqueado("usuário bloqueado")
        ativos_usuario = self._ativos_por_usuario[usuario.id_usuario]
        if len(ativos_usuario) >= LIMITES[usuario.tipo]:
            raise LimiteDeEmprestimosAtingido("limite de empréstimos atingido")
        if livro.emprestados >= livro.quantidade:
            raise ExemplarIndisponivel("nenhum exemplar livre")
        fila = self._filas[isbn]
        if fila and fila[0] != usuario.id_usuario:
            raise ExemplarIndisponivel("exemplar reservado para outro usuário")

        # Todas as verificações passaram: aplica as mudanças.
        if fila:
            fila.popleft()
        id_emp = self._proximo_id
        self._proximo_id += 1
        emp = _Emprestimo(
            id_emprestimo=id_emp,
            isbn=isbn,
            id_usuario=usuario.id_usuario,
            data_emprestimo=data,
            data_prevista=data + timedelta(days=PRAZOS[usuario.tipo]),
        )
        self._emprestimos[id_emp] = emp
        self._ativos[id_emp] = emp
        ativos_usuario[id_emp] = emp
        livro.emprestados += 1
        return id_emp

    def devolver(self, id_emprestimo, data) -> int:
        emp = self._emprestimo(id_emprestimo)
        if emp.data_devolucao is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        _validar_data(data)
        if data < emp.data_emprestimo:
            raise DadosInvalidos("data de devolução anterior ao empréstimo")
        atraso = (data - emp.data_prevista).days
        multa = min(max(atraso, 0) * MULTA_POR_DIA, TETO_MULTA)

        emp.data_devolucao = data
        emp.multa = multa
        del self._ativos[emp.id_emprestimo]
        del self._ativos_por_usuario[emp.id_usuario][emp.id_emprestimo]
        self._livros[emp.isbn].emprestados -= 1
        self._usuarios[emp.id_usuario].saldo += multa
        return multa

    def renovar(self, id_emprestimo, data) -> date:
        emp = self._emprestimo(id_emprestimo)
        if emp.data_devolucao is not None:
            raise EmprestimoJaDevolvido("empréstimo já devolvido")
        _validar_data(data)
        if data > emp.data_prevista:
            raise RenovacaoNaoPermitida("empréstimo atrasado")
        if emp.renovacoes >= MAX_RENOVACOES:
            raise RenovacaoNaoPermitida("limite de renovações atingido")
        if self._filas[emp.isbn]:
            raise RenovacaoNaoPermitida("livro com fila de reservas")
        usuario = self._usuarios[emp.id_usuario]
        if self._bloqueado(usuario, data):
            raise RenovacaoNaoPermitida("usuário bloqueado")

        emp.data_prevista = emp.data_prevista + timedelta(days=PRAZOS[usuario.tipo])
        emp.renovacoes += 1
        return emp.data_prevista

    def reservar(self, isbn, id_usuario) -> int:
        usuario = self._usuario(id_usuario)
        livro = self._livro(isbn)
        fila = self._filas[isbn]
        if livro.emprestados < livro.quantidade and not fila:
            raise ReservaNaoPermitida("há exemplar disponível")
        if usuario.id_usuario in fila:
            raise ReservaNaoPermitida("usuário já está na fila")
        for emp in self._ativos_por_usuario[usuario.id_usuario].values():
            if emp.isbn == isbn:
                raise ReservaNaoPermitida("usuário já tem exemplar deste livro")
        fila.append(usuario.id_usuario)
        return len(fila)

    # ------------------------------------------------------------------ consultas
    def exemplares_disponiveis(self, isbn) -> int:
        livro = self._livro(isbn)
        return livro.quantidade - livro.emprestados

    def saldo_devedor(self, id_usuario) -> int:
        return self._usuario(id_usuario).saldo

    def pagar_multa(self, id_usuario, valor) -> int:
        usuario = self._usuario(id_usuario)
        if not _inteiro(valor) or valor <= 0 or valor > usuario.saldo:
            raise DadosInvalidos("valor de pagamento inválido")
        usuario.saldo -= valor
        return usuario.saldo

    def emprestimos_atrasados(self, data) -> list[int]:
        _validar_data(data)
        return sorted(
            id_emp for id_emp, emp in self._ativos.items() if emp.data_prevista < data
        )

    def consultar_emprestimo(self, id_emprestimo) -> dict:
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
