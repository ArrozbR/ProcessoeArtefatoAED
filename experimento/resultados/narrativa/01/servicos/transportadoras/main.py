# arquivo: servicos/transportadoras/main.py
import threading
from typing import Annotated, List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AfterValidator, BaseModel, Field, StrictBool, StrictStr
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
        content={"erro": codigos.get(exc.status_code, "ERRO"), "mensagem": str(exc.detail)},
    )


UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}


def _nao_vazio(v: str) -> str:
    if not v.strip():
        raise ValueError("texto vazio")
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


IntNaoNegativo = Annotated[int, Field(strict=True, ge=0)]
IntPositivo = Annotated[int, Field(strict=True, ge=1)]


class TransportadoraEntrada(BaseModel):
    id: Annotated[StrictStr, Field(pattern=r"^[A-Z0-9_]{2,20}$")]
    nome: Annotated[StrictStr, AfterValidator(_nao_vazio)]
    ufs_atendidas: Annotated[List[StrictStr], AfterValidator(_validar_ufs)]
    tarifa_base_centavos: IntNaoNegativo
    tarifa_kg_centavos: IntNaoNegativo
    adicional_interestadual_pct: Annotated[int, Field(strict=True, ge=0, le=100)]
    prazo_dias_uteis: IntPositivo
    dias_adicionais_interestadual: IntNaoNegativo
    peso_maximo_g: IntPositivo


class AtivaEntrada(BaseModel):
    ativa: StrictBool


_lock = threading.Lock()
_transportadoras: dict = {}


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
        return JSONResponse(status_code=201, content=dict(registro, ufs_atendidas=list(registro["ufs_atendidas"])))


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: Optional[bool] = False):
    with _lock:
        itens = [
            dict(t, ufs_atendidas=list(t["ufs_atendidas"]))
            for _, t in sorted(_transportadoras.items(), key=lambda par: par[0])
            if (not somente_ativas) or t["ativa"]
        ]
    return itens


@app.get("/transportadoras/{id}")
def obter_transportadora(id: str):
    with _lock:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return dict(t, ufs_atendidas=list(t["ufs_atendidas"]))


@app.patch("/transportadoras/{id}")
def alterar_transportadora(id: str, entrada: AtivaEntrada):
    with _lock:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = entrada.ativa
        return dict(t, ufs_atendidas=list(t["ufs_atendidas"]))
