# arquivo: servicos/envios/main.py
import copy
import os
import re
import uuid
from datetime import date, datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RE_DATA_HORA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}

TRANSICOES = {
    ("CRIADO", "COLETADO"),
    ("COLETADO", "EM_TRANSITO"),
    ("EM_TRANSITO", "EM_TRANSITO"),
    ("EM_TRANSITO", "SAIU_PARA_ENTREGA"),
    ("SAIU_PARA_ENTREGA", "ENTREGUE"),
    ("SAIU_PARA_ENTREGA", "FALHA_ENTREGA"),
    ("FALHA_ENTREGA", "SAIU_PARA_ENTREGA"),
}

_estado = {"contador": 0, "envios": {}}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def _ler_json(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def _parse_data_hora(valor):
    if not isinstance(valor, str) or not RE_DATA_HORA.fullmatch(valor):
        return None
    try:
        return datetime.strptime(valor, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _texto_nao_vazio(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _validar_criacao(corpo) -> bool:
    if not isinstance(corpo, dict):
        return False
    if not _texto_nao_vazio(corpo.get("pedido_id")):
        return False
    for campo in ("cotacao_id", "transportadora_id"):
        v = corpo.get(campo)
        if not isinstance(v, str) or v == "":
            return False
    dest = corpo.get("destinatario")
    if not isinstance(dest, dict):
        return False
    if not _texto_nao_vazio(dest.get("nome")):
        return False
    email = dest.get("email")
    if not isinstance(email, str) or not RE_EMAIL.match(email):
        return False
    if _parse_data_hora(corpo.get("data_hora")) is None:
        return False
    return True


def _publicar(envelopes: list) -> None:
    url = f"{os.environ['URL_BARRAMENTO']}/publicar"
    for env in envelopes:
        httpx.post(url, json=env, timeout=10)


def _envelope(tipo: str, ocorrido_em: str, dados: dict) -> dict:
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def _evento_status(envio: dict, item: dict) -> dict:
    return _envelope("envio.status_atualizado", item["data_hora"], {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    })


@app.post("/envios")
async def criar(request: Request):
    corpo = await _ler_json(request)
    if not _validar_criacao(corpo):
        return erro(422, "DADOS_INVALIDOS", "Dados do envio inválidos.")

    pedido_id = corpo["pedido_id"]
    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            return erro(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

    try:
        resp = httpx.get(f"{os.environ['URL_COTACAO']}/cotacoes/{corpo['cotacao_id']}", timeout=10)
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if resp.status_code == 404:
        return erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    try:
        cotacao = resp.json()
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de cotação.")

    data_hora = corpo["data_hora"]
    if _parse_data_hora(data_hora).date() > date.fromisoformat(cotacao["valida_ate"]):
        return erro(422, "COTACAO_EXPIRADA", "A cotação expirou.")

    opcao = None
    for o in cotacao.get("opcoes", []):
        if o.get("transportadora_id") == corpo["transportadora_id"]:
            opcao = o
            break
    if opcao is None:
        return erro(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida nesta cotação.")

    numero = _estado["contador"] + 1
    envio = {
        "id": f"ENV-{numero:06d}",
        "codigo_rastreio": f"LG{numero:09d}BR",
        "pedido_id": pedido_id,
        "cotacao_id": corpo["cotacao_id"],
        "transportadora_id": corpo["transportadora_id"],
        "preco_centavos": opcao["preco_centavos"],
        "data_prevista_entrega": opcao["data_prevista_entrega"],
        "destinatario": {"nome": corpo["destinatario"]["nome"], "email": corpo["destinatario"]["email"]},
        "status": "CRIADO",
        "tentativas_falhas": 0,
        "historico": [{"status": "CRIADO", "data_hora": data_hora, "local": None}],
    }
    evento = _envelope("envio.criado", data_hora, {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": pedido_id,
        "transportadora_id": envio["transportadora_id"],
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": envio["destinatario"]["email"],
        "data_hora": data_hora,
    })

    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio
    _publicar([evento])
    return JSONResponse(status_code=201, content=envio)


@app.get("/envios")
async def listar(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        return erro(422, "DADOS_INVALIDOS", "Parâmetro pedido_id é obrigatório.")
    lista = [e for e in _estado["envios"].values() if e["pedido_id"] == pedido_id]
    lista.sort(key=lambda e: e["id"])
    return JSONResponse(status_code=200, content=lista)


@app.get("/envios/{eid}")
async def consultar(eid: str):
    e = _estado["envios"].get(eid)
    if e is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=e)


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    corpo = await _ler_json(request)
    if (
        not isinstance(corpo, dict)
        or not isinstance(corpo.get("status"), str)
        or corpo.get("status") not in STATUS_ATUALIZACAO
        or _parse_data_hora(corpo.get("data_hora")) is None
        or not _texto_nao_vazio(corpo.get("local"))
    ):
        return erro(422, "DADOS_INVALIDOS", "Dados da atualização inválidos.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    novo = corpo["status"]
    if (envio["status"], novo) not in TRANSICOES:
        return erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")

    data_hora = corpo["data_hora"]
    ultimo = envio["historico"][-1]["data_hora"]
    if _parse_data_hora(data_hora) < _parse_data_hora(ultimo):
        return erro(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

    local = corpo["local"]
    atualizado = copy.deepcopy(envio)
    novos_itens = [{"status": novo, "data_hora": data_hora, "local": local}]
    atualizado["status"] = novo
    if novo == "FALHA_ENTREGA":
        atualizado["tentativas_falhas"] += 1
        if atualizado["tentativas_falhas"] >= 3:
            novos_itens.append({"status": "DEVOLVIDO", "data_hora": data_hora, "local": local})
            atualizado["status"] = "DEVOLVIDO"
    atualizado["historico"].extend(novos_itens)

    eventos = [_evento_status(atualizado, item) for item in novos_itens]
    _estado["envios"][eid] = atualizado
    _publicar(eventos)
    return JSONResponse(status_code=200, content=atualizado)


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    corpo = await _ler_json(request)
    if not isinstance(corpo, dict) or _parse_data_hora(corpo.get("data_hora")) is None:
        return erro(422, "DADOS_INVALIDOS", "Dados do cancelamento inválidos.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if envio["status"] != "CRIADO":
        return erro(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio não coletado.")

    data_hora = corpo["data_hora"]
    ultimo = envio["historico"][-1]["data_hora"]
    if _parse_data_hora(data_hora) < _parse_data_hora(ultimo):
        return erro(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

    atualizado = copy.deepcopy(envio)
    item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
    atualizado["status"] = "CANCELADO"
    atualizado["historico"].append(item)

    evento = _evento_status(atualizado, item)
    _estado["envios"][eid] = atualizado
    _publicar([evento])
    return JSONResponse(status_code=200, content=atualizado)


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["envios"].clear()
    return Response(status_code=204)
