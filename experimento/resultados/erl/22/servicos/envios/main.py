# arquivo: servicos/envios/main.py
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

_estado = {"contador": 0, "envios": {}}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request: Request, exc: RequestValidationError):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def _data_hora_valida(valor) -> bool:
    if not isinstance(valor, str) or not DATA_HORA_RE.fullmatch(valor):
        return False
    try:
        datetime.strptime(valor, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return True


def _texto_nao_vazio(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


async def _ler_json(request: Request):
    try:
        return True, await request.json()
    except Exception:
        return False, None


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    envelope = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        url = os.environ["URL_BARRAMENTO"]
        with httpx.Client(timeout=10.0) as cliente:
            cliente.post(f"{url}/publicar", json=envelope)
    except Exception:
        pass


def _evento_status(envio: dict, item: dict) -> None:
    _publicar("envio.status_atualizado", item["data_hora"], {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    })


def _validar_criacao(corpo):
    if not isinstance(corpo, dict):
        return "Corpo deve ser um objeto JSON."
    if not _texto_nao_vazio(corpo.get("pedido_id")):
        return "Campo 'pedido_id' inválido."
    cot = corpo.get("cotacao_id")
    if not isinstance(cot, str) or cot == "":
        return "Campo 'cotacao_id' inválido."
    tr = corpo.get("transportadora_id")
    if not isinstance(tr, str) or tr == "":
        return "Campo 'transportadora_id' inválido."
    dest = corpo.get("destinatario")
    if not isinstance(dest, dict):
        return "Campo 'destinatario' inválido."
    if not _texto_nao_vazio(dest.get("nome")):
        return "Campo 'destinatario.nome' inválido."
    email = dest.get("email")
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        return "Campo 'destinatario.email' inválido."
    if not _data_hora_valida(corpo.get("data_hora")):
        return "Campo 'data_hora' inválido."
    return None


@app.post("/envios")
async def criar(request: Request):
    ok, corpo = await _ler_json(request)
    if not ok:
        return dados_invalidos("Corpo JSON inválido.")
    problema = _validar_criacao(corpo)
    if problema:
        return dados_invalidos(problema)

    pedido_id = corpo["pedido_id"]
    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            return erro(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

    try:
        url = os.environ["URL_COTACAO"]
        with httpx.Client(timeout=10.0) as cliente:
            resp = cliente.get(f"{url}/cotacoes/{corpo['cotacao_id']}")
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if resp.status_code == 404:
        return erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    try:
        cotacao = resp.json()
        valida_ate = cotacao["valida_ate"]
        opcoes = cotacao["opcoes"]
    except Exception:
        return erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de cotação.")

    if corpo["data_hora"][:10] > valida_ate:
        return erro(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in opcoes:
        if isinstance(o, dict) and o.get("transportadora_id") == corpo["transportadora_id"]:
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
        "historico": [{"status": "CRIADO", "data_hora": corpo["data_hora"], "local": None}],
    }
    _estado["envios"][envio["id"]] = envio

    _publicar("envio.criado", corpo["data_hora"], {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "transportadora_id": envio["transportadora_id"],
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": envio["destinatario"]["email"],
        "data_hora": corpo["data_hora"],
    })
    return JSONResponse(status_code=201, content=envio)


@app.get("/envios")
async def listar(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        return dados_invalidos("Parâmetro 'pedido_id' é obrigatório.")
    itens = [e for e in _estado["envios"].values() if e["pedido_id"] == pedido_id]
    itens.sort(key=lambda e: e["id"])
    return JSONResponse(status_code=200, content=itens)


@app.get("/envios/{eid}")
async def consultar(eid: str):
    e = _estado["envios"].get(eid)
    if e is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=e)


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    ok, corpo = await _ler_json(request)
    if not ok or not isinstance(corpo, dict):
        return dados_invalidos("Corpo JSON inválido.")
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        return dados_invalidos("Campo 'status' inválido.")
    data_hora = corpo.get("data_hora")
    if not _data_hora_valida(data_hora):
        return dados_invalidos("Campo 'data_hora' inválido.")
    local = corpo.get("local")
    if not _texto_nao_vazio(local):
        return dados_invalidos("Campo 'local' inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if (envio["status"], status) not in TRANSICOES:
        return erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
    if data_hora < envio["historico"][-1]["data_hora"]:
        return erro(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

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
        _evento_status(envio, item)
    return JSONResponse(status_code=200, content=envio)


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    ok, corpo = await _ler_json(request)
    if not ok or not isinstance(corpo, dict):
        return dados_invalidos("Corpo JSON inválido.")
    data_hora = corpo.get("data_hora")
    if not _data_hora_valida(data_hora):
        return dados_invalidos("Campo 'data_hora' inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if envio["status"] != "CRIADO":
        return erro(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")
    if data_hora < envio["historico"][-1]["data_hora"]:
        return erro(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

    item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
    envio["historico"].append(item)
    envio["status"] = "CANCELADO"
    _evento_status(envio, item)
    return JSONResponse(status_code=200, content=envio)


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["envios"] = {}
    return Response(status_code=204)
