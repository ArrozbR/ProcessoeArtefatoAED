# arquivo: servicos/cotacao/main.py
import copy
import datetime
import os
import re

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
CEP_RE = re.compile(r"[0-9]{8}")
DATA_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

_estado = {"cotacoes": {}, "contador": 0}

_INVALIDO = object()


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


async def _ler_json(request):
    try:
        return await request.json()
    except Exception:
        return _INVALIDO


def _eh_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _ler_data(texto):
    if not isinstance(texto, str) or not DATA_RE.fullmatch(texto):
        return None
    try:
        return datetime.date.fromisoformat(texto)
    except ValueError:
        return None


def _uf_do_cep(cep):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def _teto_div(a, b):
    return -(-a // b)


def _somar_dias_uteis(data, dias):
    atual = data
    restantes = dias
    while restantes > 0:
        atual = atual + datetime.timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _validar(corpo):
    if not isinstance(corpo, dict):
        return "Corpo deve ser um objeto JSON."
    for campo in ("cep_origem", "cep_destino"):
        v = corpo.get(campo)
        if not isinstance(v, str) or not CEP_RE.fullmatch(v):
            return f"Campo '{campo}' inválido."
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        return "Campo 'pacote' inválido."
    peso = pacote.get("peso_g")
    if not _eh_int(peso) or peso < 1 or peso > 100000:
        return "Campo 'pacote.peso_g' inválido."
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        v = pacote.get(campo)
        if not _eh_int(v) or v < 1 or v > 200:
            return f"Campo 'pacote.{campo}' inválido."
    if _ler_data(corpo.get("data")) is None:
        return "Campo 'data' inválido."
    return None


@app.post("/cotacoes")
async def cotar(request: Request):
    corpo = await _ler_json(request)
    if corpo is _INVALIDO:
        return _erro(422, "DADOS_INVALIDOS", "Corpo JSON inválido.")
    problema = _validar(corpo)
    if problema:
        return _erro(422, "DADOS_INVALIDOS", problema)

    uf_origem = _uf_do_cep(corpo["cep_origem"])
    uf_destino = _uf_do_cep(corpo["cep_destino"])
    if uf_origem is None or uf_destino is None:
        return _erro(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    try:
        url = os.environ["URL_TRANSPORTADORAS"] + "/transportadoras"
        resp = httpx.get(url, params={"somente_ativas": "true"}, timeout=10.0)
        if resp.status_code != 200:
            return _erro(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
        transportadoras = resp.json()
        if not isinstance(transportadoras, list):
            return _erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    except Exception:
        return _erro(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")

    pacote = corpo["pacote"]
    data = _ler_data(corpo["data"])
    volume = pacote["comprimento_cm"] * pacote["largura_cm"] * pacote["altura_cm"]
    peso_cubado = _teto_div(volume, 6)
    peso_taxado = max(pacote["peso_g"], peso_cubado)
    kg_cobrados = _teto_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino

    opcoes = []
    try:
        for t in transportadoras:
            if not t.get("ativa", False):
                continue
            ufs = t.get("ufs_atendidas") or []
            if uf_origem not in ufs or uf_destino not in ufs:
                continue
            if peso_taxado > t["peso_maximo_g"]:
                continue
            preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg_cobrados
            prazo = t["prazo_dias_uteis"]
            if interestadual:
                preco = _teto_div(preco * (100 + t["adicional_interestadual_pct"]), 100)
                prazo = prazo + t["dias_adicionais_interestadual"]
            opcoes.append({
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": preco,
                "prazo_dias_uteis": prazo,
                "data_prevista_entrega": _somar_dias_uteis(data, prazo).isoformat(),
            })
    except Exception:
        return _erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")

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
        "valida_ate": (data + datetime.timedelta(days=7)).isoformat(),
        "opcoes": opcoes,
    }
    _estado["cotacoes"][cid] = cotacao
    return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{cid}")
async def consultar(cid: str):
    c = _estado["cotacoes"].get(cid)
    if c is None:
        return _erro(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return JSONResponse(status_code=200, content=copy.deepcopy(c))


@app.post("/admin/reset")
async def reset():
    _estado["cotacoes"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)
