# arquivo: servicos/transportadoras/main.py
import re
import threading
from typing import Annotated, List

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_RE = re.compile(r"^[A-Z0-9_]{2,20}$")

_lock = threading.Lock()
_transportadoras = {}


class ErroAPI(Exception):
    def __init__(self, status, codigo, mensagem):
        super().__init__(mensagem)
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
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


class NovaTransportadora(BaseModel):
    id: StrictStr
    nome: Annotated[StrictStr, Field(min_length=1)]
    ufs_atendidas: List[StrictStr]
    tarifa_base_centavos: Annotated[StrictInt, Field(ge=0)]
    tarifa_kg_centavos: Annotated[StrictInt, Field(ge=0)]
    adicional_interestadual_pct: Annotated[StrictInt, Field(ge=0, le=100)]
    prazo_dias_uteis: Annotated[StrictInt, Field(ge=1)]
    dias_adicionais_interestadual: Annotated[StrictInt, Field(ge=0)]
    peso_maximo_g: Annotated[StrictInt, Field(ge=1)]

    @field_validator("id")
    @classmethod
    def _validar_id(cls, v):
        if not ID_RE.match(v):
            raise ValueError("id inválido")
        return v

    @field_validator("ufs_atendidas")
    @classmethod
    def _validar_ufs(cls, v):
        if not v:
            raise ValueError("lista vazia")
        if len(set(v)) != len(v):
            raise ValueError("UF repetida")
        for uf in v:
            if uf not in UFS_VALIDAS:
                raise ValueError("UF inválida")
        return v


class AlteracaoAtiva(BaseModel):
    ativa: StrictBool


def _copia(t):
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras", status_code=201)
def criar_transportadora(corpo: NovaTransportadora):
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
        return JSONResponse(status_code=201, content=_copia(t))


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: bool = False):
    with _lock:
        itens = [_copia(_transportadoras[k]) for k in sorted(_transportadoras)]
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return itens


@app.get("/transportadoras/{id}")
def obter_transportadora(id: str):
    with _lock:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return _copia(t)


@app.patch("/transportadoras/{id}")
def alterar_transportadora(id: str, corpo: AlteracaoAtiva):
    with _lock:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = corpo.ativa
        return _copia(t)
