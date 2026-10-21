# arquivo: servicos/cotacao/main.py
import copy
import os
import re
import threading
from datetime import date, timedelta
from typing import Any

import httpx
from fastapi import Body, FastAPI, Request
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

_lock = threading.Lock()
_cotacoes: dict = {}
_contador = {"valor": 0}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 422:
        return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _eh_int(v: Any) -> bool:
    return type(v) is int


def _parse_data(s: Any):
    if not isinstance(s, str) or not DATA_RE.match(s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_UF:
        if ini <= n <= fim:
            return uf
    return None


def _teto_div(a: int, b: int) -> int:
    return -((-a) // b)


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    d = inicio
    restantes = dias
    while restantes > 0:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            restantes -= 1
    return d


def _validar(corpo: Any):
    if not isinstance(corpo, dict):
        return None
    cep_o = corpo.get("cep_origem")
    cep_d = corpo.get("cep_destino")
    if not isinstance(cep_o, str) or not CEP_RE.match(cep_o):
        return None
    if not isinstance(cep_d, str) or not CEP_RE.match(cep_d):
        return None
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        return None
    peso = pacote.get("peso_g")
    if not _eh_int(peso) or peso < 1 or peso > 100000:
        return None
    dims = []
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        v = pacote.get(campo)
        if not _eh_int(v) or v < 1 or v > 200:
            return None
        dims.append(v)
    data_str = corpo.get("data")
    data = _parse_data(data_str)
    if data is None:
        return None
    return {
        "cep_origem": cep_o,
        "cep_destino": cep_d,
        "peso_g": peso,
        "dims": dims,
        "data_str": data_str,
        "data": data,
    }


@app.post("/cotacoes")
def cotar(corpo: Any = Body(default=None)):
    dados = _validar(corpo)
    if dados is None:
        return erro(422, "DADOS_INVALIDOS", "Dados da cotação inválidos.")

    uf_o = _uf_do_cep(dados["cep_origem"])
    uf_d = _uf_do_cep(dados["cep_destino"])
    if uf_o is None or uf_d is None:
        return erro(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    try:
        url = os.environ["URL_TRANSPORTADORAS"]
        with httpx.Client(timeout=10.0) as cliente:
            resp = cliente.get(f"{url}/transportadoras", params={"somente_ativas": "true"})
        if resp.status_code != 200:
            return erro(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
        transportadoras = resp.json()
        if not isinstance(transportadoras, list):
            return erro(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")

    c, l, a = dados["dims"]
    peso_cubado = _teto_div(c * l * a, 6)
    peso_taxado = max(dados["peso_g"], peso_cubado)
    kg_cobrados = _teto_div(peso_taxado, 1000)
    interestadual = uf_o != uf_d

    opcoes = []
    for t in transportadoras:
        if not isinstance(t, dict):
            continue
        if not t.get("ativa", True):
            continue
        ufs = t.get("ufs_atendidas") or []
        if uf_o not in ufs or uf_d not in ufs:
            continue
        if peso_taxado > t["peso_maximo_g"]:
            continue
        preco_base = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg_cobrados
        if interestadual:
            preco = _teto_div(preco_base * (100 + t["adicional_interestadual_pct"]), 100)
            prazo = t["prazo_dias_uteis"] + t["dias_adicionais_interestadual"]
        else:
            preco = preco_base
            prazo = t["prazo_dias_uteis"]
        prevista = _somar_dias_uteis(dados["data"], prazo)
        opcoes.append(
            {
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": preco,
                "prazo_dias_uteis": prazo,
                "data_prevista_entrega": prevista.isoformat(),
            }
        )
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    valida_ate = (dados["data"] + timedelta(days=7)).isoformat()

    with _lock:
        _contador["valor"] += 1
        cot_id = f"COT-{_contador['valor']:06d}"
        cotacao = {
            "id": cot_id,
            "cep_origem": dados["cep_origem"],
            "cep_destino": dados["cep_destino"],
            "uf_origem": uf_o,
            "uf_destino": uf_d,
            "peso_taxado_g": peso_taxado,
            "data": dados["data_str"],
            "valida_ate": valida_ate,
            "opcoes": opcoes,
        }
        _cotacoes[cot_id] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{cot_id}")
def consultar(cot_id: str):
    with _lock:
        c = _cotacoes.get(cot_id)
        if c is None:
            return erro(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return JSONResponse(status_code=200, content=copy.deepcopy(c))


@app.post("/admin/reset")
def reset():
    with _lock:
        _cotacoes.clear()
        _contador["valor"] = 0
    return Response(status_code=204)
