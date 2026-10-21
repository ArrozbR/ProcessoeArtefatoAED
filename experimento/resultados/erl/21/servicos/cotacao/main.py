# arquivo: servicos/cotacao/main.py
import copy
import os
import re
from datetime import date, timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

FAIXAS_CEP = [
    ("SP", 1000000, 19999999),
    ("RJ", 20000000, 28999999),
    ("ES", 29000000, 29999999),
    ("MG", 30000000, 39999999),
    ("BA", 40000000, 48999999),
    ("PR", 80000000, 87999999),
    ("SC", 88000000, 89999999),
    ("RS", 90000000, 99999999),
]

RE_CEP = re.compile(r"[0-9]{8}")
RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

_estado = {"cotacoes": {}, "contador": 0}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request, exc):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request, exc):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


_INVALIDO = object()


async def ler_json(request):
    try:
        return await request.json()
    except Exception:
        return _INVALIDO


def eh_int(v):
    return type(v) is int


def teto_div(a, b):
    return -((-a) // b)


def uf_do_cep(cep):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def parse_data(s):
    if not isinstance(s, str) or not RE_DATA.fullmatch(s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def somar_dias_uteis(d, dias):
    atual = d
    restantes = dias
    while restantes > 0:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def validar_corpo(corpo):
    if not isinstance(corpo, dict):
        return "Corpo deve ser um objeto JSON."
    for campo in ("cep_origem", "cep_destino"):
        v = corpo.get(campo)
        if not isinstance(v, str) or not RE_CEP.fullmatch(v):
            return f"Campo '{campo}' inválido."
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        return "Campo 'pacote' inválido."
    peso = pacote.get("peso_g")
    if not eh_int(peso) or peso < 1 or peso > 100000:
        return "Campo 'pacote.peso_g' inválido."
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        v = pacote.get(campo)
        if not eh_int(v) or v < 1 or v > 200:
            return f"Campo 'pacote.{campo}' inválido."
    if parse_data(corpo.get("data")) is None:
        return "Campo 'data' inválido."
    return None


@app.post("/admin/reset")
async def reset():
    _estado["cotacoes"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/cotacoes")
async def cotar(request: Request):
    corpo = await ler_json(request)
    if corpo is _INVALIDO:
        return dados_invalidos("Corpo JSON inválido.")
    problema = validar_corpo(corpo)
    if problema:
        return dados_invalidos(problema)

    uf_origem = uf_do_cep(corpo["cep_origem"])
    uf_destino = uf_do_cep(corpo["cep_destino"])
    if uf_origem is None or uf_destino is None:
        return erro(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    try:
        url = os.environ["URL_TRANSPORTADORAS"]
        resp = httpx.get(
            f"{url}/transportadoras",
            params={"somente_ativas": "true"},
            timeout=10,
        )
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    if resp.status_code != 200:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    try:
        transportadoras = resp.json()
        if not isinstance(transportadoras, list):
            raise ValueError
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")

    pacote = corpo["pacote"]
    cubado = teto_div(pacote["comprimento_cm"] * pacote["largura_cm"] * pacote["altura_cm"], 6)
    peso_taxado = max(pacote["peso_g"], cubado)
    kg_cobrados = teto_div(peso_taxado, 1000)
    data_cot = parse_data(corpo["data"])
    interestadual = uf_origem != uf_destino

    opcoes = []
    for t in transportadoras:
        try:
            if not t.get("ativa", True):
                continue
            ufs = t["ufs_atendidas"]
            if uf_origem not in ufs or uf_destino not in ufs:
                continue
            if peso_taxado > t["peso_maximo_g"]:
                continue
            preco_base = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg_cobrados
            if interestadual:
                preco = teto_div(preco_base * (100 + t["adicional_interestadual_pct"]), 100)
                prazo = t["prazo_dias_uteis"] + t["dias_adicionais_interestadual"]
            else:
                preco = preco_base
                prazo = t["prazo_dias_uteis"]
            opcoes.append(
                {
                    "transportadora_id": t["id"],
                    "nome": t["nome"],
                    "preco_centavos": preco,
                    "prazo_dias_uteis": prazo,
                    "data_prevista_entrega": somar_dias_uteis(data_cot, prazo).isoformat(),
                }
            )
        except Exception:
            return erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    _estado["contador"] += 1
    cid = f"COT-{_estado['contador']:06d}"
    cotacao = {
        "id": cid,
        "cep_origem": corpo["cep_origem"],
        "cep_destino": corpo["cep_destino"],
        "uf_origem": uf_origem,
        "uf_destino": uf_destino,
        "peso_taxado_g": peso_taxado,
        "data": corpo["data"],
        "valida_ate": (data_cot + timedelta(days=7)).isoformat(),
        "opcoes": opcoes,
    }
    _estado["cotacoes"][cid] = cotacao
    return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{cid}")
async def consultar(cid: str):
    c = _estado["cotacoes"].get(cid)
    if c is None:
        return erro(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return JSONResponse(status_code=200, content=copy.deepcopy(c))
