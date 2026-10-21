# arquivo: servicos/envios/main.py
import copy
import datetime
import os
import re
import urllib.parse
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATA_HORA_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}
TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_estado = {"envios": {}, "contador": 0}

_INVALIDO = object()


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


async def _ler_json(request):
    try:
        return await request.json()
    except Exception:
        return _INVALIDO


def _ler_data_hora(texto):
    if not isinstance(texto, str) or not DATA_HORA_RE.fullmatch(texto):
        return None
    try:
        return datetime.datetime.strptime(texto, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _texto_nao_vazio(v):
    return isinstance(v, str) and bool(v.strip())


def _publicar(tipo, ocorrido_em, dados):
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        httpx.post(os.environ["URL_BARRAMENTO"] + "/publicar", json=evento, timeout=10.0)
    except Exception:
        pass


def _publicar_status(envio, item):
    _publicar("envio.status_atualizado", item["data_hora"], {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    })


def _validar_criacao(corpo):
    if not isinstance(corpo, dict):
        return "Corpo deve ser um objeto JSON."
    if not _texto_nao_vazio(corpo.get("pedido_id")):
        return "Campo 'pedido_id' inválido."
    for campo in ("cotacao_id", "transportadora_id"):
        v = corpo.get(campo)
        if not isinstance(v, str) or v == "":
            return f"Campo '{campo}' inválido."
    dest = corpo.get("destinatario")
    if not isinstance(dest, dict):
        return "Campo 'destinatario' inválido."
    if not _texto_nao_vazio(dest.get("nome")):
        return "Campo 'destinatario.nome' inválido."
    email = dest.get("email")
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        return "Campo 'destinatario.email' inválido."
    if _ler_data_hora(corpo.get("data_hora")) is None:
        return "Campo 'data_hora' inválido."
    return None


@app.post("/envios")
async def criar(request: Request):
    corpo = await _ler_json(request)
    if corpo is _INVALIDO:
        return _erro(422, "DADOS_INVALIDOS", "Corpo JSON inválido.")
    problema = _validar_criacao(corpo)
    if problema:
        return _erro(422, "DADOS_INVALIDOS", problema)

    pedido_id = corpo["pedido_id"]
    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            return _erro(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

    try:
        url = os.environ["URL_COTACAO"] + "/cotacoes/" + urllib.parse.quote(corpo["cotacao_id"], safe="")
        resp = httpx.get(url, timeout=10.0)
    except Exception:
        return _erro(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    if resp.status_code == 404:
        return _erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        return _erro(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    try:
        cotacao = resp.json()
        valida_ate = cotacao["valida_ate"]
        opcoes = cotacao["opcoes"]
    except Exception:
        return _erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")

    data_envio = corpo["data_hora"][:10]
    if data_envio > valida_ate:
        return _erro(422, "COTACAO_EXPIRADA", "A cotação expirou.")

    opcao = None
    for o in opcoes:
        if o.get("transportadora_id") == corpo["transportadora_id"]:
            opcao = o
            break
    if opcao is None:
        return _erro(422, "TRANSPORTADORA_NAO_OFERECIDA", "A transportadora não está entre as opções da cotação.")

    _estado["contador"] += 1
    n = _estado["contador"]
    envio = {
        "id": f"ENV-{n:06d}",
        "codigo_rastreio": f"LG{n:09d}BR",
        "pedido_id": pedido_id,
        "cotacao_id": corpo["cotacao_id"],
        "transportadora_id": corpo["transportadora_id"],
        "preco_centavos": opcao["preco_centavos"],
        "data_prevista_entrega": opcao["data_prevista_entrega"],
        "destinatario": {"nome": corpo["destinatario"]["nome"], "email": corpo["destinatario"]["email"]},
        "status": "CRIADO",
        "tentativas_falhas": 0,
        "historico": [{"status": "CRIADO", "data_hora": corpo["data_hora"], "local": None}],
    }
    _estado["envios"][envio["id"]] = envio

    _publicar("envio.criado", corpo["data_hora"], {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "transportadora_id": envio["transportadora_id"],
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": envio["destinatario"]["email"],
        "data_hora": corpo["data_hora"],
    })
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios/{eid}")
async def consultar(eid: str):
    e = _estado["envios"].get(eid)
    if e is None:
        return _erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=copy.deepcopy(e))


@app.get("/envios")
async def listar(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        return _erro(422, "DADOS_INVALIDOS", "Parâmetro 'pedido_id' é obrigatório.")
    itens = [
        copy.deepcopy(_estado["envios"][k])
        for k in sorted(_estado["envios"])
        if _estado["envios"][k]["pedido_id"] == pedido_id
    ]
    return JSONResponse(status_code=200, content=itens)


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    corpo = await _ler_json(request)
    if corpo is _INVALIDO or not isinstance(corpo, dict):
        return _erro(422, "DADOS_INVALIDOS", "Corpo JSON inválido.")
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        return _erro(422, "DADOS_INVALIDOS", "Campo 'status' inválido.")
    data_hora = _ler_data_hora(corpo.get("data_hora"))
    if data_hora is None:
        return _erro(422, "DADOS_INVALIDOS", "Campo 'data_hora' inválido.")
    local = corpo.get("local")
    if not _texto_nao_vazio(local):
        return _erro(422, "DADOS_INVALIDOS", "Campo 'local' inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return _erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if status not in TRANSICOES.get(envio["status"], set()):
        return _erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
    ultimo = _ler_data_hora(envio["historico"][-1]["data_hora"])
    if data_hora < ultimo:
        return _erro(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

    novos = [{"status": status, "data_hora": corpo["data_hora"], "local": local}]
    envio["historico"].append(novos[0])
    envio["status"] = status
    if status == "FALHA_ENTREGA":
        envio["tentativas_falhas"] += 1
        if envio["tentativas_falhas"] >= 3:
            item = {"status": "DEVOLVIDO", "data_hora": corpo["data_hora"], "local": local}
            envio["historico"].append(item)
            envio["status"] = "DEVOLVIDO"
            novos.append(item)

    for item in novos:
        _publicar_status(envio, item)
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    corpo = await _ler_json(request)
    if corpo is _INVALIDO or not isinstance(corpo, dict):
        return _erro(422, "DADOS_INVALIDOS", "Corpo JSON inválido.")
    data_hora = _ler_data_hora(corpo.get("data_hora"))
    if data_hora is None:
        return _erro(422, "DADOS_INVALIDOS", "Campo 'data_hora' inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return _erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if envio["status"] != "CRIADO":
        return _erro(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")
    ultimo = _ler_data_hora(envio["historico"][-1]["data_hora"])
    if data_hora < ultimo:
        return _erro(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

    item = {"status": "CANCELADO", "data_hora": corpo["data_hora"], "local": None}
    envio["historico"].append(item)
    envio["status"] = "CANCELADO"
    _publicar_status(envio, item)
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/admin/reset")
async def reset():
    _estado["envios"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)
