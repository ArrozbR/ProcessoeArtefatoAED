# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime
from typing import Annotated
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_lock = threading.RLock()
_envios = {}
_contador = {"valor": 0}


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
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _url(nome, padrao):
    return os.environ.get(nome, padrao)


def _validar_data_hora(v):
    if not isinstance(v, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", v):
        raise ValueError("data_hora invalida")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def _nao_vazio(v):
    if not v or not v.strip():
        raise ValueError("texto vazio")
    return v


TextoStrict = Annotated[str, Field(strict=True)]


class Destinatario(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nome: TextoStrict
    email: TextoStrict

    @field_validator("nome")
    @classmethod
    def _v_nome(cls, v):
        return _nao_vazio(v)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v):
        if not EMAIL_RE.match(v):
            raise ValueError("email invalido")
        return v


class EnvioEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pedido_id: TextoStrict
    cotacao_id: TextoStrict
    transportadora_id: TextoStrict
    destinatario: Destinatario
    data_hora: TextoStrict

    @field_validator("pedido_id", "cotacao_id", "transportadora_id")
    @classmethod
    def _v_textos(cls, v):
        return _nao_vazio(v)

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


class AtualizacaoEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: TextoStrict
    data_hora: TextoStrict
    local: TextoStrict

    @field_validator("status")
    @classmethod
    def _v_status(cls, v):
        if v not in STATUS_ATUALIZACAO:
            raise ValueError("status invalido")
        return v

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)

    @field_validator("local")
    @classmethod
    def _v_local(cls, v):
        return _nao_vazio(v)


class CancelamentoEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data_hora: TextoStrict

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


def _publicar(tipo, ocorrido_em, dados):
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    url = _url("URL_BARRAMENTO", "http://localhost:8000")
    try:
        with httpx.Client(timeout=10.0) as cliente:
            resp = cliente.post(url + "/publicar", json=evento)
    except httpx.HTTPError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponivel.")
    if resp.status_code >= 400:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponivel.")


def _buscar_cotacao(cotacao_id):
    url = _url("URL_COTACAO", "http://localhost:8002")
    try:
        with httpx.Client(timeout=10.0) as cliente:
            resp = cliente.get(url + "/cotacoes/" + quote(cotacao_id, safe=""))
    except httpx.HTTPError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico de cotacao indisponivel.")
    if resp.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotacao nao encontrada.")
    if resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico de cotacao indisponivel.")
    try:
        dados = resp.json()
    except ValueError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta invalida do servico de cotacao.")
    if not isinstance(dados, dict):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta invalida do servico de cotacao.")
    return dados


def _obter(id):
    e = _envios.get(id)
    if e is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio nao encontrado.")
    return e


def _dados_status(envio, item):
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
    with _lock:
        _envios.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(entrada: EnvioEntrada):
    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == entrada.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "Pedido ja possui envio ativo.")

        cotacao = _buscar_cotacao(entrada.cotacao_id)

        data_envio = datetime.strptime(entrada.data_hora, "%Y-%m-%dT%H:%M:%S").date()
        try:
            valida_ate = date.fromisoformat(cotacao["valida_ate"])
        except (KeyError, TypeError, ValueError):
            raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta invalida do servico de cotacao.")
        if data_envio > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "Cotacao expirada.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == entrada.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora nao oferecida na cotacao.")

        numero = _contador["valor"] + 1
        envio = {
            "id": "ENV-%06d" % numero,
            "codigo_rastreio": "LG%09dBR" % numero,
            "pedido_id": entrada.pedido_id,
            "cotacao_id": entrada.cotacao_id,
            "transportadora_id": entrada.transportadora_id,
            "preco_centavos": opcao["preco_centavos"],
            "data_prevista_entrega": opcao["data_prevista_entrega"],
            "destinatario": {"nome": entrada.destinatario.nome, "email": entrada.destinatario.email},
            "status": "CRIADO",
            "tentativas_falhas": 0,
            "historico": [{"status": "CRIADO", "data_hora": entrada.data_hora, "local": None}],
        }

        _publicar(
            "envio.criado",
            entrada.data_hora,
            {
                "envio_id": envio["id"],
                "codigo_rastreio": envio["codigo_rastreio"],
                "pedido_id": envio["pedido_id"],
                "transportadora_id": envio["transportadora_id"],
                "data_prevista_entrega": envio["data_prevista_entrega"],
                "destinatario_email": envio["destinatario"]["email"],
                "data_hora": entrada.data_hora,
            },
        )

        _envios[envio["id"]] = envio
        _contador["valor"] = numero
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _lock:
        itens = [copy.deepcopy(e) for e in _envios.values() if e["pedido_id"] == pedido_id]
    itens.sort(key=lambda e: e["id"])
    return itens


@app.get("/envios/{id}")
def obter_envio(id: str):
    with _lock:
        return copy.deepcopy(_obter(id))


@app.post("/envios/{id}/atualizacoes")
def atualizar_envio(id: str, entrada: AtualizacaoEntrada):
    with _lock:
        envio = _obter(id)
        atual = envio["status"]
        if entrada.status not in TRANSICOES.get(atual, set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transicao de status invalida.")
        ultimo = envio["historico"][-1]["data_hora"]
        if entrada.data_hora < ultimo:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao ultimo registro.")

        novo = copy.deepcopy(envio)
        item = {"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}
        novos_itens = [item]
        novo["historico"].append(item)
        novo["status"] = entrada.status
        if entrada.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                dev = {"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local}
                novo["historico"].append(dev)
                novo["status"] = "DEVOLVIDO"
                novos_itens.append(dev)

        for it in novos_itens:
            _publicar("envio.status_atualizado", entrada.data_hora, _dados_status(novo, it))

        _envios[id] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{id}/cancelar")
def cancelar_envio(id: str, entrada: CancelamentoEntrada):
    with _lock:
        envio = _obter(id)
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Envio so pode ser cancelado enquanto CRIADO.")
        ultimo = envio["historico"][-1]["data_hora"]
        if entrada.data_hora < ultimo:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao ultimo registro.")

        novo = copy.deepcopy(envio)
        item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"

        _publicar("envio.status_atualizado", entrada.data_hora, _dados_status(novo, item))

        _envios[id] = novo
        return copy.deepcopy(novo)
