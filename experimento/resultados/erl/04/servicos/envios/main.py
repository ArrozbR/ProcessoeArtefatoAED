# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime
from typing import Any, Optional
from urllib.parse import quote

import httpx
from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RE_DATA_HORA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

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
_estado = {"contador": 0, "envios": {}}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def _parse_data_hora(v: Any) -> Optional[datetime]:
    if not isinstance(v, str) or not RE_DATA_HORA.match(v):
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _parse_data(v: Any) -> Optional[date]:
    if not isinstance(v, str) or not RE_DATA.match(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _texto_nao_vazio(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
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
def criar_envio(corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Corpo inválido.")
    pedido_id = corpo.get("pedido_id")
    cotacao_id = corpo.get("cotacao_id")
    transportadora_id = corpo.get("transportadora_id")
    destinatario = corpo.get("destinatario")
    data_hora_txt = corpo.get("data_hora")
    if not _texto_nao_vazio(pedido_id):
        return erro(422, "DADOS_INVALIDOS", "pedido_id inválido.")
    if not isinstance(cotacao_id, str) or cotacao_id == "":
        return erro(422, "DADOS_INVALIDOS", "cotacao_id inválido.")
    if not isinstance(transportadora_id, str) or transportadora_id == "":
        return erro(422, "DADOS_INVALIDOS", "transportadora_id inválido.")
    if not isinstance(destinatario, dict):
        return erro(422, "DADOS_INVALIDOS", "destinatario inválido.")
    nome = destinatario.get("nome")
    email = destinatario.get("email")
    if not _texto_nao_vazio(nome):
        return erro(422, "DADOS_INVALIDOS", "Nome do destinatário inválido.")
    if not isinstance(email, str) or not RE_EMAIL.match(email):
        return erro(422, "DADOS_INVALIDOS", "E-mail do destinatário inválido.")
    data_hora = _parse_data_hora(data_hora_txt)
    if data_hora is None:
        return erro(422, "DADOS_INVALIDOS", "data_hora inválida.")

    with _lock:
        for e in _estado["envios"].values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                return erro(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

    try:
        url = os.environ["URL_COTACAO"] + "/cotacoes/" + quote(cotacao_id, safe="")
        resp = httpx.get(url, timeout=10.0)
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    if resp.status_code == 404:
        return erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    try:
        cotacao = resp.json()
        valida_ate = _parse_data(cotacao["valida_ate"])
        opcoes = cotacao["opcoes"]
        if valida_ate is None or not isinstance(opcoes, list):
            raise ValueError
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")

    if data_hora.date() > valida_ate:
        return erro(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in opcoes:
        if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
            opcao = o
            break
    if opcao is None:
        return erro(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

    with _lock:
        for e in _estado["envios"].values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                return erro(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")
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
        resposta = copy.deepcopy(envio)

        _publicar(
            "envio.criado",
            data_hora_txt,
            {
                "envio_id": envio["id"],
                "codigo_rastreio": envio["codigo_rastreio"],
                "pedido_id": pedido_id,
                "transportadora_id": transportadora_id,
                "data_prevista_entrega": envio["data_prevista_entrega"],
                "destinatario_email": email,
                "data_hora": data_hora_txt,
            },
        )
    return JSONResponse(status_code=201, content=resposta)


@app.get("/envios")
def listar_envios(pedido_id: Optional[str] = None):
    if pedido_id is None:
        return erro(422, "DADOS_INVALIDOS", "Parâmetro pedido_id é obrigatório.")
    with _lock:
        itens = [
            copy.deepcopy(e)
            for _, e in sorted(_estado["envios"].items(), key=lambda kv: kv[0])
            if e["pedido_id"] == pedido_id
        ]
    return JSONResponse(status_code=200, content=itens)


@app.get("/envios/{envio_id}")
def consultar_envio(envio_id: str):
    with _lock:
        e = _estado["envios"].get(envio_id)
        if e is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        return JSONResponse(status_code=200, content=copy.deepcopy(e))


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar(envio_id: str, corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Corpo inválido.")
    status = corpo.get("status")
    data_hora_txt = corpo.get("data_hora")
    local = corpo.get("local")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        return erro(422, "DADOS_INVALIDOS", "status inválido.")
    data_hora = _parse_data_hora(data_hora_txt)
    if data_hora is None:
        return erro(422, "DADOS_INVALIDOS", "data_hora inválida.")
    if not isinstance(local, str) or local == "":
        return erro(422, "DADOS_INVALIDOS", "local inválido.")

    with _lock:
        envio = _estado["envios"].get(envio_id)
        if envio is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if (envio["status"], status) not in TRANSICOES:
            return erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
        ultimo = _parse_data_hora(envio["historico"][-1]["data_hora"])
        if ultimo is not None and data_hora < ultimo:
            return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último registro.")

        novos = [{"status": status, "data_hora": data_hora_txt, "local": local}]
        envio["status"] = status
        if status == "FALHA_ENTREGA":
            envio["tentativas_falhas"] += 1
            if envio["tentativas_falhas"] >= 3:
                novos.append({"status": "DEVOLVIDO", "data_hora": data_hora_txt, "local": local})
                envio["status"] = "DEVOLVIDO"
        envio["historico"].extend(novos)
        resposta = copy.deepcopy(envio)

        for item in novos:
            _publicar("envio.status_atualizado", data_hora_txt, _evento_status(envio, item))
    return JSONResponse(status_code=200, content=resposta)


@app.post("/envios/{envio_id}/cancelar")
def cancelar(envio_id: str, corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Corpo inválido.")
    data_hora_txt = corpo.get("data_hora")
    data_hora = _parse_data_hora(data_hora_txt)
    if data_hora is None:
        return erro(422, "DADOS_INVALIDOS", "data_hora inválida.")

    with _lock:
        envio = _estado["envios"].get(envio_id)
        if envio is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if envio["status"] != "CRIADO":
            return erro(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio CRIADO.")
        ultimo = _parse_data_hora(envio["historico"][-1]["data_hora"])
        if ultimo is not None and data_hora < ultimo:
            return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último registro.")

        item = {"status": "CANCELADO", "data_hora": data_hora_txt, "local": None}
        envio["status"] = "CANCELADO"
        envio["historico"].append(item)
        resposta = copy.deepcopy(envio)
        _publicar("envio.status_atualizado", data_hora_txt, _evento_status(envio, item))
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
def reset():
    with _lock:
        _estado["contador"] = 0
        _estado["envios"].clear()
    return Response(status_code=204)
