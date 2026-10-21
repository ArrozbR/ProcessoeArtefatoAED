# arquivo: servicos/transportadoras/main.py
import re
import threading
from typing import List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictBool, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"[A-Z0-9_]{2,20}")

_lock = threading.Lock()
_transportadoras = {}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


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


class TransportadoraEntrada(BaseModel):
    id: StrictStr
    nome: StrictStr
    ufs_atendidas: List[StrictStr]
    tarifa_base_centavos: StrictInt
    tarifa_kg_centavos: StrictInt
    adicional_interestadual_pct: StrictInt
    prazo_dias_uteis: StrictInt
    dias_adicionais_interestadual: StrictInt
    peso_maximo_g: StrictInt

    @field_validator("id")
    @classmethod
    def _v_id(cls, v):
        if not RE_ID.fullmatch(v):
            raise ValueError("id inválido")
        return v

    @field_validator("nome")
    @classmethod
    def _v_nome(cls, v):
        if not v.strip():
            raise ValueError("nome vazio")
        return v

    @field_validator("ufs_atendidas")
    @classmethod
    def _v_ufs(cls, v):
        if not v:
            raise ValueError("lista vazia")
        if len(set(v)) != len(v):
            raise ValueError("UF repetida")
        for uf in v:
            if uf not in UFS_VALIDAS:
                raise ValueError("UF inválida")
        return v

    @field_validator("tarifa_base_centavos", "tarifa_kg_centavos", "dias_adicionais_interestadual")
    @classmethod
    def _v_nao_negativo(cls, v):
        if v < 0:
            raise ValueError("negativo")
        return v

    @field_validator("adicional_interestadual_pct")
    @classmethod
    def _v_pct(cls, v):
        if v < 0 or v > 100:
            raise ValueError("fora da faixa")
        return v

    @field_validator("prazo_dias_uteis", "peso_maximo_g")
    @classmethod
    def _v_positivo(cls, v):
        if v < 1:
            raise ValueError("menor que 1")
        return v


class AtualizacaoAtiva(BaseModel):
    ativa: StrictBool


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
        itens = sorted(_transportadoras.values(), key=lambda t: t["id"])
        if somente_ativas:
            itens = [t for t in itens if t["ativa"]]
        return [dict(t, ufs_atendidas=list(t["ufs_atendidas"])) for t in itens]


@app.get("/transportadoras/{id}")
def obter_transportadora(id: str):
    with _lock:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return dict(t, ufs_atendidas=list(t["ufs_atendidas"]))


@app.patch("/transportadoras/{id}")
def atualizar_transportadora(id: str, entrada: AtualizacaoAtiva):
    with _lock:
        t = _transportadoras.get(id)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = entrada.ativa
        return dict(t, ufs_atendidas=list(t["ufs_atendidas"]))
