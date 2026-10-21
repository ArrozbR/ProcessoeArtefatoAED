# arquivo: servicos/cotacao/main.py
import copy
import os
import re
import threading
from datetime import date, timedelta

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

REGEX_CEP = re.compile(r"^[0-9]{8}$")
REGEX_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

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
_cotacoes = {}
_contador = {"valor": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _erro(exc.status_code, codigos.get(exc.status_code, "ERRO_HTTP"), str(exc.detail))


@app.exception_handler(Exception)
async def _tratar_generico(request, exc: Exception):
    return _erro(500, "ERRO_INTERNO", "Erro interno.")


def _validar_data(v: str) -> str:
    if not REGEX_DATA.match(v):
        raise ValueError("data invalida")
    date.fromisoformat(v)
    return v


class Pacote(BaseModel):
    peso_g: StrictInt = Field(..., ge=1, le=100000)
    comprimento_cm: StrictInt = Field(..., ge=1, le=200)
    largura_cm: StrictInt = Field(..., ge=1, le=200)
    altura_cm: StrictInt = Field(..., ge=1, le=200)


class CotacaoEntrada(BaseModel):
    cep_origem: StrictStr
    cep_destino: StrictStr
    pacote: Pacote
    data: StrictStr

    @field_validator("cep_origem", "cep_destino")
    @classmethod
    def _validar_cep(cls, v):
        if not REGEX_CEP.match(v):
            raise ValueError("cep invalido")
        return v

    @field_validator("data")
    @classmethod
    def _validar_data_campo(cls, v):
        return _validar_data(v)


def _uf_do_cep(cep: str):
    numero = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= numero <= fim:
            return uf
    return None


def _ceil_div(a: int, b: int) -> int:
    return -((-a) // b)


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    atual = inicio
    restantes = dias
    while restantes > 0:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _url(nome: str) -> str:
    return os.environ.get(nome, "")


def _buscar_transportadoras_ativas():
    try:
        with httpx.Client(timeout=10.0) as cliente:
            resposta = cliente.get(
                _url("URL_TRANSPORTADORAS") + "/transportadoras",
                params={"somente_ativas": "true"},
            )
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico de transportadoras indisponivel.")
    if resposta.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico de transportadoras indisponivel.")
    try:
        dados = resposta.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta invalida do servico de transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta invalida do servico de transportadoras.")
    return dados


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _cotacoes.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(entrada: CotacaoEntrada):
    uf_origem = _uf_do_cep(entrada.cep_origem)
    uf_destino = _uf_do_cep(entrada.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da area atendida.")

    pacote = entrada.pacote
    peso_cubado = _ceil_div(pacote.comprimento_cm * pacote.largura_cm * pacote.altura_cm, 6)
    peso_taxado = max(pacote.peso_g, peso_cubado)
    quilos = _ceil_div(peso_taxado, 1000)
    data_cotacao = date.fromisoformat(entrada.data)
    interestadual = uf_origem != uf_destino

    transportadoras = _buscar_transportadoras_ativas()

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
            opcoes.append(
                {
                    "transportadora_id": t["id"],
                    "nome": t["nome"],
                    "preco_centavos": preco,
                    "prazo_dias_uteis": prazo,
                    "data_prevista_entrega": _somar_dias_uteis(data_cotacao, prazo).isoformat(),
                }
            )
        except (KeyError, TypeError):
            raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta invalida do servico de transportadoras.")

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador["valor"] += 1
        id_cotacao = "COT-%06d" % _contador["valor"]
        cotacao = {
            "id": id_cotacao,
            "cep_origem": entrada.cep_origem,
            "cep_destino": entrada.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": entrada.data,
            "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[id_cotacao] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{id_cotacao}")
def obter_cotacao(id_cotacao: str):
    with _lock:
        c = _cotacoes.get(id_cotacao)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotacao nao encontrada.")
        return copy.deepcopy(c)
