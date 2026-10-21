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

RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
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

_estado = {"envios": {}, "contador": 0}


class FalhaServico(Exception):
    pass


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


def servico_indisponivel(mensagem="Serviço indisponível."):
    return erro(503, "SERVICO_INDISPONIVEL", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request, exc):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request, exc):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


_INVALIDO = object()


async def ler_json(request):
    try:
        return await request.json()
    except Exception:
        return _INVALIDO


def data_hora_valida(s):
    if not isinstance(s, str) or not RE_DATA_HORA.fullmatch(s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return True


def texto_nao_branco(v):
    return isinstance(v, str) and v.strip() != ""


def publicar(tipo, ocorrido_em, dados):
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        url = os.environ["URL_BARRAMENTO"]
        resp = httpx.post(f"{url}/publicar", json=evento, timeout=10)
    except Exception:
        raise FalhaServico()
    if resp.status_code >= 400:
        raise FalhaServico()


def evento_status(envio, item):
    return {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    }


def validar_criacao(corpo):
    if not isinstance(corpo, dict):
        return "Corpo deve ser um objeto JSON."
    if not texto_nao_branco(corpo.get("pedido_id")):
        return "Campo 'pedido_id' inválido."
    for campo in ("cotacao_id", "transportadora_id"):
        v = corpo.get(campo)
        if not isinstance(v, str) or v == "":
            return f"Campo '{campo}' inválido."
    dest = corpo.get("destinatario")
    if not isinstance(dest, dict):
        return "Campo 'destinatario' inválido."
    if not texto_nao_branco(dest.get("nome")):
        return "Campo 'destinatario.nome' inválido."
    email = dest.get("email")
    if not isinstance(email, str) or not RE_EMAIL.match(email):
        return "Campo 'destinatario.email' inválido."
    if not data_hora_valida(corpo.get("data_hora")):
        return "Campo 'data_hora' inválido."
    return None


@app.post("/admin/reset")
async def reset():
    _estado["envios"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/envios")
async def criar(request: Request):
    corpo = await ler_json(request)
    if corpo is _INVALIDO:
        return dados_invalidos("Corpo JSON inválido.")
    problema = validar_criacao(corpo)
    if problema:
        return dados_invalidos(problema)

    pedido_id = corpo["pedido_id"]
    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            return erro(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

    try:
        url = os.environ["URL_COTACAO"]
        resp = httpx.get(f"{url}/cotacoes/{corpo['cotacao_id']}", timeout=10)
    except Exception:
        return servico_indisponivel("Serviço Cotação indisponível.")
    if resp.status_code == 404:
        return erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        return servico_indisponivel("Serviço Cotação indisponível.")
    try:
        cotacao = resp.json()
        valida_ate = cotacao["valida_ate"]
        opcoes = cotacao["opcoes"]
    except Exception:
        return servico_indisponivel("Resposta inválida do serviço Cotação.")

    data_hora = corpo["data_hora"]
    if data_hora[:10] > valida_ate:
        return erro(422, "COTACAO_EXPIRADA", "A cotação expirou.")

    opcao = None
    for o in opcoes:
        if isinstance(o, dict) and o.get("transportadora_id") == corpo["transportadora_id"]:
            opcao = o
            break
    if opcao is None:
        return erro(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

    numero = _estado["contador"] + 1
    envio = {
        "id": f"ENV-{numero:06d}",
        "codigo_rastreio": f"LG{numero:09d}BR",
        "pedido_id": pedido_id,
        "cotacao_id": corpo["cotacao_id"],
        "transportadora_id": corpo["transportadora_id"],
        "preco_centavos": opcao["preco_centavos"],
        "data_prevista_entrega": opcao["data_prevista_entrega"],
        "destinatario": {
            "nome": corpo["destinatario"]["nome"],
            "email": corpo["destinatario"]["email"],
        },
        "status": "CRIADO",
        "tentativas_falhas": 0,
        "historico": [{"status": "CRIADO", "data_hora": data_hora, "local": None}],
    }

    dados = {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "transportadora_id": envio["transportadora_id"],
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": envio["destinatario"]["email"],
        "data_hora": data_hora,
    }
    try:
        publicar("envio.criado", data_hora, dados)
    except FalhaServico:
        return servico_indisponivel("Barramento indisponível.")

    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        return dados_invalidos("Parâmetro 'pedido_id' é obrigatório.")
    itens = [
        copy.deepcopy(e)
        for _, e in sorted(_estado["envios"].items())
        if e["pedido_id"] == pedido_id
    ]
    return JSONResponse(status_code=200, content=itens)


@app.get("/envios/{eid}")
async def consultar(eid: str):
    e = _estado["envios"].get(eid)
    if e is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=copy.deepcopy(e))


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    corpo = await ler_json(request)
    if corpo is _INVALIDO or not isinstance(corpo, dict):
        return dados_invalidos("Corpo JSON inválido.")
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        return dados_invalidos("Campo 'status' inválido.")
    data_hora = corpo.get("data_hora")
    if not data_hora_valida(data_hora):
        return dados_invalidos("Campo 'data_hora' inválido.")
    local = corpo.get("local")
    if not isinstance(local, str) or local == "":
        return dados_invalidos("Campo 'local' inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if (envio["status"], status) not in TRANSICOES:
        return erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")

    if data_hora < envio["historico"][-1]["data_hora"]:
        return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último registro.")

    novo = copy.deepcopy(envio)
    novos_itens = [{"status": status, "data_hora": data_hora, "local": local}]
    novo["status"] = status
    if status == "FALHA_ENTREGA":
        novo["tentativas_falhas"] += 1
        if novo["tentativas_falhas"] >= 3:
            novos_itens.append({"status": "DEVOLVIDO", "data_hora": data_hora, "local": local})
            novo["status"] = "DEVOLVIDO"
    novo["historico"].extend(novos_itens)

    try:
        for item in novos_itens:
            publicar("envio.status_atualizado", data_hora, evento_status(novo, item))
    except FalhaServico:
        return servico_indisponivel("Barramento indisponível.")

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    corpo = await ler_json(request)
    if corpo is _INVALIDO or not isinstance(corpo, dict):
        return dados_invalidos("Corpo JSON inválido.")
    data_hora = corpo.get("data_hora")
    if not data_hora_valida(data_hora):
        return dados_invalidos("Campo 'data_hora' inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if envio["status"] != "CRIADO":
        return erro(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")
    if data_hora < envio["historico"][-1]["data_hora"]:
        return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último registro.")

    novo = copy.deepcopy(envio)
    item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
    novo["status"] = "CANCELADO"
    novo["historico"].append(item)

    try:
        publicar("envio.status_atualizado", data_hora, evento_status(novo, item))
    except FalhaServico:
        return servico_indisponivel("Barramento indisponível.")

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))
