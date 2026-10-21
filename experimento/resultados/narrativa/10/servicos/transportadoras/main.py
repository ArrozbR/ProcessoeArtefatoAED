# arquivo: servicos/transportadoras/main.py
import re
import threading
from typing import List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"[A-Z0-9_]{2,20}")


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()

_lock = threading.Lock()
_transportadoras = {}


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


class TransportadoraEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: StrictStr
    nome: StrictStr
    ufs_atendidas: List[StrictStr]
    tarifa_base_centavos: StrictInt = Field(ge=0)
    tarifa_kg_centavos: StrictInt = Field(ge=0)
    adicional_interestadual_pct: StrictInt = Field(ge=0, le=100)
    prazo_dias_uteis: StrictInt = Field(ge=1)
    dias_adicionais_interestadual: StrictInt = Field(ge=0)
    peso_maximo_g: StrictInt = Field(ge=1)

    @field_validator("id")
    @classmethod
    def _validar_id(cls, v):
        if not RE_ID.fullmatch(v):
            raise ValueError("id invalido")
        return v

    @field_validator("nome")
    @classmethod
    def _validar_nome(cls, v):
        if not v.strip():
            raise ValueError("nome vazio")
        return v

    @field_validator("ufs_atendidas")
    @classmethod
    def _validar_ufs(cls, v):
        if not v:
            raise ValueError("lista vazia")
        if len(set(v)) != len(v):
            raise ValueError("ufs repetidas")
        for uf in v:
            if uf not in UFS_VALIDAS:
                raise ValueError("uf invalida")
        return v


class AtivacaoEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ativa: StrictBool


def _copia(t: dict) -> dict:
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.post("/admin/reset")
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras", status_code=201)
def criar_transportadora(entrada: TransportadoraEntrada):
    with _lock:
        if entrada.id in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Ja existe transportadora com este id.")
        t = {
            "id": entrada.id,
            "nome": entrada.nome,
            "ufs_atendidas": list(entrada.ufs_atendidas),
            "tarifa_base_centavos": entrada.tarifa_base_centavos,
            "tarifa_kg_centavos": entrada.tarifa_kg_centavos,
            "adicional_interestadual_pct": entrada.adicional_interestadual_pct,
            "prazo_dias_uteis": entrada.prazo_dias_uteis,
            "dias_adicionais_interestadual": entrada.dias_adicionais_interestadual,
            "peso_maximo_g": entrada.peso_maximo_g,
            "ativa": True,
        }
        _transportadoras[entrada.id] = t
        return JSONResponse(status_code=201, content=_copia(t))


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: Optional[bool] = None):
    with _lock:
        lista = [_copia(_transportadoras[k]) for k in sorted(_transportadoras)]
    if somente_ativas:
        lista = [t for t in lista if t["ativa"]]
    return lista


@app.get("/transportadoras/{transportadora_id}")
def obter_transportadora(transportadora_id: str):
    with _lock:
        t = _transportadoras.get(transportadora_id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
        return _copia(t)


@app.patch("/transportadoras/{transportadora_id}")
def alterar_transportadora(transportadora_id: str, entrada: AtivacaoEntrada):
    with _lock:
        t = _transportadoras.get(transportadora_id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
        t["ativa"] = entrada.ativa
        return _copia(t)
