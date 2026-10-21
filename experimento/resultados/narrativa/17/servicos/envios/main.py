# arquivo: servicos/envios/main.py
import copy
import os
import re
import uuid
from datetime import date, datetime
from typing import Literal

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

TIMEOUT = 10.0
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATA_HORA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_estado = {"contador": 0, "envios": {}}


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
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _validar_data_hora(v: str) -> str:
    if not DATA_HORA_RE.match(v):
        raise ValueError("data e hora inválida")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def _validar_nao_vazio(v: str) -> str:
    if not v or not v.strip():
        raise ValueError("texto vazio")
    return v


class Destinatario(BaseModel):
    nome: StrictStr
    email: StrictStr

    @field_validator("nome")
    @classmethod
    def _v_nome(cls, v):
        return _validar_nao_vazio(v)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v):
        if not EMAIL_RE.match(v):
            raise ValueError("email inválido")
        return v


class EnvioEntrada(BaseModel):
    pedido_id: StrictStr
    cotacao_id: StrictStr
    transportadora_id: StrictStr
    destinatario: Destinatario
    data_hora: StrictStr

    @field_validator("pedido_id", "cotacao_id", "transportadora_id")
    @classmethod
    def _v_textos(cls, v):
        return _validar_nao_vazio(v)

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


class AtualizacaoEntrada(BaseModel):
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
        return _validar_nao_vazio(v)


class CancelamentoEntrada(BaseModel):
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


def _indisponivel(nome: str):
    return ErroAPI(503, "SERVICO_INDISPONIVEL", f"Serviço de {nome} indisponível.")


def _buscar_cotacao(cotacao_id: str):
    base = os.environ.get("URL_COTACAO", "")
    try:
        resp = httpx.get(f"{base}/cotacoes/{cotacao_id}", timeout=TIMEOUT)
    except httpx.HTTPError:
        raise _indisponivel("cotação")
    if resp.status_code >= 500:
        raise _indisponivel("cotação")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        raise _indisponivel("cotação")


def _publicar(eventos):
    base = os.environ.get("URL_BARRAMENTO", "")
    for evento in eventos:
        try:
            resp = httpx.post(f"{base}/publicar", json=evento, timeout=TIMEOUT)
        except httpx.HTTPError:
            raise _indisponivel("barramento")
        if resp.status_code >= 500:
            raise _indisponivel("barramento")


def _evento(tipo: str, ocorrido_em: str, dados: dict) -> dict:
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def _evento_status(envio: dict, item: dict) -> dict:
    return _evento(
        "envio.status_atualizado",
        item["data_hora"],
        {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "status": item["status"],
            "data_hora": item["data_hora"],
            "local": item["local"],
            "destinatario_email": envio["destinatario"]["email"],
        },
    )


def _obter_envio(envio_id: str) -> dict:
    envio = _estado["envios"].get(envio_id)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return envio


def _conferir_data_hora(envio: dict, data_hora: str):
    ultimo = envio["historico"][-1]["data_hora"]
    if datetime.strptime(data_hora, "%Y-%m-%dT%H:%M:%S") < datetime.strptime(ultimo, "%Y-%m-%dT%H:%M:%S"):
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao último registro.")


@app.post("/admin/reset", status_code=204)
def reset():
    _estado["contador"] = 0
    _estado["envios"].clear()
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(entrada: EnvioEntrada):
    for e in _estado["envios"].values():
        if e["pedido_id"] == entrada.pedido_id and e["status"] != "CANCELADO":
            raise ErroAPI(409, "ENVIO_DUPLICADO", "Pedido já possui envio ativo.")

    cotacao = _buscar_cotacao(entrada.cotacao_id)
    if cotacao is None:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")

    data_envio = date.fromisoformat(entrada.data_hora[:10])
    if data_envio > date.fromisoformat(cotacao["valida_ate"]):
        raise ErroAPI(422, "COTACAO_EXPIRADA", "Cotação expirada.")

    opcao = None
    for o in cotacao.get("opcoes", []):
        if o.get("transportadora_id") == entrada.transportadora_id:
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
    evento = _evento(
        "envio.criado",
        entrada.data_hora,
        {
            "envio_id": envio_id,
            "codigo_rastreio": codigo,
            "pedido_id": entrada.pedido_id,
            "transportadora_id": entrada.transportadora_id,
            "data_prevista_entrega": opcao["data_prevista_entrega"],
            "destinatario_email": entrada.destinatario.email,
            "data_hora": entrada.data_hora,
        },
    )
    _publicar([evento])
    _estado["contador"] = numero
    _estado["envios"][envio_id] = envio
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    itens = [e for e in _estado["envios"].values() if e["pedido_id"] == pedido_id]
    itens.sort(key=lambda e: e["id"])
    return copy.deepcopy(itens)


@app.get("/envios/{envio_id}")
def obter_envio(envio_id: str):
    return copy.deepcopy(_obter_envio(envio_id))


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar_envio(envio_id: str, entrada: AtualizacaoEntrada):
    envio = _obter_envio(envio_id)
    permitidos = TRANSICOES.get(envio["status"], set())
    if entrada.status not in permitidos:
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
    _conferir_data_hora(envio, entrada.data_hora)

    novo = copy.deepcopy(envio)
    item = {"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}
    novo["historico"].append(item)
    novo["status"] = entrada.status
    eventos = [_evento_status(novo, item)]
    if entrada.status == "FALHA_ENTREGA":
        novo["tentativas_falhas"] += 1
        if novo["tentativas_falhas"] >= 3:
            item_dev = {"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local}
            novo["historico"].append(item_dev)
            novo["status"] = "DEVOLVIDO"
            eventos.append(_evento_status(novo, item_dev))

    _publicar(eventos)
    _estado["envios"][envio_id] = novo
    return copy.deepcopy(novo)


@app.post("/envios/{envio_id}/cancelar")
def cancelar_envio(envio_id: str, entrada: CancelamentoEntrada):
    envio = _obter_envio(envio_id)
    if envio["status"] != "CRIADO":
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio criado.")
    _conferir_data_hora(envio, entrada.data_hora)

    novo = copy.deepcopy(envio)
    item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
    novo["historico"].append(item)
    novo["status"] = "CANCELADO"
    _publicar([_evento_status(novo, item)])
    _estado["envios"][envio_id] = novo
    return copy.deepcopy(novo)
