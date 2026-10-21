# arquivo: servicos/transportadoras/main.py
import re
from typing import List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictBool, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_RE = re.compile(r"^[A-Z0-9_]{2,20}$")

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
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


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
        if not ID_RE.match(v):
            raise ValueError("id inválido")
        return v

    @field_validator("nome")
    @classmethod
    def _v_nome(cls, v):
        if not v or not v.strip():
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
            raise ValueError("valor negativo")
        return v

    @field_validator("adicional_interestadual_pct")
    @classmethod
    def _v_pct(cls, v):
        if v < 0 or v > 100:
            raise ValueError("percentual fora da faixa")
        return v

    @field_validator("prazo_dias_uteis", "peso_maximo_g")
    @classmethod
    def _v_positivo(cls, v):
        if v < 1:
            raise ValueError("valor deve ser pelo menos 1")
        return v


class AtivaEntrada(BaseModel):
    ativa: StrictBool


@app.post("/admin/reset", status_code=204)
def reset():
    _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras", status_code=201)
def criar_transportadora(entrada: TransportadoraEntrada):
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
def listar_transportadoras(somente_ativas: bool = False):
    itens = sorted(_transportadoras.values(), key=lambda t: t["id"])
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return [dict(t, ufs_atendidas=list(t["ufs_atendidas"])) for t in itens]


@app.get("/transportadoras/{transportadora_id}")
def obter_transportadora(transportadora_id: str):
    t = _transportadoras.get(transportadora_id)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return dict(t, ufs_atendidas=list(t["ufs_atendidas"]))


@app.patch("/transportadoras/{transportadora_id}")
def alterar_transportadora(transportadora_id: str, entrada: AtivaEntrada):
    t = _transportadoras.get(transportadora_id)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = entrada.ativa
    return dict(t, ufs_atendidas=list(t["ufs_atendidas"]))
