"""Oráculo de aceitação do ERS-BIB-001 (fase F5 do processo PRECISA).

Cada teste verifica exatamente um critério (CA = funcional, CV = não funcional),
identificado pelo decorador @criterio. Um teste só verifica o que está escrito
na especificação. Este arquivo NUNCA é mostrado à LLM.

Uso direto (sem o avaliador):  python executor.py <pasta com biblioteca.py>
"""

import ast
import dataclasses
import pathlib
import subprocess
import sys
import time
import unittest
from datetime import date
from decimal import Decimal

try:
    import biblioteca as m
    ERRO_IMPORTACAO = None
except BaseException as erro:  # SyntaxError, ImportError, SystemExit...
    m = None
    ERRO_IMPORTACAO = erro

D = date
A = "9788533302273"  # Dom Casmurro
B = "9788575223796"  # Memórias Póstumas de Brás Cubas
C = "9788593541131"  # O Cortiço
DC2 = "9788535902778"  # Dom Casmurro (outra edição)
F = "9780061120084"
G = "9788576082675"
NAO_CADASTRADO = "9780140449136"


def criterio(cid):
    def marcar(funcao):
        funcao.criterio = cid
        return funcao
    return marcar


class Base(unittest.TestCase):
    def setUp(self):
        if m is None:
            self.fail(f"não foi possível importar biblioteca.py: {type(ERRO_IMPORTACAO).__name__}")
        self.b = m.Biblioteca()

    def livro(self, isbn, exemplares=1, titulo="Livro", autor="Autor"):
        return self.b.cadastrar_livro(isbn, titulo, autor, exemplares)

    def aluno(self, uid):
        return self.b.cadastrar_usuario(uid, f"Aluno {uid}", "ALUNO")

    def professor(self, uid):
        return self.b.cadastrar_usuario(uid, f"Professor {uid}", "PROFESSOR")


# --------------------------------------------------------------------------- RF-01
class RF01CadastrarLivro(Base):
    @criterio("CA-01.1")
    def test_cadastro_normaliza(self):
        l = self.b.cadastrar_livro("978-85-333-0227-3", "  Dom Casmurro ", "Machado de Assis", 2)
        self.assertEqual(
            (l.isbn, l.titulo, l.autor, l.exemplares_total, l.exemplares_disponiveis),
            (A, "Dom Casmurro", "Machado de Assis", 2, 2))

    @criterio("CA-01.2")
    def test_digito_verificador_errado(self):
        with self.assertRaises(m.ISBNInvalidoErro):
            self.b.cadastrar_livro("9788533302274", "Dom Casmurro", "Machado de Assis", 1)

    @criterio("CA-01.3")
    def test_isbn_tamanho_ou_letra(self):
        with self.assertRaises(m.ISBNInvalidoErro):
            self.b.cadastrar_livro("978853330227", "Dom Casmurro", "Machado de Assis", 1)
        with self.assertRaises(m.ISBNInvalidoErro):
            self.b.cadastrar_livro("978853330227X", "Dom Casmurro", "Machado de Assis", 1)

    @criterio("CA-01.4")
    def test_titulo_ou_autor_vazio(self):
        with self.assertRaises(m.DadosInvalidosErro):
            self.b.cadastrar_livro(A, "   ", "Machado de Assis", 1)
        with self.assertRaises(m.DadosInvalidosErro):
            self.b.cadastrar_livro(A, "Dom Casmurro", "", 1)

    @criterio("CA-01.5")
    def test_exemplares_invalidos(self):
        with self.assertRaises(m.DadosInvalidosErro):
            self.b.cadastrar_livro(A, "Dom Casmurro", "Machado de Assis", 0)
        with self.assertRaises(m.DadosInvalidosErro):
            self.b.cadastrar_livro(A, "Dom Casmurro", "Machado de Assis", "2")

    @criterio("CA-01.6")
    def test_duplicado(self):
        self.b.cadastrar_livro(A, "Dom Casmurro", "Machado de Assis", 1)
        with self.assertRaises(m.DuplicadoErro):
            self.b.cadastrar_livro("978-8533302273", "Outro", "Outro Autor", 1)
        self.assertEqual(self.b.buscar_livro(A).titulo, "Dom Casmurro")

    @criterio("CA-01.7")
    def test_ordem_de_validacao(self):
        with self.assertRaises(m.ISBNInvalidoErro):
            self.b.cadastrar_livro("123", "", "Autor", 1)
        self.b.cadastrar_livro(A, "Dom Casmurro", "Machado de Assis", 1)
        with self.assertRaises(m.DadosInvalidosErro) as ctx:
            self.b.cadastrar_livro(A, "", "Autor", 1)
        self.assertNotIsInstance(ctx.exception, m.DuplicadoErro)


