# arquivo: servicos/transportadoras/main.py
import re
import threading
from typing import Annotated, List, Literal, Optional, Union

from fastapi import FastAPI, Query, Request
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


UF = Literal["SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"]

Percentual = Union[
    Annotated[int, Field(strict=True, ge=0, le=100)],
    Annotated[float, Field(strict=True, ge=0, le=100)],
]


class TransportadoraEntrada(BaseModel):
    id: str = Field(..., strict=True)
    nome: str = Field(..., strict=True, min_length=1)
    ufs_atendidas: List[UF] = Field(..., min_length=1)
    tarifa_base_centavos: int = Field(..., strict=True, ge=0)
    tarifa_kg_centavos: int = Field(..., strict=True, ge=0)
    adicional_interestadual_pct: Percentual
    prazo_dias_uteis: int = Field(..., strict=True, ge=1)
    dias_adicionais_interestadual: int = Field(..., strict=True, ge=0)
    peso_maximo_g: int = Field(..., strict=True, ge=1)

    @field_validator("id")
    @classmethod
    def _validar_id(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Z0-9_]{2,20}", v):
            raise ValueError("id inválido")
        return v

    @field_validator("ufs_atendidas")
    @classmethod
    def _validar_ufs(cls, v: List[str]) -> List[str]:
        if len(set(v)) != len(v):
            raise ValueError("UFs repetidas")
        return v


class AtivacaoEntrada(BaseModel):
    ativa: bool = Field(..., strict=True)


_trava = threading.RLock()
_transportadoras: dict = {}


def _copia(t: dict) -> dict:
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.post("/admin/reset", status_code=204)
def reset():
    with _trava:
        _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras", status_code=201)
def criar_transportadora(entrada: TransportadoraEntrada):
    with _trava:
        if entrada.id in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe uma transportadora com esse id.")
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
        return JSONResponse(status_code=201, content=_copia(registro))


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: Optional[bool] = Query(None)):
    with _trava:
        itens = [
            _copia(t)
            for _, t in sorted(_transportadoras.items(), key=lambda par: par[0])
            if not somente_ativas or t["ativa"]
        ]
    return itens


@app.get("/transportadoras/{transportadora_id}")
def obter_transportadora(transportadora_id: str):
    with _trava:
        t = _transportadoras.get(transportadora_id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return _copia(t)


@app.patch("/transportadoras/{transportadora_id}")
def alterar_transportadora(transportadora_id: str, entrada: AtivacaoEntrada):
    with _trava:
        t = _transportadoras.get(transportadora_id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = entrada.ativa
        return _copia(t)
