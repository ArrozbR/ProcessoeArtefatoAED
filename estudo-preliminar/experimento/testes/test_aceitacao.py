"""Testes de aceitação: um teste por critério de aceitação (CA) do documento de requisitos.

O nome de cada teste é o id do CA (CA-RF03-07 -> test_CA_RF03_07). O módulo testado é
importado como `m` para que a falta de um nome derrube só os testes que o usam.
"""
import ast
import importlib
import sys
import time
from datetime import date

import pytest

m = importlib.import_module("biblioteca")

L1 = "9788535914849"
L2 = "9788575228050"
L3 = "9788576082675"
D = date(2026, 3, 2)


def nova():
    return m.Biblioteca()


def com_usuarios(*livros):
    b = nova()
    for isbn, qtd in livros:
        b.cadastrar_livro(isbn, "Titulo " + isbn[-3:], "Autor", qtd)
    for u in ("u1", "u2", "u3", "u4"):
        b.cadastrar_usuario(u, "Nome " + u, "aluno")
    b.cadastrar_usuario("p1", "Paulo", "professor")
    return b


# RF-01 — Cadastrar livro

def test_CA_RF01_01():
    b = nova()
    b.cadastrar_livro(L1, "Dom Casmurro", "Machado de Assis", 2)
    assert b.exemplares_disponiveis(L1) == 2


def test_CA_RF01_02():
    b = nova()
    with pytest.raises(m.DadosInvalidos):
        b.cadastrar_livro("978853591484", "Dom Casmurro", "Machado de Assis", 2)


def test_CA_RF01_03():
    b = nova()
    with pytest.raises(m.DadosInvalidos):
        b.cadastrar_livro(L1, "   ", "Machado de Assis", 2)
    with pytest.raises(m.LivroNaoEncontrado):
        b.exemplares_disponiveis(L1)


def test_CA_RF01_04():
    b = nova()
    with pytest.raises(m.DadosInvalidos):
        b.cadastrar_livro(L1, "Dom Casmurro", "Machado de Assis", 0)


def test_CA_RF01_05():
    b = nova()
    b.cadastrar_livro(L1, "Dom Casmurro", "Machado de Assis", 2)
    with pytest.raises(m.CadastroDuplicado):
        b.cadastrar_livro(L1, "Outro", "Outro Autor", 5)
    assert b.exemplares_disponiveis(L1) == 2


# RF-02 — Cadastrar usuário

def test_CA_RF02_01():
    b = nova()
    b.cadastrar_usuario("u1", "Ana", "aluno")
    assert b.saldo_devedor("u1") == 0


def test_CA_RF02_02():
    b = nova()
    with pytest.raises(m.DadosInvalidos):
        b.cadastrar_usuario("u1", "Ana", "Aluno")


def test_CA_RF02_03():
    b = nova()
    b.cadastrar_usuario("u1", "Ana", "aluno")
    with pytest.raises(m.CadastroDuplicado):
        b.cadastrar_usuario("u1", "Outra Ana", "professor")


def test_CA_RF02_04():
    b = nova()
    with pytest.raises(m.DadosInvalidos):
        b.cadastrar_usuario("u1", "", "aluno")
    with pytest.raises(m.UsuarioNaoEncontrado):
        b.saldo_devedor("u1")


# RF-03 — Emprestar

def test_CA_RF03_01():
    b = com_usuarios((L1, 2))
    assert b.emprestar(L1, "u1", D) == 1
    assert b.consultar_emprestimo(1)["data_prevista"] == date(2026, 3, 16)
    assert b.exemplares_disponiveis(L1) == 1


def test_CA_RF03_02():
    b = com_usuarios((L1, 2))
    b.emprestar(L1, "p1", D)
    assert b.consultar_emprestimo(1)["data_prevista"] == date(2026, 3, 30)


def test_CA_RF03_03():
    b = com_usuarios((L1, 5))
    assert b.emprestar(L1, "u1", D) == 1
    with pytest.raises(m.UsuarioNaoEncontrado):
        b.emprestar(L1, "xx", D)
    assert b.emprestar(L1, "u1", D) == 2


def test_CA_RF03_04():
    b = com_usuarios((L1, 5))
    for _ in range(3):
        b.emprestar(L1, "u1", D)
    with pytest.raises(m.LimiteDeEmprestimosAtingido):
        b.emprestar(L1, "u1", date(2026, 3, 3))
    assert b.exemplares_disponiveis(L1) == 2


def test_CA_RF03_05():
    b = com_usuarios((L1, 6))
    for _ in range(5):
        b.emprestar(L1, "p1", D)
    with pytest.raises(m.LimiteDeEmprestimosAtingido):
        b.emprestar(L1, "p1", date(2026, 3, 3))


