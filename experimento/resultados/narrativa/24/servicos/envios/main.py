# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA_HORA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")
RE_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _erro_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


def _validar_data_hora(v: str) -> str:
    if not RE_DATA_HORA.fullmatch(v):
        raise ValueError("data_hora inválida")
    datetime.fromisoformat(v)
    return v


class Destinatario(BaseModel):
    nome: StrictStr = Field(min_length=1)
    email: StrictStr

    @field_validator("email")
    @classmethod
    def _email_valido(cls, v):
        if not RE_EMAIL.fullmatch(v):
            raise ValueError("email inválido")
        return v


class EnvioEntrada(BaseModel):
    pedido_id: StrictStr = Field(min_length=1)
    cotacao_id: StrictStr = Field(min_length=1)
    transportadora_id: StrictStr = Field(min_length=1)
    destinatario: Destinatario
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _dh(cls, v):
        return _validar_data_hora(v)


class AtualizacaoEntrada(BaseModel):
    status: Literal["COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"]
    data_hora: StrictStr
    local: StrictStr = Field(min_length=1)

    @field_validator("data_hora")
    @classmethod
    def _dh(cls, v):
        return _validar_data_hora(v)


class CancelamentoEntrada(BaseModel):
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _dh(cls, v):
        return _validar_data_hora(v)


_lock = threading.Lock()
_envios = {}
_contador = [0]


def _indisponivel(servico: str):
    return ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de %s indisponível." % servico)


def _buscar_cotacao(cotacao_id: str):
    base = os.environ.get("URL_COTACAO", "")
    try:
        with httpx.Client(timeout=10.0) as cliente:
            r = cliente.get(base + "/cotacoes/" + quote(cotacao_id, safe=""))
    except Exception:
        raise _indisponivel("cotação")
    if r.status_code >= 500:
        raise _indisponivel("cotação")
    if r.status_code != 200:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    try:
        dados = r.json()
    except Exception:
        raise _indisponivel("cotação")
    if not isinstance(dados, dict):
        raise _indisponivel("cotação")
    return dados


def _publicar(eventos):
    base = os.environ.get("URL_BARRAMENTO", "")
    try:
        with httpx.Client(timeout=10.0) as cliente:
            for ev in eventos:
                r = cliente.post(base + "/publicar", json=ev)
                if r.status_code >= 400:
                    raise _indisponivel("barramento")
    except ErroAPI:
        raise
    except Exception:
        raise _indisponivel("barramento")


def _evento_status(envio, item):
    return {
        "id": str(uuid.uuid4()),
        "tipo": "envio.status_atualizado",
        "ocorrido_em": item["data_hora"],
        "dados": {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "status": item["status"],
            "data_hora": item["data_hora"],
            "local": item["local"],
            "destinatario_email": envio["destinatario"]["email"],
        },
    }


def _obter_envio(id: str):
    e = _envios.get(id)
    if e is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return e


def _checar_data_hora(envio, data_hora: str):
    ultimo = envio["historico"][-1]["data_hora"]
    if datetime.fromisoformat(data_hora) < datetime.fromisoformat(ultimo):
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao último registro.")


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _contador[0] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar(entrada: EnvioEntrada):
    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == entrada.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui envio ativo.")

        cotacao = _buscar_cotacao(entrada.cotacao_id)
        try:
            valida_ate = date.fromisoformat(cotacao["valida_ate"])
            opcoes = cotacao.get("opcoes") or []
        except Exception:
            raise _indisponivel("cotação")
        if datetime.fromisoformat(entrada.data_hora).date() > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "Cotação expirada.")
        opcao = None
        for o in opcoes:
            if isinstance(o, dict) and o.get("transportadora_id") == entrada.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _contador[0] + 1
        eid = "ENV-%06d" % numero
        codigo = "LG%09dBR" % numero
        envio = {
            "id": eid,
            "codigo_rastreio": codigo,
            "pedido_id": entrada.pedido_id,
            "cotacao_id": entrada.cotacao_id,
            "transportadora_id": entrada.transportadora_id,
            "preco_centavos": opcao.get("preco_centavos"),
            "data_prevista_entrega": opcao.get("data_prevista_entrega"),
            "destinatario": {"nome": entrada.destinatario.nome, "email": entrada.destinatario.email},
            "status": "CRIADO",
            "tentativas_falhas": 0,
            "historico": [{"status": "CRIADO", "data_hora": entrada.data_hora, "local": None}],
        }
        evento = {
            "id": str(uuid.uuid4()),
            "tipo": "envio.criado",
            "ocorrido_em": entrada.data_hora,
            "dados": {
                "envio_id": eid,
                "codigo_rastreio": codigo,
                "pedido_id": entrada.pedido_id,
                "transportadora_id": entrada.transportadora_id,
                "data_prevista_entrega": envio["data_prevista_entrega"],
                "destinatario_email": entrada.destinatario.email,
                "data_hora": entrada.data_hora,
            },
        }
        _publicar([evento])
        _contador[0] = numero
        _envios[eid] = envio
        return copy.deepcopy(envio)


@app.get("/envios")
def listar(pedido_id: str = Query(...)):
    with _lock:
        itens = [e for e in _envios.values() if e["pedido_id"] == pedido_id]
        itens.sort(key=lambda e: e["id"])
        return copy.deepcopy(itens)


@app.get("/envios/{id}")
def obter(id: str):
    with _lock:
        return copy.deepcopy(_obter_envio(id))


@app.post("/envios/{id}/atualizacoes")
def atualizar(id: str, entrada: AtualizacaoEntrada):
    with _lock:
        atual = _obter_envio(id)
        if entrada.status not in TRANSICOES.get(atual["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        _checar_data_hora(atual, entrada.data_hora)

        novo = copy.deepcopy(atual)
        itens = [{"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}]
        novo["status"] = entrada.status
        if entrada.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                itens.append({"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local})
                novo["status"] = "DEVOLVIDO"
        novo["historico"].extend(itens)

        _publicar([_evento_status(novo, item) for item in itens])
        _envios[id] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{id}/cancelar")
def cancelar(id: str, entrada: CancelamentoEntrada):
    with _lock:
        atual = _obter_envio(id)
        if atual["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio criado.")
        _checar_data_hora(atual, entrada.data_hora)

        novo = copy.deepcopy(atual)
        item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
        novo["status"] = "CANCELADO"
        novo["historico"].append(item)

        _publicar([_evento_status(novo, item)])
        _envios[id] = novo
        return copy.deepcopy(novo)
