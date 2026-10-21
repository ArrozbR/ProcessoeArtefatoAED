# arquivo: servicos/envios/main.py
import copy
import os
import re
import uuid
from datetime import date, datetime
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATA_HORA_REGEX = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_cliente = httpx.Client(timeout=10.0)


class ErroApi(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def tratar_erro_api(request: Request, exc: ErroApi):
    return resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def tratar_validacao(request: Request, exc: RequestValidationError):
    return resposta_erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return resposta_erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return resposta_erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def validar_data_hora_texto(v: str) -> str:
    if not DATA_HORA_REGEX.fullmatch(v):
        raise ValueError("data_hora invalida")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def texto_nao_vazio(v: str) -> str:
    if not v.strip():
        raise ValueError("texto vazio")
    return v


class Destinatario(BaseModel):
    nome: StrictStr = Field(min_length=1)
    email: StrictStr

    @field_validator("nome")
    @classmethod
    def validar_nome(cls, v):
        return texto_nao_vazio(v)

    @field_validator("email")
    @classmethod
    def validar_email(cls, v):
        if not EMAIL_REGEX.match(v):
            raise ValueError("email invalido")
        return v


class EnvioEntrada(BaseModel):
    pedido_id: StrictStr = Field(min_length=1)
    cotacao_id: StrictStr = Field(min_length=1)
    transportadora_id: StrictStr = Field(min_length=1)
    destinatario: Destinatario
    data_hora: StrictStr

    @field_validator("pedido_id", "cotacao_id", "transportadora_id")
    @classmethod
    def validar_textos(cls, v):
        return texto_nao_vazio(v)

    @field_validator("data_hora")
    @classmethod
    def validar_data_hora(cls, v):
        return validar_data_hora_texto(v)


class AtualizacaoEntrada(BaseModel):
    status: StrictStr
    data_hora: StrictStr
    local: StrictStr = Field(min_length=1)

    @field_validator("status")
    @classmethod
    def validar_status(cls, v):
        if v not in STATUS_ATUALIZACAO:
            raise ValueError("status invalido")
        return v

    @field_validator("data_hora")
    @classmethod
    def validar_data_hora(cls, v):
        return validar_data_hora_texto(v)

    @field_validator("local")
    @classmethod
    def validar_local(cls, v):
        return texto_nao_vazio(v)


class CancelamentoEntrada(BaseModel):
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def validar_data_hora(cls, v):
        return validar_data_hora_texto(v)


envios = {}
estado = {"contador": 0}


def indisponivel(nome: str):
    return ErroApi(503, "SERVICO_INDISPONIVEL", "Servico %s indisponivel." % nome)


def buscar_cotacao(cotacao_id: str):
    base = os.environ.get("URL_COTACAO", "")
    try:
        resp = _cliente.get(base + "/cotacoes/" + quote(cotacao_id, safe=""))
    except Exception:
        raise indisponivel("de cotacao")
    if resp.status_code == 404:
        raise ErroApi(422, "COTACAO_NAO_ENCONTRADA", "Cotacao nao encontrada.")
    if resp.status_code != 200:
        raise indisponivel("de cotacao")
    try:
        dados = resp.json()
    except Exception:
        raise indisponivel("de cotacao")
    if not isinstance(dados, dict):
        raise indisponivel("de cotacao")
    return dados


def publicar_evento(tipo: str, ocorrido_em: str, dados: dict):
    base = os.environ.get("URL_BARRAMENTO", "")
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        resp = _cliente.post(base + "/publicar", json=evento)
    except Exception:
        raise indisponivel("de barramento")
    if resp.status_code >= 400:
        raise indisponivel("de barramento")


def evento_status(envio: dict, item: dict) -> dict:
    return {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    }


def obter_ou_404(id: str) -> dict:
    if id not in envios:
        raise ErroApi(404, "ENVIO_NAO_ENCONTRADO", "Envio nao encontrado.")
    return envios[id]


@app.post("/admin/reset", status_code=204)
def reset():
    envios.clear()
    estado["contador"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(dados: EnvioEntrada):
    for e in envios.values():
        if e["pedido_id"] == dados.pedido_id and e["status"] != "CANCELADO":
            raise ErroApi(409, "ENVIO_DUPLICADO", "Pedido ja possui envio ativo.")

    cotacao = buscar_cotacao(dados.cotacao_id)

    data_operacao = dados.data_hora[:10]
    valida_ate = cotacao.get("valida_ate")
    if isinstance(valida_ate, str) and data_operacao > valida_ate:
        raise ErroApi(422, "COTACAO_EXPIRADA", "Cotacao expirada.")

    opcao = None
    for o in cotacao.get("opcoes") or []:
        if isinstance(o, dict) and o.get("transportadora_id") == dados.transportadora_id:
            opcao = o
            break
    if opcao is None:
        raise ErroApi(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora nao oferecida na cotacao.")

    numero = estado["contador"] + 1
    envio = {
        "id": "ENV-%06d" % numero,
        "codigo_rastreio": "LG%09dBR" % numero,
        "pedido_id": dados.pedido_id,
        "cotacao_id": dados.cotacao_id,
        "transportadora_id": dados.transportadora_id,
        "preco_centavos": opcao.get("preco_centavos"),
        "data_prevista_entrega": opcao.get("data_prevista_entrega"),
        "destinatario": {"nome": dados.destinatario.nome, "email": dados.destinatario.email},
        "status": "CRIADO",
        "tentativas_falhas": 0,
        "historico": [{"status": "CRIADO", "data_hora": dados.data_hora, "local": None}],
    }

    publicar_evento("envio.criado", dados.data_hora, {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "transportadora_id": envio["transportadora_id"],
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": envio["destinatario"]["email"],
        "data_hora": dados.data_hora,
    })

    estado["contador"] = numero
    envios[envio["id"]] = envio
    return JSONResponse(status_code=201, content=envio)


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    resultado = [e for e in envios.values() if e["pedido_id"] == pedido_id]
    resultado.sort(key=lambda e: e["id"])
    return resultado


@app.get("/envios/{id}")
def obter_envio(id: str):
    return obter_ou_404(id)


@app.post("/envios/{id}/atualizacoes")
def atualizar_envio(id: str, dados: AtualizacaoEntrada):
    atual = obter_ou_404(id)
    permitidos = TRANSICOES.get(atual["status"], set())
    if dados.status not in permitidos:
        raise ErroApi(409, "TRANSICAO_INVALIDA", "Transicao de status invalida.")
    if dados.data_hora < atual["historico"][-1]["data_hora"]:
        raise ErroApi(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao ultimo registro.")

    novo = copy.deepcopy(atual)
    novos_itens = []
    item = {"status": dados.status, "data_hora": dados.data_hora, "local": dados.local}
    novo["historico"].append(item)
    novo["status"] = dados.status
    novos_itens.append(item)
    if dados.status == "FALHA_ENTREGA":
        novo["tentativas_falhas"] += 1
        if novo["tentativas_falhas"] >= 3:
            item_dev = {"status": "DEVOLVIDO", "data_hora": dados.data_hora, "local": dados.local}
            novo["historico"].append(item_dev)
            novo["status"] = "DEVOLVIDO"
            novos_itens.append(item_dev)

    for it in novos_itens:
        publicar_evento("envio.status_atualizado", dados.data_hora, evento_status(novo, it))

    envios[id] = novo
    return novo


@app.post("/envios/{id}/cancelar")
def cancelar_envio(id: str, dados: CancelamentoEntrada):
    atual = obter_ou_404(id)
    if atual["status"] != "CRIADO":
        raise ErroApi(409, "TRANSICAO_INVALIDA", "Envio so pode ser cancelado enquanto CRIADO.")
    if dados.data_hora < atual["historico"][-1]["data_hora"]:
        raise ErroApi(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao ultimo registro.")

    novo = copy.deepcopy(atual)
    item = {"status": "CANCELADO", "data_hora": dados.data_hora, "local": None}
    novo["historico"].append(item)
    novo["status"] = "CANCELADO"

    publicar_evento("envio.status_atualizado", dados.data_hora, evento_status(novo, item))

    envios[id] = novo
    return novo
