# arquivo: servicos/cotacao/main.py
import os
import re
import threading
from datetime import date, datetime, timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroApi(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"erro": codigos.get(exc.status_code, "ERRO"), "mensagem": str(exc.detail)},
    )


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

_lock = threading.Lock()
_cotacoes: dict = {}
_contador = {"valor": 0}


def _validar_data(v: str) -> str:
    if not RE_DATA.match(v):
        raise ValueError("data inválida")
    datetime.strptime(v, "%Y-%m-%d")
    return v


class Pacote(BaseModel):
    peso_g: StrictInt
    comprimento_cm: StrictInt
    largura_cm: StrictInt
    altura_cm: StrictInt

    @field_validator("peso_g")
    @classmethod
    def _v_peso(cls, v):
        if v < 1 or v > 100000:
            raise ValueError("peso fora da faixa")
        return v

    @field_validator("comprimento_cm", "largura_cm", "altura_cm")
    @classmethod
    def _v_medida(cls, v):
        if v < 1 or v > 200:
            raise ValueError("medida fora da faixa")
        return v


class NovaCotacao(BaseModel):
    cep_origem: StrictStr
    cep_destino: StrictStr
    pacote: Pacote
    data: StrictStr

    @field_validator("cep_origem", "cep_destino")
    @classmethod
    def _v_cep(cls, v):
        if not RE_CEP.match(v):
            raise ValueError("CEP inválido")
        return v

    @field_validator("data")
    @classmethod
    def _v_data(cls, v):
        return _validar_data(v)


def _uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    d = inicio
    restantes = dias
    while restantes > 0:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            restantes -= 1
    return d


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def _buscar_transportadoras_ativas():
    base = os.environ.get("URL_TRANSPORTADORAS", "")
    try:
        resp = httpx.get(f"{base}/transportadoras", params={"somente_ativas": "true"}, timeout=10.0)
    except httpx.HTTPError:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if resp.status_code != 200:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    try:
        dados = resp.json()
    except ValueError:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if not isinstance(dados, list):
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    return dados


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _cotacoes.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(dados: NovaCotacao):
    uf_origem = _uf_do_cep(dados.cep_origem)
    uf_destino = _uf_do_cep(dados.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroApi(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    transportadoras = _buscar_transportadoras_ativas()

    p = dados.pacote
    peso_cubado = _ceil_div(p.comprimento_cm * p.largura_cm * p.altura_cm, 6)
    peso_taxado = max(p.peso_g, peso_cubado)
    quilos = _ceil_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino
    data_cotacao = datetime.strptime(dados.data, "%Y-%m-%d").date()

    opcoes = []
    for t in transportadoras:
        try:
            if not t.get("ativa", True):
                continue
            ufs = t["ufs_atendidas"]
            if uf_origem not in ufs or uf_destino not in ufs:
                continue
            if t["peso_maximo_g"] < peso_taxado:
                continue
            preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * quilos
            prazo = t["prazo_dias_uteis"]
            if interestadual:
                preco = _ceil_div(preco * (100 + t["adicional_interestadual_pct"]), 100)
                prazo = prazo + t["dias_adicionais_interestadual"]
            entrega = _somar_dias_uteis(data_cotacao, prazo)
            opcoes.append(
                {
                    "transportadora_id": t["id"],
                    "nome": t["nome"],
                    "preco_centavos": preco,
                    "prazo_dias_uteis": prazo,
                    "data_prevista_entrega": entrega.isoformat(),
                }
            )
        except (KeyError, TypeError):
            continue

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador["valor"] += 1
        cid = f"COT-{_contador['valor']:06d}"
        cotacao = {
            "id": cid,
            "cep_origem": dados.cep_origem,
            "cep_destino": dados.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": dados.data,
            "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cid] = cotacao
        return JSONResponse(status_code=201, content=_copia(cotacao))


def _copia(c: dict) -> dict:
    r = dict(c)
    r["opcoes"] = [dict(o) for o in c["opcoes"]]
    return r


@app.get("/cotacoes/{cotacao_id}")
def obter_cotacao(cotacao_id: str):
    with _lock:
        c = _cotacoes.get(cotacao_id)
        if c is None:
            raise ErroApi(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return _copia(c)
