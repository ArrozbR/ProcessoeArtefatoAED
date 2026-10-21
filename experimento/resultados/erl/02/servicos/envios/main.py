# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

import httpx
from fastapi import Body, FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_HORA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}
TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_lock = threading.RLock()
_envios: dict = {}
_contador = 0


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    codigos = {404: "RECURSO_NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return erro(exc.status_code, codigos.get(exc.status_code, "ERRO_HTTP"), str(exc.detail))


def _data_hora_valida(s) -> bool:
    if not isinstance(s, str) or not DATA_HORA_RE.match(s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return True


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    envelope = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        httpx.post(os.environ["URL_BARRAMENTO"] + "/publicar", json=envelope, timeout=10.0)
    except Exception:
        pass


def _publicar_status(envio: dict, item: dict) -> None:
    _publicar("envio.status_atualizado", item["data_hora"], {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    })


def _validar_criacao(p) -> Optional[str]:
    if not isinstance(p, dict):
        return "Corpo deve ser um objeto JSON."
    v = p.get("pedido_id")
    if not isinstance(v, str) or not v.strip():
        return "Campo 'pedido_id' inválido."
    for campo in ("cotacao_id", "transportadora_id"):
        v = p.get(campo)
        if not isinstance(v, str) or len(v) == 0:
            return f"Campo '{campo}' inválido."
    dest = p.get("destinatario")
    if not isinstance(dest, dict):
        return "Campo 'destinatario' inválido."
    nome = dest.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return "Campo 'destinatario.nome' inválido."
    email = dest.get("email")
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        return "Campo 'destinatario.email' inválido."
    if not _data_hora_valida(p.get("data_hora")):
        return "Campo 'data_hora' inválido."
    return None


@app.post("/admin/reset", status_code=204)
def reset():
    global _contador
    with _lock:
        _envios.clear()
        _contador = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar(payload: Any = Body(None)):
    global _contador
    msg = _validar_criacao(payload)
    if msg:
        return erro(422, "DADOS_INVALIDOS", msg)

    with _lock:
        pedido_id = payload["pedido_id"]
        for e in _envios.values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                return erro(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

        try:
            url = os.environ["URL_COTACAO"] + "/cotacoes/" + quote(payload["cotacao_id"], safe="")
            resp = httpx.get(url, timeout=10.0)
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

        data_hora = payload["data_hora"]
        if data_hora[:10] > valida_ate:
            return erro(422, "COTACAO_EXPIRADA", "A cotação expirou.")

        opcao = None
        for o in opcoes:
            if o.get("transportadora_id") == payload["transportadora_id"]:
                opcao = o
                break
        if opcao is None:
            return erro(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não está entre as opções da cotação.")

        _contador += 1
        envio = {
            "id": f"ENV-{_contador:06d}",
            "codigo_rastreio": f"LG{_contador:09d}BR",
            "pedido_id": pedido_id,
            "cotacao_id": payload["cotacao_id"],
            "transportadora_id": payload["transportadora_id"],
            "preco_centavos": opcao["preco_centavos"],
            "data_prevista_entrega": opcao["data_prevista_entrega"],
            "destinatario": {
                "nome": payload["destinatario"]["nome"],
                "email": payload["destinatario"]["email"],
            },
            "status": "CRIADO",
            "tentativas_falhas": 0,
            "historico": [{"status": "CRIADO", "data_hora": data_hora, "local": None}],
        }
        _envios[envio["id"]] = envio

        _publicar("envio.criado", data_hora, {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "transportadora_id": envio["transportadora_id"],
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": envio["destinatario"]["email"],
            "data_hora": data_hora,
        })
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar(pedido_id: str = Query(...)):
    with _lock:
        itens = [copy.deepcopy(_envios[k]) for k in sorted(_envios) if _envios[k]["pedido_id"] == pedido_id]
    return JSONResponse(status_code=200, content=itens)


@app.get("/envios/{eid}")
def consultar(eid: str):
    with _lock:
        e = _envios.get(eid)
        if e is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        return JSONResponse(status_code=200, content=copy.deepcopy(e))


@app.post("/envios/{eid}/atualizacoes")
def atualizar(eid: str, payload: Any = Body(None)):
    if not isinstance(payload, dict):
        return erro(422, "DADOS_INVALIDOS", "Corpo deve ser um objeto JSON.")
    status = payload.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        return erro(422, "DADOS_INVALIDOS", "Campo 'status' inválido.")
    data_hora = payload.get("data_hora")
    if not _data_hora_valida(data_hora):
        return erro(422, "DADOS_INVALIDOS", "Campo 'data_hora' inválido.")
    local = payload.get("local")
    if not isinstance(local, str) or len(local) == 0:
        return erro(422, "DADOS_INVALIDOS", "Campo 'local' inválido.")

    with _lock:
        e = _envios.get(eid)
        if e is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if status not in TRANSICOES.get(e["status"], set()):
            return erro(409, "TRANSICAO_INVALIDA", f"Transição de {e['status']} para {status} não permitida.")
        if data_hora < e["historico"][-1]["data_hora"]:
            return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

        novos = [{"status": status, "data_hora": data_hora, "local": local}]
        e["status"] = status
        if status == "FALHA_ENTREGA":
            e["tentativas_falhas"] += 1
            if e["tentativas_falhas"] >= 3:
                novos.append({"status": "DEVOLVIDO", "data_hora": data_hora, "local": local})
                e["status"] = "DEVOLVIDO"
        e["historico"].extend(novos)

        for item in novos:
            _publicar_status(e, item)
        return JSONResponse(status_code=200, content=copy.deepcopy(e))


@app.post("/envios/{eid}/cancelar")
def cancelar(eid: str, payload: Any = Body(None)):
    if not isinstance(payload, dict) or not _data_hora_valida(payload.get("data_hora")):
        return erro(422, "DADOS_INVALIDOS", "Campo 'data_hora' inválido.")
    data_hora = payload["data_hora"]

    with _lock:
        e = _envios.get(eid)
        if e is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if e["status"] != "CRIADO":
            return erro(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")
        if data_hora < e["historico"][-1]["data_hora"]:
            return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

        item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
        e["status"] = "CANCELADO"
        e["historico"].append(item)
        _publicar_status(e, item)
        return JSONResponse(status_code=200, content=copy.deepcopy(e))