def test_CA_RF03_06():
    b = com_usuarios((L1, 1))
    b.emprestar(L1, "u1", D)
    with pytest.raises(m.ExemplarIndisponivel):
        b.emprestar(L1, "u2", date(2026, 3, 3))


def test_CA_RF03_07():
    b = nova()
    with pytest.raises(m.UsuarioNaoEncontrado):
        b.emprestar(L1, "u1", D)


def test_CA_RF03_08():
    b = nova()
    b.cadastrar_usuario("u1", "Ana", "aluno")
    with pytest.raises(m.LivroNaoEncontrado):
        b.emprestar(L1, "u1", D)


def test_CA_RF03_09():
    b = com_usuarios((L1, 2), (L2, 2))
    b.emprestar(L1, "u1", D)
    b.devolver(1, date(2026, 3, 19))
    with pytest.raises(m.UsuarioBloqueado):
        b.emprestar(L2, "u1", date(2026, 3, 20))


def test_CA_RF03_10():
    b = com_usuarios((L1, 2), (L2, 2))
    b.emprestar(L1, "u1", D)
    with pytest.raises(m.UsuarioBloqueado):
        b.emprestar(L2, "u1", date(2026, 3, 17))


def test_CA_RF03_11():
    b = com_usuarios((L1, 2), (L2, 2))
    b.emprestar(L1, "u1", D)
    assert b.emprestar(L2, "u1", date(2026, 3, 16)) == 2


def test_CA_RF03_12():
    b = com_usuarios((L1, 5))
    for _ in range(3):
        b.emprestar(L1, "u1", D)
    with pytest.raises(m.UsuarioBloqueado):
        b.emprestar(L1, "u1", date(2026, 3, 20))


# RF-04 — Devolver

def com_emprestimo(qtd=2):
    b = com_usuarios((L1, qtd))
    b.emprestar(L1, "u1", D)
    return b


def test_CA_RF04_01():
    b = com_emprestimo()
    assert b.devolver(1, date(2026, 3, 16)) == 0
    assert b.exemplares_disponiveis(L1) == 2
    assert b.consultar_emprestimo(1)["data_devolucao"] == date(2026, 3, 16)


def test_CA_RF04_02():
    b = com_emprestimo()
    assert b.devolver(1, date(2026, 3, 19)) == 300
    assert b.saldo_devedor("u1") == 300


def test_CA_RF04_03():
    b = com_emprestimo()
    assert b.devolver(1, date(2026, 4, 30)) == 2000
    assert b.saldo_devedor("u1") == 2000


def test_CA_RF04_04():
    b = com_emprestimo()
    b.devolver(1, date(2026, 3, 10))
    with pytest.raises(m.EmprestimoJaDevolvido):
        b.devolver(1, date(2026, 3, 17))


def test_CA_RF04_05():
    b = com_emprestimo()
    with pytest.raises(m.EmprestimoNaoEncontrado):
        b.devolver(99, date(2026, 3, 10))


def test_CA_RF04_06():
    b = com_emprestimo()
    with pytest.raises(m.DadosInvalidos):
        b.devolver(1, date(2026, 3, 1))
    assert b.consultar_emprestimo(1)["data_devolucao"] is None


def test_CA_RF04_07():
    b = com_emprestimo()
    b.emprestar(L1, "u1", D)
    assert b.devolver(1, date(2026, 3, 18)) == 200
    assert b.devolver(2, date(2026, 3, 19)) == 300
    assert b.saldo_devedor("u1") == 500


# RF-05 — Renovar

def test_CA_RF05_01():
    b = com_emprestimo(1)
    assert b.renovar(1, date(2026, 3, 10)) == date(2026, 3, 30)
    assert b.consultar_emprestimo(1)["renovacoes"] == 1


def test_CA_RF05_02():
    b = com_emprestimo(1)
    assert b.renovar(1, date(2026, 3, 10)) == date(2026, 3, 30)
    assert b.renovar(1, date(2026, 3, 20)) == date(2026, 4, 13)
    with pytest.raises(m.RenovacaoNaoPermitida):
        b.renovar(1, date(2026, 4, 1))
    assert b.consultar_emprestimo(1)["data_prevista"] == date(2026, 4, 13)


def test_CA_RF05_03():
    b = com_emprestimo(1)
    with pytest.raises(m.RenovacaoNaoPermitida):
        b.renovar(1, date(2026, 3, 17))


def test_CA_RF05_04():
    b = com_emprestimo(1)
    assert b.renovar(1, date(2026, 3, 16)) == date(2026, 3, 30)


