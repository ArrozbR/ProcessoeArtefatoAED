# arquivo: servicos/transportadoras/main.py
import threading
from typing import List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(title="Transportadoras")

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}

_lock = threading.Lock()
_transportadoras = {}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


@app.exception_handler(Exception)
async def _tratar_inesperado(request: Request, exc: Exception):
    return _resposta_erro(500, "ERRO_INTERNO", "Erro interno.")


class TransportadoraIn(BaseModel):
    id: StrictStr = Field(pattern=r"^[A-Z0-9_]{2,20}$")
    nome: StrictStr
    ufs_atendidas: List[StrictStr]
    tarifa_base_centavos: StrictInt = Field(ge=0)
    tarifa_kg_centavos: StrictInt = Field(ge=0)
    adicional_interestadual_pct: StrictInt = Field(ge=0, le=100)
    prazo_dias_uteis: StrictInt = Field(ge=1)
    dias_adicionais_interestadual: StrictInt = Field(ge=0)
    peso_maximo_g: StrictInt = Field(ge=1)

    @field_validator("nome")
    @classmethod
    def _nome(cls, v):
        if not v.strip():
            raise ValueError("nome vazio")
        return v

    @field_validator("ufs_atendidas")
    @classmethod
    def _ufs(cls, v):
        if len(v) == 0:
            raise ValueError("lista vazia")
        if len(set(v)) != len(v):
            raise ValueError("UF repetida")
        for uf in v:
            if uf not in UFS_VALIDAS:
                raise ValueError("UF inválida")
        return v


class AtivaIn(BaseModel):
    ativa: StrictBool


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras", status_code=201)
def criar_transportadora(corpo: TransportadoraIn):
    with _lock:
        if corpo.id in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com esse id.")
        t = {
            "id": corpo.id,
            "nome": corpo.nome,
            "ufs_atendidas": list(corpo.ufs_atendidas),
            "tarifa_base_centavos": corpo.tarifa_base_centavos,
            "tarifa_kg_centavos": corpo.tarifa_kg_centavos,
            "adicional_interestadual_pct": corpo.adicional_interestadual_pct,
            "prazo_dias_uteis": corpo.prazo_dias_uteis,
            "dias_adicionais_interestadual": corpo.dias_adicionais_interestadual,
            "peso_maximo_g": corpo.peso_maximo_g,
            "ativa": True,
        }
        _transportadoras[corpo.id] = t
        return JSONResponse(status_code=201, content=dict(t, ufs_atendidas=list(t["ufs_atendidas"])))


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: Optional[bool] = False):
    with _lock:
        lista = [
            dict(t, ufs_atendidas=list(t["ufs_atendidas"]))
            for _, t in sorted(_transportadoras.items(), key=lambda kv: kv[0])
            if (not somente_ativas) or t["ativa"]
        ]
    return lista


@app.get("/transportadoras/{transportadora_id}")
def obter_transportadora(transportadora_id: str):
    with _lock:
        t = _transportadoras.get(transportadora_id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return dict(t, ufs_atendidas=list(t["ufs_atendidas"]))


@app.patch("/transportadoras/{transportadora_id}")
def alterar_transportadora(transportadora_id: str, corpo: AtivaIn):
    with _lock:
        t = _transportadoras.get(transportadora_id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = corpo.ativa
        return dict(t, ufs_atendidas=list(t["ufs_atendidas"]))
