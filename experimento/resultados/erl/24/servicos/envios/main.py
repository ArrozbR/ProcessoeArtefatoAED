# arquivo: servicos/envios/main.py
import copy
import datetime
import json
import os
import re
import threading
import urllib.parse
import uuid

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
    ("CRIADO", "COLETADO"),
    ("COLETADO", "EM_TRANSITO"),
    ("EM_TRANSITO", "EM_TRANSITO"),
    ("EM_TRANSITO", "SAIU_PARA_ENTREGA"),
    ("SAIU_PARA_ENTREGA", "ENTREGUE"),
    ("SAIU_PARA_ENTREGA", "FALHA_ENTREGA"),
    ("FALHA_ENTREGA", "SAIU_PARA_ENTREGA"),
}

_lock = threading.RLock()
_envios = {}
_contador = 0


class FalhaBarramento(Exception):
    pass


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request, exc):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _http(request, exc):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def data_hora_valida(s):
    if not isinstance(s, str) or not DATA_HORA_RE.fullmatch(s):
        return False
    try:
        datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
        return True
    except ValueError:
        return False


async def ler_json(request):
    try:
        raw = await request.body()
        if not raw:
            return None, False
        return json.loads(raw), True
    except Exception:
        return None, False


def publicar(tipo, ocorrido_em, dados):
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        resp = httpx.post(os.environ["URL_BARRAMENTO"] + "/publicar", json=evento, timeout=10.0)
    except Exception:
        raise FalhaBarramento()
    if resp.status_code >= 300:
        raise FalhaBarramento()


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


def validar_criacao(corpo):
    if not isinstance(corpo, dict):
        return False
    pedido_id = corpo.get("pedido_id")
    if not isinstance(pedido_id, str) or not pedido_id.strip():
        return False
    cotacao_id = corpo.get("cotacao_id")
    if not isinstance(cotacao_id, str) or cotacao_id == "":
        return False
    tid = corpo.get("transportadora_id")
    if not isinstance(tid, str) or tid == "":
        return False
    dest = corpo.get("destinatario")
    if not isinstance(dest, dict):
        return False
    nome = dest.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return False
    email = dest.get("email")
    if not isinstance(email, str) or not EMAIL_RE.fullmatch(email):
        return False
    if not data_hora_valida(corpo.get("data_hora")):
        return False
    return True


@app.post("/envios")
async def criar(request: Request):
    global _contador
    corpo, ok = await ler_json(request)
    if not ok or not validar_criacao(corpo):
        return dados_invalidos("Corpo fora das restrições.")

    pedido_id = corpo["pedido_id"]
    cotacao_id = corpo["cotacao_id"]
    tid = corpo["transportadora_id"]
    data_hora = corpo["data_hora"]
    destinatario = {"nome": corpo["destinatario"]["nome"], "email": corpo["destinatario"]["email"]}

    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                return erro(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

        try:
            resp = httpx.get(
                os.environ["URL_COTACAO"] + "/cotacoes/" + urllib.parse.quote(cotacao_id, safe=""),
                timeout=10.0,
            )
        except Exception:
            return erro(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
        if resp.status_code == 404:
            return erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        if resp.status_code != 200:
            return erro(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
        try:
            cotacao = resp.json()
            valida_ate = cotacao["valida_ate"]
            opcoes = cotacao["opcoes"]
        except Exception:
            return erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")

        if data_hora[:10] > valida_ate:
            return erro(422, "COTACAO_EXPIRADA", "A cotação expirou.")

        opcao = None
        for o in opcoes:
            if o.get("transportadora_id") == tid:
                opcao = o
                break
        if opcao is None:
            return erro(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não está entre as opções da cotação.")

        numero = _contador + 1
        envio = {
            "id": "ENV-%06d" % numero,
            "codigo_rastreio": "LG%09dBR" % numero,
            "pedido_id": pedido_id,
            "cotacao_id": cotacao_id,
            "transportadora_id": tid,
            "preco_centavos": opcao["preco_centavos"],
            "data_prevista_entrega": opcao["data_prevista_entrega"],
            "destinatario": destinatario,
            "status": "CRIADO",
            "tentativas_falhas": 0,
            "historico": [{"status": "CRIADO", "data_hora": data_hora, "local": None}],
        }
        dados = {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": pedido_id,
            "transportadora_id": tid,
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": destinatario["email"],
            "data_hora": data_hora,
        }
        try:
            publicar("envio.criado", data_hora, dados)
        except FalhaBarramento:
            return erro(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")
        _contador = numero
        _envios[envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar(request: Request):
    if "pedido_id" not in request.query_params:
        return dados_invalidos("Parâmetro pedido_id é obrigatório.")
    pedido_id = request.query_params["pedido_id"]
    with _lock:
        itens = [copy.deepcopy(e) for k, e in sorted(_envios.items()) if e["pedido_id"] == pedido_id]
    return JSONResponse(status_code=200, content=itens)


@app.get("/envios/{eid}")
async def consultar(eid: str):
    with _lock:
        e = _envios.get(eid)
        if e is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        return JSONResponse(status_code=200, content=copy.deepcopy(e))


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    corpo, ok = await ler_json(request)
    if not ok or not isinstance(corpo, dict):
        return dados_invalidos("Corpo ausente ou inválido.")
    status = corpo.get("status")
    data_hora = corpo.get("data_hora")
    local = corpo.get("local")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        return dados_invalidos("Status inválido.")
    if not data_hora_valida(data_hora):
        return dados_invalidos("data_hora inválida.")
    if not isinstance(local, str) or local == "":
        return dados_invalidos("local inválido.")

    with _lock:
        envio = _envios.get(eid)
        if envio is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if (envio["status"], status) not in TRANSICOES:
            return erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
        if data_hora < envio["historico"][-1]["data_hora"]:
            return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

        novo = copy.deepcopy(envio)
        itens = [{"status": status, "data_hora": data_hora, "local": local}]
        novo["status"] = status
        if status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                itens.append({"status": "DEVOLVIDO", "data_hora": data_hora, "local": local})
                novo["status"] = "DEVOLVIDO"
        novo["historico"].extend(itens)

        try:
            for item in itens:
                publicar("envio.status_atualizado", data_hora, evento_status(novo, item))
        except FalhaBarramento:
            return erro(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")
        _envios[eid] = novo
        return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    corpo, ok = await ler_json(request)
    if not ok or not isinstance(corpo, dict) or not data_hora_valida(corpo.get("data_hora")):
        return dados_invalidos("Corpo fora das restrições.")
    data_hora = corpo["data_hora"]

    with _lock:
        envio = _envios.get(eid)
        if envio is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if envio["status"] != "CRIADO":
            return erro(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")
        if data_hora < envio["historico"][-1]["data_hora"]:
            return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

        novo = copy.deepcopy(envio)
        item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
        novo["status"] = "CANCELADO"
        novo["historico"].append(item)
        try:
            publicar("envio.status_atualizado", data_hora, evento_status(novo, item))
        except FalhaBarramento:
            return erro(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")
        _envios[eid] = novo
        return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/admin/reset")
async def reset():
    global _contador
    with _lock:
        _envios.clear()
        _contador = 0
    return Response(status_code=204)
