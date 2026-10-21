# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

TIMEOUT = 10.0

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_lock = threading.RLock()
_envios = {}
_contador = {"valor": 0}


class ErroApi(Exception):
    def __init__(self, status, codigo, mensagem):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _validar_data_hora(v):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", v):
        raise ValueError("data e hora inválida")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def _nao_vazio(v):
    if not v.strip():
        raise ValueError("texto vazio")
    return v


class Destinatario(BaseModel):
    nome: StrictStr
    email: StrictStr

    @field_validator("nome")
    @classmethod
    def _v_nome(cls, v):
        return _nao_vazio(v)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v):
        if not EMAIL_RE.match(v):
            raise ValueError("e-mail inválido")
        return v


class EnvioEntrada(BaseModel):
    pedido_id: StrictStr
    cotacao_id: StrictStr
    transportadora_id: StrictStr
    destinatario: Destinatario
    data_hora: StrictStr

    @field_validator("pedido_id", "cotacao_id", "transportadora_id")
    @classmethod
    def _v_textos(cls, v):
        return _nao_vazio(v)

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


class AtualizacaoEntrada(BaseModel):
    status: StrictStr
    data_hora: StrictStr
    local: StrictStr

    @field_validator("status")
    @classmethod
    def _v_status(cls, v):
        if v not in STATUS_ATUALIZACAO:
            raise ValueError("status inválido")
        return v

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)

    @field_validator("local")
    @classmethod
    def _v_local(cls, v):
        return _nao_vazio(v)


class CancelamentoEntrada(BaseModel):
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


def _parse_dh(v):
    return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")


def _publicar(evento):
    base = os.environ.get("URL_BARRAMENTO", "")
    try:
        resposta = httpx.post(f"{base}/publicar", json=evento, timeout=TIMEOUT)
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")
    if resposta.status_code >= 300:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")


def _evento(tipo, ocorrido_em, dados):
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def _evento_status(envio, item):
    return _evento(
        "envio.status_atualizado",
        item["data_hora"],
        {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "status": item["status"],
            "data_hora": item["data_hora"],
            "local": item["local"],
            "destinatario_email": envio["destinatario"]["email"],
        },
    )


def _buscar_cotacao(cotacao_id):
    base = os.environ.get("URL_COTACAO", "")
    try:
        resposta = httpx.get(f"{base}/cotacoes/{quote(cotacao_id, safe='')}", timeout=TIMEOUT)
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if resposta.status_code >= 500:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if resposta.status_code == 404:
        raise ErroApi(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resposta.status_code != 200:
        raise ErroApi(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    try:
        dados = resposta.json()
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if not isinstance(dados, dict):
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    return dados


@app.post("/admin/reset")
def reset():
    with _lock:
        _envios.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(entrada: EnvioEntrada):
    with _lock:
        for envio in _envios.values():
            if envio["pedido_id"] == entrada.pedido_id and envio["status"] != "CANCELADO":
                raise ErroApi(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

        cotacao = _buscar_cotacao(entrada.cotacao_id)

        data_operacao = _parse_dh(entrada.data_hora).date()
        try:
            valida_ate = date.fromisoformat(cotacao["valida_ate"])
        except Exception:
            raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
        if data_operacao > valida_ate:
            raise ErroApi(422, "COTACAO_EXPIRADA", "A cotação expirou.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if o.get("transportadora_id") == entrada.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroApi(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _contador["valor"] + 1
        envio_id = f"ENV-{numero:06d}"
        codigo = f"LG{numero:09d}BR"
        envio = {
            "id": envio_id,
            "codigo_rastreio": codigo,
            "pedido_id": entrada.pedido_id,
            "cotacao_id": entrada.cotacao_id,
            "transportadora_id": entrada.transportadora_id,
            "preco_centavos": opcao["preco_centavos"],
            "data_prevista_entrega": opcao["data_prevista_entrega"],
            "destinatario": {"nome": entrada.destinatario.nome, "email": entrada.destinatario.email},
            "status": "CRIADO",
            "tentativas_falhas": 0,
            "historico": [{"status": "CRIADO", "data_hora": entrada.data_hora, "local": None}],
        }
        evento = _evento(
            "envio.criado",
            entrada.data_hora,
            {
                "envio_id": envio_id,
                "codigo_rastreio": codigo,
                "pedido_id": entrada.pedido_id,
                "transportadora_id": entrada.transportadora_id,
                "data_prevista_entrega": opcao["data_prevista_entrega"],
                "destinatario_email": entrada.destinatario.email,
                "data_hora": entrada.data_hora,
            },
        )
        _publicar(evento)
        _contador["valor"] = numero
        _envios[envio_id] = envio
        return copy.deepcopy(envio)


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _lock:
        itens = [copy.deepcopy(e) for e in _envios.values() if e["pedido_id"] == pedido_id]
    itens.sort(key=lambda e: e["id"])
    return itens


@app.get("/envios/{envio_id}")
def obter_envio(envio_id: str):
    with _lock:
        envio = _envios.get(envio_id)
        if envio is None:
            raise ErroApi(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        return copy.deepcopy(envio)


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar_envio(envio_id: str, entrada: AtualizacaoEntrada):
    with _lock:
        envio = _envios.get(envio_id)
        if envio is None:
            raise ErroApi(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        permitidos = TRANSICOES.get(envio["status"], set())
        if entrada.status not in permitidos:
            raise ErroApi(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        ultimo = envio["historico"][-1]
        if _parse_dh(entrada.data_hora) < _parse_dh(ultimo["data_hora"]):
            raise ErroApi(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao último registro.")

        novos_itens = [{"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}]
        tentativas = envio["tentativas_falhas"]
        status_final = entrada.status
        if entrada.status == "FALHA_ENTREGA":
            tentativas += 1
            if tentativas >= 3:
                novos_itens.append(
                    {"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local}
                )
                status_final = "DEVOLVIDO"

        for item in novos_itens:
            _publicar(_evento_status(envio, item))

        envio["historico"].extend(novos_itens)
        envio["status"] = status_final
        envio["tentativas_falhas"] = tentativas
        return copy.deepcopy(envio)


@app.post("/envios/{envio_id}/cancelar")
def cancelar_envio(envio_id: str, entrada: CancelamentoEntrada):
    with _lock:
        envio = _envios.get(envio_id)
        if envio is None:
            raise ErroApi(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if envio["status"] != "CRIADO":
            raise ErroApi(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio criado.")
        ultimo = envio["historico"][-1]
        if _parse_dh(entrada.data_hora) < _parse_dh(ultimo["data_hora"]):
            raise ErroApi(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao último registro.")
        item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
        _publicar(_evento_status(envio, item))
        envio["historico"].append(item)
        envio["status"] = "CANCELADO"
        return copy.deepcopy(envio)
