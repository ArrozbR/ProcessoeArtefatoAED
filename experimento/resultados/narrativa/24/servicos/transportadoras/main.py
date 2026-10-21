# arquivo: servicos/transportadoras/main.py
import threading
from typing import List, Literal, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = Literal["SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"]


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _erro_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


class TransportadoraEntrada(BaseModel):
    id: StrictStr = Field(pattern=r"^[A-Z0-9_]{2,20}$")
    nome: StrictStr = Field(min_length=1)
    ufs_atendidas: List[UFS_VALIDAS] = Field(min_length=1)
    tarifa_base_centavos: StrictInt = Field(ge=0)
    tarifa_kg_centavos: StrictInt = Field(ge=0)
    adicional_interestadual_pct: StrictInt = Field(ge=0, le=100)
    prazo_dias_uteis: StrictInt = Field(ge=1)
    dias_adicionais_interestadual: StrictInt = Field(ge=0)
    peso_maximo_g: StrictInt = Field(ge=1)

    @field_validator("ufs_atendidas")
    @classmethod
    def _sem_repeticao(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("UFs repetidas")
        return v


class AtivaEntrada(BaseModel):
    ativa: StrictBool


_lock = threading.Lock()
_transportadoras = {}


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras", status_code=201)
def criar(entrada: TransportadoraEntrada):
    with _lock:
        if entrada.id in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Transportadora já cadastrada.")
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
        return dict(t, ufs_atendidas=list(t["ufs_atendidas"]))


@app.get("/transportadoras")
def listar(somente_ativas: Optional[bool] = False):
    with _lock:
        itens = sorted(_transportadoras.values(), key=lambda t: t["id"])
        if somente_ativas:
            itens = [t for t in itens if t["ativa"]]
        return [dict(t, ufs_atendidas=list(t["ufs_atendidas"])) for t in itens]


@app.get("/transportadoras/{id}")
def obter(id: str):
    with _lock:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return dict(t, ufs_atendidas=list(t["ufs_atendidas"]))


@app.patch("/transportadoras/{id}")
def alterar(id: str, entrada: AtivaEntrada):
    with _lock:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = entrada.ativa
        return dict(t, ufs_atendidas=list(t["ufs_atendidas"]))
