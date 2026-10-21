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
DATA_HORA_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")

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


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return invalido("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request):
    try:
        corpo = await request.body()
        dados = json.loads(corpo)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    return dados


def data_hora_valida(v) -> bool:
    if not isinstance(v, str) or not DATA_HORA_RE.fullmatch(v):
        return False
    try:
        datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return True


def texto_nao_vazio(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        url = os.environ["URL_BARRAMENTO"]
        httpx.post(f"{url}/publicar", json=evento, timeout=10)
    except Exception:
        pass


def evento_status(envio: dict, item: dict) -> None:
    publicar(
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


@app.post("/admin/reset")
async def reset():
    _estado["envios"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/envios")
async def criar(request: Request):
    d = await ler_json(request)
    if d is None:
        return invalido("Corpo da requisição inválido.")
    if not texto_nao_vazio(d.get("pedido_id")):
        return invalido("Campo 'pedido_id' inválido.")
    for campo in ("cotacao_id", "transportadora_id"):
        v = d.get(campo)
        if not isinstance(v, str) or v == "":
            return invalido(f"Campo '{campo}' inválido.")
    dest = d.get("destinatario")
    if not isinstance(dest, dict):
        return invalido("Campo 'destinatario' inválido.")
    if not texto_nao_vazio(dest.get("nome")):
        return invalido("Campo 'destinatario.nome' inválido.")
    email = dest.get("email")
    if not isinstance(email, str) or not EMAIL_RE.search(email):
        return invalido("Campo 'destinatario.email' inválido.")
    if not data_hora_valida(d.get("data_hora")):
        return invalido("Campo 'data_hora' inválido.")

    pedido_id = d["pedido_id"]
    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            return erro(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

    try:
        url = os.environ["URL_COTACAO"]
        resp = httpx.get(f"{url}/cotacoes/{d['cotacao_id']}", timeout=10)
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    if resp.status_code == 404:
        return erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    try:
        cotacao = resp.json()
        valida_ate = date.fromisoformat(cotacao["valida_ate"])
        opcoes = cotacao["opcoes"]
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")

    data_envio = date.fromisoformat(d["data_hora"][:10])
    if data_envio > valida_ate:
        return erro(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in opcoes:
        if o.get("transportadora_id") == d["transportadora_id"]:
            opcao = o
            break
    if opcao is None:
        return erro(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

    _estado["contador"] += 1
    n = _estado["contador"]
    envio = {
        "id": f"ENV-{n:06d}",
        "codigo_rastreio": f"LG{n:09d}BR",
        "pedido_id": pedido_id,
        "cotacao_id": d["cotacao_id"],
        "transportadora_id": d["transportadora_id"],
        "preco_centavos": opcao["preco_centavos"],
        "data_prevista_entrega": opcao["data_prevista_entrega"],
        "destinatario": {"nome": dest["nome"], "email": email},
        "status": "CRIADO",
        "tentativas_falhas": 0,
        "historico": [{"status": "CRIADO", "data_hora": d["data_hora"], "local": None}],
    }
    _estado["envios"][envio["id"]] = envio

    publicar(
        "envio.criado",
        d["data_hora"],
        {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "transportadora_id": envio["transportadora_id"],
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": email,
            "data_hora": d["data_hora"],
        },
    )
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar(request: Request):
    if "pedido_id" not in request.query_params:
        return invalido("Parâmetro 'pedido_id' é obrigatório.")
    pedido_id = request.query_params["pedido_id"]
    lista = [
        copy.deepcopy(_estado["envios"][k])
        for k in sorted(_estado["envios"])
        if _estado["envios"][k]["pedido_id"] == pedido_id
    ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/envios/{eid}")
async def consultar(eid: str):
    e = _estado["envios"].get(eid)
    if e is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=copy.deepcopy(e))


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    d = await ler_json(request)
    if d is None:
        return invalido("Corpo da requisição inválido.")
    status = d.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        return invalido("Campo 'status' inválido.")
    if not data_hora_valida(d.get("data_hora")):
        return invalido("Campo 'data_hora' inválido.")
    if not texto_nao_vazio(d.get("local")):
        return invalido("Campo 'local' inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if (envio["status"], status) not in TRANSICOES:
        return erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
    data_hora = d["data_hora"]
    if data_hora < envio["historico"][-1]["data_hora"]:
        return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    local = d["local"]
    novos = [{"status": status, "data_hora": data_hora, "local": local}]
    envio["status"] = status
    if status == "FALHA_ENTREGA":
        envio["tentativas_falhas"] += 1
        if envio["tentativas_falhas"] >= 3:
            novos.append({"status": "DEVOLVIDO", "data_hora": data_hora, "local": local})
            envio["status"] = "DEVOLVIDO"
    envio["historico"].extend(novos)

    for item in novos:
        evento_status(envio, item)
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    d = await ler_json(request)
    if d is None:
        return invalido("Corpo da requisição inválido.")
    if not data_hora_valida(d.get("data_hora")):
        return invalido("Campo 'data_hora' inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if envio["status"] != "CRIADO":
        return erro(409, "TRANSICAO_INVALIDA", "O envio só pode ser cancelado no estado CRIADO.")
    data_hora = d["data_hora"]
    if data_hora < envio["historico"][-1]["data_hora"]:
        return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
    envio["status"] = "CANCELADO"
    envio["historico"].append(item)
    evento_status(envio, item)
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))
