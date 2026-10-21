# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

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

_lock = threading.RLock()
_envios: dict = {}
_contador = {"envio": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _invalido(msg: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", msg)


def _validar_data_hora(v: Any, campo: str = "data_hora") -> datetime:
    if not isinstance(v, str) or not RE_DATA_HORA.fullmatch(v):
        raise _invalido(f"Campo '{campo}' inválido.")
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise _invalido(f"Campo '{campo}' inválido.")


def _texto_preenchido(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _texto_nao_vazio(v: Any) -> bool:
    return isinstance(v, str) and v != ""


def _indisponivel(servico: str) -> ErroAPI:
    return ErroAPI(503, "SERVICO_INDISPONIVEL", f"Serviço {servico} indisponível.")


def _buscar_cotacao(cotacao_id: str) -> dict:
    url = os.environ["URL_COTACAO"] + "/cotacoes/" + quote(cotacao_id, safe="")
    try:
        with httpx.Client(timeout=10.0) as cliente:
            resp = cliente.get(url)
    except httpx.HTTPError:
        raise _indisponivel("Cotação")
    if resp.status_code >= 500:
        raise _indisponivel("Cotação")
    if resp.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    try:
        dados = resp.json()
    except ValueError:
        raise _indisponivel("Cotação")
    if not isinstance(dados, dict):
        raise _indisponivel("Cotação")
    return dados


def _publicar(eventos: list) -> None:
    url = os.environ["URL_BARRAMENTO"] + "/publicar"
    with httpx.Client(timeout=10.0) as cliente:
        for evento in eventos:
            resp = cliente.post(url, json=evento)
            if resp.status_code >= 500:
                raise httpx.HTTPError("barramento respondeu erro")


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


def _obter_envio(eid: str) -> dict:
    envio = _envios.get(eid)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return envio


def _ultima_data_hora(envio: dict) -> datetime:
    return datetime.strptime(envio["historico"][-1]["data_hora"], "%Y-%m-%dT%H:%M:%S")


def _aplicar_e_publicar(eid: str, novo: dict, itens: list) -> dict:
    anterior = _envios[eid]
    _envios[eid] = novo
    try:
        _publicar([_evento_status(novo, item) for item in itens])
    except httpx.HTTPError:
        _envios[eid] = anterior
        raise _indisponivel("Barramento")
    return copy.deepcopy(novo)


@app.post("/envios", status_code=201)
def criar_envio(corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        raise _invalido("Corpo deve ser um objeto JSON.")
    pedido_id = corpo.get("pedido_id")
    cotacao_id = corpo.get("cotacao_id")
    transportadora_id = corpo.get("transportadora_id")
    destinatario = corpo.get("destinatario")
    if not _texto_preenchido(pedido_id):
        raise _invalido("Campo 'pedido_id' inválido.")
    if not _texto_nao_vazio(cotacao_id):
        raise _invalido("Campo 'cotacao_id' inválido.")
    if not _texto_nao_vazio(transportadora_id):
        raise _invalido("Campo 'transportadora_id' inválido.")
    if not isinstance(destinatario, dict):
        raise _invalido("Campo 'destinatario' inválido.")
    nome = destinatario.get("nome")
    email = destinatario.get("email")
    if not _texto_preenchido(nome):
        raise _invalido("Campo 'destinatario.nome' inválido.")
    if not isinstance(email, str) or not RE_EMAIL.match(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    data_hora_str = corpo.get("data_hora")
    data_hora = _validar_data_hora(data_hora_str)

    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

        cotacao = _buscar_cotacao(cotacao_id)
        try:
            valida_ate = date.fromisoformat(cotacao["valida_ate"])
        except (KeyError, TypeError, ValueError):
            raise _indisponivel("Cotação")
        if data_hora.date() > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação expirou.")
        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _contador["envio"] + 1
        eid = f"ENV-{numero:06d}"
        codigo = f"LG{numero:09d}BR"
        envio = {
            "id": eid,
            "codigo_rastreio": codigo,
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
        evento = _evento("envio.criado", data_hora_str, {
            "envio_id": eid,
            "codigo_rastreio": codigo,
            "pedido_id": pedido_id,
            "transportadora_id": transportadora_id,
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": email,
            "data_hora": data_hora_str,
        })
        _contador["envio"] = numero
        _envios[eid] = envio
        try:
            _publicar([evento])
        except httpx.HTTPError:
            del _envios[eid]
            _contador["envio"] = numero - 1
            raise _indisponivel("Barramento")
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        raise _invalido("Parâmetro 'pedido_id' é obrigatório.")
    with _lock:
        itens = [copy.deepcopy(_envios[k]) for k in sorted(_envios) if _envios[k]["pedido_id"] == pedido_id]
    return itens


@app.get("/envios/{eid}")
def consultar_envio(eid: str):
    with _lock:
        return copy.deepcopy(_obter_envio(eid))


@app.post("/envios/{eid}/atualizacoes")
def registrar_atualizacao(eid: str, corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        raise _invalido("Corpo deve ser um objeto JSON.")
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    data_hora_str = corpo.get("data_hora")
    data_hora = _validar_data_hora(data_hora_str)
    local = corpo.get("local")
    if not _texto_nao_vazio(local):
        raise _invalido("Campo 'local' inválido.")

    with _lock:
        envio = _obter_envio(eid)
        if status not in TRANSICOES.get(envio["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
        if data_hora < _ultima_data_hora(envio):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

        novo = copy.deepcopy(envio)
        itens = [{"status": status, "data_hora": data_hora_str, "local": local}]
        novo["status"] = status
        if status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                itens.append({"status": "DEVOLVIDO", "data_hora": data_hora_str, "local": local})
                novo["status"] = "DEVOLVIDO"
        novo["historico"].extend(itens)
        return _aplicar_e_publicar(eid, novo, itens)


@app.post("/envios/{eid}/cancelar")
def cancelar_envio(eid: str, corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        raise _invalido("Corpo deve ser um objeto JSON.")
    data_hora_str = corpo.get("data_hora")
    data_hora = _validar_data_hora(data_hora_str)

    with _lock:
        envio = _obter_envio(eid)
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só envios CRIADO podem ser cancelados.")
        if data_hora < _ultima_data_hora(envio):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")
        novo = copy.deepcopy(envio)
        item = {"status": "CANCELADO", "data_hora": data_hora_str, "local": None}
        novo["status"] = "CANCELADO"
        novo["historico"].append(item)
        return _aplicar_e_publicar(eid, novo, [item])


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _contador["envio"] = 0
    return Response(status_code=204)