# --------------------------------------------------------------------------- RF-02
class RF02CadastrarUsuario(Base):
    @criterio("CA-02.1")
    def test_cadastro_normaliza(self):
        u = self.b.cadastrar_usuario(" u1 ", " Ana Lima ", " aluno ")
        self.assertEqual((u.id, u.nome, u.tipo), ("u1", "Ana Lima", "ALUNO"))
        self.assertEqual(u.saldo_devedor, Decimal("0.00"))

    @criterio("CA-02.2")
    def test_tipo_invalido(self):
        with self.assertRaises(m.DadosInvalidosErro):
            self.b.cadastrar_usuario("u9", "Caio", "VISITANTE")

    @criterio("CA-02.3")
    def test_duplicado(self):
        self.b.cadastrar_usuario("u1", "Ana Lima", "ALUNO")
        with self.assertRaises(m.DuplicadoErro):
            self.b.cadastrar_usuario("u1 ", "Outra Pessoa", "PROFESSOR")
        self.assertEqual(self.b.buscar_usuario("u1").nome, "Ana Lima")

    @criterio("CA-02.4")
    def test_id_ou_nome_vazio(self):
        with self.assertRaises(m.DadosInvalidosErro):
            self.b.cadastrar_usuario("", "Ana Lima", "ALUNO")
        with self.assertRaises(m.DadosInvalidosErro):
            self.b.cadastrar_usuario("u1", "  ", "ALUNO")


# --------------------------------------------------------------------------- RF-03
class RF03Consultar(Base):
    @criterio("CA-03.1")
    def test_buscar_livro_com_espacos(self):
        self.livro(A)
        self.assertEqual(self.b.buscar_livro("978 85 333 0227 3").isbn, A)

    @criterio("CA-03.2")
    def test_nao_encontrados(self):
        with self.assertRaises(m.NaoEncontradoErro):
            self.b.buscar_livro(NAO_CADASTRADO)
        with self.assertRaises(m.NaoEncontradoErro):
            self.b.buscar_usuario("x")
        with self.assertRaises(m.NaoEncontradoErro):
            self.b.buscar_emprestimo(1)

    @criterio("CA-03.3")
    def test_buscar_usuario_com_espacos(self):
        self.aluno("u1")
        self.assertEqual(self.b.buscar_usuario(" u1 ").id, "u1")


# --------------------------------------------------------------------------- RF-04
class RF04Pesquisar(Base):
    def setUp(self):
        super().setUp()
        self.livro(A, titulo="Dom Casmurro", autor="Machado de Assis")
        self.livro(B, titulo="Memórias Póstumas de Brás Cubas", autor="Machado de Assis")
        self.livro(C, titulo="O Cortiço", autor="Aluísio Azevedo")

    def isbns(self, termo):
        return [l.isbn for l in self.b.pesquisar_livros(termo)]

    @criterio("CA-04.1")
    def test_por_autor(self):
        self.assertEqual(self.isbns("machado"), [A, B])

    @criterio("CA-04.2")
    def test_maiusculas(self):
        self.assertEqual(self.isbns("CORTIÇO"), [C])

    @criterio("CA-04.3")
    def test_nao_ignora_acentos(self):
        self.assertEqual(self.isbns("cortico"), [])

    @criterio("CA-04.4")
    def test_sem_resultado(self):
        self.assertEqual(self.isbns("xyz"), [])

    @criterio("CA-04.5")
    def test_termo_vazio_e_desempate(self):
        self.livro(DC2, titulo="Dom Casmurro", autor="Machado de Assis")
        esperado = [A, DC2, B, C]
        self.assertEqual(self.isbns(""), esperado)
        self.assertEqual(self.isbns("   "), esperado)


