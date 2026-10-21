# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query, Request
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


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


app = FastAPI(title="Envios")


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


_RE_DATA_HORA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validar_data_hora(v: str) -> str:
    if not _RE_DATA_HORA.match(v):
        raise ValueError("data_hora deve estar no formato AAAA-MM-DDTHH:MM:SS")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def _dt(v: str) -> datetime:
    return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")


TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_trava = threading.RLock()
_envios: dict = {}
_contador = {"envio": 0}

_cliente_http = None
_trava_cliente = threading.Lock()


def _cliente() -> httpx.Client:
    global _cliente_http
    with _trava_cliente:
        if _cliente_http is None:
            _cliente_http = httpx.Client(timeout=10.0)
        return _cliente_http


class Destinatario(BaseModel):
    nome: Annotated[str, Field(strict=True, min_length=1)]
    email: Annotated[str, Field(strict=True)]

    @field_validator("nome")
    @classmethod
    def _nome(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("nome vazio")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not _RE_EMAIL.match(v):
            raise ValueError("email invalido")
        return v


class EnvioEntrada(BaseModel):
    pedido_id: Annotated[str, Field(strict=True, min_length=1)]
    cotacao_id: Annotated[str, Field(strict=True, min_length=1)]
    transportadora_id: Annotated[str, Field(strict=True, min_length=1)]
    destinatario: Destinatario
    data_hora: Annotated[str, Field(strict=True)]

    @field_validator("data_hora")
    @classmethod
    def _dh(cls, v: str) -> str:
        return _validar_data_hora(v)


class AtualizacaoEntrada(BaseModel):
    status: Literal["COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"]
    data_hora: Annotated[str, Field(strict=True)]
    local: Annotated[str, Field(strict=True, min_length=1)]

    @field_validator("data_hora")
    @classmethod
    def _dh(cls, v: str) -> str:
        return _validar_data_hora(v)

    @field_validator("local")
    @classmethod
    def _local(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("local vazio")
        return v


class CancelamentoEntrada(BaseModel):
    data_hora: Annotated[str, Field(strict=True)]

    @field_validator("data_hora")
    @classmethod
    def _dh(cls, v: str) -> str:
        return _validar_data_hora(v)


def _indisponivel(nome: str) -> ErroAPI:
    return ErroAPI(503, "SERVICO_INDISPONIVEL", f"Servico de {nome} indisponivel.")


def _buscar_cotacao(cotacao_id: str):
    base = os.environ.get("URL_COTACAO", "")
    try:
        resp = _cliente().get(f"{base}/cotacoes/{quote(cotacao_id, safe='')}")
    except Exception:
        raise _indisponivel("cotacao")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise _indisponivel("cotacao")
    try:
        dados = resp.json()
    except Exception:
        raise _indisponivel("cotacao")
    if not isinstance(dados, dict):
        raise _indisponivel("cotacao")
    return dados


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    base = os.environ.get("URL_BARRAMENTO", "")
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        resp = _cliente().post(f"{base}/publicar", json=evento)
    except Exception:
        raise _indisponivel("barramento")
    if resp.status_code >= 300:
        raise _indisponivel("barramento")


def _evento_status(envio: dict, item: dict) -> dict:
    return {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    }


@app.post("/admin/reset", status_code=204)
def reset():
    with _trava:
        _envios.clear()
        _contador["envio"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(entrada: EnvioEntrada):
    with _trava:
        for e in _envios.values():
            if e["pedido_id"] == entrada.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", f"Pedido {entrada.pedido_id} ja possui envio.")

        cotacao = _buscar_cotacao(entrada.cotacao_id)
        if cotacao is None:
            raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", f"Cotacao {entrada.cotacao_id} nao encontrada.")

        try:
            valida_ate = date.fromisoformat(cotacao["valida_ate"])
        except Exception:
            raise _indisponivel("cotacao")
        if _dt(entrada.data_hora).date() > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "Cotacao expirada.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == entrada.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA",
                          f"Transportadora {entrada.transportadora_id} nao oferecida na cotacao.")

        numero = _contador["envio"] + 1
        envio = {
            "id": f"ENV-{numero:06d}",
            "codigo_rastreio": f"LG{numero:09d}BR",
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

        _publicar("envio.criado", entrada.data_hora, {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "transportadora_id": envio["transportadora_id"],
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": envio["destinatario"]["email"],
            "data_hora": entrada.data_hora,
        })

        _contador["envio"] = numero
        _envios[envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _trava:
        lista = [copy.deepcopy(e) for e in _envios.values() if e["pedido_id"] == pedido_id]
    lista.sort(key=lambda e: e["id"])
    return lista


@app.get("/envios/{id}")
def obter_envio(id: str):
    with _trava:
        e = _envios.get(id)
        if e is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", f"Envio {id} nao encontrado.")
        return copy.deepcopy(e)


@app.post("/envios/{id}/atualizacoes")
def atualizar_envio(id: str, entrada: AtualizacaoEntrada):
    with _trava:
        envio = _envios.get(id)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", f"Envio {id} nao encontrado.")
        permitidos = TRANSICOES.get(envio["status"], set())
        if entrada.status not in permitidos:
            raise ErroAPI(409, "TRANSICAO_INVALIDA",
                          f"Transicao de {envio['status']} para {entrada.status} nao permitida.")
        ultimo = envio["historico"][-1]
        if _dt(entrada.data_hora) < _dt(ultimo["data_hora"]):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao ultimo item do historico.")

        novos = [{"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}]
        tentativas = envio["tentativas_falhas"]
        if entrada.status == "FALHA_ENTREGA":
            tentativas += 1
            if tentativas >= 3:
                novos.append({"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local})

        for item in novos:
            _publicar("envio.status_atualizado", entrada.data_hora, _evento_status(envio, item))

        envio["historico"].extend(novos)
        envio["status"] = novos[-1]["status"]
        envio["tentativas_falhas"] = tentativas
        return copy.deepcopy(envio)


@app.post("/envios/{id}/cancelar")
def cancelar_envio(id: str, entrada: CancelamentoEntrada):
    with _trava:
        envio = _envios.get(id)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", f"Envio {id} nao encontrado.")
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", f"Envio com status {envio['status']} nao pode ser cancelado.")
        ultimo = envio["historico"][-1]
        if _dt(entrada.data_hora) < _dt(ultimo["data_hora"]):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao ultimo item do historico.")

        item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
        _publicar("envio.status_atualizado", entrada.data_hora, _evento_status(envio, item))

        envio["historico"].append(item)
        envio["status"] = "CANCELADO"
        return copy.deepcopy(envio)
