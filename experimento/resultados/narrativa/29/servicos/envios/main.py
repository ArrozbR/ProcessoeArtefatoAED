# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_HORA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}
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
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _validar_data_hora(v: str) -> str:
    if not DATA_HORA_RE.match(v):
        raise ValueError("data_hora mal formada")
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
        if not EMAIL_RE.match(v):
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
    def _v_nao_vazio(cls, v):
        if not v.strip():
            raise ValueError("vazio")
        return v

    @field_validator("data_hora")
    @classmethod
    def _v_dh(cls, v):
        return _validar_data_hora(v)


class AtualizacaoEntrada(BaseModel):
    status: StrictStr
    data_hora: StrictStr
    local: StrictStr

    @field_validator("status")
    @classmethod
    def _v_status(cls, v):
        if v not in STATUS_ATUALIZACAO:
            raise ValueError("status invalido")
        return v

    @field_validator("data_hora")
    @classmethod
    def _v_dh(cls, v):
        return _validar_data_hora(v)

    @field_validator("local")
    @classmethod
    def _v_local(cls, v):
        if not v.strip():
            raise ValueError("local vazio")
        return v


class CancelamentoEntrada(BaseModel):
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _v_dh(cls, v):
        return _validar_data_hora(v)


def _indisponivel(msg: str) -> ErroAPI:
    return ErroAPI(503, "SERVICO_INDISPONIVEL", msg)


def _buscar_cotacao(cotacao_id: str):
    base = os.environ.get("URL_COTACAO", "")
    try:
        resp = httpx.get(f"{base}/cotacoes/{cotacao_id}", timeout=10.0)
    except Exception:
        raise _indisponivel("Servico de cotacao indisponivel.")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise _indisponivel("Servico de cotacao indisponivel.")
    try:
        dados = resp.json()
    except Exception:
        raise _indisponivel("Resposta invalida do servico de cotacao.")
    if not isinstance(dados, dict):
        raise _indisponivel("Resposta invalida do servico de cotacao.")
    return dados


def _publicar(eventos):
    base = os.environ.get("URL_BARRAMENTO", "")
    for evento in eventos:
        try:
            resp = httpx.post(f"{base}/publicar", json=evento, timeout=10.0)
        except Exception:
            raise _indisponivel("Barramento de eventos indisponivel.")
        if resp.status_code >= 300:
            raise _indisponivel("Barramento de eventos indisponivel.")


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


def _obter_ou_404(envio_id: str) -> dict:
    envio = _envios.get(envio_id)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio nao encontrado.")
    return envio


def _verificar_data_hora(envio: dict, data_hora: str):
    ultimo = envio["historico"][-1]["data_hora"]
    if _parse_dh(data_hora) < _parse_dh(ultimo):
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao ultimo item do historico.")


@app.post("/admin/reset")
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
        if cotacao is None:
            raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotacao nao encontrada.")

        data_operacao = _parse_dh(entrada.data_hora).date()
        try:
            valida_ate = date.fromisoformat(cotacao.get("valida_ate"))
        except Exception:
            raise _indisponivel("Resposta invalida do servico de cotacao.")
        if data_operacao > valida_ate:
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
        evento = _evento("envio.criado", entrada.data_hora, {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "transportadora_id": envio["transportadora_id"],
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": envio["destinatario"]["email"],
            "data_hora": entrada.data_hora,
        })
        _publicar([evento])

        _contador["valor"] = numero
        _envios[envio["id"]] = envio
        return copy.deepcopy(envio)


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _lock:
        itens = [copy.deepcopy(e) for e in _envios.values() if e["pedido_id"] == pedido_id]
    itens.sort(key=lambda e: e["id"])
    return itens


@app.get("/envios/{envio_id}")
def obter_envio(envio_id: str):
    with _lock:
        return copy.deepcopy(_obter_ou_404(envio_id))


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar_envio(envio_id: str, entrada: AtualizacaoEntrada):
    with _lock:
        envio = _obter_ou_404(envio_id)
        permitidos = TRANSICOES.get(envio["status"], set())
        if entrada.status not in permitidos:
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transicao de status invalida.")
        _verificar_data_hora(envio, entrada.data_hora)

        novo = copy.deepcopy(envio)
        itens_novos = []
        item = {"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}
        novo["historico"].append(item)
        novo["status"] = entrada.status
        itens_novos.append(item)
        if entrada.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                item_dev = {"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local}
                novo["historico"].append(item_dev)
                novo["status"] = "DEVOLVIDO"
                itens_novos.append(item_dev)

        _publicar([_evento_status(novo, it) for it in itens_novos])
        _envios[envio_id] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{envio_id}/cancelar")
def cancelar_envio(envio_id: str, entrada: CancelamentoEntrada):
    with _lock:
        envio = _obter_ou_404(envio_id)
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Envio so pode ser cancelado enquanto CRIADO.")
        _verificar_data_hora(envio, entrada.data_hora)

        novo = copy.deepcopy(envio)
        item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"

        _publicar([_evento_status(novo, item)])
        _envios[envio_id] = novo
        return copy.deepcopy(novo)
