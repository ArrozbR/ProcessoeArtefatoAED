# arquivo: servicos/cotacao/main.py
import copy
import os
import re
import threading
from datetime import date, timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
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
_contador = {"cotacao": 0}


class ServicoIndisponivel(Exception):
    pass


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request: Request, exc: RequestValidationError):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_data(valor):
    if not isinstance(valor, str) or not DATA_RE.match(valor):
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_UF:
        if ini <= n <= fim:
            return uf
    return None


def validar_corpo(corpo):
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
    if not is_int(peso) or peso < 1 or peso > 100000:
        return None
    dims = []
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        v = pacote.get(campo)
        if not is_int(v) or v < 1 or v > 200:
            return None
        dims.append(v)
    data = parse_data(corpo.get("data"))
    if data is None:
        return None
    return {
        "cep_origem": cep_o,
        "cep_destino": cep_d,
        "peso_g": peso,
        "dims": dims,
        "data_txt": corpo.get("data"),
        "data": data,
    }


def somar_dias_uteis(inicio: date, dias: int) -> date:
    d = inicio
    contados = 0
    while contados < dias:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            contados += 1
    return d


def buscar_transportadoras_ativas():
    url = os.environ["URL_TRANSPORTADORAS"] + "/transportadoras"
    try:
        resp = httpx.get(url, params={"somente_ativas": "true"}, timeout=10.0)
    except Exception:
        raise ServicoIndisponivel()
    if resp.status_code >= 500 or resp.status_code != 200:
        raise ServicoIndisponivel()
    try:
        dados = resp.json()
    except Exception:
        raise ServicoIndisponivel()
    if not isinstance(dados, list):
        raise ServicoIndisponivel()
    return dados


@app.post("/cotacoes")
async def cotar(request: Request):
    corpo = await ler_json(request)
    v = validar_corpo(corpo)
    if v is None:
        return dados_invalidos("Corpo da cotação fora das restrições.")

    uf_origem = uf_do_cep(v["cep_origem"])
    uf_destino = uf_do_cep(v["cep_destino"])
    if uf_origem is None or uf_destino is None:
        return erro(422, "CEP_FORA_DA_AREA", "CEP de origem ou de destino fora da área atendida.")

    try:
        transportadoras = await run_in_threadpool(buscar_transportadoras_ativas)
    except ServicoIndisponivel:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")

    c, l, a = v["dims"]
    peso_cubado = (c * l * a + 5) // 6
    peso_taxado = max(v["peso_g"], peso_cubado)
    kg_cobrados = (peso_taxado + 999) // 1000
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
                preco = (preco_base * (100 + t["adicional_interestadual_pct"]) + 99) // 100
                prazo = t["prazo_dias_uteis"] + t["dias_adicionais_interestadual"]
            else:
                preco = preco_base
                prazo = t["prazo_dias_uteis"]
            prevista = somar_dias_uteis(v["data"], prazo)
            opcoes.append({
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": preco,
                "prazo_dias_uteis": prazo,
                "data_prevista_entrega": prevista.isoformat(),
            })
        except (KeyError, TypeError):
            continue

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador["cotacao"] += 1
        cid = "COT-%06d" % _contador["cotacao"]
        cotacao = {
            "id": cid,
            "cep_origem": v["cep_origem"],
            "cep_destino": v["cep_destino"],
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": v["data_txt"],
            "valida_ate": (v["data"] + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cid] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{cid}")
async def consultar(cid: str):
    with _lock:
        c = _cotacoes.get(cid)
        if c is None:
            return erro(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return JSONResponse(status_code=200, content=copy.deepcopy(c))


@app.post("/admin/reset")
async def reset():
    with _lock:
        _cotacoes.clear()
        _contador["cotacao"] = 0
    return Response(status_code=204)
