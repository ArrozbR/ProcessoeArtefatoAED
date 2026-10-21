# arquivo: servicos/transportadoras/main.py
import re
import threading
from typing import Annotated, List

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(title="Transportadoras")

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}

_lock = threading.Lock()
_transportadoras = {}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def _descrever_erros(exc):
    partes = []
    try:
        for e in exc.errors():
            loc = ".".join(str(p) for p in e.get("loc", ()))
            partes.append(f"{loc}: {e.get('msg', '')}")
    except Exception:
        pass
    return "Dados inválidos" + (": " + "; ".join(partes) if partes else ".")


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", _descrever_erros(exc))


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    codigo = "RECURSO_NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return erro(exc.status_code, codigo, str(exc.detail))


class TransportadoraEntrada(BaseModel):
    id: StrictStr
    nome: StrictStr
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
        if not re.fullmatch(r"[A-Z0-9_]{2,20}", v):
            raise ValueError("id deve ter de 2 a 20 caracteres: letras maiúsculas, dígitos ou _")
        return v

    @field_validator("nome")
    @classmethod
    def _validar_nome(cls, v):
        if not v.strip():
            raise ValueError("nome não pode ser vazio")
        return v

    @field_validator("ufs_atendidas")
    @classmethod
    def _validar_ufs(cls, v):
        if not v:
            raise ValueError("ufs_atendidas não pode ser vazia")
        if len(set(v)) != len(v):
            raise ValueError("ufs_atendidas não pode ter repetição")
        for uf in v:
            if uf not in UFS_VALIDAS:
                raise ValueError(f"UF não atendida: {uf}")
        return v


class AtualizacaoAtiva(BaseModel):
    ativa: StrictBool


@app.post("/admin/reset")
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras")
def criar_transportadora(entrada: TransportadoraEntrada):
    with _lock:
        if entrada.id in _transportadoras:
            return erro(409, "TRANSPORTADORA_DUPLICADA", f"Transportadora {entrada.id} já cadastrada.")
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


def _copiar(t):
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: bool = False):
    with _lock:
        itens = [_copiar(t) for _, t in sorted(_transportadoras.items())]
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{transportadora_id}")
def obter_transportadora(transportadora_id: str):
    with _lock:
        t = _transportadoras.get(transportadora_id)
        if t is None:
            return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", f"Transportadora {transportadora_id} não encontrada.")
        return JSONResponse(status_code=200, content=_copiar(t))


@app.patch("/transportadoras/{transportadora_id}")
def atualizar_transportadora(transportadora_id: str, entrada: AtualizacaoAtiva):
    with _lock:
        t = _transportadoras.get(transportadora_id)
        if t is None:
            return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", f"Transportadora {transportadora_id} não encontrada.")
        t["ativa"] = entrada.ativa
        return JSONResponse(status_code=200, content=_copiar(t))
