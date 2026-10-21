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

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATA_HORA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}
TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_estado = {"contador": 0, "envios": {}}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request, exc):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request, exc):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


class Invalido(Exception):
    pass


class Indisponivel(Exception):
    pass


class NaoEncontrada(Exception):
    pass


async def ler_json(request):
    try:
        corpo = await request.json()
    except Exception:
        raise Invalido()
    if not isinstance(corpo, dict):
        raise Invalido()
    return corpo


def validar_data_hora(v):
    if not isinstance(v, str) or not DATA_HORA_RE.match(v):
        raise Invalido()
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise Invalido()


def texto_nao_vazio(v, permitir_espacos=False):
    if not isinstance(v, str) or v == "":
        raise Invalido()
    if not permitir_espacos and not v.strip():
        raise Invalido()
    return v


def publicar(tipo, ocorrido_em, dados):
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        url = os.environ["URL_BARRAMENTO"]
        httpx.post(url + "/publicar", json=evento, timeout=10.0)
    except Exception:
        pass


def obter_cotacao(cotacao_id):
    try:
        url = os.environ["URL_COTACAO"]
        resp = httpx.get(url + "/cotacoes/" + cotacao_id, timeout=10.0)
    except Exception:
        raise Indisponivel()
    if resp.status_code == 404:
        raise NaoEncontrada()
    if resp.status_code != 200:
        raise Indisponivel()
    try:
        dados = resp.json()
    except Exception:
        raise Indisponivel()
    if not isinstance(dados, dict):
        raise Indisponivel()
    return dados


def evento_status(envio, item):
    return {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    }


@app.post("/envios")
async def criar(request: Request):
    try:
        corpo = await ler_json(request)
        pedido_id = texto_nao_vazio(corpo.get("pedido_id"))
        cotacao_id = texto_nao_vazio(corpo.get("cotacao_id"), permitir_espacos=True)
        transportadora_id = texto_nao_vazio(corpo.get("transportadora_id"), permitir_espacos=True)
        dest = corpo.get("destinatario")
        if not isinstance(dest, dict):
            raise Invalido()
        nome = texto_nao_vazio(dest.get("nome"))
        email = dest.get("email")
        if not isinstance(email, str) or not EMAIL_RE.match(email):
            raise Invalido()
        data_hora_txt = corpo.get("data_hora")
        data_hora = validar_data_hora(data_hora_txt)
    except Invalido:
        return erro(422, "DADOS_INVALIDOS", "Dados do envio inválidos.")

    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            return erro(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

    try:
        cotacao = obter_cotacao(cotacao_id)
    except NaoEncontrada:
        return erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    except Indisponivel:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")

    try:
        valida_ate = date.fromisoformat(cotacao["valida_ate"])
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de cotação.")
    if data_hora.date() > valida_ate:
        return erro(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in cotacao.get("opcoes") or []:
        if o.get("transportadora_id") == transportadora_id:
            opcao = o
            break
    if opcao is None:
        return erro(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

    _estado["contador"] += 1
    n = _estado["contador"]
    envio = {
        "id": "ENV-%06d" % n,
        "codigo_rastreio": "LG%09dBR" % n,
        "pedido_id": pedido_id,
        "cotacao_id": cotacao_id,
        "transportadora_id": transportadora_id,
        "preco_centavos": opcao["preco_centavos"],
        "data_prevista_entrega": opcao["data_prevista_entrega"],
        "destinatario": {"nome": nome, "email": email},
        "status": "CRIADO",
        "tentativas_falhas": 0,
        "historico": [{"status": "CRIADO", "data_hora": data_hora_txt, "local": None}],
    }
    _estado["envios"][envio["id"]] = envio

    publicar("envio.criado", data_hora_txt, {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": pedido_id,
        "transportadora_id": transportadora_id,
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": email,
        "data_hora": data_hora_txt,
    })
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        return erro(422, "DADOS_INVALIDOS", "Parâmetro pedido_id obrigatório.")
    lista = [
        copy.deepcopy(e)
        for k, e in sorted(_estado["envios"].items())
        if e["pedido_id"] == pedido_id
    ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/envios/{eid}")
async def consultar(eid: str):
    e = _estado["envios"].get(eid)
    if e is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=copy.deepcopy(e))


def data_hora_ultimo(envio):
    return datetime.strptime(envio["historico"][-1]["data_hora"], "%Y-%m-%dT%H:%M:%S")


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    try:
        corpo = await ler_json(request)
        status = corpo.get("status")
        if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
            raise Invalido()
        data_hora_txt = corpo.get("data_hora")
        data_hora = validar_data_hora(data_hora_txt)
        local = corpo.get("local")
        if not isinstance(local, str) or local == "":
            raise Invalido()
    except Invalido:
        return erro(422, "DADOS_INVALIDOS", "Dados da atualização inválidos.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if status not in TRANSICOES.get(envio["status"], set()):
        return erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")

    if data_hora < data_hora_ultimo(envio):
        return erro(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

    novos = [{"status": status, "data_hora": data_hora_txt, "local": local}]
    envio["status"] = status
    if status == "FALHA_ENTREGA":
        envio["tentativas_falhas"] += 1
        if envio["tentativas_falhas"] >= 3:
            novos.append({"status": "DEVOLVIDO", "data_hora": data_hora_txt, "local": local})
            envio["status"] = "DEVOLVIDO"
    envio["historico"].extend(novos)

    for item in novos:
        publicar("envio.status_atualizado", item["data_hora"], evento_status(envio, item))
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    try:
        corpo = await ler_json(request)
        data_hora_txt = corpo.get("data_hora")
        data_hora = validar_data_hora(data_hora_txt)
    except Invalido:
        return erro(422, "DADOS_INVALIDOS", "Dados do cancelamento inválidos.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if envio["status"] != "CRIADO":
        return erro(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio CRIADO.")

    if data_hora < data_hora_ultimo(envio):
        return erro(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

    item = {"status": "CANCELADO", "data_hora": data_hora_txt, "local": None}
    envio["status"] = "CANCELADO"
    envio["historico"].append(item)
    publicar("envio.status_atualizado", data_hora_txt, evento_status(envio, item))
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["envios"].clear()
    return Response(status_code=204)
