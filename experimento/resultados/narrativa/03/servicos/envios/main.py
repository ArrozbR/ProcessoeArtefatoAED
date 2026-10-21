# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AfterValidator, BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados invalidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


_RE_DATA_HORA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validar_data_hora(v: str) -> str:
    if not _RE_DATA_HORA.match(v):
        raise ValueError("data e hora mal formada")
    try:
        datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise ValueError("data e hora invalida")
    return v


def _validar_nao_vazio(v: str) -> str:
    if not v.strip():
        raise ValueError("texto vazio")
    return v


def _validar_email(v: str) -> str:
    if not _RE_EMAIL.match(v):
        raise ValueError("email invalido")
    return v


DataHora = Annotated[str, Field(strict=True), AfterValidator(_validar_data_hora)]
TextoNaoVazio = Annotated[str, Field(strict=True, min_length=1), AfterValidator(_validar_nao_vazio)]
Email = Annotated[str, Field(strict=True), AfterValidator(_validar_email)]


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


TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

MAX_FALHAS = 3

_cliente = httpx.Client(timeout=10.0)


def _indisponivel(msg: str = "Servico indisponivel."):
    return ErroAPI(503, "SERVICO_INDISPONIVEL", msg)


def _buscar_cotacao(cotacao_id: str) -> dict:
    url = os.environ.get("URL_COTACAO", "") + "/cotacoes/" + quote(cotacao_id, safe="")
    try:
        r = _cliente.get(url)
    except Exception:
        raise _indisponivel("Servico de cotacao indisponivel.")
    if r.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotacao nao encontrada.")
    if r.status_code != 200:
        raise _indisponivel("Servico de cotacao indisponivel.")
    try:
        dados = r.json()
    except Exception:
        raise _indisponivel("Resposta invalida do servico de cotacao.")
    if not isinstance(dados, dict):
        raise _indisponivel("Resposta invalida do servico de cotacao.")
    return dados


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    evento = {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}
    url = os.environ.get("URL_BARRAMENTO", "") + "/publicar"
    try:
        r = _cliente.post(url, json=evento)
    except Exception:
        raise _indisponivel("Barramento de eventos indisponivel.")
    if r.status_code >= 500:
        raise _indisponivel("Barramento de eventos indisponivel.")


def _dados_status(envio: dict, item: dict) -> dict:
    return {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    }


_trava = threading.RLock()
_envios: dict = {}
_contador = {"envio": 0}


def _numero(id_envio: str) -> int:
    return int(id_envio.split("-")[1])


@app.post("/admin/reset", status_code=204)
def reset():
    with _trava:
        _envios.clear()
        _contador["envio"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(corpo: NovoEnvio):
    with _trava:
        for e in _envios.values():
            if e["pedido_id"] == corpo.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido ja possui envio ativo.")

        cotacao = _buscar_cotacao(corpo.cotacao_id)

        valida_ate = cotacao.get("valida_ate")
        if not isinstance(valida_ate, str):
            raise _indisponivel("Resposta invalida do servico de cotacao.")
        if corpo.data_hora[:10] > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "Cotacao expirada.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == corpo.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora nao oferecida na cotacao.")

        numero = _contador["envio"] + 1
        id_envio = "ENV-%06d" % numero
        codigo = "LG%09dBR" % numero
        envio = {
            "id": id_envio,
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
            "envio_id": id_envio,
            "codigo_rastreio": codigo,
            "pedido_id": corpo.pedido_id,
            "transportadora_id": corpo.transportadora_id,
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": corpo.destinatario.email,
            "data_hora": corpo.data_hora,
        })

        _contador["envio"] = numero
        _envios[id_envio] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _trava:
        itens = [copy.deepcopy(e) for e in _envios.values() if e["pedido_id"] == pedido_id]
    itens.sort(key=lambda e: _numero(e["id"]))
    return itens


@app.get("/envios/{id}")
def obter_envio(id: str):
    with _trava:
        e = _envios.get(id)
        if e is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio nao encontrado.")
        return copy.deepcopy(e)


def _aplicar(envio: dict, novos_itens: list, novo_status: str, novas_falhas: int) -> dict:
    for item in novos_itens:
        _publicar("envio.status_atualizado", item["data_hora"], _dados_status(envio, item))
    envio["historico"].extend(novos_itens)
    envio["status"] = novo_status
    envio["tentativas_falhas"] = novas_falhas
    return copy.deepcopy(envio)


@app.post("/envios/{id}/atualizacoes")
def atualizar_envio(id: str, corpo: Atualizacao):
    with _trava:
        envio = _envios.get(id)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio nao encontrado.")
        if corpo.status not in TRANSICOES.get(envio["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transicao de status invalida.")
        if corpo.data_hora < envio["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao ultimo evento.")

        item = {"status": corpo.status, "data_hora": corpo.data_hora, "local": corpo.local}
        novos = [item]
        novo_status = corpo.status
        falhas = envio["tentativas_falhas"]
        if corpo.status == "FALHA_ENTREGA":
            falhas += 1
            if falhas >= MAX_FALHAS:
                novos.append({"status": "DEVOLVIDO", "data_hora": corpo.data_hora, "local": corpo.local})
                novo_status = "DEVOLVIDO"
        return _aplicar(envio, novos, novo_status, falhas)


@app.post("/envios/{id}/cancelar")
def cancelar_envio(id: str, corpo: Cancelamento):
    with _trava:
        envio = _envios.get(id)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio nao encontrado.")
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Somente envios criados podem ser cancelados.")
        if corpo.data_hora < envio["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao ultimo evento.")
        item = {"status": "CANCELADO", "data_hora": corpo.data_hora, "local": None}
        return _aplicar(envio, [item], "CANCELADO", envio["tentativas_falhas"])