# --------------------------------------------------------------------------- RF-05
class RF05Emprestar(Base):
    @criterio("CA-05.1")
    def test_emprestimo_aluno(self):
        self.livro(A, 2)
        self.aluno("u1")
        e = self.b.emprestar("978-85-333-0227-3", "u1", D(2026, 3, 2))
        self.assertEqual(
            (e.id, e.isbn, e.usuario_id, e.data_emprestimo, e.data_prevista_devolucao,
             e.data_devolucao, e.renovacoes),
            (1, A, "u1", D(2026, 3, 2), D(2026, 3, 16), None, 0))
        self.assertEqual(e.multa, Decimal("0.00"))
        self.assertEqual(self.b.buscar_livro(A).exemplares_disponiveis, 1)

    @criterio("CA-05.2")
    def test_prazo_professor(self):
        self.livro(A)
        self.professor("p1")
        e = self.b.emprestar(A, "p1", D(2026, 3, 2))
        self.assertEqual(e.data_prevista_devolucao, D(2026, 3, 30))

    @criterio("CA-05.3")
    def test_ids_sequenciais_sem_consumir_em_falha(self):
        self.livro(A, 2)
        self.aluno("u1")
        self.aluno("u2")
        self.assertEqual(self.b.emprestar(A, "u1", D(2026, 3, 2)).id, 1)
        with self.assertRaises(m.NaoEncontradoErro):
            self.b.emprestar(NAO_CADASTRADO, "u2", D(2026, 3, 2))
        self.assertEqual(self.b.emprestar(A, "u2", D(2026, 3, 2)).id, 2)

    @criterio("CA-05.4")
    def test_limite_aluno(self):
        for isbn in (A, B, C, F):
            self.livro(isbn)
        self.aluno("u1")
        for isbn in (A, B, C):
            self.b.emprestar(isbn, "u1", D(2026, 3, 2))
        with self.assertRaises(m.LimiteExcedidoErro):
            self.b.emprestar(F, "u1", D(2026, 3, 2))
        self.assertEqual(self.b.buscar_livro(F).exemplares_disponiveis, 1)

    @criterio("CA-05.5")
    def test_limite_professor(self):
        isbns = (A, B, C, DC2, F, G)
        for isbn in isbns:
            self.livro(isbn)
        self.professor("p1")
        for isbn in isbns[:5]:
            self.b.emprestar(isbn, "p1", D(2026, 3, 2))
        with self.assertRaises(m.LimiteExcedidoErro):
            self.b.emprestar(G, "p1", D(2026, 3, 2))

    @criterio("CA-05.6")
    def test_indisponivel(self):
        self.livro(A, 1)
        self.aluno("u1")
        self.aluno("u2")
        self.b.emprestar(A, "u1", D(2026, 3, 2))
        with self.assertRaises(m.IndisponivelErro):
            self.b.emprestar(A, "u2", D(2026, 3, 2))

    @criterio("CA-05.7")
    def test_mesmo_isbn_ativo(self):
        self.livro(A, 2)
        self.aluno("u1")
        self.b.emprestar(A, "u1", D(2026, 3, 2))
        with self.assertRaises(m.OperacaoInvalidaErro):
            self.b.emprestar(A, "u1", D(2026, 3, 3))
        self.assertEqual(self.b.buscar_livro(A).exemplares_disponiveis, 1)

    @criterio("CA-05.8")
    def test_pendencia_por_atraso(self):
        for isbn in (A, B, C):
            self.livro(isbn)
        self.aluno("u1")
        self.b.emprestar(A, "u1", D(2026, 3, 2))
        self.b.emprestar(B, "u1", D(2026, 3, 16))
        with self.assertRaises(m.PendenciaErro):
            self.b.emprestar(C, "u1", D(2026, 3, 17))

    @criterio("CA-05.9")
    def test_pendencia_por_saldo(self):
        self.livro(A)
        self.livro(B)
        self.aluno("u1")
        e = self.b.emprestar(A, "u1", D(2026, 3, 2))
        self.b.devolver(e.id, D(2026, 3, 17))  # 1 dia de atraso -> saldo 2.00
        self.assertEqual(self.b.buscar_usuario("u1").saldo_devedor, Decimal("2.00"))
        with self.assertRaises(m.PendenciaErro):
            self.b.emprestar(B, "u1", D(2026, 3, 18))

    @criterio("CA-05.10")
    def test_nao_encontrado(self):
        self.livro(A)
        self.aluno("u1")
        with self.assertRaises(m.NaoEncontradoErro):
            self.b.emprestar(NAO_CADASTRADO, "u1", D(2026, 3, 2))
        with self.assertRaises(m.NaoEncontradoErro):
            self.b.emprestar(A, "x", D(2026, 3, 2))

    @criterio("CA-05.11")
    def test_ordem_limite_antes_de_indisponivel(self):
        for isbn in (A, B, C, F):
            self.livro(isbn)
        self.aluno("u1")
        self.aluno("u2")
        for isbn in (A, B, C):
            self.b.emprestar(isbn, "u1", D(2026, 3, 2))
        self.b.emprestar(F, "u2", D(2026, 3, 2))
        with self.assertRaises(m.LimiteExcedidoErro):
            self.b.emprestar(F, "u1", D(2026, 3, 2))


