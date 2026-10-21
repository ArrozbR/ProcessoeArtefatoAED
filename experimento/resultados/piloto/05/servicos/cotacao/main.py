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
CEP_RE = re.compile(r"^[0-9]{8}$")
DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

_estado = {"contador": 0, "cotacoes": {}}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request, exc):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request, exc):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


class Invalido(Exception):
    pass


class Indisponivel(Exception):
    pass


async def ler_json(request):
    try:
        corpo = await request.json()
    except Exception:
        raise Invalido()
    if not isinstance(corpo, dict):
        raise Invalido()
    return corpo


def eh_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def int_faixa(d, campo, minimo, maximo):
    v = d.get(campo)
    if not eh_int(v) or v < minimo or v > maximo:
        raise Invalido()
    return v


def validar_corpo(corpo):
    cep_o = corpo.get("cep_origem")
    cep_d = corpo.get("cep_destino")
    if not isinstance(cep_o, str) or not CEP_RE.match(cep_o):
        raise Invalido()
    if not isinstance(cep_d, str) or not CEP_RE.match(cep_d):
        raise Invalido()
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        raise Invalido()
    peso = int_faixa(pacote, "peso_g", 1, 100000)
    comp = int_faixa(pacote, "comprimento_cm", 1, 200)
    larg = int_faixa(pacote, "largura_cm", 1, 200)
    alt = int_faixa(pacote, "altura_cm", 1, 200)
    data_txt = corpo.get("data")
    if not isinstance(data_txt, str) or not DATA_RE.match(data_txt):
        raise Invalido()
    try:
        data = date.fromisoformat(data_txt)
    except ValueError:
        raise Invalido()
    return cep_o, cep_d, peso, comp, larg, alt, data_txt, data


def uf_do_cep(cep):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def teto_div(a, b):
    return -(-a // b)


def somar_dias_uteis(inicio, dias):
    d = inicio
    contados = 0
    while contados < dias:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            contados += 1
    return d


def obter_transportadoras():
    try:
        url = os.environ["URL_TRANSPORTADORAS"]
        resp = httpx.get(
            url + "/transportadoras",
            params={"somente_ativas": "true"},
            timeout=10.0,
        )
    except Exception:
        raise Indisponivel()
    if resp.status_code != 200:
        raise Indisponivel()
    try:
        dados = resp.json()
    except Exception:
        raise Indisponivel()
    if not isinstance(dados, list):
        raise Indisponivel()
    return dados


@app.post("/cotacoes")
async def cotar(request: Request):
    try:
        corpo = await ler_json(request)
        cep_o, cep_d, peso, comp, larg, alt, data_txt, data = validar_corpo(corpo)
    except Invalido:
        return erro(422, "DADOS_INVALIDOS", "Dados da cotação inválidos.")

    uf_o = uf_do_cep(cep_o)
    uf_d = uf_do_cep(cep_d)
    if uf_o is None or uf_d is None:
        return erro(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    try:
        transportadoras = obter_transportadoras()
    except Indisponivel:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")

    peso_cubado = teto_div(comp * larg * alt, 6)
    peso_taxado = max(peso, peso_cubado)
    kg = teto_div(peso_taxado, 1000)
    interestadual = uf_o != uf_d

    opcoes = []
    for t in transportadoras:
        if not t.get("ativa", True):
            continue
        ufs = t.get("ufs_atendidas") or []
        if uf_o not in ufs or uf_d not in ufs:
            continue
        if peso_taxado > t["peso_maximo_g"]:
            continue
        preco_base = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg
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
            "data_prevista_entrega": somar_dias_uteis(data, prazo).isoformat(),
        })

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    _estado["contador"] += 1
    cid = "COT-%06d" % _estado["contador"]
    cotacao = {
        "id": cid,
        "cep_origem": cep_o,
        "cep_destino": cep_d,
        "uf_origem": uf_o,
        "uf_destino": uf_d,
        "peso_taxado_g": peso_taxado,
        "data": data_txt,
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
