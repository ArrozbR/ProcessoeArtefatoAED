# arquivo: servicos/cotacao/main.py
import copy
import math
import os
import re
import threading
from datetime import date, datetime, timedelta
from fractions import Fraction

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
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
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"erro": codigos.get(exc.status_code, "ERRO_HTTP"), "mensagem": str(exc.detail)},
    )


_cliente = httpx.Client(timeout=10.0)


def _url(nome: str) -> str:
    return os.environ.get(nome, "")


def _validar_data(v: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v):
        raise ValueError("data inválida")
    datetime.strptime(v, "%Y-%m-%d")
    return v


class Pacote(BaseModel):
    peso_g: int = Field(..., strict=True, ge=1, le=100000)
    comprimento_cm: int = Field(..., strict=True, ge=1, le=200)
    largura_cm: int = Field(..., strict=True, ge=1, le=200)
    altura_cm: int = Field(..., strict=True, ge=1, le=200)


class CotacaoEntrada(BaseModel):
    cep_origem: str = Field(..., strict=True)
    cep_destino: str = Field(..., strict=True)
    pacote: Pacote
    data: str = Field(..., strict=True)

    @field_validator("cep_origem", "cep_destino")
    @classmethod
    def _validar_cep(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9]{8}", v):
            raise ValueError("CEP inválido")
        return v

    @field_validator("data")
    @classmethod
    def _validar_data_campo(cls, v: str) -> str:
        return _validar_data(v)


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


def _uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    atual = inicio
    restantes = dias
    while restantes > 0:
        atual += timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _teto_div(a: int, b: int) -> int:
    return -((-a) // b)


_trava = threading.RLock()
_cotacoes: dict = {}
_contador = {"cotacao": 0}


@app.post("/admin/reset", status_code=204)
def reset():
    with _trava:
        _cotacoes.clear()
        _contador["cotacao"] = 0
    return Response(status_code=204)


def _buscar_transportadoras_ativas() -> list:
    try:
        r = _cliente.get(
            f"{_url('URL_TRANSPORTADORAS')}/transportadoras",
            params={"somente_ativas": "true"},
        )
    except httpx.HTTPError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if r.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    try:
        dados = r.json()
    except ValueError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    return dados


@app.post("/cotacoes", status_code=201)
def criar_cotacao(entrada: CotacaoEntrada):
    uf_origem = _uf_do_cep(entrada.cep_origem)
    uf_destino = _uf_do_cep(entrada.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    transportadoras = _buscar_transportadoras_ativas()

    p = entrada.pacote
    peso_cubado = _teto_div(p.comprimento_cm * p.largura_cm * p.altura_cm, 6)
    peso_taxado = max(p.peso_g, peso_cubado)
    quilos = _teto_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino
    data_cotacao = datetime.strptime(entrada.data, "%Y-%m-%d").date()

    opcoes = []
    for t in transportadoras:
        if not isinstance(t, dict):
            continue
        if t.get("ativa") is False:
            continue
        ufs = t.get("ufs_atendidas") or []
        if uf_origem not in ufs or uf_destino not in ufs:
            continue
        if t.get("peso_maximo_g", 0) < peso_taxado:
            continue
        preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * quilos
        prazo = t["prazo_dias_uteis"]
        if interestadual:
            adicional = Fraction(str(t["adicional_interestadual_pct"]))
            preco = math.ceil(Fraction(preco) * (100 + adicional) / 100)
            prazo = prazo + t["dias_adicionais_interestadual"]
        entrega = _somar_dias_uteis(data_cotacao, prazo)
        opcoes.append(
            {
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": int(preco),
                "prazo_dias_uteis": prazo,
                "data_prevista_entrega": entrega.isoformat(),
            }
        )
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _trava:
        _contador["cotacao"] += 1
        cotacao_id = f"COT-{_contador['cotacao']:06d}"
        cotacao = {
            "id": cotacao_id,
            "cep_origem": entrada.cep_origem,
            "cep_destino": entrada.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": entrada.data,
            "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cotacao_id] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{cotacao_id}")
def obter_cotacao(cotacao_id: str):
    with _trava:
        c = _cotacoes.get(cotacao_id)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return copy.deepcopy(c)
