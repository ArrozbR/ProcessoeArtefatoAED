# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import Body, FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resp_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return _resp_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resp_erro(422, "DADOS_INVALIDOS", "Dados invalidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resp_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _invalido(msg: str = "Dados invalidos.") -> None:
    raise ErroAPI(422, "DADOS_INVALIDOS", msg)


def _indisponivel() -> None:
    raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico dependente indisponivel.")


RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RE_DATA_HORA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}
TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}
MAX_FALHAS = 3

_lock = threading.RLock()
_envios: dict = {}
_contador = {"valor": 0}
_cliente = httpx.Client(timeout=10.0)


def _texto_nao_vazio(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _parse_data_hora(v: Any) -> datetime:
    if not isinstance(v, str) or not RE_DATA_HORA.match(v):
        _invalido("Campo 'data_hora' invalido.")
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        _invalido("Campo 'data_hora' invalido.")


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    base = os.environ.get("URL_BARRAMENTO", "")
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        r = _cliente.post(f"{base}/publicar", json=evento)
    except Exception:
        _indisponivel()
    if r.status_code >= 300:
        _indisponivel()


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


def _buscar_cotacao(cotacao_id: str) -> dict:
    base = os.environ.get("URL_COTACAO", "")
    try:
        r = _cliente.get(f"{base}/cotacoes/{quote(cotacao_id, safe='')}")
    except Exception:
        _indisponivel()
    if r.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotacao nao encontrada.")
    if r.status_code != 200:
        _indisponivel()
    try:
        dados = r.json()
    except ValueError:
        _indisponivel()
    if not isinstance(dados, dict):
        _indisponivel()
    return dados


def _obter_envio(eid: str) -> dict:
    e = _envios.get(eid)
    if e is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio nao encontrado.")
    return e


def _ultimo_data_hora(envio: dict) -> datetime:
    return datetime.strptime(envio["historico"][-1]["data_hora"], "%Y-%m-%dT%H:%M:%S")


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(corpo: Any = Body(None)):
    if not isinstance(corpo, dict):
        _invalido("Corpo deve ser um objeto JSON.")
    pedido_id = corpo.get("pedido_id")
    cotacao_id = corpo.get("cotacao_id")
    transportadora_id = corpo.get("transportadora_id")
    for nome, v in (("pedido_id", pedido_id), ("cotacao_id", cotacao_id), ("transportadora_id", transportadora_id)):
        if not _texto_nao_vazio(v):
            _invalido(f"Campo '{nome}' invalido.")
    dest = corpo.get("destinatario")
    if not isinstance(dest, dict):
        _invalido("Campo 'destinatario' invalido.")
    nome_dest = dest.get("nome")
    email = dest.get("email")
    if not _texto_nao_vazio(nome_dest):
        _invalido("Campo 'destinatario.nome' invalido.")
    if not isinstance(email, str) or not RE_EMAIL.match(email):
        _invalido("Campo 'destinatario.email' invalido.")
    data_hora = corpo.get("data_hora")
    dh = _parse_data_hora(data_hora)

    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "Pedido ja possui envio ativo.")

        cotacao = _buscar_cotacao(cotacao_id)
        valida_ate = cotacao.get("valida_ate")
        if isinstance(valida_ate, str) and dh.date().isoformat() > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "Cotacao expirada.")
        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora nao oferecida na cotacao.")

        numero = _contador["valor"] + 1
        eid = f"ENV-{numero:06d}"
        codigo = f"LG{numero:09d}BR"
        envio = {
            "id": eid,
            "codigo_rastreio": codigo,
            "pedido_id": pedido_id,
            "cotacao_id": cotacao_id,
            "transportadora_id": transportadora_id,
            "preco_centavos": opcao.get("preco_centavos"),
            "data_prevista_entrega": opcao.get("data_prevista_entrega"),
            "destinatario": {"nome": nome_dest, "email": email},
            "status": "CRIADO",
            "tentativas_falhas": 0,
            "historico": [{"status": "CRIADO", "data_hora": data_hora, "local": None}],
        }
        _publicar("envio.criado", data_hora, {
            "envio_id": eid,
            "codigo_rastreio": codigo,
            "pedido_id": pedido_id,
            "transportadora_id": transportadora_id,
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": email,
            "data_hora": data_hora,
        })
        _contador["valor"] = numero
        _envios[eid] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _lock:
        itens = [copy.deepcopy(e) for k, e in sorted(_envios.items()) if e["pedido_id"] == pedido_id]
    return itens


@app.get("/envios/{eid}")
def obter_envio(eid: str):
    with _lock:
        return copy.deepcopy(_obter_envio(eid))


@app.post("/envios/{eid}/atualizacoes")
def atualizar_envio(eid: str, corpo: Any = Body(None)):
    if not isinstance(corpo, dict):
        _invalido("Corpo deve ser um objeto JSON.")
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        _invalido("Campo 'status' invalido.")
    data_hora = corpo.get("data_hora")
    dh = _parse_data_hora(data_hora)
    local = corpo.get("local")
    if not _texto_nao_vazio(local):
        _invalido("Campo 'local' invalido.")

    with _lock:
        envio = _obter_envio(eid)
        if status not in TRANSICOES.get(envio["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transicao de status invalida.")
        if dh < _ultimo_data_hora(envio):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao ultimo evento.")

        novo = copy.deepcopy(envio)
        itens_novos = [{"status": status, "data_hora": data_hora, "local": local}]
        novo["status"] = status
        if status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= MAX_FALHAS:
                itens_novos.append({"status": "DEVOLVIDO", "data_hora": data_hora, "local": local})
                novo["status"] = "DEVOLVIDO"
        novo["historico"].extend(itens_novos)

        for item in itens_novos:
            _publicar("envio.status_atualizado", data_hora, _evento_status(novo, item))

        _envios[eid] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{eid}/cancelar")
def cancelar_envio(eid: str, corpo: Any = Body(None)):
    if not isinstance(corpo, dict):
        _invalido("Corpo deve ser um objeto JSON.")
    data_hora = corpo.get("data_hora")
    dh = _parse_data_hora(data_hora)

    with _lock:
        envio = _obter_envio(eid)
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Envio so pode ser cancelado enquanto CRIADO.")
        if dh < _ultimo_data_hora(envio):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao ultimo evento.")

        novo = copy.deepcopy(envio)
        item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
        novo["status"] = "CANCELADO"
        novo["historico"].append(item)

        _publicar("envio.status_atualizado", data_hora, _evento_status(novo, item))

        _envios[eid] = novo
        return copy.deepcopy(novo)
