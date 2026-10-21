# arquivo: servicos/cotacao/main.py
import copy
import datetime
import os
import re
import threading
from typing import Annotated

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException


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
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos na requisição."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"erro": codigos.get(exc.status_code, "ERRO"), "mensagem": str(exc.detail)},
    )


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

_lock = threading.Lock()
_cotacoes: dict = {}
_estado = {"contador": 0}
_cliente_http = {"cliente": None}


def _cliente() -> httpx.Client:
    if _cliente_http["cliente"] is None:
        _cliente_http["cliente"] = httpx.Client(timeout=10.0)
    return _cliente_http["cliente"]


class Pacote(BaseModel):
    peso_g: Annotated[StrictInt, Field(ge=1, le=100000)]
    comprimento_cm: Annotated[StrictInt, Field(ge=1, le=200)]
    largura_cm: Annotated[StrictInt, Field(ge=1, le=200)]
    altura_cm: Annotated[StrictInt, Field(ge=1, le=200)]


class CotacaoEntrada(BaseModel):
    cep_origem: StrictStr
    cep_destino: StrictStr
    pacote: Pacote
    data: StrictStr

    @field_validator("cep_origem", "cep_destino")
    @classmethod
    def _validar_cep(cls, v):
        if not RE_CEP.fullmatch(v):
            raise ValueError("CEP inválido")
        return v

    @field_validator("data")
    @classmethod
    def _validar_data(cls, v):
        if not RE_DATA.fullmatch(v):
            raise ValueError("data inválida")
        try:
            datetime.date.fromisoformat(v)
        except ValueError:
            raise ValueError("data inválida")
        return v


def _uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def _somar_dias_uteis(data: datetime.date, dias: int) -> datetime.date:
    atual = data
    restantes = dias
    while restantes > 0:
        atual = atual + datetime.timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _buscar_transportadoras_ativas() -> list:
    base = os.environ.get("URL_TRANSPORTADORAS", "")
    try:
        resp = _cliente().get(f"{base}/transportadoras", params={"somente_ativas": "true"})
    except httpx.HTTPError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if resp.status_code != 200:
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
        _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(entrada: CotacaoEntrada):
    uf_origem = _uf_do_cep(entrada.cep_origem)
    uf_destino = _uf_do_cep(entrada.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    transportadoras = _buscar_transportadoras_ativas()

    p = entrada.pacote
    peso_cubado = _ceil_div(p.comprimento_cm * p.largura_cm * p.altura_cm, 6)
    peso_taxado = max(p.peso_g, peso_cubado)
    quilos = _ceil_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino
    data_cotacao = datetime.date.fromisoformat(entrada.data)

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
        _estado["contador"] += 1
        cid = "COT-%06d" % _estado["contador"]
        cotacao = {
            "id": cid,
            "cep_origem": entrada.cep_origem,
            "cep_destino": entrada.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": entrada.data,
            "valida_ate": (data_cotacao + datetime.timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cid] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{cotacao_id}")
def obter_cotacao(cotacao_id: str):
    with _lock:
        c = _cotacoes.get(cotacao_id)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return copy.deepcopy(c)
