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
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
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
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


RE_DATA_HORA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FORMATO_DATA_HORA = "%Y-%m-%dT%H:%M:%S"

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_lock = threading.RLock()
_envios = {}
_contador = {"envio": 0}


def _validar_data_hora(v: str) -> str:
    if not RE_DATA_HORA.match(v):
        raise ValueError("data e hora mal formada")
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
            raise ValueError("email inválido")
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


def _indisponivel(nome: str) -> ErroAPI:
    return ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de %s indisponível." % nome)


def _buscar_cotacao(cotacao_id: str):
    base = os.environ.get("URL_COTACAO", "")
    try:
        r = httpx.get(base + "/cotacoes/" + quote(cotacao_id, safe=""), timeout=10.0)
    except Exception:
        raise _indisponivel("cotação")
    if r.status_code >= 500:
        raise _indisponivel("cotação")
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise _indisponivel("cotação")
    try:
        dados = r.json()
    except Exception:
        raise _indisponivel("cotação")
    if not isinstance(dados, dict):
        raise _indisponivel("cotação")
    return dados


def _publicar(evento: dict):
    base = os.environ.get("URL_BARRAMENTO", "")
    try:
        r = httpx.post(base + "/publicar", json=evento, timeout=10.0)
    except Exception:
        raise _indisponivel("barramento")
    if r.status_code >= 500:
        raise _indisponivel("barramento")


def _evento(tipo: str, ocorrido_em: str, dados: dict) -> dict:
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def _evento_status(envio: dict, item: dict) -> dict:
    return _evento("envio.status_atualizado", item["data_hora"], {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    })


def _dt(v: str) -> datetime:
    return datetime.strptime(v, FORMATO_DATA_HORA)


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _contador["envio"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(corpo: NovoEnvio):
    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == corpo.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

        cotacao = _buscar_cotacao(corpo.cotacao_id)
        if cotacao is None:
            raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")

        try:
            valida_ate = date.fromisoformat(cotacao["valida_ate"])
        except Exception:
            raise _indisponivel("cotação")
        if _dt(corpo.data_hora).date() > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação expirou.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == corpo.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _contador["envio"] + 1
        envio = {
            "id": "ENV-%06d" % numero,
            "codigo_rastreio": "LG%09dBR" % numero,
            "pedido_id": corpo.pedido_id,
            "cotacao_id": corpo.cotacao_id,
            "transportadora_id": corpo.transportadora_id,
            "preco_centavos": opcao.get("preco_centavos"),
            "data_prevista_entrega": opcao.get("data_prevista_entrega"),
            "destinatario": {"nome": corpo.destinatario.nome, "email": corpo.destinatario.email},
            "status": "CRIADO",
            "tentativas_falhas": 0,
            "historico": [{"status": "CRIADO", "data_hora": corpo.data_hora, "local": None}],
        }

        _publicar(_evento("envio.criado", corpo.data_hora, {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "transportadora_id": envio["transportadora_id"],
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": envio["destinatario"]["email"],
            "data_hora": corpo.data_hora,
        }))

        _contador["envio"] = numero
        _envios[envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str):
    with _lock:
        itens = [copy.deepcopy(e) for e in _envios.values() if e["pedido_id"] == pedido_id]
    itens.sort(key=lambda e: e["id"])
    return itens


@app.get("/envios/{id}")
def obter_envio(id: str):
    with _lock:
        e = _envios.get(id)
        if e is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        return copy.deepcopy(e)


@app.post("/envios/{id}/atualizacoes")
def atualizar_envio(id: str, corpo: Atualizacao):
    with _lock:
        atual = _envios.get(id)
        if atual is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if corpo.status not in TRANSICOES.get(atual["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        if _dt(corpo.data_hora) < _dt(atual["historico"][-1]["data_hora"]):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        novo = copy.deepcopy(atual)
        novos_itens = []
        item = {"status": corpo.status, "data_hora": corpo.data_hora, "local": corpo.local}
        novo["historico"].append(item)
        novo["status"] = corpo.status
        novos_itens.append(item)
        if corpo.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                devolvido = {"status": "DEVOLVIDO", "data_hora": corpo.data_hora, "local": corpo.local}
                novo["historico"].append(devolvido)
                novo["status"] = "DEVOLVIDO"
                novos_itens.append(devolvido)

        for it in novos_itens:
            _publicar(_evento_status(novo, it))

        _envios[id] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{id}/cancelar")
def cancelar_envio(id: str, corpo: Cancelamento):
    with _lock:
        atual = _envios.get(id)
        if atual is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if atual["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no status CRIADO.")
        if _dt(corpo.data_hora) < _dt(atual["historico"][-1]["data_hora"]):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        novo = copy.deepcopy(atual)
        item = {"status": "CANCELADO", "data_hora": corpo.data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"

        _publicar(_evento_status(novo, item))

        _envios[id] = novo
        return copy.deepcopy(novo)
