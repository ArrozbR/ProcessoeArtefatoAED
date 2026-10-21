# arquivo: servicos/transportadoras/main.py
import copy
import re
import threading
from typing import List

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resposta_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}

_trava = threading.RLock()
_transportadoras: dict = {}


class TransportadoraEntrada(BaseModel):
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
    def _validar_id(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Z0-9_]{2,20}", v):
            raise ValueError("id inválido")
        return v

    @field_validator("nome")
    @classmethod
    def _validar_nome(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("nome vazio")
        return v

    @field_validator("ufs_atendidas")
    @classmethod
    def _validar_ufs(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("lista vazia")
        if len(set(v)) != len(v):
            raise ValueError("UF repetida")
        for uf in v:
            if uf not in UFS_VALIDAS:
                raise ValueError("UF inválida")
        return v


class AtualizacaoAtiva(BaseModel):
    ativa: StrictBool


@app.post("/admin/reset")
def reset():
    with _trava:
        _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras")
def criar_transportadora(dados: TransportadoraEntrada):
    with _trava:
        if dados.id in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com esse id.")
        registro = {
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
        _transportadoras[dados.id] = registro
        return JSONResponse(status_code=201, content=copy.deepcopy(registro))


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: bool = Query(False)):
    with _trava:
        lista = [copy.deepcopy(t) for _, t in sorted(_transportadoras.items())]
    if somente_ativas:
        lista = [t for t in lista if t["ativa"]]
    return JSONResponse(status_code=200, content=lista)


@app.get("/transportadoras/{id}")
def obter_transportadora(id: str):
    with _trava:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return JSONResponse(status_code=200, content=copy.deepcopy(t))


@app.patch("/transportadoras/{id}")
def atualizar_transportadora(id: str, dados: AtualizacaoAtiva):
    with _trava:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = dados.ativa
        return JSONResponse(status_code=200, content=copy.deepcopy(t))
