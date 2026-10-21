# arquivo: servicos/envios/main.py
import copy
import json
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

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATA_HORA_REGEX = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
DATA_REGEX = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

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

_estado = {"envios": {}, "contador": 0}


class ErroApi(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str = "Dados inválidos na requisição.") -> ErroApi:
    return ErroApi(422, "DADOS_INVALIDOS", mensagem)


async def _ler_json(request: Request) -> dict:
    corpo = await request.body()
    try:
        dados = json.loads(corpo)
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(dados, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return dados


def _parse_data_hora(valor):
    if not isinstance(valor, str) or not DATA_HORA_REGEX.match(valor):
        return None
    try:
        return datetime.strptime(valor, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _parse_data(valor):
    if not isinstance(valor, str) or not DATA_REGEX.match(valor):
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _texto_nao_vazio(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    envelope = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    url = os.environ["URL_BARRAMENTO"] + "/publicar"
    try:
        resp = httpx.post(url, json=envelope, timeout=10.0)
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")
    if resp.status_code >= 500:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")


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


def _buscar_cotacao(cotacao_id: str) -> dict:
    url = os.environ["URL_COTACAO"] + "/cotacoes/" + cotacao_id
    try:
        resp = httpx.get(url, timeout=10.0)
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    if resp.status_code == 404:
        raise ErroApi(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    try:
        cotacao = resp.json()
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    if not isinstance(cotacao, dict):
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    return cotacao


def _obter_envio(eid: str) -> dict:
    envio = _estado["envios"].get(eid)
    if envio is None:
        raise ErroApi(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return envio


@app.post("/envios")
async def criar_envio(request: Request):
    dados = await _ler_json(request)

    for c in ("pedido_id", "cotacao_id", "transportadora_id", "destinatario", "data_hora"):
        if c not in dados:
            raise _invalido(f"Campo obrigatório ausente: {c}.")
    pedido_id = dados["pedido_id"]
    if not _texto_nao_vazio(pedido_id):
        raise _invalido("Campo pedido_id inválido.")
    cotacao_id = dados["cotacao_id"]
    if not isinstance(cotacao_id, str) or cotacao_id == "":
        raise _invalido("Campo cotacao_id inválido.")
    transportadora_id = dados["transportadora_id"]
    if not isinstance(transportadora_id, str) or transportadora_id == "":
        raise _invalido("Campo transportadora_id inválido.")
    destinatario = dados["destinatario"]
    if not isinstance(destinatario, dict):
        raise _invalido("Campo destinatario inválido.")
    nome = destinatario.get("nome")
    if not _texto_nao_vazio(nome):
        raise _invalido("Campo destinatario.nome inválido.")
    email = destinatario.get("email")
    if not isinstance(email, str) or not EMAIL_REGEX.match(email):
        raise _invalido("Campo destinatario.email inválido.")
    data_hora_str = dados["data_hora"]
    data_hora = _parse_data_hora(data_hora_str)
    if data_hora is None:
        raise _invalido("Campo data_hora inválido.")

    for envio in _estado["envios"].values():
        if envio["pedido_id"] == pedido_id and envio["status"] != "CANCELADO":
            raise ErroApi(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

    cotacao = _buscar_cotacao(cotacao_id)

    valida_ate = _parse_data(cotacao.get("valida_ate"))
    if valida_ate is None:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    if data_hora.date() > valida_ate:
        raise ErroApi(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in cotacao.get("opcoes", []) or []:
        if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
            opcao = o
            break
    if opcao is None:
        raise ErroApi(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

    numero = _estado["contador"] + 1
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

    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar_envios(request: Request):
    params = request.query_params
    if "pedido_id" not in params:
        raise _invalido("Parâmetro pedido_id é obrigatório.")
    pedido_id = params["pedido_id"]
    resultado = [
        copy.deepcopy(e)
        for eid, e in sorted(_estado["envios"].items())
        if e["pedido_id"] == pedido_id
    ]
    return JSONResponse(status_code=200, content=resultado)


@app.get("/envios/{eid}")
async def consultar_envio(eid: str):
    envio = _obter_envio(eid)
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{eid}/atualizacoes")
async def atualizar_envio(eid: str, request: Request):
    dados = await _ler_json(request)
    for c in ("status", "data_hora", "local"):
        if c not in dados:
            raise _invalido(f"Campo obrigatório ausente: {c}.")
    status = dados["status"]
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo status inválido.")
    data_hora_str = dados["data_hora"]
    data_hora = _parse_data_hora(data_hora_str)
    if data_hora is None:
        raise _invalido("Campo data_hora inválido.")
    local = dados["local"]
    if not _texto_nao_vazio(local):
        raise _invalido("Campo local inválido.")

    envio = _obter_envio(eid)

    if (envio["status"], status) not in TRANSICOES:
        raise ErroApi(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")

    ultimo = _parse_data_hora(envio["historico"][-1]["data_hora"])
    if data_hora < ultimo:
        raise ErroApi(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novo = copy.deepcopy(envio)
    itens = [{"status": status, "data_hora": data_hora_str, "local": local}]
    novo["status"] = status
    if status == "FALHA_ENTREGA":
        novo["tentativas_falhas"] += 1
        if novo["tentativas_falhas"] >= 3:
            itens.append({"status": "DEVOLVIDO", "data_hora": data_hora_str, "local": local})
            novo["status"] = "DEVOLVIDO"
    novo["historico"].extend(itens)

    for item in itens:
        _publicar("envio.status_atualizado", data_hora_str, _evento_status(novo, item))

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/envios/{eid}/cancelar")
async def cancelar_envio(eid: str, request: Request):
    dados = await _ler_json(request)
    if "data_hora" not in dados:
        raise _invalido("Campo obrigatório ausente: data_hora.")
    data_hora_str = dados["data_hora"]
    data_hora = _parse_data_hora(data_hora_str)
    if data_hora is None:
        raise _invalido("Campo data_hora inválido.")

    envio = _obter_envio(eid)

    if envio["status"] != "CRIADO":
        raise ErroApi(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")

    ultimo = _parse_data_hora(envio["historico"][-1]["data_hora"])
    if data_hora < ultimo:
        raise ErroApi(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novo = copy.deepcopy(envio)
    item = {"status": "CANCELADO", "data_hora": data_hora_str, "local": None}
    novo["status"] = "CANCELADO"
    novo["historico"].append(item)

    _publicar("envio.status_atualizado", data_hora_str, _evento_status(novo, item))

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/admin/reset")
async def reset():
    _estado["envios"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)
