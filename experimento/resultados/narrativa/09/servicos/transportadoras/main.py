# arquivo: servicos/transportadoras/main.py
import threading
from typing import Annotated, List, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
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


UF = Literal["SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"]


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
    def _sem_repeticao(cls, v: List[str]) -> List[str]:
        if len(set(v)) != len(v):
            raise ValueError("ufs repetidas")
        return v


class AlteracaoAtiva(BaseModel):
    ativa: Annotated[bool, Field(strict=True)]


_trava = threading.RLock()
_transportadoras = {}


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
        registro = {
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
        _transportadoras[corpo.id] = registro
        return JSONResponse(status_code=201, content=dict(registro, ufs_atendidas=list(registro["ufs_atendidas"])))


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: bool = False):
    with _trava:
        itens = [
            dict(t, ufs_atendidas=list(t["ufs_atendidas"]))
            for _, t in sorted(_transportadoras.items())
            if (not somente_ativas) or t["ativa"]
        ]
    return itens


@app.get("/transportadoras/{transportadora_id}")
def obter_transportadora(transportadora_id: str):
    with _trava:
        t = _transportadoras.get(transportadora_id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
        return dict(t, ufs_atendidas=list(t["ufs_atendidas"]))


@app.patch("/transportadoras/{transportadora_id}")
def alterar_transportadora(transportadora_id: str, corpo: AlteracaoAtiva):
    with _trava:
        t = _transportadoras.get(transportadora_id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
        t["ativa"] = corpo.ativa
        return dict(t, ufs_atendidas=list(t["ufs_atendidas"]))
