# arquivo: servicos/transportadoras/main.py
import re
from typing import List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_REGEX = re.compile(r"[A-Z0-9_]{2,20}")

app = FastAPI()


class ErroApi(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def tratar_erro_api(request: Request, exc: ErroApi):
    return resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def tratar_validacao(request: Request, exc: RequestValidationError):
    return resposta_erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return resposta_erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return resposta_erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return resposta_erro(exc.status_code, "ERRO", str(exc.detail))


class TransportadoraEntrada(BaseModel):
    id: StrictStr
    nome: StrictStr = Field(min_length=1)
    ufs_atendidas: List[StrictStr] = Field(min_length=1)
    tarifa_base_centavos: StrictInt = Field(ge=0)
    tarifa_kg_centavos: StrictInt = Field(ge=0)
    adicional_interestadual_pct: StrictInt = Field(ge=0, le=100)
    prazo_dias_uteis: StrictInt = Field(ge=1)
    dias_adicionais_interestadual: StrictInt = Field(ge=0)
    peso_maximo_g: StrictInt = Field(ge=1)

    @field_validator("id")
    @classmethod
    def validar_id(cls, v):
        if not ID_REGEX.fullmatch(v):
            raise ValueError("id invalido")
        return v

    @field_validator("nome")
    @classmethod
    def validar_nome(cls, v):
        if not v.strip():
            raise ValueError("nome vazio")
        return v

    @field_validator("ufs_atendidas")
    @classmethod
    def validar_ufs(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("ufs repetidas")
        for uf in v:
            if uf not in UFS_VALIDAS:
                raise ValueError("uf invalida")
        return v


class AtualizacaoAtiva(BaseModel):
    ativa: StrictBool


transportadoras = {}


@app.post("/admin/reset", status_code=204)
def reset():
    transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras", status_code=201)
def criar_transportadora(dados: TransportadoraEntrada):
    if dados.id in transportadoras:
        raise ErroApi(409, "TRANSPORTADORA_DUPLICADA", "Ja existe transportadora com esse id.")
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
    transportadoras[dados.id] = registro
    return JSONResponse(status_code=201, content=registro)


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: Optional[bool] = False):
    resultado = [transportadoras[k] for k in sorted(transportadoras.keys())]
    if somente_ativas:
        resultado = [t for t in resultado if t["ativa"]]
    return resultado


@app.get("/transportadoras/{id}")
def obter_transportadora(id: str):
    if id not in transportadoras:
        raise ErroApi(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
    return transportadoras[id]


@app.patch("/transportadoras/{id}")
def atualizar_transportadora(id: str, dados: AtualizacaoAtiva):
    if id not in transportadoras:
        raise ErroApi(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
    transportadoras[id]["ativa"] = dados.ativa
    return transportadoras[id]
