# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime
from typing import Literal

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr, field_validator

app = FastAPI(title="Envios")

RE_DATA_HORA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")
RE_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_lock = threading.Lock()
_envios = {}
_contador = {"envio": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos na requisição."},
    )


def _validar_data_hora(v: str) -> str:
    if not RE_DATA_HORA.fullmatch(v):
        raise ValueError("data e hora mal formadas")
    datetime.fromisoformat(v)
    return v


def _nao_vazio(v: str) -> str:
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
        if not RE_EMAIL.fullmatch(v):
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
    def _v_textos(cls, v):
        return _nao_vazio(v)

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
        return _nao_vazio(v)


class CancelamentoEntrada(BaseModel):
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


def _indisponivel(nome: str):
    return ErroAPI(503, "SERVICO_INDISPONIVEL", f"Serviço de {nome} indisponível.")


def _buscar_cotacao(cotacao_id: str):
    base = os.environ.get("URL_COTACAO", "")
    try:
        resp = httpx.get(f"{base}/cotacoes/{cotacao_id}", timeout=10.0)
    except Exception:
        raise _indisponivel("cotação")
    if resp.status_code >= 500:
        raise _indisponivel("cotação")
    if resp.status_code == 404 or resp.status_code >= 400:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    try:
        dados = resp.json()
    except Exception:
        raise _indisponivel("cotação")
    if not isinstance(dados, dict):
        raise _indisponivel("cotação")
    return dados


def _publicar(evento: dict):
    base = os.environ.get("URL_BARRAMENTO", "")
    try:
        resp = httpx.post(f"{base}/publicar", json=evento, timeout=10.0)
    except Exception:
        raise _indisponivel("barramento")
    if resp.status_code >= 400:
        raise _indisponivel("barramento")


def _novo_evento(tipo: str, ocorrido_em: str, dados: dict) -> dict:
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def _evento_status(envio: dict, item: dict) -> dict:
    return _novo_evento(
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


def _obter_ou_404(envio_id: str) -> dict:
    e = _envios.get(envio_id)
    if e is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return e


def _verificar_data_hora(envio: dict, data_hora: str):
    ultima = datetime.fromisoformat(envio["historico"][-1]["data_hora"])
    if datetime.fromisoformat(data_hora) < ultima:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _contador["envio"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(entrada: EnvioEntrada):
    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == entrada.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui envio ativo.")

        cotacao = _buscar_cotacao(entrada.cotacao_id)

        try:
            valida_ate = date.fromisoformat(str(cotacao.get("valida_ate")))
        except Exception:
            raise _indisponivel("cotação")
        if datetime.fromisoformat(entrada.data_hora).date() > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação expirou.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == entrada.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _contador["envio"] + 1
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
        evento = _novo_evento(
            "envio.criado",
            entrada.data_hora,
            {
                "envio_id": envio["id"],
                "codigo_rastreio": envio["codigo_rastreio"],
                "pedido_id": envio["pedido_id"],
                "transportadora_id": envio["transportadora_id"],
                "data_prevista_entrega": envio["data_prevista_entrega"],
                "destinatario_email": envio["destinatario"]["email"],
                "data_hora": entrada.data_hora,
            },
        )
        _publicar(evento)
        _contador["envio"] = numero
        _envios[envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _lock:
        itens = [e for e in _envios.values() if e["pedido_id"] == pedido_id]
        itens.sort(key=lambda e: e["id"])
        return copy.deepcopy(itens)


@app.get("/envios/{envio_id}")
def obter_envio(envio_id: str):
    with _lock:
        return copy.deepcopy(_obter_ou_404(envio_id))


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar_envio(envio_id: str, entrada: AtualizacaoEntrada):
    with _lock:
        atual = _obter_ou_404(envio_id)
        if entrada.status not in TRANSICOES.get(atual["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        _verificar_data_hora(atual, entrada.data_hora)

        novo = copy.deepcopy(atual)
        itens_novos = [{"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}]
        novo["status"] = entrada.status
        if entrada.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                itens_novos.append({"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local})
                novo["status"] = "DEVOLVIDO"
        novo["historico"].extend(itens_novos)

        for item in itens_novos:
            _publicar(_evento_status(novo, item))

        _envios[envio_id] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{envio_id}/cancelar")
def cancelar_envio(envio_id: str, entrada: CancelamentoEntrada):
    with _lock:
        atual = _obter_ou_404(envio_id)
        if atual["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio criado.")
        _verificar_data_hora(atual, entrada.data_hora)

        novo = copy.deepcopy(atual)
        item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
        novo["status"] = "CANCELADO"
        novo["historico"].append(item)
        _publicar(_evento_status(novo, item))
        _envios[envio_id] = novo
        return copy.deepcopy(novo)
