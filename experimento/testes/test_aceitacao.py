"""Testes de aceitação do ERS-LOG-001: um teste por critério de aceitação (CA).

O nome de cada teste é o id do CA (CA-E04-07 -> test_CA_E04_07). Os serviços rodam como processos
separados (conftest.py) e são testados só pela API HTTP.
"""
import ast
import copy
import sys
import time
from pathlib import Path

import httpx

from conftest import ALVO, SERVICOS

RAPIDA = {"id": "RAPIDA", "nome": "Rápida Log", "ufs_atendidas": ["SP", "RJ", "MG"],
          "tarifa_base_centavos": 1500, "tarifa_kg_centavos": 300, "adicional_interestadual_pct": 20,
          "prazo_dias_uteis": 2, "dias_adicionais_interestadual": 2, "peso_maximo_g": 30000}
ECONOMICA = {"id": "ECONOMICA", "nome": "Econômica Express", "ufs_atendidas": ["SP", "RJ", "MG", "PR", "SC", "RS"],
             "tarifa_base_centavos": 900, "tarifa_kg_centavos": 200, "adicional_interestadual_pct": 10,
             "prazo_dias_uteis": 5, "dias_adicionais_interestadual": 3, "peso_maximo_g": 50000}
EXPRESSA = {"id": "EXPRESSA", "nome": "Expressa Já", "ufs_atendidas": ["SP"],
            "tarifa_base_centavos": 2500, "tarifa_kg_centavos": 100, "adicional_interestadual_pct": 0,
            "prazo_dias_uteis": 1, "dias_adicionais_interestadual": 0, "peso_maximo_g": 10000}
QA = {"cep_origem": "01310100", "cep_destino": "04538133",
      "pacote": {"peso_g": 2000, "comprimento_cm": 30, "largura_cm": 20, "altura_cm": 10}, "data": "2026-03-02"}
QB = {"cep_origem": "01310100", "cep_destino": "20040002",
      "pacote": {"peso_g": 1500, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 20}, "data": "2026-03-02"}
E1 = {"pedido_id": "PED-1", "cotacao_id": "COT-000001", "transportadora_id": "RAPIDA",
      "destinatario": {"nome": "Ana Souza", "email": "ana@exemplo.com"}, "data_hora": "2026-03-02T10:00:00"}
DADOS_CRIADO = {"envio_id": "ENV-000001", "codigo_rastreio": "LG000000001BR", "pedido_id": "PED-1",
                "transportadora_id": "RAPIDA", "data_prevista_entrega": "2026-03-04",
                "destinatario_email": "ana@exemplo.com", "data_hora": "2026-03-02T10:00:00"}
ENTREGA = [("COLETADO", "2026-03-02T15:00:00"), ("EM_TRANSITO", "2026-03-03T08:00:00"),
           ("SAIU_PARA_ENTREGA", "2026-03-04T07:00:00"), ("ENTREGUE", "2026-03-04T14:00:00")]
DEVOLUCAO = [("COLETADO", "2026-03-02T15:00:00"), ("EM_TRANSITO", "2026-03-03T08:00:00"),
             ("SAIU_PARA_ENTREGA", "2026-03-04T07:00:00"), ("FALHA_ENTREGA", "2026-03-04T12:00:00"),
             ("SAIU_PARA_ENTREGA", "2026-03-05T07:00:00"), ("FALHA_ENTREGA", "2026-03-05T12:00:00"),
             ("SAIU_PARA_ENTREGA", "2026-03-06T07:00:00"), ("FALHA_ENTREGA", "2026-03-06T12:00:00")]


_CLIENTE = httpx.Client(timeout=30)


def _req(sistema, metodo, servico, caminho, **kw):
    return _CLIENTE.request(metodo, f"{sistema.urls[servico]}{caminho}", **kw)


def _cadastrar(sistema, *transportadoras):
    for t in transportadoras:
        assert _req(sistema, "POST", "transportadoras", "/transportadoras", json=t).status_code == 201


def _s0(sistema):
    _cadastrar(sistema, RAPIDA, ECONOMICA, EXPRESSA)


def _cotar(sistema, corpo=QA, **mudancas):
    corpo = copy.deepcopy(corpo)
    for chave, valor in mudancas.items():
        if chave in corpo["pacote"]:
            corpo["pacote"][chave] = valor
        else:
            corpo[chave] = valor
    return _req(sistema, "POST", "cotacao", "/cotacoes", json=corpo)


