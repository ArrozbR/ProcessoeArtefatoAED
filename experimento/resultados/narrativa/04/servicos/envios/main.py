# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(title="Envios")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

MAX_FALHAS = 3

_lock = threading.RLock()
_envios = {}
_contador = {"valor": 0}
_cliente = httpx.Client(timeout=10.0)


class ServicoIndisponivel(Exception):
    pass


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def _descrever_erros(exc):
    partes = []
    try:
        for e in exc.errors():
            loc = ".".join(str(p) for p in e.get("loc", ()))
            partes.append(f"{loc}: {e.get('msg', '')}")
    except Exception:
        pass
    return "Dados inválidos" + (": " + "; ".join(partes) if partes else ".")


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", _descrever_erros(exc))


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    codigo = "RECURSO_NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return erro(exc.status_code, codigo, str(exc.detail))


def _validar_data_hora(v):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", v):
        raise ValueError("data_hora deve estar no formato AAAA-MM-DDTHH:MM:SS")
    try:
        datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise ValueError("data_hora inexistente")
    return v


def _nao_vazio(v, campo):
    if not v.strip():
        raise ValueError(f"{campo} não pode ser vazio")
    return v


class Destinatario(BaseModel):
    nome: StrictStr
    email: StrictStr

    @field_validator("nome")
    @classmethod
    def _v_nome(cls, v):
        return _nao_vazio(v, "nome")

    @field_validator("email")
    @classmethod
    def _v_email(cls, v):
        if not EMAIL_RE.match(v):
            raise ValueError("email inválido")
        return v


class EnvioEntrada(BaseModel):
    pedido_id: StrictStr
    cotacao_id: StrictStr
    transportadora_id: StrictStr
    destinatario: Destinatario
    data_hora: StrictStr

    @field_validator("pedido_id", "cotacao_id", "transportadora_id")
    @classmethod
    def _v_textos(cls, v, info):
        return _nao_vazio(v, info.field_name)

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


class AtualizacaoEntrada(BaseModel):
    status: Literal["COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"]
    data_hora: StrictStr
    local: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)

    @field_validator("local")
    @classmethod
    def _v_local(cls, v):
        return _nao_vazio(v, "local")


class CancelamentoEntrada(BaseModel):
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


def _url(nome):
    return os.environ.get(nome, "")


def buscar_cotacao(cotacao_id):
    """Devolve a cotação, ou None se não existir. Lança ServicoIndisponivel."""
    try:
        resp = _cliente.get(f"{_url('URL_COTACAO')}/cotacoes/{quote(cotacao_id, safe='')}", timeout=10.0)
    except Exception:
        raise ServicoIndisponivel()
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ServicoIndisponivel()
    try:
        dados = resp.json()
    except Exception:
        raise ServicoIndisponivel()
    if not isinstance(dados, dict):
        raise ServicoIndisponivel()
    return dados


def publicar(evento):
    try:
        resp = _cliente.post(f"{_url('URL_BARRAMENTO')}/publicar", json=evento, timeout=10.0)
    except Exception:
        raise ServicoIndisponivel()
    if resp.status_code >= 300:
        raise ServicoIndisponivel()


def _evento(tipo, ocorrido_em, dados):
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def _evento_status(envio, item):
    return _evento("envio.status_atualizado", item["data_hora"], {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    })


@app.post("/admin/reset")
def reset():
    with _lock:
        _envios.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


@app.post("/envios")
def criar_envio(entrada: EnvioEntrada):
    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == entrada.pedido_id and e["status"] != "CANCELADO":
                return erro(409, "ENVIO_DUPLICADO", f"O pedido {entrada.pedido_id} já tem um envio ativo.")

        try:
            cotacao = buscar_cotacao(entrada.cotacao_id)
        except ServicoIndisponivel:
            return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
        if cotacao is None:
            return erro(422, "COTACAO_NAO_ENCONTRADA", f"Cotação {entrada.cotacao_id} não encontrada.")

        valida_ate = cotacao.get("valida_ate")
        if isinstance(valida_ate, str) and entrada.data_hora[:10] > valida_ate:
            return erro(422, "COTACAO_EXPIRADA", f"A cotação {entrada.cotacao_id} expirou em {valida_ate}.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == entrada.transportadora_id:
                opcao = o
                break
        if opcao is None:
            return erro(422, "TRANSPORTADORA_NAO_OFERECIDA",
                        f"A transportadora {entrada.transportadora_id} não foi oferecida na cotação.")

        numero = _contador["valor"] + 1
        envio = {
            "id": f"ENV-{numero:06d}",
            "codigo_rastreio": f"LG{numero:09d}BR",
            "pedido_id": entrada.pedido_id,
            "cotacao_id": entrada.cotacao_id,
            "transportadora_id": entrada.transportadora_id,
            "preco_centavos": opcao.get("preco_centavos"),
            "data_prevista_entrega": opcao.get("data_prevista_entrega"),
            "destinatario": {"nome": entrada.destinatario.nome, "email": entrada.destinatario.email},
            "status": "CRIADO",
            "tentativas_falhas": 0,
            "historico": [{"status": "CRIADO", "data_hora": entrada.data_hora, "local": None}],
        }

        evento = _evento("envio.criado", entrada.data_hora, {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "transportadora_id": envio["transportadora_id"],
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": envio["destinatario"]["email"],
            "data_hora": entrada.data_hora,
        })
        try:
            publicar(evento)
        except ServicoIndisponivel:
            return erro(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")

        _contador["valor"] = numero
        _envios[envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _lock:
        itens = [copy.deepcopy(e) for k, e in sorted(_envios.items()) if e["pedido_id"] == pedido_id]
    return JSONResponse(status_code=200, content=itens)


@app.get("/envios/{envio_id}")
def obter_envio(envio_id: str):
    with _lock:
        e = _envios.get(envio_id)
        if e is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", f"Envio {envio_id} não encontrado.")
        return JSONResponse(status_code=200, content=copy.deepcopy(e))


def _aplicar(envio_id, novos_itens_fn, status_permitido_fn, data_hora):
    """Lógica comum de atualização e cancelamento."""
    envio = _envios.get(envio_id)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", f"Envio {envio_id} não encontrado.")
    if not status_permitido_fn(envio["status"]):
        return erro(409, "TRANSICAO_INVALIDA", f"Transição inválida a partir de {envio['status']}.")
    if data_hora < envio["historico"][-1]["data_hora"]:
        return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novo = copy.deepcopy(envio)
    itens = novos_itens_fn(novo)
    for item in itens:
        novo["historico"].append(item)
        novo["status"] = item["status"]

    eventos = [_evento_status(novo, item) for item in itens]
    try:
        for ev in eventos:
            publicar(ev)
    except ServicoIndisponivel:
        return erro(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")

    _envios[envio_id] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar_envio(envio_id: str, entrada: AtualizacaoEntrada):
    def permitido(status_atual):
        return entrada.status in TRANSICOES.get(status_atual, set())

    def itens(novo):
        item = {"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}
        resultado = [item]
        if entrada.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= MAX_FALHAS:
                resultado.append({"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local})
        return resultado

    with _lock:
        return _aplicar(envio_id, itens, permitido, entrada.data_hora)


@app.post("/envios/{envio_id}/cancelar")
def cancelar_envio(envio_id: str, entrada: CancelamentoEntrada):
    def permitido(status_atual):
        return status_atual == "CRIADO"

    def itens(novo):
        return [{"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}]

    with _lock:
        return _aplicar(envio_id, itens, permitido, entrada.data_hora)
