# arquivo: servicos/envios/main.py
import copy
import json
import os
import re
import uuid
from datetime import date, datetime
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATA_HORA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

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

_estado = {"contador": 0, "envios": {}}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request, exc):
    return invalido("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request, exc):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_corpo(request):
    try:
        raw = await request.body()
        dados = json.loads(raw)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    return dados


def parse_data_hora(v):
    if not isinstance(v, str) or not DATA_HORA_RE.match(v):
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def parse_data(v):
    if not isinstance(v, str) or not DATA_RE.match(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def texto_nao_vazio(v):
    return isinstance(v, str) and v.strip() != ""


def publicar(evento):
    try:
        base = os.environ["URL_BARRAMENTO"]
        httpx.post(f"{base}/publicar", json=evento, timeout=10)
    except Exception:
        pass


def novo_evento(tipo, ocorrido_em, dados):
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def evento_status(envio, item):
    return novo_evento(
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


def validar_criacao(d):
    if not texto_nao_vazio(d.get("pedido_id")):
        return None
    cid = d.get("cotacao_id")
    if not isinstance(cid, str) or cid == "":
        return None
    tid = d.get("transportadora_id")
    if not isinstance(tid, str) or tid == "":
        return None
    dest = d.get("destinatario")
    if not isinstance(dest, dict):
        return None
    if not texto_nao_vazio(dest.get("nome")):
        return None
    email = dest.get("email")
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        return None
    return parse_data_hora(d.get("data_hora"))


@app.post("/envios")
async def criar(request: Request):
    d = await ler_corpo(request)
    if d is None:
        return invalido("Corpo inválido.")
    dh = validar_criacao(d)
    if dh is None:
        return invalido("Dados do envio inválidos.")

    pedido_id = d["pedido_id"]
    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            return erro(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

    base = os.environ["URL_COTACAO"]
    try:
        r = httpx.get(f"{base}/cotacoes/{quote(d['cotacao_id'], safe='')}", timeout=10)
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if r.status_code == 404:
        return erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if r.status_code != 200:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    try:
        cotacao = r.json()
        valida_ate = parse_data(cotacao["valida_ate"])
        opcoes = cotacao["opcoes"]
        if valida_ate is None or not isinstance(opcoes, list):
            raise ValueError
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de cotação.")

    if dh.date() > valida_ate:
        return erro(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in opcoes:
        if isinstance(o, dict) and o.get("transportadora_id") == d["transportadora_id"]:
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
        "destinatario": {"nome": d["destinatario"]["nome"], "email": d["destinatario"]["email"]},
        "status": "CRIADO",
        "tentativas_falhas": 0,
        "historico": [{"status": "CRIADO", "data_hora": d["data_hora"], "local": None}],
    }
    _estado["envios"][envio["id"]] = envio

    publicar(
        novo_evento(
            "envio.criado",
            d["data_hora"],
            {
                "envio_id": envio["id"],
                "codigo_rastreio": envio["codigo_rastreio"],
                "pedido_id": envio["pedido_id"],
                "transportadora_id": envio["transportadora_id"],
                "data_prevista_entrega": envio["data_prevista_entrega"],
                "destinatario_email": envio["destinatario"]["email"],
                "data_hora": d["data_hora"],
            },
        )
    )
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios/{eid}")
async def consultar(eid: str):
    e = _estado["envios"].get(eid)
    if e is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=copy.deepcopy(e))


@app.get("/envios")
async def listar(request: Request):
    if "pedido_id" not in request.query_params:
        return invalido("Parâmetro pedido_id obrigatório.")
    pedido_id = request.query_params["pedido_id"]
    lista = [
        copy.deepcopy(_estado["envios"][k])
        for k in sorted(_estado["envios"])
        if _estado["envios"][k]["pedido_id"] == pedido_id
    ]
    return JSONResponse(status_code=200, content=lista)


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    d = await ler_corpo(request)
    if d is None:
        return invalido("Corpo inválido.")
    status = d.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        return invalido("Status inválido.")
    dh = parse_data_hora(d.get("data_hora"))
    if dh is None:
        return invalido("data_hora inválida.")
    local = d.get("local")
    if not isinstance(local, str) or local == "":
        return invalido("local inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if (envio["status"], status) not in TRANSICOES:
        return erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")

    ultimo = parse_data_hora(envio["historico"][-1]["data_hora"])
    if dh < ultimo:
        return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novos = [{"status": status, "data_hora": d["data_hora"], "local": local}]
    envio["historico"].append(novos[0])
    envio["status"] = status
    if status == "FALHA_ENTREGA":
        envio["tentativas_falhas"] += 1
        if envio["tentativas_falhas"] >= 3:
            item = {"status": "DEVOLVIDO", "data_hora": d["data_hora"], "local": local}
            envio["historico"].append(item)
            envio["status"] = "DEVOLVIDO"
            novos.append(item)

    for item in novos:
        publicar(evento_status(envio, item))
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    d = await ler_corpo(request)
    if d is None:
        return invalido("Corpo inválido.")
    dh = parse_data_hora(d.get("data_hora"))
    if dh is None:
        return invalido("data_hora inválida.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if envio["status"] != "CRIADO":
        return erro(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")
    ultimo = parse_data_hora(envio["historico"][-1]["data_hora"])
    if dh < ultimo:
        return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    item = {"status": "CANCELADO", "data_hora": d["data_hora"], "local": None}
    envio["historico"].append(item)
    envio["status"] = "CANCELADO"
    publicar(evento_status(envio, item))
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["envios"].clear()
    return Response(status_code=204)