def _envio(sistema, **mudancas):
    corpo = copy.deepcopy(E1)
    for chave, valor in mudancas.items():
        if chave in corpo["destinatario"]:
            corpo["destinatario"][chave] = valor
        else:
            corpo[chave] = valor
    return _req(sistema, "POST", "envios", "/envios", json=corpo)


def _s1(sistema):
    _s0(sistema)
    assert _cotar(sistema).status_code == 201
    assert _envio(sistema).status_code == 201


def _atualizar(sistema, status, data_hora, local="São Paulo/SP", envio="ENV-000001"):
    return _req(sistema, "POST", "envios", f"/envios/{envio}/atualizacoes",
                json={"status": status, "data_hora": data_hora, "local": local})


def _sequencia(sistema, passos):
    respostas = [_atualizar(sistema, s, h) for s, h in passos]
    assert all(r.status_code == 200 for r in respostas)
    return respostas[-1]


def _erro(resposta, status, codigo):
    assert resposta.status_code == status
    assert resposta.json()["erro"] == codigo


def _eventos(sistema):
    return _req(sistema, "GET", "barramento", "/eventos").json()


def _ids(lista, chave):
    return [item[chave] for item in lista]


def _notificacoes(sistema, email="ana@exemplo.com"):
    r = _req(sistema, "GET", "notificacoes", "/notificacoes", params={"email": email})
    assert r.status_code == 200
    return r.json()


# RF-T01 — Cadastrar transportadora

def test_CA_T01_01(sistema):
    r = _req(sistema, "POST", "transportadoras", "/transportadoras", json=RAPIDA)
    assert r.status_code == 201
    assert r.json() == {**RAPIDA, "ativa": True}


def test_CA_T01_02(sistema):
    _erro(_req(sistema, "POST", "transportadoras", "/transportadoras", json={**RAPIDA, "id": "rapida"}),
          422, "DADOS_INVALIDOS")


def test_CA_T01_03(sistema):
    _erro(_req(sistema, "POST", "transportadoras", "/transportadoras", json={**RAPIDA, "ufs_atendidas": ["SP", "AM"]}),
          422, "DADOS_INVALIDOS")


def test_CA_T01_04(sistema):
    _erro(_req(sistema, "POST", "transportadoras", "/transportadoras", json={**RAPIDA, "tarifa_kg_centavos": -1}),
          422, "DADOS_INVALIDOS")


def test_CA_T01_05(sistema):
    corpo = {k: v for k, v in RAPIDA.items() if k != "nome"}
    r = _req(sistema, "POST", "transportadoras", "/transportadoras", json=corpo)
    assert r.status_code == 422
    assert set(r.json()) == {"erro", "mensagem"}
    assert r.json()["erro"] == "DADOS_INVALIDOS"
    assert isinstance(r.json()["mensagem"], str)


def test_CA_T01_06(sistema):
    _cadastrar(sistema, RAPIDA)
    _erro(_req(sistema, "POST", "transportadoras", "/transportadoras", json=RAPIDA), 409, "TRANSPORTADORA_DUPLICADA")


# RF-T02 — Listar transportadoras

def test_CA_T02_01(sistema):
    _s0(sistema)
    r = _req(sistema, "GET", "transportadoras", "/transportadoras")
    assert r.status_code == 200
    assert _ids(r.json(), "id") == ["ECONOMICA", "EXPRESSA", "RAPIDA"]


def test_CA_T02_02(sistema):
    _s0(sistema)
    assert _req(sistema, "PATCH", "transportadoras", "/transportadoras/EXPRESSA", json={"ativa": False}).status_code == 200
    r = _req(sistema, "GET", "transportadoras", "/transportadoras", params={"somente_ativas": "true"})
    assert _ids(r.json(), "id") == ["ECONOMICA", "RAPIDA"]
    assert len(_req(sistema, "GET", "transportadoras", "/transportadoras").json()) == 3


# RF-T03 — Consultar transportadora

def test_CA_T03_01(sistema):
    _s0(sistema)
    r = _req(sistema, "GET", "transportadoras", "/transportadoras/RAPIDA")
    assert r.status_code == 200
    assert r.json() == {**RAPIDA, "ativa": True}


def test_CA_T03_02(sistema):
    _erro(_req(sistema, "GET", "transportadoras", "/transportadoras/XPTO"), 404, "TRANSPORTADORA_NAO_ENCONTRADA")


# RF-T04 — Ativar ou desativar