# --------------------------------------------------------------------------- RF-06
class RF06Devolver(Base):
    def setUp(self):
        super().setUp()
        self.livro(A, 2)
        self.aluno("u1")
        self.b.emprestar(A, "u1", D(2026, 3, 2))  # id 1, prevista 2026-03-16

    @criterio("CA-06.1")
    def test_no_prazo(self):
        self.assertEqual(self.b.devolver(1, D(2026, 3, 16)), Decimal("0.00"))
        self.assertEqual(self.b.buscar_emprestimo(1).data_devolucao, D(2026, 3, 16))
        self.assertEqual(self.b.buscar_livro(A).exemplares_disponiveis, 2)
        self.assertEqual(self.b.buscar_usuario("u1").saldo_devedor, Decimal("0.00"))

    @criterio("CA-06.2")
    def test_tres_dias_de_atraso(self):
        self.assertEqual(self.b.devolver(1, D(2026, 3, 19)), Decimal("6.00"))
        self.assertEqual(self.b.buscar_emprestimo(1).multa, Decimal("6.00"))
        self.assertEqual(self.b.buscar_usuario("u1").saldo_devedor, Decimal("6.00"))

    @criterio("CA-06.3")
    def test_teto(self):
        self.aluno("u2")
        self.b.emprestar(A, "u2", D(2026, 3, 2))  # id 2
        self.assertEqual(self.b.devolver(1, D(2026, 4, 9)), Decimal("48.00"))
        self.assertEqual(self.b.devolver(2, D(2026, 4, 11)), Decimal("50.00"))

    @criterio("CA-06.4")
    def test_ja_devolvido(self):
        self.b.devolver(1, D(2026, 3, 16))
        with self.assertRaises(m.OperacaoInvalidaErro):
            self.b.devolver(1, D(2026, 3, 20))
        self.assertEqual(self.b.buscar_usuario("u1").saldo_devedor, Decimal("0.00"))
        self.assertEqual(self.b.buscar_livro(A).exemplares_disponiveis, 2)

    @criterio("CA-06.5")
    def test_data_anterior(self):
        with self.assertRaises(m.DadosInvalidosErro):
            self.b.devolver(1, D(2026, 3, 1))
        self.assertIsNone(self.b.buscar_emprestimo(1).data_devolucao)
        self.assertEqual(self.b.buscar_livro(A).exemplares_disponiveis, 1)

    @criterio("CA-06.6")
    def test_inexistente(self):
        with self.assertRaises(m.NaoEncontradoErro):
            self.b.devolver(99, D(2026, 3, 16))

    @criterio("CA-06.7")
    def test_saldo_acumula(self):
        self.livro(B)
        self.b.emprestar(B, "u1", D(2026, 3, 2))  # id 2
        self.b.devolver(1, D(2026, 3, 17))
        self.b.devolver(2, D(2026, 3, 18))
        self.assertEqual(self.b.buscar_usuario("u1").saldo_devedor, Decimal("6.00"))


