# arquivo: servicos/cotacao/main.py
import os
import re
from datetime import date, timedelta
from typing import Any, Optional
from urllib.parse import quote

import httpx
from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_CEP = re.compile(r"^[0-9]{8}$")
RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

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

_estado = {"contador": 0, "cotacoes": {}}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def _eh_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _parse_data(v: Any) -> Optional[date]:
    if not isinstance(v, str) or not RE_DATA.match(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _uf_do_cep(cep: str) -> Optional[str]:
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def _teto_div(a: int, b: int) -> int:
    return -((-a) // b)


def _somar_dias_uteis(d: date, n: int) -> date:
    atual = d
    restantes = n
    while restantes > 0:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _validar(corpo: Any) -> Optional[dict]:
    if not isinstance(corpo, dict):
        return None
    cep_o = corpo.get("cep_origem")
    cep_d = corpo.get("cep_destino")
    if not isinstance(cep_o, str) or not RE_CEP.match(cep_o):
        return None
    if not isinstance(cep_d, str) or not RE_CEP.match(cep_d):
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
    d = _parse_data(corpo.get("data"))
    if d is None:
        return None
    return {
        "cep_origem": cep_o,
        "cep_destino": cep_d,
        "peso_g": peso,
        "dims": dims,
        "data": d,
        "data_texto": corpo.get("data"),
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
        url = os.environ["URL_TRANSPORTADORAS"] + "/transportadoras"
        resp = httpx.get(url, params={"somente_ativas": "true"}, timeout=10.0)
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

    c, l, a = dados["dims"]
    peso_cubado = _teto_div(c * l * a, 6)
    peso_taxado = max(dados["peso_g"], peso_cubado)
    kg_cobrados = _teto_div(peso_taxado, 1000)
    interestadual = uf_o != uf_d

    opcoes = []
    for t in transportadoras:
        try:
            if not t.get("ativa", True):
                continue
            ufs = t["ufs_atendidas"]
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
        except (KeyError, TypeError):
            continue

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
        "data": dados["data_texto"],
        "valida_ate": (dados["data"] + timedelta(days=7)).isoformat(),
        "opcoes": opcoes,
    }
    _estado["cotacoes"][cid] = cotacao
    return JSONResponse(status_code=201, content=cotacao)


@app.get("/cotacoes/{cid}")
def consultar(cid: str):
    cot = _estado["cotacoes"].get(cid)
    if cot is None:
        return erro(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return JSONResponse(status_code=200, content=cot)


@app.post("/admin/reset")
def reset():
    _estado["contador"] = 0
    _estado["cotacoes"].clear()
    return Response(status_code=204)


_ = quote