def test_CA_T04_01(sistema):
    _s0(sistema)
    r = _req(sistema, "PATCH", "transportadoras", "/transportadoras/EXPRESSA", json={"ativa": False})
    assert r.status_code == 200
    assert r.json()["ativa"] is False


def test_CA_T04_02(sistema):
    _erro(_req(sistema, "PATCH", "transportadoras", "/transportadoras/XPTO", json={"ativa": False}),
          404, "TRANSPORTADORA_NAO_ENCONTRADA")


def test_CA_T04_03(sistema):
    _s0(sistema)
    _erro(_req(sistema, "PATCH", "transportadoras", "/transportadoras/RAPIDA", json={}), 422, "DADOS_INVALIDOS")


# RF-C01 — Cotar frete

def test_CA_C01_01(sistema):
    _s0(sistema)
    r = _cotar(sistema)
    assert r.status_code == 201
    c = r.json()
    assert c["id"] == "COT-000001"
    assert (c["uf_origem"], c["uf_destino"], c["peso_taxado_g"], c["valida_ate"]) == ("SP", "SP", 2000, "2026-03-09")
    assert c["opcoes"] == [
        {"transportadora_id": "ECONOMICA", "nome": "Econômica Express", "preco_centavos": 1300,
         "prazo_dias_uteis": 5, "data_prevista_entrega": "2026-03-09"},
        {"transportadora_id": "RAPIDA", "nome": "Rápida Log", "preco_centavos": 2100,
         "prazo_dias_uteis": 2, "data_prevista_entrega": "2026-03-04"},
        {"transportadora_id": "EXPRESSA", "nome": "Expressa Já", "preco_centavos": 2700,
         "prazo_dias_uteis": 1, "data_prevista_entrega": "2026-03-03"},
    ]


def test_CA_C01_02(sistema):
    _s0(sistema)
    c = _cotar(sistema, QB).json()
    assert (c["uf_destino"], c["peso_taxado_g"]) == ("RJ", 4000)
    assert c["opcoes"] == [
        {"transportadora_id": "ECONOMICA", "nome": "Econômica Express", "preco_centavos": 1870,
         "prazo_dias_uteis": 8, "data_prevista_entrega": "2026-03-12"},
        {"transportadora_id": "RAPIDA", "nome": "Rápida Log", "preco_centavos": 3240,
         "prazo_dias_uteis": 4, "data_prevista_entrega": "2026-03-06"},
    ]


def test_CA_C01_03(sistema):
    _s0(sistema)
    c = _cotar(sistema, peso_g=100, comprimento_cm=10, largura_cm=10, altura_cm=11).json()
    assert c["peso_taxado_g"] == 184


def test_CA_C01_04(sistema):
    _cadastrar(sistema, {"id": "ARRED", "nome": "Arredonda", "ufs_atendidas": ["SP", "RJ"],
                         "tarifa_base_centavos": 1001, "tarifa_kg_centavos": 0, "adicional_interestadual_pct": 10,
                         "prazo_dias_uteis": 1, "dias_adicionais_interestadual": 0, "peso_maximo_g": 50000})
    c = _cotar(sistema, QB).json()
    assert len(c["opcoes"]) == 1
    assert c["opcoes"][0]["preco_centavos"] == 1102


def test_CA_C01_05(sistema):
    _s0(sistema)
    no_limite = _cotar(sistema, peso_g=10000, comprimento_cm=10, largura_cm=10, altura_cm=10).json()
    acima = _cotar(sistema, peso_g=10001, comprimento_cm=10, largura_cm=10, altura_cm=10).json()
    assert "EXPRESSA" in _ids(no_limite["opcoes"], "transportadora_id")
    assert "EXPRESSA" not in _ids(acima["opcoes"], "transportadora_id")


def test_CA_C01_06(sistema):
    _s0(sistema)
    _req(sistema, "PATCH", "transportadoras", "/transportadoras/RAPIDA", json={"ativa": False})
    assert _ids(_cotar(sistema).json()["opcoes"], "transportadora_id") == ["ECONOMICA", "EXPRESSA"]


def test_CA_C01_07(sistema):
    _s0(sistema)
    for data in ("2026-03-06", "2026-03-07"):
        opcoes = {o["transportadora_id"]: o for o in _cotar(sistema, data=data).json()["opcoes"]}
        assert opcoes["EXPRESSA"]["data_prevista_entrega"] == "2026-03-09"