# --------------------------------------------------------------------------- RF-07
class RF07Renovar(Base):
    def setUp(self):
        super().setUp()
        self.livro(A, 1)
        self.aluno("u1")
        self.b.emprestar(A, "u1", D(2026, 3, 2))  # id 1, prevista 2026-03-16

    @criterio("CA-07.1")
    def test_renovacao(self):
        e = self.b.renovar(1, D(2026, 3, 10))
        self.assertEqual((e.data_prevista_devolucao, e.renovacoes), (D(2026, 3, 24), 1))

    @criterio("CA-07.2")
    def test_renovacao_na_data_prevista(self):
        e = self.b.renovar(1, D(2026, 3, 16))
        self.assertEqual(e.data_prevista_devolucao, D(2026, 3, 30))

    @criterio("CA-07.3")
    def test_atrasado(self):
        with self.assertRaises(m.RenovacaoNegadaErro):
            self.b.renovar(1, D(2026, 3, 17))
        e = self.b.buscar_emprestimo(1)
        self.assertEqual((e.data_prevista_devolucao, e.renovacoes), (D(2026, 3, 16), 0))

    @criterio("CA-07.4")
    def test_maximo_duas(self):
        self.assertEqual(self.b.renovar(1, D(2026, 3, 10)).data_prevista_devolucao, D(2026, 3, 24))
        self.assertEqual(self.b.renovar(1, D(2026, 3, 20)).data_prevista_devolucao, D(2026, 4, 3))
        with self.assertRaises(m.RenovacaoNegadaErro):
            self.b.renovar(1, D(2026, 3, 25))
        self.assertEqual(self.b.buscar_emprestimo(1).renovacoes, 2)

    @criterio("CA-07.5")
    def test_com_fila(self):
        self.aluno("u2")
        self.b.reservar(A, "u2")
        with self.assertRaises(m.RenovacaoNegadaErro):
            self.b.renovar(1, D(2026, 3, 10))

    @criterio("CA-07.6")
    def test_inexistente_ou_devolvido(self):
        with self.assertRaises(m.NaoEncontradoErro):
            self.b.renovar(99, D(2026, 3, 10))
        self.b.devolver(1, D(2026, 3, 5))
        with self.assertRaises(m.OperacaoInvalidaErro):
            self.b.renovar(1, D(2026, 3, 10))

    @criterio("CA-07.7")
    def test_prazo_professor(self):
        self.livro(B)
        self.professor("p1")
        e = self.b.emprestar(B, "p1", D(2026, 3, 2))
        r = self.b.renovar(e.id, D(2026, 3, 10))
        self.assertEqual(r.data_prevista_devolucao, D(2026, 4, 7))


# --------------------------------------------------------------------------- RF-08
class RF08Reservar(Base):
    def setUp(self):
        super().setUp()
        self.livro(A, 1)
        self.livro(B, 2)
        for uid in ("u1", "u2", "u3"):
            self.aluno(uid)
        self.b.emprestar(A, "u1", D(2026, 3, 2))

    @criterio("CA-08.1")
    def test_fila_em_ordem(self):
        self.assertEqual(self.b.reservar(A, "u2"), 1)
        self.assertEqual(self.b.reservar(A, "u3"), 2)
        self.assertEqual(self.b.fila_reservas(A), ["u2", "u3"])

    @criterio("CA-08.2")
    def test_livro_disponivel(self):
        with self.assertRaises(m.OperacaoInvalidaErro):
            self.b.reservar(B, "u2")
        self.assertEqual(self.b.fila_reservas(B), [])

    @criterio("CA-08.3")
    def test_reserva_duplicada(self):
        self.b.reservar(A, "u2")
        with self.assertRaises(m.DuplicadoErro):
            self.b.reservar(A, "u2")
        self.assertEqual(self.b.fila_reservas(A), ["u2"])

    @criterio("CA-08.4")
    def test_quem_esta_com_o_livro(self):
        with self.assertRaises(m.OperacaoInvalidaErro):
            self.b.reservar(A, "u1")

    @criterio("CA-08.5")
    def test_nao_encontrado(self):
        with self.assertRaises(m.NaoEncontradoErro):
            self.b.reservar(NAO_CADASTRADO, "u2")
        with self.assertRaises(m.NaoEncontradoErro):
            self.b.reservar(A, "x")


