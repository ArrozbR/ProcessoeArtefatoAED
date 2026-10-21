# arquivo: servicos/envios/main.py
import copy
import os
import re
import uuid
from datetime import date, datetime
from typing import Any

import httpx
from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RE_DATA_HORA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}
TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_estado = {"contador": 0, "envios": {}}


class FalhaPublicacao(Exception):
    pass


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def _parse_data_hora(v: Any):
    if not isinstance(v, str) or not RE_DATA_HORA.fullmatch(v):
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _texto_nao_vazio(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    envelope = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        resp = httpx.post(os.environ["URL_BARRAMENTO"] + "/publicar", json=envelope, timeout=10)
    except httpx.HTTPError as e:
        raise FalhaPublicacao() from e
    if resp.status_code >= 500:
        raise FalhaPublicacao()


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


@app.post("/admin/reset")
def reset():
    _estado["contador"] = 0
    _estado["envios"] = {}
    return Response(status_code=204)


def _validar_criacao(corpo: Any):
    if not isinstance(corpo, dict):
        return None
    if not _texto_nao_vazio(corpo.get("pedido_id")):
        return None
    for campo in ("cotacao_id", "transportadora_id"):
        v = corpo.get(campo)
        if not isinstance(v, str) or v == "":
            return None
    dest = corpo.get("destinatario")
    if not isinstance(dest, dict):
        return None
    if not _texto_nao_vazio(dest.get("nome")):
        return None
    email = dest.get("email")
    if not isinstance(email, str) or not RE_EMAIL.match(email):
        return None
    return _parse_data_hora(corpo.get("data_hora"))


@app.post("/envios")
def criar(corpo: Any = Body(None)):
    dh = _validar_criacao(corpo)
    if dh is None:
        return erro(422, "DADOS_INVALIDOS", "Dados do envio inválidos.")

    pedido_id = corpo["pedido_id"]
    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            return erro(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

    try:
        resp = httpx.get(
            os.environ["URL_COTACAO"] + "/cotacoes/" + corpo["cotacao_id"],
            timeout=10,
        )
    except httpx.HTTPError:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if resp.status_code == 404:
        return erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    try:
        cotacao = resp.json()
        valida_ate = date.fromisoformat(cotacao["valida_ate"])
        opcoes = cotacao["opcoes"]
    except (ValueError, KeyError, TypeError):
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")

    if dh.date() > valida_ate:
        return erro(422, "COTACAO_EXPIRADA", "A cotação expirou.")

    opcao = None
    for o in opcoes:
        if o.get("transportadora_id") == corpo["transportadora_id"]:
            opcao = o
            break
    if opcao is None:
        return erro(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

    numero = _estado["contador"] + 1
    envio = {
        "id": "ENV-%06d" % numero,
        "codigo_rastreio": "LG%09dBR" % numero,
        "pedido_id": pedido_id,
        "cotacao_id": corpo["cotacao_id"],
        "transportadora_id": corpo["transportadora_id"],
        "preco_centavos": opcao["preco_centavos"],
        "data_prevista_entrega": opcao["data_prevista_entrega"],
        "destinatario": {
            "nome": corpo["destinatario"]["nome"],
            "email": corpo["destinatario"]["email"],
        },
        "status": "CRIADO",
        "tentativas_falhas": 0,
        "historico": [{"status": "CRIADO", "data_hora": corpo["data_hora"], "local": None}],
    }
    dados = {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "transportadora_id": envio["transportadora_id"],
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": envio["destinatario"]["email"],
        "data_hora": corpo["data_hora"],
    }
    try:
        _publicar("envio.criado", corpo["data_hora"], dados)
    except FalhaPublicacao:
        return erro(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")

    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar(request: Request):
    if "pedido_id" not in request.query_params:
        return erro(422, "DADOS_INVALIDOS", "Parâmetro pedido_id obrigatório.")
    pedido_id = request.query_params["pedido_id"]
    lista = [
        copy.deepcopy(_estado["envios"][k])
        for k in sorted(_estado["envios"])
        if _estado["envios"][k]["pedido_id"] == pedido_id
    ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/envios/{eid}")
def consultar(eid: str):
    e = _estado["envios"].get(eid)
    if e is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=copy.deepcopy(e))


def _aplicar_e_publicar(envio: dict, novos_itens: list, novo_status: str, tentativas: int):
    for item in novos_itens:
        _publicar("envio.status_atualizado", item["data_hora"], _dados_status(envio, item))
    envio["historico"].extend(novos_itens)
    envio["status"] = novo_status
    envio["tentativas_falhas"] = tentativas


@app.post("/envios/{eid}/atualizacoes")
def atualizar(eid: str, corpo: Any = Body(None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Corpo inválido.")
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        return erro(422, "DADOS_INVALIDOS", "Status inválido.")
    dh = _parse_data_hora(corpo.get("data_hora"))
    if dh is None:
        return erro(422, "DADOS_INVALIDOS", "data_hora inválida.")
    local = corpo.get("local")
    if not _texto_nao_vazio(local):
        return erro(422, "DADOS_INVALIDOS", "local inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if status not in TRANSICOES.get(envio["status"], set()):
        return erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")

    ultimo = datetime.fromisoformat(envio["historico"][-1]["data_hora"])
    if dh < ultimo:
        return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último registro.")

    itens = [{"status": status, "data_hora": corpo["data_hora"], "local": local}]
    tentativas = envio["tentativas_falhas"]
    novo_status = status
    if status == "FALHA_ENTREGA":
        tentativas += 1
        if tentativas >= 3:
            itens.append({"status": "DEVOLVIDO", "data_hora": corpo["data_hora"], "local": local})
            novo_status = "DEVOLVIDO"

    try:
        _aplicar_e_publicar(envio, itens, novo_status, tentativas)
    except FalhaPublicacao:
        return erro(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{eid}/cancelar")
def cancelar(eid: str, corpo: Any = Body(None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Corpo inválido.")
    dh = _parse_data_hora(corpo.get("data_hora"))
    if dh is None:
        return erro(422, "DADOS_INVALIDOS", "data_hora inválida.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if envio["status"] != "CRIADO":
        return erro(409, "TRANSICAO_INVALIDA", "Só envios no estado CRIADO podem ser cancelados.")

    ultimo = datetime.fromisoformat(envio["historico"][-1]["data_hora"])
    if dh < ultimo:
        return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último registro.")

    itens = [{"status": "CANCELADO", "data_hora": corpo["data_hora"], "local": None}]
    try:
        _aplicar_e_publicar(envio, itens, "CANCELADO", envio["tentativas_falhas"])
    except FalhaPublicacao:
        return erro(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))
