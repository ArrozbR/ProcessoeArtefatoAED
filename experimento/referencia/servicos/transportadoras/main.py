import re

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

UFS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}

app = FastAPI(title="Transportadoras")
_transportadoras = {}


class ErroApi(Exception):
    def __init__(self, status, erro, mensagem):
        self.status, self.erro, self.mensagem = status, erro, mensagem


@app.exception_handler(ErroApi)
def _erro_api(_: Request, e: ErroApi):
    return JSONResponse({"erro": e.erro, "mensagem": e.mensagem}, status_code=e.status)


@app.exception_handler(RequestValidationError)
def _erro_validacao(_: Request, e: RequestValidationError):
    return JSONResponse({"erro": "DADOS_INVALIDOS", "mensagem": "dados inválidos"}, status_code=422)


class NovaTransportadora(BaseModel):
    id: str
    nome: str
    ufs_atendidas: list[str]
    tarifa_base_centavos: int
    tarifa_kg_centavos: int
    adicional_interestadual_pct: int
    prazo_dias_uteis: int
    dias_adicionais_interestadual: int
    peso_maximo_g: int

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        if not re.fullmatch(r"[A-Z0-9_]{2,20}", v):
            raise ValueError("id inválido")
        return v

    @field_validator("nome")
    @classmethod
    def _nome(cls, v):
        if not v.strip():
            raise ValueError("nome vazio")
        return v

    @field_validator("ufs_atendidas")
    @classmethod
    def _ufs(cls, v):
        if not v or len(set(v)) != len(v) or not set(v) <= UFS:
            raise ValueError("ufs inválidas")
        return v

    @field_validator("tarifa_base_centavos", "tarifa_kg_centavos", "dias_adicionais_interestadual")
    @classmethod
    def _nao_negativo(cls, v):
        if v < 0:
            raise ValueError("negativo")
        return v

    @field_validator("adicional_interestadual_pct")
    @classmethod
    def _pct(cls, v):
        if not 0 <= v <= 100:
            raise ValueError("percentual inválido")
        return v

    @field_validator("prazo_dias_uteis", "peso_maximo_g")
    @classmethod
    def _positivo(cls, v):
        if v < 1:
            raise ValueError("deve ser >= 1")
        return v


class Ativacao(BaseModel):
    ativa: bool


@app.post("/transportadoras", status_code=201)
def cadastrar(dados: NovaTransportadora):
    if dados.id in _transportadoras:
        raise ErroApi(409, "TRANSPORTADORA_DUPLICADA", "já existe transportadora com esse id")
    t = {**dados.model_dump(), "ativa": True}
    _transportadoras[dados.id] = t
    return t


@app.get("/transportadoras")
def listar(somente_ativas: bool = False):
    return [t for _, t in sorted(_transportadoras.items()) if t["ativa"] or not somente_ativas]


@app.get("/transportadoras/{id}")
def consultar(id: str):
    return _buscar(id)


@app.patch("/transportadoras/{id}")
def ativar(id: str, dados: Ativacao):
    t = _buscar(id)
    t["ativa"] = dados.ativa
    return t


@app.post("/admin/reset", status_code=204)
def reset():
    _transportadoras.clear()
    return Response(status_code=204)


def _buscar(id):
    if id not in _transportadoras:
        raise ErroApi(404, "TRANSPORTADORA_NAO_ENCONTRADA", "transportadora não encontrada")
    return _transportadoras[id]