def test_CA_RF05_05():
    b = com_emprestimo(1)
    b.reservar(L1, "u2")
    with pytest.raises(m.RenovacaoNaoPermitida):
        b.renovar(1, date(2026, 3, 10))


def test_CA_RF05_06():
    b = com_usuarios((L2, 1))
    b.emprestar(L2, "p1", D)
    assert b.renovar(1, date(2026, 3, 10)) == date(2026, 4, 27)


def test_CA_RF05_07():
    b = com_emprestimo(1)
    b.devolver(1, date(2026, 3, 5))
    with pytest.raises(m.EmprestimoJaDevolvido):
        b.renovar(1, date(2026, 3, 6))


# RF-06 — Reservar

def test_CA_RF06_01():
    b = com_emprestimo(1)
    assert b.reservar(L1, "u2") == 1
    assert b.reservar(L1, "u3") == 2


def test_CA_RF06_02():
    b = com_emprestimo(1)
    b.cadastrar_livro(L2, "Outro", "Autor", 1)
    with pytest.raises(m.ReservaNaoPermitida):
        b.reservar(L2, "u2")


def test_CA_RF06_03():
    b = com_emprestimo(1)
    b.reservar(L1, "u2")
    with pytest.raises(m.ReservaNaoPermitida):
        b.reservar(L1, "u2")


def test_CA_RF06_04():
    b = com_emprestimo(1)
    with pytest.raises(m.ReservaNaoPermitida):
        b.reservar(L1, "u1")


def _fila_u2_u3_devolvido():
    b = com_emprestimo(1)
    b.reservar(L1, "u2")
    b.reservar(L1, "u3")
    b.devolver(1, date(2026, 3, 10))
    return b


def test_CA_RF06_05():
    b = _fila_u2_u3_devolvido()
    with pytest.raises(m.ExemplarIndisponivel):
        b.emprestar(L1, "u3", date(2026, 3, 11))
    assert b.emprestar(L1, "u2", date(2026, 3, 11)) == 2


def test_CA_RF06_06():
    b = _fila_u2_u3_devolvido()
    b.emprestar(L1, "u2", date(2026, 3, 11))
    b.devolver(2, date(2026, 3, 12))
    assert b.reservar(L1, "u4") == 2


# RF-07 — Exemplares disponíveis

def test_CA_RF07_01():
    b = com_usuarios((L1, 3))
    b.emprestar(L1, "u1", D)
    b.emprestar(L1, "u2", D)
    assert b.exemplares_disponiveis(L1) == 1


def test_CA_RF07_02():
    b = com_usuarios((L1, 3))
    b.emprestar(L1, "u1", D)
    b.emprestar(L1, "u2", D)
    b.devolver(1, date(2026, 3, 5))
    assert b.exemplares_disponiveis(L1) == 2


def test_CA_RF07_03():
    b = nova()
    with pytest.raises(m.LivroNaoEncontrado):
        b.exemplares_disponiveis(L1)


def test_CA_RF07_04():
    b = com_emprestimo(1)
    b.reservar(L1, "u2")
    b.devolver(1, date(2026, 3, 5))
    assert b.exemplares_disponiveis(L1) == 1


# RF-08 — Saldo devedor

def test_CA_RF08_01():
    b = nova()
    b.cadastrar_usuario("u1", "Ana", "aluno")
    assert b.saldo_devedor("u1") == 0


def test_CA_RF08_02():
    b = nova()
    with pytest.raises(m.UsuarioNaoEncontrado):
        b.saldo_devedor("u1")


# RF-09 — Pagar multa

def com_multa_300():
    b = com_emprestimo()
    b.devolver(1, date(2026, 3, 19))
    return b


def test_CA_RF09_01():
    b = com_multa_300()
    assert b.pagar_multa("u1", 100) == 200
    assert b.saldo_devedor("u1") == 200


def test_CA_RF09_02():
    b = com_multa_300()
    with pytest.raises(m.DadosInvalidos):
        b.pagar_multa("u1", 500)
    assert b.saldo_devedor("u1") == 300


def test_CA_RF09_03():
    b = com_multa_300()
    with pytest.raises(m.DadosInvalidos):
        b.pagar_multa("u1", 0)


def test_CA_RF09_04():
    b = com_multa_300()
    assert b.pagar_multa("u1", 300) == 0
    assert b.emprestar(L1, "u1", date(2026, 3, 20)) == 2


# RF-10 — Empréstimos atrasados

def com_dois_emprestimos():
    b = com_usuarios((L1, 2))
    b.emprestar(L1, "u1", D)
    b.emprestar(L1, "p1", D)
    return b


def test_CA_RF10_01():
    b = com_dois_emprestimos()
    assert b.emprestimos_atrasados(date(2026, 3, 20)) == [1]
    assert b.emprestimos_atrasados(date(2026, 4, 1)) == [1, 2]


