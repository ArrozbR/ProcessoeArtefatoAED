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


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _erro_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        codigo = "NAO_ENCONTRADO"
    elif exc.status_code == 405:
        codigo = "METODO_NAO_PERMITIDO"
    elif exc.status_code == 422:
        codigo = "DADOS_INVALIDOS"
    else:
        codigo = "ERRO"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


def invalido(mensagem: str = "Dados inválidos.") -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


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

RE_CEP = re.compile(r"^[0-9]{8}$")
RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TIMEOUT = 10.0

_lock = threading.RLock()
_cotacoes: dict = {}
_contador = {"cotacao": 0}


def _eh_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _int_faixa(obj: dict, campo: str, minimo: int, maximo: int) -> int:
    v = obj.get(campo)
    if not _eh_int(v) or v < minimo or v > maximo:
        raise invalido(f"Campo '{campo}' inválido.")
    return v


def _parse_data(v: Any) -> date:
    if not isinstance(v, str) or not RE_DATA.match(v):
        raise invalido("Data inválida.")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise invalido("Data inválida.")


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
    contados = 0
    while contados < dias:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            contados += 1
    return d


def _buscar_transportadoras() -> list:
    url = os.environ["URL_TRANSPORTADORAS"] + "/transportadoras"
    try:
        resp = httpx.get(url, params={"somente_ativas": "true"}, timeout=TIMEOUT)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    if resp.status_code >= 500 or resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    return dados


@app.post("/cotacoes", status_code=201)
def cotar(body: Any = Body(None)):
    # 1. Validação do corpo
    if not isinstance(body, dict):
        raise invalido("Corpo deve ser um objeto JSON.")
    cep_origem = body.get("cep_origem")
    cep_destino = body.get("cep_destino")
    if not isinstance(cep_origem, str) or not RE_CEP.match(cep_origem):
        raise invalido("Campo 'cep_origem' inválido.")
    if not isinstance(cep_destino, str) or not RE_CEP.match(cep_destino):
        raise invalido("Campo 'cep_destino' inválido.")
    pacote = body.get("pacote")
    if not isinstance(pacote, dict):
        raise invalido("Campo 'pacote' inválido.")
    peso_g = _int_faixa(pacote, "peso_g", 1, 100000)
    comp = _int_faixa(pacote, "comprimento_cm", 1, 200)
    larg = _int_faixa(pacote, "largura_cm", 1, 200)
    alt = _int_faixa(pacote, "altura_cm", 1, 200)
    data_str = body.get("data")
    data_cot = _parse_data(data_str)

    # 2. Área atendida
    uf_origem = _uf_do_cep(cep_origem)
    uf_destino = _uf_do_cep(cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    # 3. Transportadoras
    transportadoras = _buscar_transportadoras()

    peso_cubado = _teto_div(comp * larg * alt, 6)
    peso_taxado = max(peso_g, peso_cubado)
    kg_cobrados = _teto_div(peso_taxado, 1000)
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
                "data_prevista_entrega": _somar_dias_uteis(data_cot, prazo).isoformat(),
            })
        except (KeyError, TypeError):
            raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador["cotacao"] += 1
        cid = f"COT-{_contador['cotacao']:06d}"
        cotacao = {
            "id": cid,
            "cep_origem": cep_origem,
            "cep_destino": cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": data_str,
            "valida_ate": (data_cot + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cid] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{cid}")
def consultar(cid: str):
    with _lock:
        c = _cotacoes.get(cid)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return copy.deepcopy(c)


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _cotacoes.clear()
        _contador["cotacao"] = 0
    return Response(status_code=204)
