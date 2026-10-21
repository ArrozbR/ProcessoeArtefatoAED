# arquivo: servicos/cotacao/main.py
import copy
import os
import re
import threading
from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, conint, constr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

RE_CEP = re.compile(r"[0-9]{8}")
RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

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


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


_lock = threading.Lock()
_cotacoes = {}
_contador = [0]
_cliente_http = httpx.Client(timeout=10.0)


def _validar_data(v):
    if not RE_DATA.fullmatch(v):
        raise ValueError("data inválida")
    datetime.strptime(v, "%Y-%m-%d")
    return v


class Pacote(BaseModel):
    peso_g: conint(strict=True, ge=1, le=100000)
    comprimento_cm: conint(strict=True, ge=1, le=200)
    largura_cm: conint(strict=True, ge=1, le=200)
    altura_cm: conint(strict=True, ge=1, le=200)


class CotacaoEntrada(BaseModel):
    cep_origem: constr(strict=True)
    cep_destino: constr(strict=True)
    pacote: Pacote
    data: constr(strict=True)

    @field_validator("cep_origem", "cep_destino")
    @classmethod
    def _validar_cep(cls, v):
        if not RE_CEP.fullmatch(v):
            raise ValueError("CEP inválido")
        return v

    @field_validator("data")
    @classmethod
    def _validar_data_campo(cls, v):
        return _validar_data(v)


def _uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def _somar_dias_uteis(data_inicial, dias: int):
    d = data_inicial
    restantes = dias
    while restantes > 0:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            restantes -= 1
    return d


def _buscar_transportadoras_ativas():
    base = os.environ.get("URL_TRANSPORTADORAS", "")
    try:
        resp = _cliente_http.get(base + "/transportadoras", params={"somente_ativas": "true"}, timeout=10.0)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if resp.status_code >= 500 or resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")
    return dados


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _cotacoes.clear()
        _contador[0] = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(entrada: CotacaoEntrada):
    uf_origem = _uf_do_cep(entrada.cep_origem)
    uf_destino = _uf_do_cep(entrada.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    p = entrada.pacote
    peso_cubado = _ceil_div(p.comprimento_cm * p.largura_cm * p.altura_cm, 6)
    peso_taxado = max(p.peso_g, peso_cubado)
    kg = _ceil_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino
    data_cotacao = datetime.strptime(entrada.data, "%Y-%m-%d").date()

    transportadoras = _buscar_transportadoras_ativas()

    opcoes = []
    for t in transportadoras:
        try:
            if not t.get("ativa", True):
                continue
            ufs = t.get("ufs_atendidas") or []
            if uf_origem not in ufs or uf_destino not in ufs:
                continue
            if t["peso_maximo_g"] < peso_taxado:
                continue
            preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg
            prazo = t["prazo_dias_uteis"]
            if interestadual:
                preco = _ceil_div(preco * (100 + t["adicional_interestadual_pct"]), 100)
                prazo = prazo + t["dias_adicionais_interestadual"]
            entrega = _somar_dias_uteis(data_cotacao, prazo)
            opcoes.append({
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": preco,
                "prazo_dias_uteis": prazo,
                "data_prevista_entrega": entrega.isoformat(),
            })
        except (KeyError, TypeError):
            continue

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador[0] += 1
        cot_id = "COT-%06d" % _contador[0]
        cotacao = {
            "id": cot_id,
            "cep_origem": entrada.cep_origem,
            "cep_destino": entrada.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": entrada.data,
            "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cot_id] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{id}")
def obter_cotacao(id: str):
    with _lock:
        c = _cotacoes.get(id)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return copy.deepcopy(c)
