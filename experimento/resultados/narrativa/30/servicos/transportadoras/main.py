# arquivo: servicos/transportadoras/main.py
import re
import threading
from typing import Annotated, List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}

_lock = threading.Lock()
_transportadoras = {}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


StrictIntNaoNeg = Annotated[int, Field(strict=True, ge=0)]


class TransportadoraEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Annotated[str, Field(strict=True)]
    nome: Annotated[str, Field(strict=True)]
    ufs_atendidas: List[Annotated[str, Field(strict=True)]]
    tarifa_base_centavos: StrictIntNaoNeg
    tarifa_kg_centavos: StrictIntNaoNeg
    adicional_interestadual_pct: Annotated[int, Field(strict=True, ge=0, le=100)]
    prazo_dias_uteis: Annotated[int, Field(strict=True, ge=1)]
    dias_adicionais_interestadual: StrictIntNaoNeg
    peso_maximo_g: Annotated[int, Field(strict=True, ge=1)]

    @field_validator("id")
    @classmethod
    def _validar_id(cls, v):
        if not re.fullmatch(r"[A-Z0-9_]{2,20}", v):
            raise ValueError("id invalido")
        return v

    @field_validator("nome")
    @classmethod
    def _validar_nome(cls, v):
        if not v or not v.strip():
            raise ValueError("nome vazio")
        return v

    @field_validator("ufs_atendidas")
    @classmethod
    def _validar_ufs(cls, v):
        if len(v) == 0:
            raise ValueError("lista vazia")
        if len(set(v)) != len(v):
            raise ValueError("ufs repetidas")
        for uf in v:
            if uf not in UFS_VALIDAS:
                raise ValueError("uf invalida")
        return v


class AtivaEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ativa: Annotated[bool, Field(strict=True)]


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
        dados = {
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
        _transportadoras[entrada.id] = dados
        return JSONResponse(status_code=201, content=_copiar(dados))


def _copiar(t):
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: Optional[bool] = False):
    with _lock:
        itens = [
            _copiar(t)
            for _, t in sorted(_transportadoras.items(), key=lambda kv: kv[0])
            if not somente_ativas or t["ativa"]
        ]
    return itens


@app.get("/transportadoras/{id}")
def obter_transportadora(id: str):
    with _lock:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
        return _copiar(t)


@app.patch("/transportadoras/{id}")
def alterar_transportadora(id: str, entrada: AtivaEntrada):
    with _lock:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
        t["ativa"] = entrada.ativa
        return _copiar(t)
