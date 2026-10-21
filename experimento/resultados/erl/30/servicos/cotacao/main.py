# arquivo: servicos/cotacao/main.py
import copy
import json
import os
import re
from datetime import date, timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

CEP_RE = re.compile(r"^[0-9]{8}$")
DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

FAIXAS_UF = [
    ("SP", 1000000, 19999999),
    ("RJ", 20000000, 28999999),
    ("ES", 29000000, 29999999),
    ("MG", 30000000, 39999999),
    ("BA", 40000000, 48999999),
    ("PR", 80000000, 87999999),
    ("SC", 88000000, 89999999),
    ("RS", 90000000, 99999999),
]

_estado = {"contador": 0, "cotacoes": {}}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request, exc):
    return invalido("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request, exc):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_corpo(request):
    try:
        raw = await request.body()
        dados = json.loads(raw)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    return dados


def eh_int(v, minimo, maximo):
    return isinstance(v, int) and not isinstance(v, bool) and minimo <= v <= maximo


def teto_div(a, b):
    return -((-a) // b)


def parse_data(v):
    if not isinstance(v, str) or not DATA_RE.match(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def uf_do_cep(cep):
    n = int(cep)
    for uf, ini, fim in FAIXAS_UF:
        if ini <= n <= fim:
            return uf
    return None


def somar_dias_uteis(inicio, dias):
    d = inicio
    restantes = dias
    while restantes > 0:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            restantes -= 1
    return d


def validar(d):
    for campo in ("cep_origem", "cep_destino"):
        v = d.get(campo)
        if not isinstance(v, str) or not CEP_RE.match(v):
            return None
    pacote = d.get("pacote")
    if not isinstance(pacote, dict):
        return None
    if not eh_int(pacote.get("peso_g"), 1, 100000):
        return None
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        if not eh_int(pacote.get(campo), 1, 200):
            return None
    data = parse_data(d.get("data"))
    if data is None:
        return None
    return data


def obter_transportadoras():
    base = os.environ["URL_TRANSPORTADORAS"]
    try:
        r = httpx.get(
            f"{base}/transportadoras",
            params={"somente_ativas": "true"},
            timeout=10,
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        lista = r.json()
    except Exception:
        return None
    if not isinstance(lista, list):
        return None
    return lista


@app.post("/cotacoes")
async def cotar(request: Request):
    d = await ler_corpo(request)
    if d is None:
        return invalido("Corpo inválido.")
    data = validar(d)
    if data is None:
        return invalido("Dados da cotação inválidos.")

    uf_origem = uf_do_cep(d["cep_origem"])
    uf_destino = uf_do_cep(d["cep_destino"])
    if uf_origem is None or uf_destino is None:
        return erro(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    transportadoras = obter_transportadoras()
    if transportadoras is None:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")

    pacote = d["pacote"]
    peso_cubado = teto_div(
        pacote["comprimento_cm"] * pacote["largura_cm"] * pacote["altura_cm"], 6
    )
    peso_taxado = max(pacote["peso_g"], peso_cubado)
    kg_cobrados = teto_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino

    opcoes = []
    try:
        for t in transportadoras:
            if not t.get("ativa", True):
                continue
            ufs = t.get("ufs_atendidas", [])
            if uf_origem not in ufs or uf_destino not in ufs:
                continue
            if peso_taxado > t["peso_maximo_g"]:
                continue
            preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg_cobrados
            prazo = t["prazo_dias_uteis"]
            if interestadual:
                preco = teto_div(preco * (100 + t["adicional_interestadual_pct"]), 100)
                prazo = prazo + t["dias_adicionais_interestadual"]
            prevista = somar_dias_uteis(data, prazo)
            opcoes.append(
                {
                    "transportadora_id": t["id"],
                    "nome": t["nome"],
                    "preco_centavos": preco,
                    "prazo_dias_uteis": prazo,
                    "data_prevista_entrega": prevista.isoformat(),
                }
            )
    except (KeyError, TypeError, AttributeError):
        return erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    _estado["contador"] += 1
    cid = f"COT-{_estado['contador']:06d}"
    cotacao = {
        "id": cid,
        "cep_origem": d["cep_origem"],
        "cep_destino": d["cep_destino"],
        "uf_origem": uf_origem,
        "uf_destino": uf_destino,
        "peso_taxado_g": peso_taxado,
        "data": d["data"],
        "valida_ate": (data + timedelta(days=7)).isoformat(),
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


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["cotacoes"].clear()
    return Response(status_code=204)
