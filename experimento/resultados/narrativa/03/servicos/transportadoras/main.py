# arquivo: servicos/transportadoras/main.py
import threading
from typing import Annotated, List, Literal

from fastapi import FastAPI, Query
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
async def _tratar_erro_api(request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados invalidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


UF = Literal["SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"]

TextoEstrito = Annotated[str, Field(strict=True)]
InteiroEstrito = Annotated[int, Field(strict=True)]


class NovaTransportadora(BaseModel):
    id: Annotated[str, Field(strict=True, pattern=r"^[A-Z0-9_]{2,20}$")]
    nome: Annotated[str, Field(strict=True, min_length=1)]
    ufs_atendidas: Annotated[List[UF], Field(min_length=1)]
    tarifa_base_centavos: Annotated[int, Field(strict=True, ge=0)]
    tarifa_kg_centavos: Annotated[int, Field(strict=True, ge=0)]
    adicional_interestadual_pct: Annotated[int, Field(strict=True, ge=0, le=100)]
    prazo_dias_uteis: Annotated[int, Field(strict=True, ge=1)]
    dias_adicionais_interestadual: Annotated[int, Field(strict=True, ge=0)]
    peso_maximo_g: Annotated[int, Field(strict=True, ge=1)]

    @field_validator("nome")
    @classmethod
    def _nome_nao_vazio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("nome vazio")
        return v

    @field_validator("ufs_atendidas")
    @classmethod
    def _sem_repeticao(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("UF repetida")
        return v


class AlteracaoAtiva(BaseModel):
    ativa: Annotated[bool, Field(strict=True)]


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
def criar_transportadora(corpo: NovaTransportadora):
    with _trava:
        if corpo.id in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Ja existe transportadora com esse id.")
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
def listar_transportadoras(somente_ativas: bool = Query(False)):
    with _trava:
        itens = [_copia(t) for t in _transportadoras.values() if (t["ativa"] or not somente_ativas)]
    itens.sort(key=lambda t: t["id"])
    return itens


@app.get("/transportadoras/{id}")
def obter_transportadora(id: str):
    with _trava:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
        return _copia(t)


@app.patch("/transportadoras/{id}")
def alterar_transportadora(id: str, corpo: AlteracaoAtiva):
    with _trava:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
        t["ativa"] = corpo.ativa
        return _copia(t)