def test_CA_C01_08(sistema):
    base = {"nome": "Igual", "ufs_atendidas": ["SP"], "tarifa_base_centavos": 1000, "tarifa_kg_centavos": 100,
            "adicional_interestadual_pct": 0, "prazo_dias_uteis": 3, "dias_adicionais_interestadual": 0,
            "peso_maximo_g": 30000}
    _cadastrar(sistema, {**base, "id": "BETA"}, {**base, "id": "ALFA"})
    assert _ids(_cotar(sistema).json()["opcoes"], "transportadora_id") == ["ALFA", "BETA"]


def test_CA_C01_09(sistema):
    _erro(_cotar(sistema, cep_origem="0131010"), 422, "DADOS_INVALIDOS")


def test_CA_C01_10(sistema):
    _s0(sistema)
    _erro(_cotar(sistema, cep_destino="69000000"), 422, "CEP_FORA_DA_AREA")


def test_CA_C01_11(sistema):
    _s0(sistema)
    r = _cotar(sistema, cep_destino="40020000")
    assert r.status_code == 201
    assert r.json()["opcoes"] == []


def test_CA_C01_12(sistema):
    _s0(sistema)
    primeira = _cotar(sistema)
    _erro(_cotar(sistema, altura_cm=0), 422, "DADOS_INVALIDOS")
    segunda = _cotar(sistema, QB)
    assert (primeira.json()["id"], segunda.json()["id"]) == ("COT-000001", "COT-000002")


# RF-C02 — Consultar cotação

def test_CA_C02_01(sistema):
    _s0(sistema)
    criada = _cotar(sistema).json()
    r = _req(sistema, "GET", "cotacao", "/cotacoes/COT-000001")
    assert r.status_code == 200
    assert r.json() == criada


def test_CA_C02_02(sistema):
    _erro(_req(sistema, "GET", "cotacao", "/cotacoes/COT-999999"), 404, "COTACAO_NAO_ENCONTRADA")


# RF-E01 — Criar envio

def _base_envio(sistema):
    _s0(sistema)
    assert _cotar(sistema).status_code == 201


def test_CA_E01_01(sistema):
    _base_envio(sistema)
    r = _envio(sistema)
    assert r.status_code == 201
    assert r.json() == {
        "id": "ENV-000001", "codigo_rastreio": "LG000000001BR", "pedido_id": "PED-1",
        "cotacao_id": "COT-000001", "transportadora_id": "RAPIDA", "preco_centavos": 2100,
        "data_prevista_entrega": "2026-03-04",
        "destinatario": {"nome": "Ana Souza", "email": "ana@exemplo.com"},
        "status": "CRIADO", "tentativas_falhas": 0,
        "historico": [{"status": "CRIADO", "data_hora": "2026-03-02T10:00:00", "local": None}],
    }


def test_CA_E01_02(sistema):
    _base_envio(sistema)
    r = _envio(sistema, transportadora_id="EXPRESSA")
    assert r.status_code == 201
    assert (r.json()["preco_centavos"], r.json()["data_prevista_entrega"]) == (2700, "2026-03-03")


def test_CA_E01_03(sistema):
    _base_envio(sistema)
    assert _cotar(sistema, QB).status_code == 201
    _erro(_envio(sistema, cotacao_id="COT-000002", transportadora_id="EXPRESSA"), 422, "TRANSPORTADORA_NAO_OFERECIDA")


def test_CA_E01_04(sistema):
    _base_envio(sistema)
    _erro(_envio(sistema, data_hora="2026-03-10T00:00:00"), 422, "COTACAO_EXPIRADA")
    assert _envio(sistema, data_hora="2026-03-09T23:59:59").status_code == 201


def test_CA_E01_05(sistema):
    _base_envio(sistema)
    _erro(_envio(sistema, cotacao_id="COT-999999"), 422, "COTACAO_NAO_ENCONTRADA")


def test_CA_E01_06(sistema):
    _s1(sistema)
    _erro(_envio(sistema), 409, "ENVIO_DUPLICADO")


def test_CA_E01_07(sistema):
    _s1(sistema)
    _erro(_envio(sistema, cotacao_id="COT-999999"), 409, "ENVIO_DUPLICADO")


def test_CA_E01_08(sistema):
    _base_envio(sistema)
    _req(sistema, "PATCH", "transportadoras", "/transportadoras/RAPIDA", json={"ativa": False})
    r = _envio(sistema)
    assert r.status_code == 201
    assert r.json()["preco_centavos"] == 2100


def test_CA_E01_09(sistema):
    _base_envio(sistema)
    _erro(_envio(sistema, email="ana.exemplo.com"), 422, "DADOS_INVALIDOS")


