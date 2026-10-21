# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime
from typing import Any

import httpx
from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATA_HORA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

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
_envios: dict = {}
_contador = {"valor": 0}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 422:
        return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _parse_data_hora(s: Any):
    if not isinstance(s, str) or not DATA_HORA_RE.match(s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _parse_data(s: Any):
    if not isinstance(s, str) or not DATA_RE.match(s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _texto_preenchido(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    envelope = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        url = os.environ["URL_BARRAMENTO"]
        with httpx.Client(timeout=10.0) as cliente:
            cliente.post(f"{url}/publicar", json=envelope)
    except Exception:
        pass


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


@app.post("/envios")
def criar(corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Corpo inválido.")
    pedido_id = corpo.get("pedido_id")
    cotacao_id = corpo.get("cotacao_id")
    transportadora_id = corpo.get("transportadora_id")
    destinatario = corpo.get("destinatario")
    data_hora_str = corpo.get("data_hora")
    if not _texto_preenchido(pedido_id):
        return erro(422, "DADOS_INVALIDOS", "pedido_id inválido.")
    if not isinstance(cotacao_id, str) or cotacao_id == "":
        return erro(422, "DADOS_INVALIDOS", "cotacao_id inválido.")
    if not isinstance(transportadora_id, str) or transportadora_id == "":
        return erro(422, "DADOS_INVALIDOS", "transportadora_id inválido.")
    if not isinstance(destinatario, dict):
        return erro(422, "DADOS_INVALIDOS", "destinatario inválido.")
    nome = destinatario.get("nome")
    email = destinatario.get("email")
    if not _texto_preenchido(nome):
        return erro(422, "DADOS_INVALIDOS", "Nome do destinatário inválido.")
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        return erro(422, "DADOS_INVALIDOS", "E-mail do destinatário inválido.")
    data_hora = _parse_data_hora(data_hora_str)
    if data_hora is None:
        return erro(422, "DADOS_INVALIDOS", "data_hora inválida.")

    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                return erro(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

        try:
            url = os.environ["URL_COTACAO"]
            with httpx.Client(timeout=10.0) as cliente:
                resp = cliente.get(f"{url}/cotacoes/{cotacao_id}")
        except Exception:
            return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
        if resp.status_code == 404:
            return erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        if resp.status_code != 200:
            return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
        try:
            cotacao = resp.json()
        except Exception:
            return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
        if not isinstance(cotacao, dict):
            return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")

        valida_ate = _parse_data(cotacao.get("valida_ate"))
        if valida_ate is not None and data_hora.date() > valida_ate:
            return erro(422, "COTACAO_EXPIRADA", "Cotação expirada.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
                opcao = o
                break
        if opcao is None:
            return erro(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _contador["valor"] + 1
        envio = {
            "id": f"ENV-{numero:06d}",
            "codigo_rastreio": f"LG{numero:09d}BR",
            "pedido_id": pedido_id,
            "cotacao_id": cotacao_id,
            "transportadora_id": transportadora_id,
            "preco_centavos": opcao["preco_centavos"],
            "data_prevista_entrega": opcao["data_prevista_entrega"],
            "destinatario": {"nome": nome, "email": email},
            "status": "CRIADO",
            "tentativas_falhas": 0,
            "historico": [{"status": "CRIADO", "data_hora": data_hora_str, "local": None}],
        }
        _contador["valor"] = numero
        _envios[envio["id"]] = envio
        resposta = copy.deepcopy(envio)

        _publicar(
            "envio.criado",
            data_hora_str,
            {
                "envio_id": envio["id"],
                "codigo_rastreio": envio["codigo_rastreio"],
                "pedido_id": pedido_id,
                "transportadora_id": transportadora_id,
                "data_prevista_entrega": envio["data_prevista_entrega"],
                "destinatario_email": email,
                "data_hora": data_hora_str,
            },
        )
    return JSONResponse(status_code=201, content=resposta)


@app.get("/envios")
def listar(request: Request):
    if "pedido_id" not in request.query_params:
        return erro(422, "DADOS_INVALIDOS", "Parâmetro pedido_id obrigatório.")
    pedido_id = request.query_params["pedido_id"]
    with _lock:
        itens = [
            copy.deepcopy(_envios[k])
            for k in sorted(_envios.keys())
            if _envios[k]["pedido_id"] == pedido_id
        ]
    return JSONResponse(status_code=200, content=itens)


@app.get("/envios/{envio_id}")
def consultar(envio_id: str):
    with _lock:
        e = _envios.get(envio_id)
        if e is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        return JSONResponse(status_code=200, content=copy.deepcopy(e))


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar(envio_id: str, corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Corpo inválido.")
    status = corpo.get("status")
    data_hora_str = corpo.get("data_hora")
    local = corpo.get("local")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        return erro(422, "DADOS_INVALIDOS", "status inválido.")
    data_hora = _parse_data_hora(data_hora_str)
    if data_hora is None:
        return erro(422, "DADOS_INVALIDOS", "data_hora inválida.")
    if not isinstance(local, str) or local == "":
        return erro(422, "DADOS_INVALIDOS", "local inválido.")

    with _lock:
        envio = _envios.get(envio_id)
        if envio is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if (envio["status"], status) not in TRANSICOES:
            return erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
        ultimo = _parse_data_hora(envio["historico"][-1]["data_hora"])
        if data_hora < ultimo:
            return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último registro.")

        novos = [{"status": status, "data_hora": data_hora_str, "local": local}]
        envio["status"] = status
        if status == "FALHA_ENTREGA":
            envio["tentativas_falhas"] += 1
            if envio["tentativas_falhas"] >= 3:
                novos.append({"status": "DEVOLVIDO", "data_hora": data_hora_str, "local": local})
                envio["status"] = "DEVOLVIDO"
        envio["historico"].extend(novos)
        resposta = copy.deepcopy(envio)

        for item in novos:
            _publicar("envio.status_atualizado", item["data_hora"], _evento_status(envio, item))
    return JSONResponse(status_code=200, content=resposta)


@app.post("/envios/{envio_id}/cancelar")
def cancelar(envio_id: str, corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Corpo inválido.")
    data_hora_str = corpo.get("data_hora")
    data_hora = _parse_data_hora(data_hora_str)
    if data_hora is None:
        return erro(422, "DADOS_INVALIDOS", "data_hora inválida.")

    with _lock:
        envio = _envios.get(envio_id)
        if envio is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if envio["status"] != "CRIADO":
            return erro(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio não coletado.")
        ultimo = _parse_data_hora(envio["historico"][-1]["data_hora"])
        if data_hora < ultimo:
            return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último registro.")

        item = {"status": "CANCELADO", "data_hora": data_hora_str, "local": None}
        envio["status"] = "CANCELADO"
        envio["historico"].append(item)
        resposta = copy.deepcopy(envio)
        _publicar("envio.status_atualizado", data_hora_str, _evento_status(envio, item))
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
def reset():
    with _lock:
        _envios.clear()
        _contador["valor"] = 0
    return Response(status_code=204)
