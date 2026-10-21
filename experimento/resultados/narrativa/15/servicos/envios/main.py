# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr, field_validator
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
        content={"erro": codigos.get(exc.status_code, "ERRO_HTTP"), "mensagem": str(exc.detail)},
    )


RE_DATA_HORA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_lock = threading.RLock()
_envios = {}
_contador = [0]
_cliente = httpx.Client(timeout=10.0)


def _validar_data_hora(v: str) -> str:
    if not RE_DATA_HORA.fullmatch(v):
        raise ValueError("data_hora inválida")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def _parse_dh(v: str) -> datetime:
    return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")


class Destinatario(BaseModel):
    nome: StrictStr
    email: StrictStr

    @field_validator("nome")
    @classmethod
    def _v_nome(cls, v):
        if not v.strip():
            raise ValueError("nome vazio")
        return v

    @field_validator("email")
    @classmethod
    def _v_email(cls, v):
        if not RE_EMAIL.fullmatch(v):
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
    def _v_nao_vazio(cls, v):
        if not v.strip():
            raise ValueError("vazio")
        return v

    @field_validator("data_hora")
    @classmethod
    def _v_dh(cls, v):
        return _validar_data_hora(v)


class Atualizacao(BaseModel):
    status: Literal["COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"]
    data_hora: StrictStr
    local: StrictStr

    @field_validator("local")
    @classmethod
    def _v_local(cls, v):
        if not v.strip():
            raise ValueError("local vazio")
        return v

    @field_validator("data_hora")
    @classmethod
    def _v_dh(cls, v):
        return _validar_data_hora(v)


class Cancelamento(BaseModel):
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _v_dh(cls, v):
        return _validar_data_hora(v)


def _indisponivel(mensagem: str):
    return ErroAPI(503, "SERVICO_INDISPONIVEL", mensagem)


def _buscar_cotacao(cotacao_id: str):
    base = os.environ.get("URL_COTACAO", "")
    try:
        resposta = _cliente.get(base + "/cotacoes/" + quote(cotacao_id, safe=""))
    except Exception:
        raise _indisponivel("Serviço de cotação indisponível.")
    if resposta.status_code >= 500:
        raise _indisponivel("Serviço de cotação indisponível.")
    if resposta.status_code != 200:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    try:
        dados = resposta.json()
    except Exception:
        raise _indisponivel("Resposta inválida do serviço de cotação.")
    if not isinstance(dados, dict):
        raise _indisponivel("Resposta inválida do serviço de cotação.")
    return dados


def _publicar(tipo: str, ocorrido_em: str, dados: dict):
    base = os.environ.get("URL_BARRAMENTO", "")
    evento = {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}
    try:
        resposta = _cliente.post(base + "/publicar", json=evento)
    except Exception:
        raise _indisponivel("Barramento de eventos indisponível.")
    if resposta.status_code >= 500:
        raise _indisponivel("Barramento de eventos indisponível.")


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


def _obter(id_envio: str) -> dict:
    envio = _envios.get(id_envio)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return envio


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _contador[0] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(corpo: NovoEnvio):
    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == corpo.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui envio ativo.")

        cotacao = _buscar_cotacao(corpo.cotacao_id)

        valida_ate = cotacao.get("valida_ate")
        if isinstance(valida_ate, str) and corpo.data_hora[:10] > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == corpo.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _contador[0] + 1
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

        _publicar(
            "envio.criado",
            corpo.data_hora,
            {
                "envio_id": envio["id"],
                "codigo_rastreio": envio["codigo_rastreio"],
                "pedido_id": envio["pedido_id"],
                "transportadora_id": envio["transportadora_id"],
                "data_prevista_entrega": envio["data_prevista_entrega"],
                "destinatario_email": envio["destinatario"]["email"],
                "data_hora": corpo.data_hora,
            },
        )

        _contador[0] = numero
        _envios[envio["id"]] = envio
        return copy.deepcopy(envio)


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _lock:
        itens = [e for e in _envios.values() if e["pedido_id"] == pedido_id]
        itens.sort(key=lambda e: e["id"])
        return copy.deepcopy(itens)


@app.get("/envios/{id_envio}")
def obter_envio(id_envio: str):
    with _lock:
        return copy.deepcopy(_obter(id_envio))


@app.post("/envios/{id_envio}/atualizacoes")
def atualizar_envio(id_envio: str, corpo: Atualizacao):
    with _lock:
        envio = _obter(id_envio)
        if corpo.status not in TRANSICOES.get(envio["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        if _parse_dh(corpo.data_hora) < _parse_dh(envio["historico"][-1]["data_hora"]):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        novo = copy.deepcopy(envio)
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
            _publicar("envio.status_atualizado", corpo.data_hora, _evento_status(novo, it))

        _envios[id_envio] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{id_envio}/cancelar")
def cancelar_envio(id_envio: str, corpo: Cancelamento):
    with _lock:
        envio = _obter(id_envio)
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no status CRIADO.")
        if _parse_dh(corpo.data_hora) < _parse_dh(envio["historico"][-1]["data_hora"]):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        novo = copy.deepcopy(envio)
        item = {"status": "CANCELADO", "data_hora": corpo.data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"

        _publicar("envio.status_atualizado", corpo.data_hora, _evento_status(novo, item))

        _envios[id_envio] = novo
        return copy.deepcopy(novo)
