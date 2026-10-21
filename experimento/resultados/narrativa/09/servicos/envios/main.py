# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AfterValidator, BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
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
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _validar_data_hora(v: str) -> str:
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def _nao_vazio(v: str) -> str:
    if not v.strip():
        raise ValueError("texto vazio")
    return v


_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validar_email(v: str) -> str:
    if not _RE_EMAIL.match(v):
        raise ValueError("email invalido")
    return v


DataHora = Annotated[
    str,
    Field(strict=True, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$"),
    AfterValidator(_validar_data_hora),
]
Texto = Annotated[str, Field(strict=True, min_length=1), AfterValidator(_nao_vazio)]
Email = Annotated[str, Field(strict=True), AfterValidator(_validar_email)]


class Destinatario(BaseModel):
    nome: Texto
    email: Email


class NovoEnvio(BaseModel):
    pedido_id: Texto
    cotacao_id: Texto
    transportadora_id: Texto
    destinatario: Destinatario
    data_hora: DataHora


class Atualizacao(BaseModel):
    status: Literal["COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"]
    data_hora: DataHora
    local: Texto


class Cancelamento(BaseModel):
    data_hora: DataHora


TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_cliente_trava = threading.Lock()
_cliente = None


def _http() -> httpx.Client:
    global _cliente
    with _cliente_trava:
        if _cliente is None:
            _cliente = httpx.Client(timeout=10.0)
        return _cliente


def _indisponivel(nome: str) -> ErroAPI:
    return ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico de %s indisponivel." % nome)


def _buscar_cotacao(cotacao_id: str):
    base = os.environ.get("URL_COTACAO", "")
    try:
        resp = _http().get(base + "/cotacoes/" + cotacao_id)
    except httpx.HTTPError:
        raise _indisponivel("cotacao")
    if resp.status_code >= 500:
        raise _indisponivel("cotacao")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise _indisponivel("cotacao")
    try:
        dados = resp.json()
    except ValueError:
        raise _indisponivel("cotacao")
    if not isinstance(dados, dict):
        raise _indisponivel("cotacao")
    return dados


def _publicar(eventos):
    base = os.environ.get("URL_BARRAMENTO", "")
    for evento in eventos:
        try:
            resp = _http().post(base + "/publicar", json=evento)
        except httpx.HTTPError:
            raise _indisponivel("barramento")
        if resp.status_code >= 500:
            raise _indisponivel("barramento")


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


_trava = threading.RLock()
_envios = {}
_contador = 0


@app.post("/admin/reset", status_code=204)
def reset():
    global _contador
    with _trava:
        _envios.clear()
        _contador = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(corpo: NovoEnvio):
    global _contador
    with _trava:
        for e in _envios.values():
            if e["pedido_id"] == corpo.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido ja possui envio ativo.")

        cotacao = _buscar_cotacao(corpo.cotacao_id)
        if cotacao is None:
            raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotacao nao encontrada.")
        if corpo.data_hora[:10] > str(cotacao.get("valida_ate", "")):
            raise ErroAPI(422, "COTACAO_EXPIRADA", "Cotacao expirada.")
        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == corpo.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora nao oferecida na cotacao.")

        numero = _contador + 1
        envio = {
            "id": "ENV-%06d" % numero,
            "codigo_rastreio": "LG%09dBR" % numero,
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
        evento = _evento("envio.criado", corpo.data_hora, {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "transportadora_id": envio["transportadora_id"],
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": envio["destinatario"]["email"],
            "data_hora": corpo.data_hora,
        })
        _publicar([evento])
        _contador = numero
        _envios[envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _trava:
        itens = [copy.deepcopy(e) for k, e in sorted(_envios.items()) if e["pedido_id"] == pedido_id]
    return itens


@app.get("/envios/{envio_id}")
def obter_envio(envio_id: str):
    with _trava:
        e = _envios.get(envio_id)
        if e is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio nao encontrado.")
        return copy.deepcopy(e)


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar_envio(envio_id: str, corpo: Atualizacao):
    with _trava:
        e = _envios.get(envio_id)
        if e is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio nao encontrado.")
        if corpo.status not in TRANSICOES.get(e["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transicao de status invalida.")
        if corpo.data_hora < e["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao ultimo registro.")

        novo = copy.deepcopy(e)
        item = {"status": corpo.status, "data_hora": corpo.data_hora, "local": corpo.local}
        novo["historico"].append(item)
        novo["status"] = corpo.status
        eventos = [_evento_status(novo, item)]
        if corpo.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                devolvido = {"status": "DEVOLVIDO", "data_hora": corpo.data_hora, "local": corpo.local}
                novo["historico"].append(devolvido)
                novo["status"] = "DEVOLVIDO"
                eventos.append(_evento_status(novo, devolvido))
        _publicar(eventos)
        _envios[envio_id] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{envio_id}/cancelar")
def cancelar_envio(envio_id: str, corpo: Cancelamento):
    with _trava:
        e = _envios.get(envio_id)
        if e is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio nao encontrado.")
        if e["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Envio so pode ser cancelado enquanto CRIADO.")
        if corpo.data_hora < e["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao ultimo registro.")

        novo = copy.deepcopy(e)
        item = {"status": "CANCELADO", "data_hora": corpo.data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"
        _publicar([_evento_status(novo, item)])
        _envios[envio_id] = novo
        return copy.deepcopy(novo)
