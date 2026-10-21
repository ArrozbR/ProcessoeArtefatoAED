import copy
import os
import re
import uuid
from datetime import date, datetime
from typing import Literal

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

TRANSICOES = {
    ("CRIADO", "COLETADO"),
    ("COLETADO", "EM_TRANSITO"),
    ("EM_TRANSITO", "EM_TRANSITO"),
    ("EM_TRANSITO", "SAIU_PARA_ENTREGA"),
    ("SAIU_PARA_ENTREGA", "ENTREGUE"),
    ("SAIU_PARA_ENTREGA", "FALHA_ENTREGA"),
    ("FALHA_ENTREGA", "SAIU_PARA_ENTREGA"),
}
MAX_FALHAS = 3
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = FastAPI(title="Envios")
_envios = {}
_contador = 0


class ErroApi(Exception):
    def __init__(self, status, erro, mensagem):
        self.status, self.erro, self.mensagem = status, erro, mensagem


@app.exception_handler(ErroApi)
def _erro_api(_: Request, e: ErroApi):
    return JSONResponse({"erro": e.erro, "mensagem": e.mensagem}, status_code=e.status)


@app.exception_handler(RequestValidationError)
def _erro_validacao(_: Request, e: RequestValidationError):
    return JSONResponse({"erro": "DADOS_INVALIDOS", "mensagem": "dados inválidos"}, status_code=422)


def _texto(v):
    if not v.strip():
        raise ValueError("texto vazio")
    return v


class Destinatario(BaseModel):
    nome: str
    email: str

    @field_validator("nome")
    @classmethod
    def _nome(cls, v):
        return _texto(v)

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        if not EMAIL.match(v):
            raise ValueError("email inválido")
        return v


class NovoEnvio(BaseModel):
    pedido_id: str
    cotacao_id: str
    transportadora_id: str
    destinatario: Destinatario
    data_hora: datetime

    @field_validator("pedido_id", "cotacao_id", "transportadora_id")
    @classmethod
    def _textos(cls, v):
        return _texto(v)


class Atualizacao(BaseModel):
    status: Literal["COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"]
    data_hora: datetime
    local: str

    @field_validator("local")
    @classmethod
    def _local(cls, v):
        return _texto(v)


class Cancelamento(BaseModel):
    data_hora: datetime


@app.post("/envios", status_code=201)
def criar(dados: NovoEnvio):
    global _contador
    if any(e["pedido_id"] == dados.pedido_id and e["status"] != "CANCELADO" for e in _envios.values()):
        raise ErroApi(409, "ENVIO_DUPLICADO", "o pedido já tem um envio ativo")
    cotacao = _buscar_cotacao(dados.cotacao_id)
    if dados.data_hora.date() > date.fromisoformat(cotacao["valida_ate"]):
        raise ErroApi(422, "COTACAO_EXPIRADA", "cotação expirada")
    opcao = next((o for o in cotacao["opcoes"] if o["transportadora_id"] == dados.transportadora_id), None)
    if opcao is None:
        raise ErroApi(422, "TRANSPORTADORA_NAO_OFERECIDA", "transportadora não oferecida na cotação")

    _contador += 1
    data_hora = _iso(dados.data_hora)
    envio = {
        "id": f"ENV-{_contador:06d}",
        "codigo_rastreio": f"LG{_contador:09d}BR",
        "pedido_id": dados.pedido_id,
        "cotacao_id": dados.cotacao_id,
        "transportadora_id": dados.transportadora_id,
        "preco_centavos": opcao["preco_centavos"],
        "data_prevista_entrega": opcao["data_prevista_entrega"],
        "destinatario": {"nome": dados.destinatario.nome, "email": dados.destinatario.email},
        "status": "CRIADO",
        "tentativas_falhas": 0,
        "historico": [{"status": "CRIADO", "data_hora": data_hora, "local": None}],
    }
    _envios[envio["id"]] = envio
    _publicar("envio.criado", data_hora, {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "transportadora_id": envio["transportadora_id"],
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": envio["destinatario"]["email"],
        "data_hora": data_hora,
    })
    return envio


@app.get("/envios")
def listar(pedido_id: str):
    return [e for _, e in sorted(_envios.items()) if e["pedido_id"] == pedido_id]


@app.get("/envios/{id}")
def consultar(id: str):
    return _buscar(id)


@app.post("/envios/{id}/atualizacoes")
def atualizar(id: str, dados: Atualizacao):
    envio = _buscar(id)
    if (envio["status"], dados.status) not in TRANSICOES:
        raise ErroApi(409, "TRANSICAO_INVALIDA", "transição de estado inválida")
    data_hora = _iso(dados.data_hora)
    _validar_data_hora(envio, data_hora)
    novos = [dados.status]
    if dados.status == "FALHA_ENTREGA":
        envio["tentativas_falhas"] += 1
        if envio["tentativas_falhas"] >= MAX_FALHAS:
            novos.append("DEVOLVIDO")
    for status in novos:
        _registrar(envio, status, data_hora, dados.local)
    return envio


@app.post("/envios/{id}/cancelar")
def cancelar(id: str, dados: Cancelamento):
    envio = _buscar(id)
    if envio["status"] != "CRIADO":
        raise ErroApi(409, "TRANSICAO_INVALIDA", "só é possível cancelar envio não coletado")
    data_hora = _iso(dados.data_hora)
    _validar_data_hora(envio, data_hora)
    _registrar(envio, "CANCELADO", data_hora, None)
    return envio


@app.post("/admin/reset", status_code=204)
def reset():
    global _contador
    _envios.clear()
    _contador = 0
    return Response(status_code=204)


def _buscar(id):
    if id not in _envios:
        raise ErroApi(404, "ENVIO_NAO_ENCONTRADO", "envio não encontrado")
    return _envios[id]


def _buscar_cotacao(cotacao_id):
    try:
        r = httpx.get(f"{os.environ['URL_COTACAO']}/cotacoes/{cotacao_id}", timeout=10)
    except httpx.HTTPError:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "serviço de cotação indisponível")
    if r.status_code == 404:
        raise ErroApi(422, "COTACAO_NAO_ENCONTRADA", "cotação não encontrada")
    if r.status_code >= 500:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "serviço de cotação indisponível")
    return r.json()


def _validar_data_hora(envio, data_hora):
    if data_hora < envio["historico"][-1]["data_hora"]:
        raise ErroApi(422, "DATA_HORA_INVALIDA", "data e hora anteriores ao último registro")


def _registrar(envio, status, data_hora, local):
    envio["status"] = status
    envio["historico"].append({"status": status, "data_hora": data_hora, "local": local})
    _publicar("envio.status_atualizado", data_hora, {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": status,
        "data_hora": data_hora,
        "local": local,
        "destinatario_email": envio["destinatario"]["email"],
    })


def _publicar(tipo, ocorrido_em, dados):
    evento = {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": copy.deepcopy(dados)}
    httpx.post(f"{os.environ['URL_BARRAMENTO']}/publicar", json=evento, timeout=10)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")
