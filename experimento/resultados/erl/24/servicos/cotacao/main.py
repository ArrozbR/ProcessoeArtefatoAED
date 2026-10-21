# arquivo: servicos/cotacao/main.py
import copy
import datetime
import json
import os
import re
import threading

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

_lock = threading.Lock()
_cotacoes = {}
_contador = 0


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request, exc):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _http(request, exc):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def eh_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def teto_div(a, b):
    return -((-a) // b)


def parse_data(s):
    if not isinstance(s, str) or not DATA_RE.fullmatch(s):
        return None
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def uf_do_cep(cep):
    n = int(cep)
    for uf, ini, fim in FAIXAS_UF:
        if ini <= n <= fim:
            return uf
    return None


def somar_dias_uteis(data, dias):
    atual = data
    restantes = dias
    while restantes > 0:
        atual = atual + datetime.timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


async def ler_json(request):
    try:
        raw = await request.body()
        if not raw:
            return None, False
        return json.loads(raw), True
    except Exception:
        return None, False


def validar(corpo):
    if not isinstance(corpo, dict):
        return None
    co = corpo.get("cep_origem")
    cd = corpo.get("cep_destino")
    if not isinstance(co, str) or not CEP_RE.fullmatch(co):
        return None
    if not isinstance(cd, str) or not CEP_RE.fullmatch(cd):
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
    data = parse_data(corpo.get("data"))
    if data is None:
        return None
    return {
        "cep_origem": co,
        "cep_destino": cd,
        "peso_g": peso,
        "dims": dims,
        "data_str": corpo["data"],
        "data": data,
    }


@app.post("/cotacoes")
async def cotar(request: Request):
    global _contador
    corpo, ok = await ler_json(request)
    if not ok:
        return dados_invalidos("Corpo ausente ou JSON inválido.")
    d = validar(corpo)
    if d is None:
        return dados_invalidos("Corpo fora das restrições.")

    uf_origem = uf_do_cep(d["cep_origem"])
    uf_destino = uf_do_cep(d["cep_destino"])
    if uf_origem is None or uf_destino is None:
        return erro(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    try:
        resp = httpx.get(
            os.environ["URL_TRANSPORTADORAS"] + "/transportadoras",
            params={"somente_ativas": "true"},
            timeout=10.0,
        )
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    if resp.status_code != 200:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    try:
        transportadoras = resp.json()
        if not isinstance(transportadoras, list):
            raise ValueError()
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")

    c, l, a = d["dims"]
    peso_cubado = teto_div(c * l * a, 6)
    peso_taxado = max(d["peso_g"], peso_cubado)
    kg = teto_div(peso_taxado, 1000)
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
            base = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg
            prazo = t["prazo_dias_uteis"]
            if interestadual:
                preco = teto_div(base * (100 + t["adicional_interestadual_pct"]), 100)
                prazo += t["dias_adicionais_interestadual"]
            else:
                preco = base
            prevista = somar_dias_uteis(d["data"], prazo)
            opcoes.append({
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": preco,
                "prazo_dias_uteis": prazo,
                "data_prevista_entrega": prevista.isoformat(),
            })
        except Exception:
            continue

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))
    valida_ate = (d["data"] + datetime.timedelta(days=7)).isoformat()

    with _lock:
        _contador += 1
        cid = "COT-%06d" % _contador
        cotacao = {
            "id": cid,
            "cep_origem": d["cep_origem"],
            "cep_destino": d["cep_destino"],
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": d["data_str"],
            "valida_ate": valida_ate,
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
    global _contador
    with _lock:
        _cotacoes.clear()
        _contador = 0
    return Response(status_code=204)
