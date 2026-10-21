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
from pydantic import BaseModel, constr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

RE_DATA_HORA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}
TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}
MAX_FALHAS = 3


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
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


_lock = threading.RLock()
_envios = {}
_contador = [0]
_cliente_http = httpx.Client(timeout=10.0)


def _validar_data_hora(v):
    if not RE_DATA_HORA.fullmatch(v):
        raise ValueError("data e hora inválidas")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def _parse_dh(v):
    return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")


class Destinatario(BaseModel):
    nome: constr(strict=True, min_length=1)
    email: constr(strict=True)

    @field_validator("email")
    @classmethod
    def _validar_email(cls, v):
        if not RE_EMAIL.match(v):
            raise ValueError("e-mail inválido")
        return v


class EnvioEntrada(BaseModel):
    pedido_id: constr(strict=True, min_length=1)
    cotacao_id: constr(strict=True, min_length=1)
    transportadora_id: constr(strict=True, min_length=1)
    destinatario: Destinatario
    data_hora: constr(strict=True)

    @field_validator("data_hora")
    @classmethod
    def _validar_dh(cls, v):
        return _validar_data_hora(v)


class AtualizacaoEntrada(BaseModel):
    status: constr(strict=True)
    data_hora: constr(strict=True)
    local: constr(strict=True, min_length=1)

    @field_validator("status")
    @classmethod
    def _validar_status(cls, v):
        if v not in STATUS_ATUALIZACAO:
            raise ValueError("status inválido")
        return v

    @field_validator("data_hora")
    @classmethod
    def _validar_dh(cls, v):
        return _validar_data_hora(v)


class CancelamentoEntrada(BaseModel):
    data_hora: constr(strict=True)

    @field_validator("data_hora")
    @classmethod
    def _validar_dh(cls, v):
        return _validar_data_hora(v)


def _indisponivel(nome):
    return ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de %s indisponível." % nome)


def _buscar_cotacao(cotacao_id: str):
    base = os.environ.get("URL_COTACAO", "")
    try:
        resp = _cliente_http.get(base + "/cotacoes/" + quote(cotacao_id, safe=""), timeout=10.0)
    except Exception:
        raise _indisponivel("cotação")
    if resp.status_code >= 500:
        raise _indisponivel("cotação")
    if resp.status_code != 200:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    try:
        dados = resp.json()
    except Exception:
        raise _indisponivel("cotação")
    if not isinstance(dados, dict):
        raise _indisponivel("cotação")
    return dados


def _publicar(evento: dict):
    base = os.environ.get("URL_BARRAMENTO", "")
    try:
        resp = _cliente_http.post(base + "/publicar", json=evento, timeout=10.0)
    except Exception:
        raise _indisponivel("barramento")
    if resp.status_code >= 500:
        raise _indisponivel("barramento")


def _evento(tipo: str, ocorrido_em: str, dados: dict):
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def _evento_status(envio: dict, item: dict):
    return _evento("envio.status_atualizado", item["data_hora"], {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    })


def _obter(envio_id: str):
    e = _envios.get(envio_id)
    if e is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return e


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _contador[0] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(entrada: EnvioEntrada):
    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == entrada.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

        cotacao = _buscar_cotacao(entrada.cotacao_id)

        valida_ate = cotacao.get("valida_ate")
        data_envio = entrada.data_hora[:10]
        if isinstance(valida_ate, str) and data_envio > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "Cotação expirada.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == entrada.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _contador[0] + 1
        envio_id = "ENV-%06d" % numero
        codigo = "LG%09dBR" % numero
        envio = {
            "id": envio_id,
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

        evento = _evento("envio.criado", entrada.data_hora, {
            "envio_id": envio_id,
            "codigo_rastreio": codigo,
            "pedido_id": entrada.pedido_id,
            "transportadora_id": entrada.transportadora_id,
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": entrada.destinatario.email,
            "data_hora": entrada.data_hora,
        })
        _publicar(evento)

        _contador[0] = numero
        _envios[envio_id] = envio
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
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        ultimo = envio["historico"][-1]
        if _parse_dh(entrada.data_hora) < _parse_dh(ultimo["data_hora"]):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        novos_itens = [{"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}]
        falhas = envio["tentativas_falhas"]
        status_final = entrada.status
        if entrada.status == "FALHA_ENTREGA":
            falhas += 1
            if falhas >= MAX_FALHAS:
                novos_itens.append({"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local})
                status_final = "DEVOLVIDO"

        for item in novos_itens:
            _publicar(_evento_status(envio, item))

        envio["historico"].extend(novos_itens)
        envio["status"] = status_final
        envio["tentativas_falhas"] = falhas
        return copy.deepcopy(envio)


@app.post("/envios/{id}/cancelar")
def cancelar_envio(id: str, entrada: CancelamentoEntrada):
    with _lock:
        envio = _obter(id)
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no status CRIADO.")
        ultimo = envio["historico"][-1]
        if _parse_dh(entrada.data_hora) < _parse_dh(ultimo["data_hora"]):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
        _publicar(_evento_status(envio, item))

        envio["historico"].append(item)
        envio["status"] = "CANCELADO"
        return copy.deepcopy(envio)