def test_CA_E01_10(sistema):
    _s1(sistema)
    eventos = [e for e in _eventos(sistema) if e.get("tipo") == "envio.criado"]
    assert len(eventos) == 1
    e = eventos[0]
    assert isinstance(e["id"], str) and e["id"]
    assert e["ocorrido_em"] == "2026-03-02T10:00:00"
    assert e["dados"] == DADOS_CRIADO


# RF-E02 — Consultar envio

def test_CA_E02_01(sistema):
    _base_envio(sistema)
    criado = _envio(sistema).json()
    r = _req(sistema, "GET", "envios", "/envios/ENV-000001")
    assert r.status_code == 200
    assert r.json() == criado


def test_CA_E02_02(sistema):
    _erro(_req(sistema, "GET", "envios", "/envios/ENV-999999"), 404, "ENVIO_NAO_ENCONTRADO")


# RF-E03 — Listar envios de um pedido

def test_CA_E03_01(sistema):
    _s1(sistema)
    assert _req(sistema, "POST", "envios", "/envios/ENV-000001/cancelar",
                json={"data_hora": "2026-03-02T11:00:00"}).status_code == 200
    assert _envio(sistema).status_code == 201
    r = _req(sistema, "GET", "envios", "/envios", params={"pedido_id": "PED-1"})
    assert r.status_code == 200
    assert [(e["id"], e["status"]) for e in r.json()] == [("ENV-000001", "CANCELADO"), ("ENV-000002", "CRIADO")]


def test_CA_E03_02(sistema):
    r = _req(sistema, "GET", "envios", "/envios", params={"pedido_id": "PED-9"})
    assert r.status_code == 200
    assert r.json() == []


# RF-E04 — Atualizações da transportadora

def test_CA_E04_01(sistema):
    _s1(sistema)
    envio = _sequencia(sistema, ENTREGA).json()
    assert envio["status"] == "ENTREGUE"
    assert _ids(envio["historico"], "status") == ["CRIADO", "COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE"]


def test_CA_E04_02(sistema):
    _s1(sistema)
    assert _atualizar(sistema, "COLETADO", "2026-03-02T15:00:00").status_code == 200
    assert _atualizar(sistema, "EM_TRANSITO", "2026-03-03T08:00:00", "Campinas/SP").status_code == 200
    r = _atualizar(sistema, "EM_TRANSITO", "2026-03-03T20:00:00", "Jundiaí/SP")
    assert r.status_code == 200
    assert _ids(r.json()["historico"][-2:], "local") == ["Campinas/SP", "Jundiaí/SP"]


def test_CA_E04_03(sistema):
    _s1(sistema)
    _erro(_atualizar(sistema, "ENTREGUE", "2026-03-02T15:00:00"), 409, "TRANSICAO_INVALIDA")
    envio = _req(sistema, "GET", "envios", "/envios/ENV-000001").json()
    assert envio["status"] == "CRIADO"
    assert len(envio["historico"]) == 1


def test_CA_E04_04(sistema):
    _s1(sistema)
    _erro(_atualizar(sistema, "CANCELADO", "2026-03-02T15:00:00"), 422, "DADOS_INVALIDOS")


def test_CA_E04_05(sistema):
    _s1(sistema)
    _erro(_atualizar(sistema, "COLETADO", "2026-03-02T09:00:00"), 422, "DATA_HORA_INVALIDA")


def test_CA_E04_06(sistema):
    _s1(sistema)
    envio = _sequencia(sistema, [
        ("COLETADO", "2026-03-02T15:00:00"), ("EM_TRANSITO", "2026-03-03T08:00:00"),
        ("SAIU_PARA_ENTREGA", "2026-03-04T07:00:00"), ("FALHA_ENTREGA", "2026-03-04T12:00:00"),
        ("SAIU_PARA_ENTREGA", "2026-03-05T07:00:00"), ("ENTREGUE", "2026-03-05T11:00:00"),
    ]).json()
    assert (envio["status"], envio["tentativas_falhas"]) == ("ENTREGUE", 1)


def test_CA_E04_07(sistema):
    _s1(sistema)
    envio = _sequencia(sistema, DEVOLUCAO).json()
    assert (envio["status"], envio["tentativas_falhas"]) == ("DEVOLVIDO", 3)
    assert envio["historico"][-2:] == [
        {"status": "FALHA_ENTREGA", "data_hora": "2026-03-06T12:00:00", "local": "São Paulo/SP"},
        {"status": "DEVOLVIDO", "data_hora": "2026-03-06T12:00:00", "local": "São Paulo/SP"},
    ]


