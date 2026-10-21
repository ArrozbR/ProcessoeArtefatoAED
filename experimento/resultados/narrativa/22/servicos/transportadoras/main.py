# arquivo: servicos/transportadoras/main.py
import re
import threading
from typing import List

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictBool, StrictInt, StrictStr, field_validator
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


UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"^[A-Z0-9_]{2,20}$")

_lock = threading.Lock()
_transportadoras: dict = {}


class NovaTransportadora(BaseModel):
    id: StrictStr
    nome: StrictStr
    ufs_atendidas: List[StrictStr]
    tarifa_base_centavos: StrictInt
    tarifa_kg_centavos: StrictInt
    adicional_interestadual_pct: StrictInt
    prazo_dias_uteis: StrictInt
    dias_adicionais_interestadual: StrictInt
    peso_maximo_g: StrictInt

    @field_validator("id")
    @classmethod
    def _v_id(cls, v):
        if not RE_ID.match(v):
            raise ValueError("id inválido")
        return v

    @field_validator("nome")
    @classmethod
    def _v_nome(cls, v):
        if not v.strip():
            raise ValueError("nome vazio")
        return v

    @field_validator("ufs_atendidas")
    @classmethod
    def _v_ufs(cls, v):
        if not v:
            raise ValueError("lista vazia")
        if len(set(v)) != len(v):
            raise ValueError("UF repetida")
        for uf in v:
            if uf not in UFS_VALIDAS:
                raise ValueError("UF inválida")
        return v

    @field_validator("tarifa_base_centavos", "tarifa_kg_centavos", "dias_adicionais_interestadual")
    @classmethod
    def _v_nao_negativo(cls, v):
        if v < 0:
            raise ValueError("valor negativo")
        return v

    @field_validator("adicional_interestadual_pct")
    @classmethod
    def _v_pct(cls, v):
        if v < 0 or v > 100:
            raise ValueError("percentual fora da faixa")
        return v

    @field_validator("prazo_dias_uteis", "peso_maximo_g")
    @classmethod
    def _v_positivo(cls, v):
        if v < 1:
            raise ValueError("valor deve ser pelo menos 1")
        return v


class AlteracaoAtiva(BaseModel):
    ativa: StrictBool


def _copia(t: dict) -> dict:
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras", status_code=201)
def criar_transportadora(dados: NovaTransportadora):
    with _lock:
        if dados.id in _transportadoras:
            raise ErroApi(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com esse id.")
        t = {
            "id": dados.id,
            "nome": dados.nome,
            "ufs_atendidas": list(dados.ufs_atendidas),
            "tarifa_base_centavos": dados.tarifa_base_centavos,
            "tarifa_kg_centavos": dados.tarifa_kg_centavos,
            "adicional_interestadual_pct": dados.adicional_interestadual_pct,
            "prazo_dias_uteis": dados.prazo_dias_uteis,
            "dias_adicionais_interestadual": dados.dias_adicionais_interestadual,
            "peso_maximo_g": dados.peso_maximo_g,
            "ativa": True,
        }
        _transportadoras[dados.id] = t
        return JSONResponse(status_code=201, content=_copia(t))


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: bool = False):
    with _lock:
        itens = [
            _copia(_transportadoras[k])
            for k in sorted(_transportadoras.keys())
            if not somente_ativas or _transportadoras[k]["ativa"]
        ]
    return itens


@app.get("/transportadoras/{transportadora_id}")
def obter_transportadora(transportadora_id: str):
    with _lock:
        t = _transportadoras.get(transportadora_id)
        if t is None:
            raise ErroApi(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return _copia(t)


@app.patch("/transportadoras/{transportadora_id}")
def alterar_transportadora(transportadora_id: str, dados: AlteracaoAtiva):
    with _lock:
        t = _transportadoras.get(transportadora_id)
        if t is None:
            raise ErroApi(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = dados.ativa
        return _copia(t)
