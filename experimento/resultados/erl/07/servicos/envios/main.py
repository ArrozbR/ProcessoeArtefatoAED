# arquivo: servicos/envios/main.py
import copy
import json
import os
import re
import uuid
from datetime import datetime

import httpx
from fastapi import FastAPI, Request
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

_estado = {"envios": {}, "contador": 0}


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 422:
        return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    raise ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        bruto = await request.body()
        corpo = json.loads(bruto)
    except Exception:
        _invalido("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _validar_data_hora(corpo):
    if "data_hora" not in corpo:
        _invalido("Campo 'data_hora' ausente.")
    texto = corpo["data_hora"]
    if not isinstance(texto, str) or not RE_DATA_HORA.fullmatch(texto):
        _invalido("Campo 'data_hora' inválido.")
    try:
        return datetime.strptime(texto, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        _invalido("Campo 'data_hora' inválido.")


def _texto_nao_vazio(obj, campo, prefixo=""):
    if campo not in obj:
        _invalido(f"Campo '{prefixo}{campo}' ausente.")
    valor = obj[campo]
    if not isinstance(valor, str) or not valor.strip():
        _invalido(f"Campo '{prefixo}{campo}' inválido.")
    return valor


def _texto_presente(obj, campo):
    if campo not in obj:
        _invalido(f"Campo '{campo}' ausente.")
    valor = obj[campo]
    if not isinstance(valor, str) or len(valor) == 0:
        _invalido(f"Campo '{campo}' inválido.")
    return valor


def _publicar(tipo, ocorrido_em, dados):
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


def _obter_envio(eid):
    envio = _estado["envios"].get(eid)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return envio


def _ultima_data_hora(envio):
    return datetime.strptime(envio["historico"][-1]["data_hora"], "%Y-%m-%dT%H:%M:%S")


@app.post("/admin/reset")
def reset():
    _estado["envios"].clear()
    _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/envios")
async def criar(request: Request):
    corpo = await _ler_corpo(request)

    pedido_id = _texto_nao_vazio(corpo, "pedido_id")
    cotacao_id = _texto_presente(corpo, "cotacao_id")
    transportadora_id = _texto_presente(corpo, "transportadora_id")
    if "destinatario" not in corpo or not isinstance(corpo["destinatario"], dict):
        _invalido("Campo 'destinatario' ausente ou inválido.")
    destinatario = corpo["destinatario"]
    nome = _texto_nao_vazio(destinatario, "nome", "destinatario.")
    if "email" not in destinatario:
        _invalido("Campo 'destinatario.email' ausente.")
    email = destinatario["email"]
    if not isinstance(email, str) or not RE_EMAIL.fullmatch(email):
        _invalido("Campo 'destinatario.email' inválido.")
    data_hora = _validar_data_hora(corpo)

    for envio in _estado["envios"].values():
        if envio["pedido_id"] == pedido_id and envio["status"] != "CANCELADO":
            raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

    url = os.environ["URL_COTACAO"] + "/cotacoes/" + cotacao_id
    try:
        resposta = httpx.get(url, timeout=10.0)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    if resposta.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resposta.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    try:
        cotacao = resposta.json()
        valida_ate = datetime.strptime(cotacao["valida_ate"], "%Y-%m-%d").date()
        opcoes = cotacao["opcoes"]
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")

    if data_hora.date() > valida_ate:
        raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in opcoes:
        if o.get("transportadora_id") == transportadora_id:
            opcao = o
            break
    if opcao is None:
        raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

    _estado["contador"] += 1
    numero = _estado["contador"]
    data_hora_txt = corpo["data_hora"]
    envio = {
        "id": "ENV-%06d" % numero,
        "codigo_rastreio": "LG%09dBR" % numero,
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

    _publicar("envio.criado", data_hora_txt, {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": pedido_id,
        "transportadora_id": transportadora_id,
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": email,
        "data_hora": data_hora_txt,
    })
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        _invalido("Parâmetro 'pedido_id' ausente.")
    envios = [e for e in _estado["envios"].values() if e["pedido_id"] == pedido_id]
    envios.sort(key=lambda e: e["id"])
    return JSONResponse(status_code=200, content=copy.deepcopy(envios))


@app.get("/envios/{eid}")
def consultar(eid: str):
    envio = _obter_envio(eid)
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    if "status" not in corpo:
        _invalido("Campo 'status' ausente.")
    status = corpo["status"]
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        _invalido("Campo 'status' inválido.")
    data_hora = _validar_data_hora(corpo)
    if "local" not in corpo:
        _invalido("Campo 'local' ausente.")
    local = corpo["local"]
    if not isinstance(local, str) or len(local) == 0:
        _invalido("Campo 'local' inválido.")

    envio = _obter_envio(eid)

    if status not in TRANSICOES.get(envio["status"], set()):
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")

    if data_hora < _ultima_data_hora(envio):
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    data_hora_txt = corpo["data_hora"]
    novos = [{"status": status, "data_hora": data_hora_txt, "local": local}]
    envio["historico"].append(novos[0])
    envio["status"] = status
    if status == "FALHA_ENTREGA":
        envio["tentativas_falhas"] += 1
        if envio["tentativas_falhas"] >= 3:
            item = {"status": "DEVOLVIDO", "data_hora": data_hora_txt, "local": local}
            envio["historico"].append(item)
            envio["status"] = "DEVOLVIDO"
            novos.append(item)

    for item in novos:
        _publicar("envio.status_atualizado", data_hora_txt, _evento_status(envio, item))

    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    data_hora = _validar_data_hora(corpo)

    envio = _obter_envio(eid)

    if envio["status"] != "CRIADO":
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio CRIADO.")

    if data_hora < _ultima_data_hora(envio):
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    data_hora_txt = corpo["data_hora"]
    item = {"status": "CANCELADO", "data_hora": data_hora_txt, "local": None}
    envio["historico"].append(item)
    envio["status"] = "CANCELADO"

    _publicar("envio.status_atualizado", data_hora_txt, _evento_status(envio, item))

    return JSONResponse(status_code=200, content=copy.deepcopy(envio))
