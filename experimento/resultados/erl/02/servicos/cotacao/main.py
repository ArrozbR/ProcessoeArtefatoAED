# arquivo: servicos/cotacao/main.py
import copy
import os
import re
import threading
from datetime import date, datetime, timedelta
from typing import Any, Optional

import httpx
from fastapi import Body, FastAPI
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
_contador = 0


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    codigos = {404: "RECURSO_NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return erro(exc.status_code, codigos.get(exc.status_code, "ERRO_HTTP"), str(exc.detail))


def _eh_int(v) -> bool:
    return type(v) is int


def _parse_data(s) -> Optional[date]:
    if not isinstance(s, str) or not DATA_RE.match(s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _uf_do_cep(cep: str) -> Optional[str]:
    n = int(cep)
    for uf, ini, fim in FAIXAS_UF:
        if ini <= n <= fim:
            return uf
    return None


def _teto_div(a: int, b: int) -> int:
    return -(-a // b)


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    d = inicio
    contados = 0
    while contados < dias:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            contados += 1
    return d


def _validar(p) -> Optional[str]:
    if not isinstance(p, dict):
        return "Corpo deve ser um objeto JSON."
    for campo in ("cep_origem", "cep_destino"):
        v = p.get(campo)
        if not isinstance(v, str) or not CEP_RE.match(v):
            return f"Campo '{campo}' inválido."
    pacote = p.get("pacote")
    if not isinstance(pacote, dict):
        return "Campo 'pacote' inválido."
    peso = pacote.get("peso_g")
    if not _eh_int(peso) or peso < 1 or peso > 100000:
        return "Campo 'pacote.peso_g' inválido."
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        v = pacote.get(campo)
        if not _eh_int(v) or v < 1 or v > 200:
            return f"Campo 'pacote.{campo}' inválido."
    if _parse_data(p.get("data")) is None:
        return "Campo 'data' inválido."
    return None


@app.post("/admin/reset", status_code=204)
def reset():
    global _contador
    with _lock:
        _cotacoes.clear()
        _contador = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def cotar(payload: Any = Body(None)):
    global _contador
    msg = _validar(payload)
    if msg:
        return erro(422, "DADOS_INVALIDOS", msg)

    uf_origem = _uf_do_cep(payload["cep_origem"])
    uf_destino = _uf_do_cep(payload["cep_destino"])
    if uf_origem is None or uf_destino is None:
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

    pacote = payload["pacote"]
    peso_cubado = _teto_div(pacote["comprimento_cm"] * pacote["largura_cm"] * pacote["altura_cm"], 6)
    peso_taxado = max(pacote["peso_g"], peso_cubado)
    kg_cobrados = _teto_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino
    data_cot = _parse_data(payload["data"])

    opcoes = []
    for t in transportadoras:
        if not t.get("ativa", False):
            continue
        ufs = t.get("ufs_atendidas", [])
        if uf_origem not in ufs or uf_destino not in ufs:
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
        opcoes.append({
            "transportadora_id": t["id"],
            "nome": t["nome"],
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": _somar_dias_uteis(data_cot, prazo).isoformat(),
        })
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador += 1
        cot = {
            "id": f"COT-{_contador:06d}",
            "cep_origem": payload["cep_origem"],
            "cep_destino": payload["cep_destino"],
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": payload["data"],
            "valida_ate": (data_cot + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cot["id"]] = cot
        return JSONResponse(status_code=201, content=copy.deepcopy(cot))


@app.get("/cotacoes/{cid}")
def consultar(cid: str):
    with _lock:
        cot = _cotacoes.get(cid)
        if cot is None:
            return erro(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return JSONResponse(status_code=200, content=copy.deepcopy(cot))
