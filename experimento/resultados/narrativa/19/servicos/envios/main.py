# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import datetime
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(title="Envios")

TIMEOUT = 10.0

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

MAX_FALHAS = 3

_lock = threading.Lock()
_envios = {}
_estado = {"contador": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


@app.exception_handler(Exception)
async def _tratar_inesperado(request: Request, exc: Exception):
    return _resposta_erro(500, "ERRO_INTERNO", "Erro interno.")


def _validar_data_hora(v: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", v):
        raise ValueError("data e hora mal formada")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def _nao_vazio(v: str) -> str:
    if not v.strip():
        raise ValueError("texto vazio")
    return v


class DestinatarioIn(BaseModel):
    nome: StrictStr
    email: StrictStr

    @field_validator("nome")
    @classmethod
    def _nome(cls, v):
        return _nao_vazio(v)

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("e-mail inválido")
        return v


class EnvioIn(BaseModel):
    pedido_id: StrictStr
    cotacao_id: StrictStr
    transportadora_id: StrictStr
    destinatario: DestinatarioIn
    data_hora: StrictStr

    @field_validator("pedido_id", "cotacao_id", "transportadora_id")
    @classmethod
    def _textos(cls, v):
        return _nao_vazio(v)

    @field_validator("data_hora")
    @classmethod
    def _dh(cls, v):
        return _validar_data_hora(v)


class AtualizacaoIn(BaseModel):
    status: StrictStr
    data_hora: StrictStr
    local: StrictStr

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v not in STATUS_ATUALIZACAO:
            raise ValueError("status inválido")
        return v

    @field_validator("data_hora")
    @classmethod
    def _dh(cls, v):
        return _validar_data_hora(v)

    @field_validator("local")
    @classmethod
    def _local(cls, v):
        return _nao_vazio(v)


class CancelamentoIn(BaseModel):
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _dh(cls, v):
        return _validar_data_hora(v)


def _url(nome: str, padrao: str) -> str:
    return os.environ.get(nome, padrao)


def _buscar_cotacao(cotacao_id: str) -> dict:
    base = _url("URL_COTACAO", "http://localhost:8002")
    try:
        resp = httpx.get(f"{base}/cotacoes/{quote(cotacao_id, safe='')}", timeout=TIMEOUT)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if resp.status_code >= 500:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if resp.status_code != 200:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de cotação.")
    if not isinstance(dados, dict):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de cotação.")
    return dados


def _publicar(evento: dict) -> None:
    base = _url("URL_BARRAMENTO", "http://localhost:8000")
    try:
        resp = httpx.post(f"{base}/publicar", json=evento, timeout=TIMEOUT)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")
    if resp.status_code >= 500:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")


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


def _obter(envio_id: str) -> dict:
    e = _envios.get(envio_id)
    if e is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return e


def _verificar_data_hora(envio: dict, data_hora: str) -> None:
    ultimo = envio["historico"][-1]["data_hora"]
    if datetime.strptime(data_hora, "%Y-%m-%dT%H:%M:%S") < datetime.strptime(ultimo, "%Y-%m-%dT%H:%M:%S"):
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao último registro.")


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(corpo: EnvioIn):
    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == corpo.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui envio ativo.")

        cotacao = _buscar_cotacao(corpo.cotacao_id)

        valida_ate = cotacao.get("valida_ate")
        if not isinstance(valida_ate, str) or corpo.data_hora[:10] > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "Cotação expirada.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == corpo.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _estado["contador"] + 1
        envio_id = f"ENV-{numero:06d}"
        codigo = f"LG{numero:09d}BR"
        envio = {
            "id": envio_id,
            "codigo_rastreio": codigo,
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
        evento = {
            "id": str(uuid.uuid4()),
            "tipo": "envio.criado",
            "ocorrido_em": corpo.data_hora,
            "dados": {
                "envio_id": envio_id,
                "codigo_rastreio": codigo,
                "pedido_id": corpo.pedido_id,
                "transportadora_id": corpo.transportadora_id,
                "data_prevista_entrega": envio["data_prevista_entrega"],
                "destinatario_email": corpo.destinatario.email,
                "data_hora": corpo.data_hora,
            },
        }
        _publicar(evento)

        _estado["contador"] = numero
        _envios[envio_id] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _lock:
        lista = [copy.deepcopy(e) for e in _envios.values() if e["pedido_id"] == pedido_id]
    lista.sort(key=lambda e: e["id"])
    return lista


@app.get("/envios/{envio_id}")
def obter_envio(envio_id: str):
    with _lock:
        return copy.deepcopy(_obter(envio_id))


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar_envio(envio_id: str, corpo: AtualizacaoIn):
    with _lock:
        atual = _obter(envio_id)
        if corpo.status not in TRANSICOES.get(atual["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        _verificar_data_hora(atual, corpo.data_hora)

        novo = copy.deepcopy(atual)
        item = {"status": corpo.status, "data_hora": corpo.data_hora, "local": corpo.local}
        novo["historico"].append(item)
        novo["status"] = corpo.status
        eventos = [_evento_status(novo, item)]

        if corpo.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= MAX_FALHAS:
                item_dev = {"status": "DEVOLVIDO", "data_hora": corpo.data_hora, "local": corpo.local}
                novo["historico"].append(item_dev)
                novo["status"] = "DEVOLVIDO"
                eventos.append(_evento_status(novo, item_dev))

        for ev in eventos:
            _publicar(ev)

        _envios[envio_id] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{envio_id}/cancelar")
def cancelar_envio(envio_id: str, corpo: CancelamentoIn):
    with _lock:
        atual = _obter(envio_id)
        if atual["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no status CRIADO.")
        _verificar_data_hora(atual, corpo.data_hora)

        novo = copy.deepcopy(atual)
        item = {"status": "CANCELADO", "data_hora": corpo.data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"
        _publicar(_evento_status(novo, item))

        _envios[envio_id] = novo
        return copy.deepcopy(novo)
