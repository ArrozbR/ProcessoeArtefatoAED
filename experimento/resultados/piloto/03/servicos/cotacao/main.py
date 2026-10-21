# arquivo: servicos/cotacao/main.py
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


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _h_erro(request, exc):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _h_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _h_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(msg="Dados inválidos na requisição."):
    raise ErroAPI(422, "DADOS_INVALIDOS", msg)


async def _ler_corpo(request):
    try:
        raw = await request.body()
        corpo = json.loads(raw)
    except Exception:
        _invalido("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        _invalido("Corpo deve ser um objeto JSON.")
    return corpo


def _eh_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _teto_div(a, b):
    return -((-a) // b)


def _uf_do_cep(cep):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def _somar_dias_uteis(inicio, dias):
    d = inicio
    contados = 0
    while contados < dias:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            contados += 1
    return d


def _buscar_transportadoras():
    url = os.environ["URL_TRANSPORTADORAS"] + "/transportadoras"
    try:
        r = httpx.get(url, params={"somente_ativas": "true"}, timeout=10.0)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    if r.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    try:
        dados = r.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    return dados


@app.post("/admin/reset")
async def reset():
    _estado["cotacoes"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/cotacoes")
def cotar(corpo: dict = None):
    # placeholder substituído abaixo (mantido para clareza de rota síncrona)
    raise ErroAPI(500, "ERRO", "não usado")


app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/cotacoes"]


def _validar_cotacao(corpo):
    cep_o = corpo.get("cep_origem")
    cep_d = corpo.get("cep_destino")
    if not isinstance(cep_o, str) or not CEP_RE.match(cep_o):
        _invalido("cep_origem inválido.")
    if not isinstance(cep_d, str) or not CEP_RE.match(cep_d):
        _invalido("cep_destino inválido.")
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        _invalido("pacote inválido.")
    peso = pacote.get("peso_g")
    if not _eh_int(peso) or peso < 1 or peso > 100000:
        _invalido("peso_g inválido.")
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        v = pacote.get(campo)
        if not _eh_int(v) or v < 1 or v > 200:
            _invalido(f"{campo} inválido.")
    data_txt = corpo.get("data")
    if not isinstance(data_txt, str) or not DATA_RE.match(data_txt):
        _invalido("data inválida.")
    try:
        data = date.fromisoformat(data_txt)
    except ValueError:
        _invalido("data inválida.")
    return cep_o, cep_d, pacote, data_txt, data


@app.post("/cotacoes")
async def criar_cotacao(request: Request):
    corpo = await _ler_corpo(request)
    cep_o, cep_d, pacote, data_txt, data = _validar_cotacao(corpo)

    uf_o = _uf_do_cep(cep_o)
    uf_d = _uf_do_cep(cep_d)
    if uf_o is None or uf_d is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    transportadoras = _buscar_transportadoras()

    peso_cubado = _teto_div(pacote["comprimento_cm"] * pacote["largura_cm"] * pacote["altura_cm"], 6)
    peso_taxado = max(pacote["peso_g"], peso_cubado)
    kg = _teto_div(peso_taxado, 1000)
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
        base = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg
        if interestadual:
            preco = _teto_div(base * (100 + t["adicional_interestadual_pct"]), 100)
            prazo = t["prazo_dias_uteis"] + t["dias_adicionais_interestadual"]
        else:
            preco = base
            prazo = t["prazo_dias_uteis"]
        prevista = _somar_dias_uteis(data, prazo)
        opcoes.append({
            "transportadora_id": t["id"],
            "nome": t["nome"],
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": prevista.isoformat(),
        })
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    numero = _estado["contador"] + 1
    cid = f"COT-{numero:06d}"
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
    _estado["contador"] = numero
    _estado["cotacoes"][cid] = cotacao
    return JSONResponse(status_code=201, content=cotacao)


@app.get("/cotacoes/{cid}")
async def consultar_cotacao(cid: str):
    c = _estado["cotacoes"].get(cid)
    if c is None:
        raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return JSONResponse(status_code=200, content=c)