def test_CA_E04_08(sistema):
    _s1(sistema)
    _sequencia(sistema, ENTREGA)
    _erro(_atualizar(sistema, "EM_TRANSITO", "2026-03-05T08:00:00"), 409, "TRANSICAO_INVALIDA")


def test_CA_E04_09(sistema):
    _s1(sistema)
    _erro(_atualizar(sistema, "COLETADO", "2026-03-02T15:00:00", envio="ENV-999999"), 404, "ENVIO_NAO_ENCONTRADO")


def test_CA_E04_10(sistema):
    _s1(sistema)
    _erro(_atualizar(sistema, "COLETADO", "2026-03-02T15:00:00", local=""), 422, "DADOS_INVALIDOS")


def test_CA_E04_11(sistema):
    _s1(sistema)
    assert _atualizar(sistema, "COLETADO", "2026-03-02T15:00:00").status_code == 200
    e = _eventos(sistema)[-1]
    assert e["tipo"] == "envio.status_atualizado"
    assert e["ocorrido_em"] == "2026-03-02T15:00:00"
    assert e["dados"] == {"envio_id": "ENV-000001", "codigo_rastreio": "LG000000001BR", "pedido_id": "PED-1",
                          "status": "COLETADO", "data_hora": "2026-03-02T15:00:00", "local": "São Paulo/SP",
                          "destinatario_email": "ana@exemplo.com"}


def test_CA_E04_12(sistema):
    _s1(sistema)
    _sequencia(sistema, DEVOLUCAO)
    ultimos = _eventos(sistema)[-2:]
    assert [e["tipo"] for e in ultimos] == ["envio.status_atualizado"] * 2
    assert [e["dados"]["status"] for e in ultimos] == ["FALHA_ENTREGA", "DEVOLVIDO"]


# RF-E05 — Cancelar envio

def test_CA_E05_01(sistema):
    _s1(sistema)
    r = _req(sistema, "POST", "envios", "/envios/ENV-000001/cancelar", json={"data_hora": "2026-03-02T11:00:00"})
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELADO"
    assert r.json()["historico"][-1] == {"status": "CANCELADO", "data_hora": "2026-03-02T11:00:00", "local": None}
    e = _eventos(sistema)[-1]
    assert e["tipo"] == "envio.status_atualizado"
    assert (e["dados"]["status"], e["dados"]["local"]) == ("CANCELADO", None)


def test_CA_E05_02(sistema):
    _s1(sistema)
    assert _atualizar(sistema, "COLETADO", "2026-03-02T15:00:00").status_code == 200
    _erro(_req(sistema, "POST", "envios", "/envios/ENV-000001/cancelar", json={"data_hora": "2026-03-02T16:00:00"}),
          409, "TRANSICAO_INVALIDA")


def test_CA_E05_03(sistema):
    _erro(_req(sistema, "POST", "envios", "/envios/ENV-999999/cancelar", json={"data_hora": "2026-03-02T16:00:00"}),
          404, "ENVIO_NAO_ENCONTRADO")


# RF-R01 — Rastreamento: consumir eventos

def _envelope(id_evento="evt-1", tipo="envio.criado", dados=None):
    return {"id": id_evento, "tipo": tipo, "ocorrido_em": "2026-03-02T10:00:00",
            "dados": DADOS_CRIADO if dados is None else dados}


def test_CA_R01_01(sistema):
    r1 = _req(sistema, "POST", "rastreamento", "/eventos", json=_envelope())
    r2 = _req(sistema, "POST", "rastreamento", "/eventos", json=_envelope())
    assert (r1.status_code, r1.json()) == (200, {"processado": True})
    assert (r2.status_code, r2.json()) == (200, {"processado": False})
    r = _req(sistema, "GET", "rastreamento", "/rastreamento/LG000000001BR")
    assert len(r.json()["historico"]) == 1


def test_CA_R01_02(sistema):
    r = _req(sistema, "POST", "rastreamento", "/eventos", json=_envelope("evt-9", "pedido.pago", {"pedido_id": "PED-1"}))
    assert (r.status_code, r.json()) == (200, {"processado": False})


def test_CA_R01_03(sistema):
    corpo = {k: v for k, v in _envelope().items() if k != "dados"}
    _erro(_req(sistema, "POST", "rastreamento", "/eventos", json=corpo), 422, "DADOS_INVALIDOS")


# RF-R02 — Consultar rastreamento

def _rastreio(sistema, **params):
    return _req(sistema, "GET", "rastreamento", "/rastreamento/LG000000001BR", params=params)


