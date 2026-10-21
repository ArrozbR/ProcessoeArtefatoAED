# arquivo: servicos/transportadoras/main.py
import re
import threading
from typing import Annotated, List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AfterValidator, BaseModel, Field, StrictBool, StrictInt, StrictStr
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
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
_RE_ID = re.compile(r"[A-Z0-9_]{2,20}")


def _validar_id(v: str) -> str:
    if not _RE_ID.fullmatch(v):
        raise ValueError("id inválido")
    return v


def _validar_nome(v: str) -> str:
    if not v.strip():
        raise ValueError("nome vazio")
    return v


def _validar_ufs(v: List[str]) -> List[str]:
    if len(v) == 0:
        raise ValueError("lista vazia")
    if len(set(v)) != len(v):
        raise ValueError("UF repetida")
    for uf in v:
        if uf not in UFS_VALIDAS:
            raise ValueError("UF inválida")
    return v


class TransportadoraEntrada(BaseModel):
    id: Annotated[StrictStr, AfterValidator(_validar_id)]
    nome: Annotated[StrictStr, AfterValidator(_validar_nome)]
    ufs_atendidas: Annotated[List[StrictStr], AfterValidator(_validar_ufs)]
    tarifa_base_centavos: Annotated[StrictInt, Field(ge=0)]
    tarifa_kg_centavos: Annotated[StrictInt, Field(ge=0)]
    adicional_interestadual_pct: Annotated[StrictInt, Field(ge=0, le=100)]
    prazo_dias_uteis: Annotated[StrictInt, Field(ge=1)]
    dias_adicionais_interestadual: Annotated[StrictInt, Field(ge=0)]
    peso_maximo_g: Annotated[StrictInt, Field(ge=1)]


class AtualizacaoAtiva(BaseModel):
    ativa: StrictBool


_lock = threading.Lock()
_transportadoras = {}


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras", status_code=201)
def criar_transportadora(entrada: TransportadoraEntrada):
    with _lock:
        if entrada.id in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com esse id.")
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
        return JSONResponse(status_code=201, content=_copiar(registro))


def _copiar(t: dict) -> dict:
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: Optional[bool] = None):
    with _lock:
        itens = [_copiar(t) for _, t in sorted(_transportadoras.items(), key=lambda kv: kv[0])]
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return itens


@app.get("/transportadoras/{id}")
def obter_transportadora(id: str):
    with _lock:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return _copiar(t)


@app.patch("/transportadoras/{id}")
def atualizar_transportadora(id: str, entrada: AtualizacaoAtiva):
    with _lock:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = entrada.ativa
        return _copiar(t)