# --------------------------------------------------------------------------- RF-09
class RF09FilaReservas(Base):
    def setUp(self):
        super().setUp()
        self.livro(A, 1)
        for uid in ("u1", "u2", "u3"):
            self.aluno(uid)

    @criterio("CA-09.1")
    def test_fila_vazia_e_inexistente(self):
        self.assertEqual(self.b.fila_reservas(A), [])
        with self.assertRaises(m.NaoEncontradoErro):
            self.b.fila_reservas(NAO_CADASTRADO)

    @criterio("CA-09.2")
    def test_prioridade_do_primeiro(self):
        self.b.emprestar(A, "u1", D(2026, 3, 2))
        self.b.reservar(A, "u2")
        self.b.reservar(A, "u3")
        self.b.devolver(1, D(2026, 3, 5))
        with self.assertRaises(m.IndisponivelErro):
            self.b.emprestar(A, "u3", D(2026, 3, 5))
        self.b.emprestar(A, "u2", D(2026, 3, 5))
        self.assertEqual(self.b.fila_reservas(A), ["u3"])

    @criterio("CA-09.3")
    def test_fila_esvaziada_libera(self):
        self.b.emprestar(A, "u1", D(2026, 3, 2))
        self.b.reservar(A, "u2")
        self.b.devolver(1, D(2026, 3, 5))
        e = self.b.emprestar(A, "u2", D(2026, 3, 5))
        self.b.devolver(e.id, D(2026, 3, 6))
        self.assertEqual(self.b.emprestar(A, "u3", D(2026, 3, 6)).usuario_id, "u3")


# --------------------------------------------------------------------------- RF-10
class RF10PagarMulta(Base):
    def setUp(self):
        super().setUp()
        self.livro(A, 1)
        self.aluno("u1")
        self.b.emprestar(A, "u1", D(2026, 3, 2))
        self.b.devolver(1, D(2026, 3, 19))  # saldo 6.00

    @criterio("CA-10.1")
    def test_pagamento_parcial(self):
        self.assertEqual(self.b.pagar_multa("u1", Decimal("4.00")), Decimal("2.00"))
        self.assertEqual(self.b.buscar_usuario("u1").saldo_devedor, Decimal("2.00"))

    @criterio("CA-10.2")
    def test_quitacao_libera_emprestimo(self):
        self.b.pagar_multa("u1", Decimal("4.00"))
        self.assertEqual(self.b.pagar_multa("u1", Decimal("2.00")), Decimal("0.00"))
        self.b.emprestar(A, "u1", D(2026, 3, 20))

    @criterio("CA-10.3")
    def test_valores_invalidos(self):
        for valor in (Decimal("0"), Decimal("-1.00"), Decimal("6.01")):
            with self.assertRaises(m.DadosInvalidosErro):
                self.b.pagar_multa("u1", valor)
        self.assertEqual(self.b.buscar_usuario("u1").saldo_devedor, Decimal("6.00"))

    @criterio("CA-10.4")
    def test_usuario_inexistente(self):
        with self.assertRaises(m.NaoEncontradoErro):
            self.b.pagar_multa("x", Decimal("1.00"))


