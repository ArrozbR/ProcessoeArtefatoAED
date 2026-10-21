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
from pydantic import BaseModel, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroApi(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
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


RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RE_DATA_HORA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
FORMATO_DATA_HORA = "%Y-%m-%dT%H:%M:%S"

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_lock = threading.Lock()
_envios: dict = {}
_contador = {"valor": 0}


def _validar_data_hora(v: str) -> str:
    if not RE_DATA_HORA.match(v):
        raise ValueError("data e hora inválidas")
    datetime.strptime(v, FORMATO_DATA_HORA)
    return v


def _nao_vazio(v: str) -> str:
    if not v.strip():
        raise ValueError("texto vazio")
    return v


class Destinatario(BaseModel):
    nome: StrictStr
    email: StrictStr

    @field_validator("nome")
    @classmethod
    def _v_nome(cls, v):
        return _nao_vazio(v)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v):
        if not RE_EMAIL.match(v):
            raise ValueError("e-mail inválido")
        return v


class NovoEnvio(BaseModel):
    pedido_id: StrictStr
    cotacao_id: StrictStr
    transportadora_id: StrictStr
    destinatario: Destinatario
    data_hora: StrictStr

    @field_validator("pedido_id", "cotacao_id", "transportadora_id")
    @classmethod
    def _v_textos(cls, v):
        return _nao_vazio(v)

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


class Atualizacao(BaseModel):
    status: Literal["COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"]
    data_hora: StrictStr
    local: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)

    @field_validator("local")
    @classmethod
    def _v_local(cls, v):
        return _nao_vazio(v)


class Cancelamento(BaseModel):
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


def _indisponivel(nome: str):
    return ErroApi(503, "SERVICO_INDISPONIVEL", f"Serviço de {nome} indisponível.")


def _buscar_cotacao(cotacao_id: str):
    base = os.environ.get("URL_COTACAO", "")
    try:
        resp = httpx.get(f"{base}/cotacoes/{quote(cotacao_id, safe='')}", timeout=10.0)
    except httpx.HTTPError:
        raise _indisponivel("cotação")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise _indisponivel("cotação")
    try:
        dados = resp.json()
    except ValueError:
        raise _indisponivel("cotação")
    if not isinstance(dados, dict):
        raise _indisponivel("cotação")
    return dados


def _publicar(eventos: list):
    base = os.environ.get("URL_BARRAMENTO", "")
    for evento in eventos:
        try:
            resp = httpx.post(f"{base}/publicar", json=evento, timeout=10.0)
        except httpx.HTTPError:
            raise _indisponivel("barramento")
        if resp.status_code >= 500 or resp.status_code >= 400:
            raise _indisponivel("barramento")


def _evento_status(envio: dict, item: dict) -> dict:
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


def _ultimo_data_hora(envio: dict) -> datetime:
    return datetime.strptime(envio["historico"][-1]["data_hora"], FORMATO_DATA_HORA)


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(dados: NovoEnvio):
    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == dados.pedido_id and e["status"] != "CANCELADO":
                raise ErroApi(409, "ENVIO_DUPLICADO", "O pedido já possui envio ativo.")

        cotacao = _buscar_cotacao(dados.cotacao_id)
        if cotacao is None:
            raise ErroApi(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")

        data_operacao = datetime.strptime(dados.data_hora, FORMATO_DATA_HORA).date()
        try:
            valida_ate = date.fromisoformat(cotacao["valida_ate"])
        except (KeyError, TypeError, ValueError):
            raise _indisponivel("cotação")
        if data_operacao > valida_ate:
            raise ErroApi(422, "COTACAO_EXPIRADA", "A cotação expirou.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == dados.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroApi(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _contador["valor"] + 1
        envio = {
            "id": f"ENV-{numero:06d}",
            "codigo_rastreio": f"LG{numero:09d}BR",
            "pedido_id": dados.pedido_id,
            "cotacao_id": dados.cotacao_id,
            "transportadora_id": dados.transportadora_id,
            "preco_centavos": opcao.get("preco_centavos"),
            "data_prevista_entrega": opcao.get("data_prevista_entrega"),
            "destinatario": {"nome": dados.destinatario.nome, "email": dados.destinatario.email},
            "status": "CRIADO",
            "tentativas_falhas": 0,
            "historico": [{"status": "CRIADO", "data_hora": dados.data_hora, "local": None}],
        }
        evento = {
            "id": str(uuid.uuid4()),
            "tipo": "envio.criado",
            "ocorrido_em": dados.data_hora,
            "dados": {
                "envio_id": envio["id"],
                "codigo_rastreio": envio["codigo_rastreio"],
                "pedido_id": envio["pedido_id"],
                "transportadora_id": envio["transportadora_id"],
                "data_prevista_entrega": envio["data_prevista_entrega"],
                "destinatario_email": envio["destinatario"]["email"],
                "data_hora": dados.data_hora,
            },
        }
        _publicar([evento])

        _contador["valor"] = numero
        _envios[envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _lock:
        itens = [copy.deepcopy(e) for e in _envios.values() if e["pedido_id"] == pedido_id]
    itens.sort(key=lambda e: e["id"])
    return itens


@app.get("/envios/{envio_id}")
def obter_envio(envio_id: str):
    with _lock:
        e = _envios.get(envio_id)
        if e is None:
            raise ErroApi(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        return copy.deepcopy(e)


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar_envio(envio_id: str, dados: Atualizacao):
    with _lock:
        atual = _envios.get(envio_id)
        if atual is None:
            raise ErroApi(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if dados.status not in TRANSICOES.get(atual["status"], set()):
            raise ErroApi(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        nova = datetime.strptime(dados.data_hora, FORMATO_DATA_HORA)
        if nova < _ultimo_data_hora(atual):
            raise ErroApi(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        envio = copy.deepcopy(atual)
        novos_itens = []
        item = {"status": dados.status, "data_hora": dados.data_hora, "local": dados.local}
        envio["historico"].append(item)
        envio["status"] = dados.status
        novos_itens.append(item)
        if dados.status == "FALHA_ENTREGA":
            envio["tentativas_falhas"] += 1
            if envio["tentativas_falhas"] >= 3:
                devolvido = {"status": "DEVOLVIDO", "data_hora": dados.data_hora, "local": dados.local}
                envio["historico"].append(devolvido)
                envio["status"] = "DEVOLVIDO"
                novos_itens.append(devolvido)

        _publicar([_evento_status(envio, i) for i in novos_itens])
        _envios[envio_id] = envio
        return copy.deepcopy(envio)


@app.post("/envios/{envio_id}/cancelar")
def cancelar_envio(envio_id: str, dados: Cancelamento):
    with _lock:
        atual = _envios.get(envio_id)
        if atual is None:
            raise ErroApi(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if atual["status"] != "CRIADO":
            raise ErroApi(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio criado.")
        nova = datetime.strptime(dados.data_hora, FORMATO_DATA_HORA)
        if nova < _ultimo_data_hora(atual):
            raise ErroApi(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        envio = copy.deepcopy(atual)
        item = {"status": "CANCELADO", "data_hora": dados.data_hora, "local": None}
        envio["historico"].append(item)
        envio["status"] = "CANCELADO"

        _publicar([_evento_status(envio, item)])
        _envios[envio_id] = envio
        return copy.deepcopy(envio)
