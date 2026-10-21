# arquivo: servicos/transportadoras/main.py
import threading
from typing import Annotated, List, Literal, Optional

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

UF = Literal["SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"]


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


app = FastAPI(title="Transportadoras")


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    partes = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", ()))
        partes.append(f"{loc}: {e.get('msg', '')}")
    return _erro(422, "DADOS_INVALIDOS", "; ".join(partes) or "Dados invalidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


_trava = threading.RLock()
_transportadoras: dict = {}


class TransportadoraEntrada(BaseModel):
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
            raise ValueError("ufs_atendidas com repeticao")
        return v


class AtivacaoEntrada(BaseModel):
    ativa: Annotated[bool, Field(strict=True)]


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
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", f"Transportadora {entrada.id} ja cadastrada.")
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
        return JSONResponse(status_code=201, content=_copia(t))


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: Optional[bool] = Query(default=None)):
    with _trava:
        lista = [_copia(_transportadoras[k]) for k in sorted(_transportadoras)]
    if somente_ativas:
        lista = [t for t in lista if t["ativa"]]
    return lista


@app.get("/transportadoras/{id}")
def obter_transportadora(id: str):
    with _trava:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", f"Transportadora {id} nao encontrada.")
        return _copia(t)


@app.patch("/transportadoras/{id}")
def alterar_transportadora(id: str, entrada: AtivacaoEntrada):
    with _trava:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", f"Transportadora {id} nao encontrada.")
        t["ativa"] = entrada.ativa
        return _copia(t)
