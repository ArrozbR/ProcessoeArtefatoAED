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
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
RE_DATA_HORA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")

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

_cliente = httpx.Client(timeout=10.0)

_estado = {"contador": 0, "envios": {}}


class ErroAPI(Exception):
    def __init__(self, status, codigo, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resposta_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _invalido(mensagem="Dados inválidos."):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


def _indisponivel(mensagem="Serviço indisponível."):
    return ErroAPI(503, "SERVICO_INDISPONIVEL", mensagem)


def _nao_encontrado():
    return ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")


async def _ler_json(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _texto_nao_vazio(v):
    return isinstance(v, str) and v.strip() != ""


def _parse_data(v):
    if not isinstance(v, str) or not RE_DATA.fullmatch(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _parse_data_hora(v):
    if not isinstance(v, str) or not RE_DATA_HORA.fullmatch(v):
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _publicar(tipo, ocorrido_em, dados):
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    base = os.environ.get("URL_BARRAMENTO", "")
    try:
        r = _cliente.post(f"{base}/publicar", json=evento)
    except Exception:
        raise _indisponivel("Barramento de eventos indisponível.")
    if r.status_code >= 400:
        raise _indisponivel("Barramento de eventos indisponível.")


def _buscar_cotacao(cotacao_id):
    base = os.environ.get("URL_COTACAO", "")
    try:
        r = _cliente.get(f"{base}/cotacoes/{quote(cotacao_id, safe='')}")
    except Exception:
        raise _indisponivel("Serviço de cotação indisponível.")
    if r.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if r.status_code != 200:
        raise _indisponivel("Serviço de cotação indisponível.")
    try:
        cotacao = r.json()
    except Exception:
        raise _indisponivel("Serviço de cotação indisponível.")
    if not isinstance(cotacao, dict):
        raise _indisponivel("Serviço de cotação indisponível.")
    return cotacao


def _evento_status(envio, item):
    return {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    }


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["envios"].clear()
    return Response(status_code=204)


@app.post("/envios")
async def criar_envio(request: Request):
    corpo = await _ler_json(request)
    for campo in ("pedido_id", "cotacao_id", "transportadora_id"):
        if not _texto_nao_vazio(corpo.get(campo)):
            raise _invalido(f"Campo '{campo}' inválido.")
    destinatario = corpo.get("destinatario")
    if not isinstance(destinatario, dict):
        raise _invalido("Campo 'destinatario' inválido.")
    if not _texto_nao_vazio(destinatario.get("nome")):
        raise _invalido("Campo 'destinatario.nome' inválido.")
    email = destinatario.get("email")
    if not isinstance(email, str) or not RE_EMAIL.match(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    data_hora = _parse_data_hora(corpo.get("data_hora"))
    if data_hora is None:
        raise _invalido("Campo 'data_hora' inválido.")

    pedido_id = corpo["pedido_id"]
    cotacao_id = corpo["cotacao_id"]
    transportadora_id = corpo["transportadora_id"]
    data_hora_txt = corpo["data_hora"]

    for envio in _estado["envios"].values():
        if envio["pedido_id"] == pedido_id and envio["status"] != "CANCELADO":
            raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui envio ativo.")

    cotacao = _buscar_cotacao(cotacao_id)
    valida_ate = _parse_data(cotacao.get("valida_ate"))
    if valida_ate is None:
        raise _indisponivel("Serviço de cotação indisponível.")
    if data_hora.date() > valida_ate:
        raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação expirou.")

    opcao = None
    for o in cotacao.get("opcoes") or []:
        if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
            opcao = o
            break
    if opcao is None:
        raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

    numero = _estado["contador"] + 1
    envio = {
        "id": f"ENV-{numero:06d}",
        "codigo_rastreio": f"LG{numero:09d}BR",
        "pedido_id": pedido_id,
        "cotacao_id": cotacao_id,
        "transportadora_id": transportadora_id,
        "preco_centavos": opcao.get("preco_centavos"),
        "data_prevista_entrega": opcao.get("data_prevista_entrega"),
        "destinatario": {"nome": destinatario["nome"], "email": email},
        "status": "CRIADO",
        "tentativas_falhas": 0,
        "historico": [{"status": "CRIADO", "data_hora": data_hora_txt, "local": None}],
    }

    _publicar("envio.criado", data_hora_txt, {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": pedido_id,
        "transportadora_id": transportadora_id,
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": email,
        "data_hora": data_hora_txt,
    })

    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio
    return JSONResponse(status_code=201, content=envio)


@app.get("/envios")
async def listar_envios(pedido_id: str = Query(...)):
    lista = [e for e in _estado["envios"].values() if e["pedido_id"] == pedido_id]
    lista.sort(key=lambda e: e["id"])
    return JSONResponse(status_code=200, content=lista)


@app.get("/envios/{envio_id}")
async def obter_envio(envio_id: str):
    envio = _estado["envios"].get(envio_id)
    if envio is None:
        raise _nao_encontrado()
    return JSONResponse(status_code=200, content=envio)


@app.post("/envios/{envio_id}/atualizacoes")
async def atualizar_envio(envio_id: str, request: Request):
    corpo = await _ler_json(request)
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    data_hora = _parse_data_hora(corpo.get("data_hora"))
    if data_hora is None:
        raise _invalido("Campo 'data_hora' inválido.")
    local = corpo.get("local")
    if not _texto_nao_vazio(local):
        raise _invalido("Campo 'local' inválido.")

    atual = _estado["envios"].get(envio_id)
    if atual is None:
        raise _nao_encontrado()
    if (atual["status"], status) not in TRANSICOES:
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
    ultimo = _parse_data_hora(atual["historico"][-1]["data_hora"])
    if ultimo is not None and data_hora < ultimo:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

    envio = copy.deepcopy(atual)
    data_hora_txt = corpo["data_hora"]
    novos = [{"status": status, "data_hora": data_hora_txt, "local": local}]
    envio["status"] = status
    if status == "FALHA_ENTREGA":
        envio["tentativas_falhas"] += 1
        if envio["tentativas_falhas"] >= 3:
            novos.append({"status": "DEVOLVIDO", "data_hora": data_hora_txt, "local": local})
            envio["status"] = "DEVOLVIDO"
    envio["historico"].extend(novos)

    for item in novos:
        _publicar("envio.status_atualizado", data_hora_txt, _evento_status(envio, item))

    _estado["envios"][envio_id] = envio
    return JSONResponse(status_code=200, content=envio)


@app.post("/envios/{envio_id}/cancelar")
async def cancelar_envio(envio_id: str, request: Request):
    corpo = await _ler_json(request)
    data_hora = _parse_data_hora(corpo.get("data_hora"))
    if data_hora is None:
        raise _invalido("Campo 'data_hora' inválido.")

    atual = _estado["envios"].get(envio_id)
    if atual is None:
        raise _nao_encontrado()
    if atual["status"] != "CRIADO":
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio criado.")
    ultimo = _parse_data_hora(atual["historico"][-1]["data_hora"])
    if ultimo is not None and data_hora < ultimo:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

    envio = copy.deepcopy(atual)
    data_hora_txt = corpo["data_hora"]
    item = {"status": "CANCELADO", "data_hora": data_hora_txt, "local": None}
    envio["status"] = "CANCELADO"
    envio["historico"].append(item)

    _publicar("envio.status_atualizado", data_hora_txt, _evento_status(envio, item))

    _estado["envios"][envio_id] = envio
    return JSONResponse(status_code=200, content=envio)