# --------------------------------------------------------------------------- RF-11
class RF11Atrasados(Base):
    def setUp(self):
        super().setUp()
        for isbn in (A, B, C, DC2):
            self.livro(isbn)
        self.aluno("u1")
        self.aluno("u2")
        self.aluno("u3")
        self.professor("p1")
        self.b.emprestar(A, "u1", D(2026, 3, 2))  # id 1, prevista 03-16
        self.b.emprestar(B, "u2", D(2026, 3, 1))  # id 2, prevista 03-15
        self.b.emprestar(C, "p1", D(2026, 3, 2))  # id 3, prevista 03-30

    def ids(self, data):
        return [e.id for e in self.b.listar_atrasados(data)]

    @criterio("CA-11.1")
    def test_ordem_por_data_prevista(self):
        self.assertEqual(self.ids(D(2026, 3, 20)), [2, 1])

    @criterio("CA-11.2")
    def test_valor_limite(self):
        self.assertEqual(self.ids(D(2026, 3, 16)), [2])

    @criterio("CA-11.3")
    def test_devolvido_nao_aparece(self):
        self.b.devolver(2, D(2026, 3, 18))
        self.assertEqual(self.ids(D(2026, 3, 20)), [1])

    @criterio("CA-11.4")
    def test_desempate_por_id(self):
        self.b.emprestar(DC2, "u3", D(2026, 3, 1))  # id 4, prevista 03-15
        self.assertEqual(self.ids(D(2026, 3, 20)), [2, 4, 1])


# --------------------------------------------------------------------------- RNF
def _arvore():
    with open(m.__file__, encoding="utf-8") as arq:
        return ast.parse(arq.read())


def _chamadas(arvore):
    return [n for n in ast.walk(arvore) if isinstance(n, ast.Call)]


