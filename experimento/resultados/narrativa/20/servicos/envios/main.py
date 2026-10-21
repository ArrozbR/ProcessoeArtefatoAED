# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AfterValidator, BaseModel, Field, StrictStr
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

_lock = threading.Lock()
_envios = {}
_contador = 0


class ErroAPI(Exception):
    def __init__(self, status, codigo, mensagem):
        super().__init__(mensagem)
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


def _validar_data_hora(v):
    if not DATA_HORA_RE.match(v):
        raise ValueError("data e hora inválidas")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def _validar_email(v):
    if not EMAIL_RE.match(v):
        raise ValueError("e-mail inválido")
    return v


DataHora = Annotated[StrictStr, AfterValidator(_validar_data_hora)]
TextoNaoVazio = Annotated[StrictStr, Field(min_length=1)]
Email = Annotated[StrictStr, AfterValidator(_validar_email)]


class Destinatario(BaseModel):
    nome: TextoNaoVazio
    email: Email


class NovoEnvio(BaseModel):
    pedido_id: TextoNaoVazio
    cotacao_id: TextoNaoVazio
    transportadora_id: TextoNaoVazio
    destinatario: Destinatario
    data_hora: DataHora


class Atualizacao(BaseModel):
    status: Literal["COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"]
    data_hora: DataHora
    local: TextoNaoVazio


class Cancelamento(BaseModel):
    data_hora: DataHora


def _url(nome, padrao):
    return os.environ.get(nome, padrao).rstrip("/")


def _indisponivel(mensagem):
    return ErroAPI(503, "SERVICO_INDISPONIVEL", mensagem)


def _buscar_cotacao(cotacao_id):
    url = _url("URL_COTACAO", "http://localhost:8002") + "/cotacoes/" + cotacao_id
    try:
        resp = httpx.get(url, timeout=TIMEOUT)
    except httpx.HTTPError:
        raise _indisponivel("Serviço de cotação indisponível.")
    if resp.status_code >= 500:
        raise _indisponivel("Serviço de cotação indisponível.")
    if resp.status_code != 200:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    try:
        dados = resp.json()
    except ValueError:
        raise _indisponivel("Resposta inválida do serviço de cotação.")
    if not isinstance(dados, dict):
        raise _indisponivel("Resposta inválida do serviço de cotação.")
    return dados


def _publicar(tipo, ocorrido_em, dados):
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    url = _url("URL_BARRAMENTO", "http://localhost:8000") + "/publicar"
    try:
        resp = httpx.post(url, json=evento, timeout=TIMEOUT)
    except httpx.HTTPError:
        raise _indisponivel("Barramento de eventos indisponível.")
    if resp.status_code >= 400:
        raise _indisponivel("Barramento de eventos indisponível.")


def _evento_status(envio, item):
    return {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    }


def _obter_ou_404(envio_id):
    envio = _envios.get(envio_id)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return envio


def _checar_data_hora(envio, data_hora):
    ultimo = envio["historico"][-1]["data_hora"]
    if datetime.strptime(data_hora, "%Y-%m-%dT%H:%M:%S") < datetime.strptime(ultimo, "%Y-%m-%dT%H:%M:%S"):
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")


@app.post("/admin/reset", status_code=204)
def reset():
    global _contador
    with _lock:
        _envios.clear()
        _contador = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(corpo: NovoEnvio):
    global _contador
    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == corpo.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

        cotacao = _buscar_cotacao(corpo.cotacao_id)

        valida_ate = cotacao.get("valida_ate")
        if not isinstance(valida_ate, str):
            raise _indisponivel("Resposta inválida do serviço de cotação.")
        if date.fromisoformat(corpo.data_hora[:10]) > date.fromisoformat(valida_ate):
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação expirou.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == corpo.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _contador + 1
        envio_id = "ENV-%06d" % numero
        codigo = "LG%09dBR" % numero
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

        _publicar("envio.criado", corpo.data_hora, {
            "envio_id": envio_id,
            "codigo_rastreio": codigo,
            "pedido_id": corpo.pedido_id,
            "transportadora_id": corpo.transportadora_id,
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": corpo.destinatario.email,
            "data_hora": corpo.data_hora,
        })

        _contador = numero
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
        return copy.deepcopy(_obter_ou_404(id))


@app.post("/envios/{id}/atualizacoes")
def atualizar_envio(id: str, corpo: Atualizacao):
    with _lock:
        atual = _obter_ou_404(id)
        if corpo.status not in TRANSICOES.get(atual["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        _checar_data_hora(atual, corpo.data_hora)

        novo = copy.deepcopy(atual)
        novos_itens = []
        item = {"status": corpo.status, "data_hora": corpo.data_hora, "local": corpo.local}
        novo["historico"].append(item)
        novo["status"] = corpo.status
        novos_itens.append(item)
        if corpo.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                dev = {"status": "DEVOLVIDO", "data_hora": corpo.data_hora, "local": corpo.local}
                novo["historico"].append(dev)
                novo["status"] = "DEVOLVIDO"
                novos_itens.append(dev)

        for it in novos_itens:
            _publicar("envio.status_atualizado", it["data_hora"], _evento_status(novo, it))

        _envios[id] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{id}/cancelar")
def cancelar_envio(id: str, corpo: Cancelamento):
    with _lock:
        atual = _obter_ou_404(id)
        if atual["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envios criados.")
        _checar_data_hora(atual, corpo.data_hora)

        novo = copy.deepcopy(atual)
        item = {"status": "CANCELADO", "data_hora": corpo.data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"

        _publicar("envio.status_atualizado", corpo.data_hora, _evento_status(novo, item))

        _envios[id] = novo
        return copy.deepcopy(novo)
