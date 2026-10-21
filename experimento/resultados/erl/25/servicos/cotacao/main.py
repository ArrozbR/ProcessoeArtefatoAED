# arquivo: servicos/cotacao/main.py
import copy
import os
import re
from datetime import date, timedelta
from typing import Any

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

_estado: dict[str, Any] = {"contador": 0, "cotacoes": {}}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return invalido("Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request) -> tuple[bool, Any]:
    try:
        return True, await request.json()
    except Exception:
        return False, None


def eh_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_data(v: Any) -> date | None:
    if not isinstance(v, str) or not DATA_RE.match(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def uf_do_cep(cep: str) -> str | None:
    n = int(cep)
    for uf, ini, fim in FAIXAS_UF:
        if ini <= n <= fim:
            return uf
    return None


def teto_div(a: int, b: int) -> int:
    return -((-a) // b)


def somar_dias_uteis(inicio: date, dias: int) -> date:
    d = inicio
    contados = 0
    while contados < dias:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            contados += 1
    return d


def validar_corpo(corpo: Any) -> str | None:
    if not isinstance(corpo, dict):
        return "Corpo deve ser um objeto JSON."
    for campo in ("cep_origem", "cep_destino"):
        v = corpo.get(campo)
        if not isinstance(v, str) or not CEP_RE.match(v):
            return f"Campo '{campo}' inválido."
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        return "Campo 'pacote' inválido."
    peso = pacote.get("peso_g")
    if not eh_int(peso) or not (1 <= peso <= 100000):
        return "Campo 'pacote.peso_g' inválido."
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        v = pacote.get(campo)
        if not eh_int(v) or not (1 <= v <= 200):
            return f"Campo 'pacote.{campo}' inválido."
    if parse_data(corpo.get("data")) is None:
        return "Campo 'data' inválido."
    return None


def buscar_transportadoras() -> list | None:
    url = os.environ["URL_TRANSPORTADORAS"] + "/transportadoras"
    try:
        r = httpx.get(url, params={"somente_ativas": "true"}, timeout=10.0)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        dados = r.json()
    except Exception:
        return None
    if not isinstance(dados, list):
        return None
    return dados


@app.post("/cotacoes")
async def cotar(request: Request):
    ok, corpo = await ler_json(request)
    if not ok:
        return invalido("JSON inválido.")
    msg = validar_corpo(corpo)
    if msg:
        return invalido(msg)

    uf_origem = uf_do_cep(corpo["cep_origem"])
    uf_destino = uf_do_cep(corpo["cep_destino"])
    if uf_origem is None or uf_destino is None:
        return erro(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    transportadoras = buscar_transportadoras()
    if transportadoras is None:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")

    pacote = corpo["pacote"]
    data_cot = parse_data(corpo["data"])
    volume = pacote["comprimento_cm"] * pacote["largura_cm"] * pacote["altura_cm"]
    peso_cubado = teto_div(volume, 6)
    peso_taxado = max(pacote["peso_g"], peso_cubado)
    kg_cobrados = teto_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino

    opcoes = []
    for t in transportadoras:
        if not isinstance(t, dict) or not t.get("ativa", False):
            continue
        ufs = t.get("ufs_atendidas") or []
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
        opcoes.append({
            "transportadora_id": t["id"],
            "nome": t["nome"],
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": somar_dias_uteis(data_cot, prazo).isoformat(),
        })
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


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["cotacoes"] = {}
    return Response(status_code=204)