class RNFs(Base):
    @criterio("CV-01.1")
    def test_somente_biblioteca_padrao(self):
        permitidos = set(sys.stdlib_module_names) | {"__future__"}
        for no in ast.walk(_arvore()):
            if isinstance(no, ast.Import):
                nomes = [a.name for a in no.names]
            elif isinstance(no, ast.ImportFrom):
                self.assertEqual(no.level, 0, "import relativo")
                nomes = [no.module]
            else:
                continue
            for nome in nomes:
                self.assertIn(nome.split(".")[0], permitidos, f"import fora da stdlib: {nome}")

    @criterio("CV-02.1")
    def test_sem_relogio_do_sistema(self):
        for no in _chamadas(_arvore()):
            f = no.func
            if isinstance(f, ast.Attribute):
                self.assertNotIn(f.attr, {"today", "now", "utcnow"}, f"chamada a .{f.attr}()")
                if f.attr == "time" and isinstance(f.value, ast.Name) and f.value.id == "time":
                    self.fail("chamada a time.time()")

    @criterio("CV-03.1")
    def test_importacao_silenciosa(self):
        pasta = str(pathlib.Path(m.__file__).resolve().parent)
        codigo = f"import sys; sys.path.insert(0, {pasta!r}); import biblioteca"
        r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    @criterio("CV-03.2")
    def test_sem_print_input_open(self):
        for no in _chamadas(_arvore()):
            if isinstance(no.func, ast.Name):
                self.assertNotIn(no.func.id, {"print", "input", "open"}, f"chamada a {no.func.id}()")

    @criterio("CV-04.1")
    def test_desempenho(self):
        def isbn(i):
            base = f"978{i:09d}"
            soma = sum(int(c) * (1 if k % 2 == 0 else 3) for k, c in enumerate(base))
            return base + str((10 - soma % 10) % 10)

        inicio = time.perf_counter()
        for i in range(2000):
            self.b.cadastrar_livro(isbn(i), f"Livro {i:04d}", "Autor", 1)
        for i in range(2000):
            self.b.cadastrar_usuario(f"u{i}", f"Usuario {i}", "ALUNO")
        for i in range(2000):
            self.b.emprestar(isbn(i), f"u{i}", D(2026, 1, 1))
        self.b.listar_atrasados(D(2026, 3, 1))
        self.b.pesquisar_livros("")
        self.assertLess(time.perf_counter() - inicio, 5.0)

    @criterio("CV-05.1")
    def test_hierarquia_de_erros(self):
        heranca = {
            "BibliotecaErro": "Exception", "DadosInvalidosErro": "BibliotecaErro",
            "ISBNInvalidoErro": "DadosInvalidosErro", "DuplicadoErro": "BibliotecaErro",
            "NaoEncontradoErro": "BibliotecaErro", "IndisponivelErro": "BibliotecaErro",
            "LimiteExcedidoErro": "BibliotecaErro", "PendenciaErro": "BibliotecaErro",
            "RenovacaoNegadaErro": "BibliotecaErro", "OperacaoInvalidaErro": "BibliotecaErro",
        }
        for nome, base in heranca.items():
            classe_base = Exception if base == "Exception" else getattr(m, base)
            self.assertTrue(issubclass(getattr(m, nome), classe_base), f"{nome} não herda de {base}")

    @criterio("CV-05.2")
    def test_dataclasses(self):
        campos = {
            "Livro": ["isbn", "titulo", "autor", "exemplares_total", "exemplares_disponiveis"],
            "Usuario": ["id", "nome", "tipo", "saldo_devedor"],
            "Emprestimo": ["id", "isbn", "usuario_id", "data_emprestimo", "data_prevista_devolucao",
                           "data_devolucao", "renovacoes", "multa"],
        }
        for nome, esperado in campos.items():
            classe = getattr(m, nome)
            self.assertTrue(dataclasses.is_dataclass(classe), f"{nome} não é dataclass")
            self.assertEqual([f.name for f in dataclasses.fields(classe)], esperado)

    @criterio("CV-06.1")
    def test_docstrings(self):
        self.assertTrue((m.Biblioteca.__doc__ or "").strip(), "Biblioteca sem docstring")
        for nome, obj in vars(m.Biblioteca).items():
            if not nome.startswith("_") and callable(obj):
                self.assertTrue((obj.__doc__ or "").strip(), f"{nome} sem docstring")

    @criterio("CV-07.1")
    def test_atomicidade(self):
        self.livro(A, 1, titulo="Dom Casmurro")
        self.aluno("u1")
        self.aluno("u2")
        self.b.emprestar(A, "u1", D(2026, 3, 2))  # id 1
        falhas = [
            lambda: self.b.cadastrar_livro(A, "Outro", "Outro", 3),
            lambda: self.b.cadastrar_usuario("u1", "Outro", "PROFESSOR"),
            lambda: self.b.emprestar(A, "u2", D(2026, 3, 3)),
            lambda: self.b.devolver(99, D(2026, 3, 3)),
            lambda: self.b.devolver(1, D(2026, 3, 1)),
            lambda: self.b.renovar(1, D(2026, 3, 20)),
            lambda: self.b.reservar(A, "u1"),
            lambda: self.b.pagar_multa("u2", Decimal("1.00")),
        ]
        for operacao in falhas:
            with self.assertRaises(m.BibliotecaErro):
                operacao()
        livro = self.b.buscar_livro(A)
        self.assertEqual((livro.titulo, livro.exemplares_total, livro.exemplares_disponiveis),
                         ("Dom Casmurro", 1, 0))
        self.assertEqual(self.b.buscar_usuario("u1").tipo, "ALUNO")
        e = self.b.buscar_emprestimo(1)
        self.assertEqual((e.data_devolucao, e.data_prevista_devolucao, e.renovacoes),
                         (None, D(2026, 3, 16), 0))
        self.assertEqual(self.b.fila_reservas(A), [])
        self.assertEqual(self.b.buscar_usuario("u2").saldo_devedor, Decimal("0.00"))
        self.livro(B)
        self.assertEqual(self.b.emprestar(B, "u2", D(2026, 3, 3)).id, 2)

    @criterio("CV-08.1")
    def test_formato_monetario(self):
        self.livro(A, 2)
        u = self.aluno("u1")
        self.aluno("u2")
        e1 = self.b.emprestar(A, "u1", D(2026, 3, 2))
        e2 = self.b.emprestar(A, "u2", D(2026, 3, 2))
        valores = [
            u.saldo_devedor,
            e1.multa,
            self.b.devolver(e1.id, D(2026, 3, 19)),
            self.b.devolver(e2.id, D(2026, 3, 16)),
            self.b.pagar_multa("u1", Decimal("1.5")),
        ]
        self.assertEqual([type(v) for v in valores], [Decimal] * 5)
        self.assertEqual([str(v) for v in valores], ["0.00", "0.00", "6.00", "0.00", "4.50"])