def test_CA_R02_01(sistema):
    _s1(sistema)
    r = _rastreio(sistema)
    assert r.status_code == 200
    assert r.json() == {
        "codigo_rastreio": "LG000000001BR", "envio_id": "ENV-000001", "pedido_id": "PED-1",
        "transportadora_id": "RAPIDA", "data_prevista_entrega": "2026-03-04", "status": "CRIADO",
        "entregue": False, "atrasado": False,
        "historico": [{"status": "CRIADO", "data_hora": "2026-03-02T10:00:00", "local": None}],
    }


def test_CA_R02_02(sistema):
    _s1(sistema)
    _sequencia(sistema, ENTREGA)
    r = _rastreio(sistema).json()
    assert (r["status"], r["entregue"]) == ("ENTREGUE", True)
    assert _ids(r["historico"], "status") == ["CRIADO", "COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE"]


def test_CA_R02_03(sistema):
    _s1(sistema)
    _sequencia(sistema, ENTREGA[:2])
    assert _rastreio(sistema, data="2026-03-05").json()["atrasado"] is True
    assert _rastreio(sistema, data="2026-03-04").json()["atrasado"] is False


def test_CA_R02_04(sistema):
    _s1(sistema)
    _sequencia(sistema, ENTREGA)
    assert _rastreio(sistema, data="2026-03-20").json()["atrasado"] is False


def test_CA_R02_05(sistema):
    _erro(_req(sistema, "GET", "rastreamento", "/rastreamento/LG999999999BR"), 404, "RASTREIO_NAO_ENCONTRADO")


# RF-N01 — Notificações: consumir eventos

def test_CA_N01_01(sistema):
    _s1(sistema)
    assert _notificacoes(sistema) == [{
        "id": "NOT-000001", "email": "ana@exemplo.com", "pedido_id": "PED-1", "codigo_rastreio": "LG000000001BR",
        "tipo": "CRIADO", "mensagem": "Pedido PED-1: envio criado. Rastreio: LG000000001BR.",
        "criada_em": "2026-03-02T10:00:00",
    }]


def test_CA_N01_02(sistema):
    _s1(sistema)
    _sequencia(sistema, ENTREGA)
    n = _notificacoes(sistema)
    assert _ids(n, "tipo") == ["CRIADO", "SAIU_PARA_ENTREGA", "ENTREGUE"]
    assert _ids(n[-2:], "mensagem") == ["Pedido PED-1: saiu para entrega.", "Pedido PED-1: entregue."]


def test_CA_N01_03(sistema):
    _s1(sistema)
    _sequencia(sistema, DEVOLUCAO)
    n = _notificacoes(sistema)
    assert _ids(n, "tipo") == ["CRIADO", "SAIU_PARA_ENTREGA", "FALHA_ENTREGA", "SAIU_PARA_ENTREGA", "FALHA_ENTREGA",
                               "SAIU_PARA_ENTREGA", "FALHA_ENTREGA", "DEVOLVIDO"]
    assert n[-1]["mensagem"] == "Pedido PED-1: devolvido ao remetente."


def test_CA_N01_04(sistema):
    _s1(sistema)
    assert _req(sistema, "POST", "envios", "/envios/ENV-000001/cancelar",
                json={"data_hora": "2026-03-02T11:00:00"}).status_code == 200
    n = _notificacoes(sistema)
    assert _ids(n, "tipo") == ["CRIADO", "CANCELADO"]
    assert n[-1]["mensagem"] == "Pedido PED-1: envio cancelado."


def test_CA_N01_05(sistema):
    r1 = _req(sistema, "POST", "notificacoes", "/eventos", json=_envelope())
    r2 = _req(sistema, "POST", "notificacoes", "/eventos", json=_envelope())
    assert (r1.json(), r2.json()) == ({"processado": True}, {"processado": False})
    assert len(_notificacoes(sistema)) == 1


# RF-N02 — Listar notificações

def test_CA_N02_01(sistema):
    _erro(_req(sistema, "GET", "notificacoes", "/notificacoes"), 422, "DADOS_INVALIDOS")


def test_CA_N02_02(sistema):
    _s1(sistema)
    assert _notificacoes(sistema, "bruno@exemplo.com") == []


# RF-X01 — Reiniciar estado

