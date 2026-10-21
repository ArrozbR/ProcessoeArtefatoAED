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

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATA_HORA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_estado = {"envios": {}, "contador": 0}


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _h_erro(request, exc):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _h_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _h_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(msg="Dados inválidos na requisição."):
    raise ErroAPI(422, "DADOS_INVALIDOS", msg)


async def _ler_corpo(request):
    try:
        raw = await request.body()
        corpo = json.loads(raw)
    except Exception:
        _invalido("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        _invalido("Corpo deve ser um objeto JSON.")
    return corpo


def _validar_data_hora(v):
    if not isinstance(v, str) or not DATA_HORA_RE.match(v):
        _invalido("data_hora inválida.")
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        _invalido("data_hora inválida.")


def _publicar(tipo, ocorrido_em, dados):
    envelope = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    url = os.environ["URL_BARRAMENTO"] + "/publicar"
    try:
        r = httpx.post(url, json=envelope, timeout=10.0)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")
    if r.status_code >= 500:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")


def _buscar_cotacao(cotacao_id):
    url = os.environ["URL_COTACAO"] + "/cotacoes/" + cotacao_id
    try:
        r = httpx.get(url, timeout=10.0)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    if r.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if r.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    try:
        dados = r.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    if not isinstance(dados, dict):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    return dados


def _obter_envio(eid):
    e = _estado["envios"].get(eid)
    if e is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return e


def _dados_status(envio, item):
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
    _estado["envios"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/envios")
async def criar_envio(request: Request):
    corpo = await _ler_corpo(request)

    pedido_id = corpo.get("pedido_id")
    if not isinstance(pedido_id, str) or not pedido_id.strip():
        _invalido("pedido_id inválido.")
    cotacao_id = corpo.get("cotacao_id")
    if not isinstance(cotacao_id, str) or cotacao_id == "":
        _invalido("cotacao_id inválido.")
    transportadora_id = corpo.get("transportadora_id")
    if not isinstance(transportadora_id, str) or transportadora_id == "":
        _invalido("transportadora_id inválido.")
    dest = corpo.get("destinatario")
    if not isinstance(dest, dict):
        _invalido("destinatario inválido.")
    nome = dest.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        _invalido("destinatario.nome inválido.")
    email = dest.get("email")
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        _invalido("destinatario.email inválido.")
    data_hora = corpo.get("data_hora")
    dh = _validar_data_hora(data_hora)

    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

    cotacao = _buscar_cotacao(cotacao_id)

    try:
        valida_ate = date.fromisoformat(cotacao["valida_ate"])
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    if dh.date() > valida_ate:
        raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in cotacao.get("opcoes", []):
        if o.get("transportadora_id") == transportadora_id:
            opcao = o
            break
    if opcao is None:
        raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

    numero = _estado["contador"] + 1
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
        "historico": [{"status": "CRIADO", "data_hora": data_hora, "local": None}],
    }

    _publicar("envio.criado", data_hora, {
        "envio_id": eid,
        "codigo_rastreio": codigo,
        "pedido_id": pedido_id,
        "transportadora_id": transportadora_id,
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": email,
        "data_hora": data_hora,
    })

    _estado["contador"] = numero
    _estado["envios"][eid] = envio
    return JSONResponse(status_code=201, content=envio)


@app.get("/envios")
async def listar_envios(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        _invalido("Parâmetro pedido_id obrigatório.")
    itens = [_estado["envios"][k] for k in sorted(_estado["envios"]) if _estado["envios"][k]["pedido_id"] == pedido_id]
    return JSONResponse(status_code=200, content=itens)


@app.get("/envios/{eid}")
async def consultar_envio(eid: str):
    return JSONResponse(status_code=200, content=_obter_envio(eid))


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        _invalido("status inválido.")
    data_hora = corpo.get("data_hora")
    dh = _validar_data_hora(data_hora)
    local = corpo.get("local")
    if not isinstance(local, str) or local == "":
        _invalido("local inválido.")

    envio = _obter_envio(eid)

    if status not in TRANSICOES.get(envio["status"], set()):
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")

    ultimo = datetime.strptime(envio["historico"][-1]["data_hora"], "%Y-%m-%dT%H:%M:%S")
    if dh < ultimo:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novo = copy.deepcopy(envio)
    itens = [{"status": status, "data_hora": data_hora, "local": local}]
    novo["status"] = status
    if status == "FALHA_ENTREGA":
        novo["tentativas_falhas"] += 1
        if novo["tentativas_falhas"] >= 3:
            itens.append({"status": "DEVOLVIDO", "data_hora": data_hora, "local": local})
            novo["status"] = "DEVOLVIDO"
    novo["historico"].extend(itens)

    for item in itens:
        _publicar("envio.status_atualizado", data_hora, _dados_status(novo, item))

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=novo)


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    data_hora = corpo.get("data_hora")
    dh = _validar_data_hora(data_hora)

    envio = _obter_envio(eid)

    if envio["status"] != "CRIADO":
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio CRIADO.")

    ultimo = datetime.strptime(envio["historico"][-1]["data_hora"], "%Y-%m-%dT%H:%M:%S")
    if dh < ultimo:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novo = copy.deepcopy(envio)
    item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
    novo["status"] = "CANCELADO"
    novo["historico"].append(item)

    _publicar("envio.status_atualizado", data_hora, _dados_status(novo, item))

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=novo)
