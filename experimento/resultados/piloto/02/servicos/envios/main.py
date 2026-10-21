# arquivo: servicos/envios/main.py
import copy
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

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_estado = {"envios": {}, "contador": 0}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "ROTA_NAO_ENCONTRADA", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_corpo(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def data_hora_valida(texto) -> bool:
    if not isinstance(texto, str):
        return False
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", texto):
        return False
    try:
        datetime.strptime(texto, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return True


def texto_nao_vazio(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    envelope = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    httpx.post(os.environ["URL_BARRAMENTO"] + "/publicar", json=envelope, timeout=10)


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


@app.post("/envios")
async def criar(request: Request):
    corpo = await ler_corpo(request)
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Corpo inválido.")
    pedido_id = corpo.get("pedido_id")
    cotacao_id = corpo.get("cotacao_id")
    transportadora_id = corpo.get("transportadora_id")
    destinatario = corpo.get("destinatario")
    data_hora = corpo.get("data_hora")
    if not texto_nao_vazio(pedido_id):
        return erro(422, "DADOS_INVALIDOS", "pedido_id inválido.")
    if not isinstance(cotacao_id, str) or cotacao_id == "":
        return erro(422, "DADOS_INVALIDOS", "cotacao_id inválido.")
    if not isinstance(transportadora_id, str) or transportadora_id == "":
        return erro(422, "DADOS_INVALIDOS", "transportadora_id inválido.")
    if not isinstance(destinatario, dict):
        return erro(422, "DADOS_INVALIDOS", "destinatario inválido.")
    nome = destinatario.get("nome")
    email = destinatario.get("email")
    if not texto_nao_vazio(nome):
        return erro(422, "DADOS_INVALIDOS", "Nome do destinatário inválido.")
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        return erro(422, "DADOS_INVALIDOS", "E-mail do destinatário inválido.")
    if not data_hora_valida(data_hora):
        return erro(422, "DADOS_INVALIDOS", "data_hora inválida.")

    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            return erro(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

    try:
        resp = httpx.get(os.environ["URL_COTACAO"] + "/cotacoes/" + cotacao_id, timeout=10)
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if resp.status_code == 404:
        return erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    try:
        cotacao = resp.json()
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")

    if data_hora[:10] > cotacao["valida_ate"]:
        return erro(422, "COTACAO_EXPIRADA", "A cotação expirou.")

    opcao = None
    for o in cotacao.get("opcoes", []):
        if o.get("transportadora_id") == transportadora_id:
            opcao = o
            break
    if opcao is None:
        return erro(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

    numero = _estado["contador"] + 1
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
        "historico": [{"status": "CRIADO", "data_hora": data_hora, "local": None}],
    }
    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio

    publicar("envio.criado", data_hora, {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": pedido_id,
        "transportadora_id": transportadora_id,
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": email,
        "data_hora": data_hora,
    })
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        return erro(422, "DADOS_INVALIDOS", "Parâmetro pedido_id obrigatório.")
    ids = sorted(eid for eid, e in _estado["envios"].items() if e["pedido_id"] == pedido_id)
    return JSONResponse(status_code=200, content=[copy.deepcopy(_estado["envios"][i]) for i in ids])


@app.get("/envios/{eid}")
async def consultar(eid: str):
    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    corpo = await ler_corpo(request)
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Corpo inválido.")
    status = corpo.get("status")
    data_hora = corpo.get("data_hora")
    local = corpo.get("local")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        return erro(422, "DADOS_INVALIDOS", "status inválido.")
    if not data_hora_valida(data_hora):
        return erro(422, "DADOS_INVALIDOS", "data_hora inválida.")
    if not texto_nao_vazio(local):
        return erro(422, "DADOS_INVALIDOS", "local inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if (envio["status"], status) not in TRANSICOES:
        return erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
    if data_hora < envio["historico"][-1]["data_hora"]:
        return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novos = [{"status": status, "data_hora": data_hora, "local": local}]
    envio["historico"].append(novos[0])
    envio["status"] = status
    if status == "FALHA_ENTREGA":
        envio["tentativas_falhas"] += 1
        if envio["tentativas_falhas"] >= 3:
            item = {"status": "DEVOLVIDO", "data_hora": data_hora, "local": local}
            envio["historico"].append(item)
            envio["status"] = "DEVOLVIDO"
            novos.append(item)

    for item in novos:
        publicar("envio.status_atualizado", item["data_hora"], evento_status(envio, item))
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    corpo = await ler_corpo(request)
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Corpo inválido.")
    data_hora = corpo.get("data_hora")
    if not data_hora_valida(data_hora):
        return erro(422, "DADOS_INVALIDOS", "data_hora inválida.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if envio["status"] != "CRIADO":
        return erro(409, "TRANSICAO_INVALIDA", "Só envios CRIADO podem ser cancelados.")
    if data_hora < envio["historico"][-1]["data_hora"]:
        return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
    envio["historico"].append(item)
    envio["status"] = "CANCELADO"
    publicar("envio.status_atualizado", data_hora, evento_status(envio, item))
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/admin/reset")
async def reset():
    _estado["envios"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)