def test_CA_X01_01(sistema):
    _s1(sistema)
    for nome in SERVICOS:
        assert _req(sistema, "POST", nome, "/admin/reset").status_code == 204
    _s0(sistema)
    cotacao = _cotar(sistema).json()
    envio = _envio(sistema).json()
    assert (cotacao["id"], envio["id"], envio["codigo_rastreio"]) == ("COT-000001", "ENV-000001", "LG000000001BR")
    assert _ids(_notificacoes(sistema), "id") == ["NOT-000001"]
    assert len(_rastreio(sistema).json()["historico"]) == 1


# Requisitos não funcionais

PERMITIDOS = {"fastapi", "pydantic", "starlette", "uvicorn", "httpx", "__future__"}


def test_CA_RNF01_01():
    for nome in SERVICOS:
        arquivo = Path(ALVO) / "servicos" / nome / "main.py"
        assert arquivo.exists(), nome
        modulos = set()
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        assert any(isinstance(no, ast.Assign) and any(getattr(a, "id", None) == "app" for a in no.targets)
                   for no in arvore.body), f"{nome} não define app"
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                modulos.update(a.name.split(".")[0] for a in no.names)
            elif isinstance(no, ast.ImportFrom):
                assert no.level == 0, f"import relativo em {nome}"
                modulos.add(no.module.split(".")[0])
        assert modulos <= set(sys.stdlib_module_names) | PERMITIDOS, (nome, modulos)


def test_CA_RNF02_01(sistema):
    _s1(sistema)
    respostas = [
        _req(sistema, "GET", "transportadoras", "/transportadoras/XPTO"),
        _cotar(sistema, cep_origem="0131010"),
        _atualizar(sistema, "ENTREGUE", "2026-03-02T15:00:00"),
    ]
    assert [r.status_code for r in respostas] == [404, 422, 409]
    for r in respostas:
        corpo = r.json()
        assert set(corpo) == {"erro", "mensagem"}
        assert isinstance(corpo["erro"], str) and isinstance(corpo["mensagem"], str)


def test_CA_RNF04_01(sistema):
    _s0(sistema)
    inicio = time.perf_counter()
    for _ in range(100):
        ultima = _cotar(sistema)
    assert time.perf_counter() - inicio < 15
    assert ultima.json()["id"] == "COT-000100"


def _cenario_integrado(sistema):
    _s0(sistema)
    c = _cotar(sistema, QB)
    assert c.status_code == 201 and c.json()["id"] == "COT-000001"
    assert [(o["transportadora_id"], o["preco_centavos"], o["data_prevista_entrega"]) for o in c.json()["opcoes"]] == \
        [("ECONOMICA", 1870, "2026-03-12"), ("RAPIDA", 3240, "2026-03-06")]
    e = _req(sistema, "POST", "envios", "/envios", json={
        "pedido_id": "PED-7", "cotacao_id": "COT-000001", "transportadora_id": "RAPIDA",
        "destinatario": {"nome": "Bruno Lima", "email": "bruno@exemplo.com"}, "data_hora": "2026-03-02T09:30:00"})
    assert e.status_code == 201
    assert (e.json()["id"], e.json()["codigo_rastreio"], e.json()["preco_centavos"]) == \
        ("ENV-000001", "LG000000001BR", 3240)
    passos = [("COLETADO", "2026-03-02T17:00:00", "São Paulo/SP"), ("EM_TRANSITO", "2026-03-03T09:00:00", "Resende/RJ"),
              ("SAIU_PARA_ENTREGA", "2026-03-05T07:00:00", "Rio de Janeiro/RJ"),
              ("ENTREGUE", "2026-03-05T15:00:00", "Rio de Janeiro/RJ")]
    for status, data_hora, local in passos:
        r = _atualizar(sistema, status, data_hora, local)
        assert r.status_code == 200
    assert r.json()["status"] == "ENTREGUE"
    rastreio = _rastreio(sistema, data="2026-03-05").json()
    assert (rastreio["entregue"], rastreio["atrasado"], len(rastreio["historico"])) == (True, False, 5)
    assert _ids(_notificacoes(sistema, "bruno@exemplo.com"), "tipo") == ["CRIADO", "SAIU_PARA_ENTREGA", "ENTREGUE"]


def test_CA_RNF05_01(sistema):
    tamanhos = {nome: sistema.saidas[nome].stat().st_size for nome in SERVICOS}
    _cenario_integrado(sistema)
    time.sleep(0.5)
    for nome in SERVICOS:
        assert sistema.saidas[nome].stat().st_size == tamanhos[nome], f"{nome} escreveu na saída padrão"


def test_CA_INT_01(sistema):
    _cenario_integrado(sistema)
