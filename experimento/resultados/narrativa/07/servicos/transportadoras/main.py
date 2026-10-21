# arquivo: servicos/transportadoras/main.py
import copy
import re
import threading
from typing import List, Optional

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
REGEX_ID = re.compile(r"^[A-Z0-9_]{2,20}$")

_lock = threading.Lock()
_transportadoras = {}


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


class TransportadoraEntrada(BaseModel):
    id: StrictStr
    nome: StrictStr
    ufs_atendidas: List[StrictStr]
    tarifa_base_centavos: StrictInt = Field(..., ge=0)
    tarifa_kg_centavos: StrictInt = Field(..., ge=0)
    adicional_interestadual_pct: StrictInt = Field(..., ge=0, le=100)
    prazo_dias_uteis: StrictInt = Field(..., ge=1)
    dias_adicionais_interestadual: StrictInt = Field(..., ge=0)
    peso_maximo_g: StrictInt = Field(..., ge=1)

    @field_validator("id")
    @classmethod
    def _validar_id(cls, v):
        if not REGEX_ID.match(v):
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
            raise ValueError("uf repetida")
        for uf in v:
            if uf not in UFS_VALIDAS:
                raise ValueError("uf invalida")
        return v


class AtualizacaoAtiva(BaseModel):
    ativa: StrictBool


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras", status_code=201)
def criar_transportadora(entrada: TransportadoraEntrada):
    with _lock:
        if entrada.id in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Ja existe transportadora com esse id.")
        registro = {
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
        _transportadoras[entrada.id] = registro
        return JSONResponse(status_code=201, content=copy.deepcopy(registro))


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: Optional[bool] = Query(None)):
    with _lock:
        itens = [copy.deepcopy(t) for _, t in sorted(_transportadoras.items())]
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return itens


@app.get("/transportadoras/{id_transportadora}")
def obter_transportadora(id_transportadora: str):
    with _lock:
        t = _transportadoras.get(id_transportadora)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
        return copy.deepcopy(t)


@app.patch("/transportadoras/{id_transportadora}")
def atualizar_transportadora(id_transportadora: str, entrada: AtualizacaoAtiva):
    with _lock:
        t = _transportadoras.get(id_transportadora)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
        t["ativa"] = entrada.ativa
        return copy.deepcopy(t)