def test_CA_RF10_02():
    b = com_dois_emprestimos()
    b.devolver(1, date(2026, 3, 25))
    assert b.emprestimos_atrasados(date(2026, 4, 1)) == [2]


def test_CA_RF10_03():
    b = com_dois_emprestimos()
    assert b.emprestimos_atrasados(date(2026, 3, 16)) == []


def test_CA_RF10_04():
    b = nova()
    assert b.emprestimos_atrasados(date(2026, 3, 16)) == []


# RF-11 — Consultar empréstimo

def test_CA_RF11_01():
    b = com_emprestimo()
    assert b.consultar_emprestimo(1) == {
        "id_emprestimo": 1,
        "isbn": L1,
        "id_usuario": "u1",
        "data_emprestimo": D,
        "data_prevista": date(2026, 3, 16),
        "data_devolucao": None,
        "renovacoes": 0,
        "multa": 0,
    }


def test_CA_RF11_02():
    b = com_emprestimo()
    b.devolver(1, date(2026, 3, 18))
    d = b.consultar_emprestimo(1)
    assert d["data_devolucao"] == date(2026, 3, 18)
    assert d["multa"] == 200


def test_CA_RF11_03():
    b = com_emprestimo()
    with pytest.raises(m.EmprestimoNaoEncontrado):
        b.consultar_emprestimo(7)


def test_CA_RF11_04():
    b = com_emprestimo()
    d = b.consultar_emprestimo(1)
    d["renovacoes"] = 99
    assert b.consultar_emprestimo(1)["renovacoes"] == 0


# Requisitos não funcionais

def test_CA_RNF01_01():
    assert hasattr(m, "Biblioteca")
    arvore = ast.parse(open(m.__file__, encoding="utf-8").read())
    modulos = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            modulos.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.level == 0 and no.module:
            modulos.add(no.module.split(".")[0])
    assert modulos <= set(sys.stdlib_module_names) | {"__future__"}


def test_CA_RNF02_01():
    nomes = [
        "DadosInvalidos", "CadastroDuplicado", "LivroNaoEncontrado", "UsuarioNaoEncontrado",
        "EmprestimoNaoEncontrado", "EmprestimoJaDevolvido", "UsuarioBloqueado",
        "LimiteDeEmprestimosAtingido", "ExemplarIndisponivel", "RenovacaoNaoPermitida",
        "ReservaNaoPermitida",
    ]
    assert issubclass(m.ErroBiblioteca, Exception)
    for nome in nomes:
        assert issubclass(getattr(m, nome), m.ErroBiblioteca), nome


def test_CA_RNF03_01():
    inicio = time.perf_counter()
    b = nova()
    for i in range(1, 2001):
        b.cadastrar_livro(f"{i:013d}", "Livro", "Autor", 1)
        b.cadastrar_usuario(f"a{i}", "Aluno", "aluno")
    for i in range(1, 2001):
        b.emprestar(f"{i:013d}", f"a{i}", D)
    for _ in range(100):
        atrasados = b.emprestimos_atrasados(date(2026, 3, 20))
    assert time.perf_counter() - inicio < 5
    assert atrasados == list(range(1, 2001))


def test_CA_RNF04_01():
    b1 = nova()
    b2 = nova()
    b1.cadastrar_livro(L1, "Dom Casmurro", "Machado de Assis", 1)
    with pytest.raises(m.LivroNaoEncontrado):
        b2.exemplares_disponiveis(L1)


def test_CA_RNF05_01(capsys):
    b = nova()
    b.cadastrar_livro(L1, "Dom Casmurro", "Machado de Assis", 1)
    b.cadastrar_usuario("u1", "Ana", "aluno")
    b.cadastrar_usuario("u2", "Bruno", "aluno")
    assert b.emprestar(L1, "u1", D) == 1
    assert b.reservar(L1, "u2") == 1
    with pytest.raises(m.RenovacaoNaoPermitida):
        b.renovar(1, date(2026, 3, 10))
    assert b.devolver(1, date(2026, 3, 18)) == 200
    with pytest.raises(m.UsuarioBloqueado):
        b.emprestar(L1, "u1", date(2026, 3, 18))
    assert b.pagar_multa("u1", 200) == 0
    with pytest.raises(m.ExemplarIndisponivel):
        b.emprestar(L1, "u1", date(2026, 3, 18))
    assert b.emprestar(L1, "u2", date(2026, 3, 18)) == 2
    assert b.consultar_emprestimo(2)["data_prevista"] == date(2026, 4, 1)
    assert b.emprestimos_atrasados(date(2026, 4, 2)) == [2]
    assert capsys.readouterr().out == ""
