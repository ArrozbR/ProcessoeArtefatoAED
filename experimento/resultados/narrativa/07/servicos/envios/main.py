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
from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

REGEX_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
REGEX_DATA_HORA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_lock = threading.RLock()
_envios = {}
_contador = {"valor": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _erro(exc.status_code, codigos.get(exc.status_code, "ERRO_HTTP"), str(exc.detail))


@app.exception_handler(Exception)
async def _tratar_generico(request, exc: Exception):
    return _erro(500, "ERRO_INTERNO", "Erro interno.")


def _validar_data_hora(v: str) -> str:
    if not REGEX_DATA_HORA.match(v):
        raise ValueError("data_hora invalida")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def _parse_dh(v: str) -> datetime:
    return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")


def _nao_vazio(v: str) -> str:
    if not v.strip():
        raise ValueError("texto vazio")
    return v


class Destinatario(BaseModel):
    nome: StrictStr
    email: StrictStr

    @field_validator("nome")
    @classmethod
    def _validar_nome(cls, v):
        return _nao_vazio(v)

    @field_validator("email")
    @classmethod
    def _validar_email(cls, v):
        if not REGEX_EMAIL.match(v):
            raise ValueError("email invalido")
        return v


class EnvioEntrada(BaseModel):
    pedido_id: StrictStr
    cotacao_id: StrictStr
    transportadora_id: StrictStr
    destinatario: Destinatario
    data_hora: StrictStr

    @field_validator("pedido_id", "cotacao_id", "transportadora_id")
    @classmethod
    def _validar_textos(cls, v):
        return _nao_vazio(v)

    @field_validator("data_hora")
    @classmethod
    def _validar_dh(cls, v):
        return _validar_data_hora(v)


class AtualizacaoEntrada(BaseModel):
    status: Literal["COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"]
    data_hora: StrictStr
    local: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _validar_dh(cls, v):
        return _validar_data_hora(v)

    @field_validator("local")
    @classmethod
    def _validar_local(cls, v):
        return _nao_vazio(v)


class CancelamentoEntrada(BaseModel):
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _validar_dh(cls, v):
        return _validar_data_hora(v)


def _url(nome: str) -> str:
    return os.environ.get(nome, "")


def _indisponivel(servico: str) -> ErroAPI:
    return ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico de %s indisponivel." % servico)


def _buscar_cotacao(cotacao_id: str) -> dict:
    try:
        with httpx.Client(timeout=10.0) as cliente:
            resposta = cliente.get(_url("URL_COTACAO") + "/cotacoes/" + quote(cotacao_id, safe=""))
    except Exception:
        raise _indisponivel("cotacao")
    if resposta.status_code >= 500:
        raise _indisponivel("cotacao")
    if resposta.status_code != 200:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotacao nao encontrada.")
    try:
        dados = resposta.json()
    except Exception:
        raise _indisponivel("cotacao")
    if not isinstance(dados, dict):
        raise _indisponivel("cotacao")
    return dados


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        with httpx.Client(timeout=10.0) as cliente:
            resposta = cliente.post(_url("URL_BARRAMENTO") + "/publicar", json=evento)
    except Exception:
        raise _indisponivel("barramento")
    if resposta.status_code < 200 or resposta.status_code >= 300:
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


def _obter_ou_404(id_envio: str) -> dict:
    envio = _envios.get(id_envio)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio nao encontrado.")
    return envio


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
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido ja possui envio ativo.")

        cotacao = _buscar_cotacao(entrada.cotacao_id)
        try:
            valida_ate = date.fromisoformat(cotacao["valida_ate"])
            opcoes = cotacao.get("opcoes") or []
        except Exception:
            raise _indisponivel("cotacao")

        if _parse_dh(entrada.data_hora).date() > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotacao expirou.")

        opcao = None
        for o in opcoes:
            if isinstance(o, dict) and o.get("transportadora_id") == entrada.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora nao oferecida na cotacao.")

        numero = _contador["valor"] + 1
        id_envio = "ENV-%06d" % numero
        codigo = "LG%09dBR" % numero
        envio = {
            "id": id_envio,
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

        _publicar(
            "envio.criado",
            entrada.data_hora,
            {
                "envio_id": id_envio,
                "codigo_rastreio": codigo,
                "pedido_id": entrada.pedido_id,
                "transportadora_id": entrada.transportadora_id,
                "data_prevista_entrega": envio["data_prevista_entrega"],
                "destinatario_email": entrada.destinatario.email,
                "data_hora": entrada.data_hora,
            },
        )

        _contador["valor"] = numero
        _envios[id_envio] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _lock:
        itens = [copy.deepcopy(e) for k, e in sorted(_envios.items()) if e["pedido_id"] == pedido_id]
    return itens


@app.get("/envios/{id_envio}")
def obter_envio(id_envio: str):
    with _lock:
        return copy.deepcopy(_obter_ou_404(id_envio))


@app.post("/envios/{id_envio}/atualizacoes")
def atualizar_envio(id_envio: str, entrada: AtualizacaoEntrada):
    with _lock:
        atual = _obter_ou_404(id_envio)
        permitidos = TRANSICOES.get(atual["status"], set())
        if entrada.status not in permitidos:
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transicao de status invalida.")
        ultimo = atual["historico"][-1]["data_hora"]
        if _parse_dh(entrada.data_hora) < _parse_dh(ultimo):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao ultimo registro.")

        novo = copy.deepcopy(atual)
        novos_itens = []
        item = {"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}
        novo["historico"].append(item)
        novo["status"] = entrada.status
        novos_itens.append(item)
        if entrada.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                devolvido = {"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local}
                novo["historico"].append(devolvido)
                novo["status"] = "DEVOLVIDO"
                novos_itens.append(devolvido)

        for it in novos_itens:
            _publicar("envio.status_atualizado", entrada.data_hora, _evento_status(novo, it))

        _envios[id_envio] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{id_envio}/cancelar")
def cancelar_envio(id_envio: str, entrada: CancelamentoEntrada):
    with _lock:
        atual = _obter_ou_404(id_envio)
        if atual["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "So e possivel cancelar envio criado.")
        ultimo = atual["historico"][-1]["data_hora"]
        if _parse_dh(entrada.data_hora) < _parse_dh(ultimo):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao ultimo registro.")

        novo = copy.deepcopy(atual)
        item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"

        _publicar("envio.status_atualizado", entrada.data_hora, _evento_status(novo, item))

        _envios[id_envio] = novo
        return copy.deepcopy(novo)
