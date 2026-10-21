# arquivo: servicos/cotacao/main.py
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

_estado = {"cotacoes": {}, "contador": 0}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "ROTA_NAO_ENCONTRADA", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_corpo(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def eh_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def teto_div(a: int, b: int) -> int:
    return -((-a) // b)


def parse_data(texto):
    if not isinstance(texto, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", texto):
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def somar_dias_uteis(inicio: date, dias: int) -> date:
    d = inicio
    contados = 0
    while contados < dias:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            contados += 1
    return d


def validar_corpo(corpo):
    if not isinstance(corpo, dict):
        return None
    cep_o = corpo.get("cep_origem")
    cep_d = corpo.get("cep_destino")
    for cep in (cep_o, cep_d):
        if not isinstance(cep, str) or not re.fullmatch(r"[0-9]{8}", cep):
            return None
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        return None
    peso = pacote.get("peso_g")
    if not eh_int(peso) or peso < 1 or peso > 100000:
        return None
    dims = []
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        v = pacote.get(campo)
        if not eh_int(v) or v < 1 or v > 200:
            return None
        dims.append(v)
    data_txt = corpo.get("data")
    data = parse_data(data_txt)
    if data is None:
        return None
    return {
        "cep_origem": cep_o,
        "cep_destino": cep_d,
        "peso_g": peso,
        "dims": dims,
        "data_txt": data_txt,
        "data": data,
    }


@app.post("/cotacoes")
async def cotar(request: Request):
    corpo = await ler_corpo(request)
    dados = validar_corpo(corpo)
    if dados is None:
        return erro(422, "DADOS_INVALIDOS", "Dados da cotação inválidos.")

    uf_o = uf_do_cep(dados["cep_origem"])
    uf_d = uf_do_cep(dados["cep_destino"])
    if uf_o is None or uf_d is None:
        return erro(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    try:
        resp = httpx.get(
            os.environ["URL_TRANSPORTADORAS"] + "/transportadoras",
            params={"somente_ativas": "true"},
            timeout=10,
        )
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if resp.status_code != 200:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    try:
        transportadoras = resp.json()
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")

    c, l, a = dados["dims"]
    peso_cubado = teto_div(c * l * a, 6)
    peso_taxado = max(dados["peso_g"], peso_cubado)
    kg_cobrados = teto_div(peso_taxado, 1000)
    interestadual = uf_o != uf_d

    opcoes = []
    for t in transportadoras:
        if not t.get("ativa", False):
            continue
        ufs = t.get("ufs_atendidas", [])
        if uf_o not in ufs or uf_d not in ufs:
            continue
        if peso_taxado > t["peso_maximo_g"]:
            continue
        preco_base = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg_cobrados
        prazo = t["prazo_dias_uteis"]
        if interestadual:
            preco = teto_div(preco_base * (100 + t["adicional_interestadual_pct"]), 100)
            prazo = prazo + t["dias_adicionais_interestadual"]
        else:
            preco = preco_base
        opcoes.append({
            "transportadora_id": t["id"],
            "nome": t["nome"],
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": somar_dias_uteis(dados["data"], prazo).isoformat(),
        })
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    _estado["contador"] += 1
    cid = "COT-%06d" % _estado["contador"]
    cotacao = {
        "id": cid,
        "cep_origem": dados["cep_origem"],
        "cep_destino": dados["cep_destino"],
        "uf_origem": uf_o,
        "uf_destino": uf_d,
        "peso_taxado_g": peso_taxado,
        "data": dados["data_txt"],
        "valida_ate": (dados["data"] + timedelta(days=7)).isoformat(),
        "opcoes": opcoes,
    }
    _estado["cotacoes"][cid] = cotacao
    return JSONResponse(status_code=201, content=cotacao)


@app.get("/cotacoes/{cid}")
async def consultar(cid: str):
    cot = _estado["cotacoes"].get(cid)
    if cot is None:
        return erro(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return JSONResponse(status_code=200, content=cot)


@app.post("/admin/reset")
async def reset():
    _estado["cotacoes"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)
