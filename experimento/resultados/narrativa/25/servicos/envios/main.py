# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AfterValidator, BaseModel, StrictStr
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
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


_RE_DATA_HORA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validar_data_hora(v: str) -> str:
    if not _RE_DATA_HORA.fullmatch(v):
        raise ValueError("data e hora inválidas")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def _nao_vazio(v: str) -> str:
    if not v.strip():
        raise ValueError("texto vazio")
    return v


def _validar_email(v: str) -> str:
    if not _RE_EMAIL.fullmatch(v):
        raise ValueError("email inválido")
    return v


DataHora = Annotated[StrictStr, AfterValidator(_validar_data_hora)]
TextoNaoVazio = Annotated[StrictStr, AfterValidator(_nao_vazio)]


class Destinatario(BaseModel):
    nome: TextoNaoVazio
    email: Annotated[StrictStr, AfterValidator(_validar_email)]


class EnvioEntrada(BaseModel):
    pedido_id: TextoNaoVazio
    cotacao_id: TextoNaoVazio
    transportadora_id: TextoNaoVazio
    destinatario: Destinatario
    data_hora: DataHora


class AtualizacaoEntrada(BaseModel):
    status: Literal["COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"]
    data_hora: DataHora
    local: TextoNaoVazio


class CancelamentoEntrada(BaseModel):
    data_hora: DataHora


TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_cliente = httpx.Client(timeout=10.0)
_lock = threading.Lock()
_envios = {}
_contador = {"n": 0}


def _indisponivel(nome: str):
    return ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço %s indisponível." % nome)


def _buscar_cotacao(cotacao_id: str):
    url = os.environ.get("URL_COTACAO", "") + "/cotacoes/" + quote(cotacao_id, safe="")
    try:
        resp = _cliente.get(url)
    except Exception:
        raise _indisponivel("de cotação")
    if resp.status_code >= 500:
        raise _indisponivel("de cotação")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        return None
    try:
        dados = resp.json()
    except Exception:
        raise _indisponivel("de cotação")
    if not isinstance(dados, dict):
        raise _indisponivel("de cotação")
    return dados


def _publicar(evento: dict):
    url = os.environ.get("URL_BARRAMENTO", "") + "/publicar"
    try:
        resp = _cliente.post(url, json=evento)
    except Exception:
        raise _indisponivel("do barramento")
    if resp.status_code >= 500:
        raise _indisponivel("do barramento")


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


def _obter(id: str) -> dict:
    envio = _envios.get(id)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return envio


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _contador["n"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(entrada: EnvioEntrada):
    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == entrada.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

        cotacao = _buscar_cotacao(entrada.cotacao_id)
        if cotacao is None:
            raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        valida_ate = cotacao.get("valida_ate")
        if not isinstance(valida_ate, str):
            raise _indisponivel("de cotação")
        if entrada.data_hora[:10] > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "Cotação expirada.")
        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == entrada.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _contador["n"] + 1
        envio = {
            "id": "ENV-%06d" % numero,
            "codigo_rastreio": "LG%09dBR" % numero,
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
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "transportadora_id": envio["transportadora_id"],
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": envio["destinatario"]["email"],
            "data_hora": entrada.data_hora,
        })
        _publicar(evento)
        _contador["n"] = numero
        _envios[envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str):
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
        atual = _obter(id)
        permitidos = TRANSICOES.get(atual["status"], set())
        if entrada.status not in permitidos:
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        if entrada.data_hora < atual["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        novo = copy.deepcopy(atual)
        eventos = []
        item = {"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}
        novo["historico"].append(item)
        novo["status"] = entrada.status
        eventos.append(_evento_status(novo, item))
        if entrada.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                devolucao = {"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local}
                novo["historico"].append(devolucao)
                novo["status"] = "DEVOLVIDO"
                eventos.append(_evento_status(novo, devolucao))

        for ev in eventos:
            _publicar(ev)
        _envios[id] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{id}/cancelar")
def cancelar_envio(id: str, entrada: CancelamentoEntrada):
    with _lock:
        atual = _obter(id)
        if atual["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio criado.")
        if entrada.data_hora < atual["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        novo = copy.deepcopy(atual)
        item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"
        _publicar(_evento_status(novo, item))
        _envios[id] = novo
        return copy.deepcopy(novo)
