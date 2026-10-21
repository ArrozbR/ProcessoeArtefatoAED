# arquivo: servicos/envios/main.py
import copy
import os
import re
import uuid
from datetime import datetime
from typing import Any

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
    ("CRIADO", "COLETADO"),
    ("COLETADO", "EM_TRANSITO"),
    ("EM_TRANSITO", "EM_TRANSITO"),
    ("EM_TRANSITO", "SAIU_PARA_ENTREGA"),
    ("SAIU_PARA_ENTREGA", "ENTREGUE"),
    ("SAIU_PARA_ENTREGA", "FALHA_ENTREGA"),
    ("FALHA_ENTREGA", "SAIU_PARA_ENTREGA"),
}

_estado: dict[str, Any] = {"contador": 0, "envios": {}}


class ServicoIndisponivel(Exception):
    pass


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return invalido("Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request) -> tuple[bool, Any]:
    try:
        return True, await request.json()
    except Exception:
        return False, None


def data_hora_valida(v: Any) -> bool:
    if not isinstance(v, str) or not DATA_HORA_RE.match(v):
        return False
    try:
        datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return True


def texto_nao_vazio(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    envelope = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    url = os.environ["URL_BARRAMENTO"] + "/publicar"
    try:
        r = httpx.post(url, json=envelope, timeout=10.0)
    except Exception as exc:
        raise ServicoIndisponivel() from exc
    if r.status_code >= 500:
        raise ServicoIndisponivel()


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


def validar_criacao(corpo: Any) -> str | None:
    if not isinstance(corpo, dict):
        return "Corpo deve ser um objeto JSON."
    if not texto_nao_vazio(corpo.get("pedido_id")):
        return "Campo 'pedido_id' inválido."
    cid = corpo.get("cotacao_id")
    if not isinstance(cid, str) or cid == "":
        return "Campo 'cotacao_id' inválido."
    tid = corpo.get("transportadora_id")
    if not isinstance(tid, str) or tid == "":
        return "Campo 'transportadora_id' inválido."
    dest = corpo.get("destinatario")
    if not isinstance(dest, dict):
        return "Campo 'destinatario' inválido."
    if not texto_nao_vazio(dest.get("nome")):
        return "Campo 'destinatario.nome' inválido."
    email = dest.get("email")
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        return "Campo 'destinatario.email' inválido."
    if not data_hora_valida(corpo.get("data_hora")):
        return "Campo 'data_hora' inválido."
    return None


def buscar_cotacao(cid: str) -> dict | None:
    url = os.environ["URL_COTACAO"] + "/cotacoes/" + cid
    try:
        r = httpx.get(url, timeout=10.0)
    except Exception as exc:
        raise ServicoIndisponivel() from exc
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise ServicoIndisponivel()
    try:
        dados = r.json()
    except Exception as exc:
        raise ServicoIndisponivel() from exc
    if not isinstance(dados, dict):
        raise ServicoIndisponivel()
    return dados


@app.post("/envios")
async def criar(request: Request):
    ok, corpo = await ler_json(request)
    if not ok:
        return invalido("JSON inválido.")
    msg = validar_criacao(corpo)
    if msg:
        return invalido(msg)

    pedido_id = corpo["pedido_id"]
    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            return erro(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

    try:
        cotacao = buscar_cotacao(corpo["cotacao_id"])
    except ServicoIndisponivel:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if cotacao is None:
        return erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")

    data_hora = corpo["data_hora"]
    if data_hora[:10] > str(cotacao.get("valida_ate", "")):
        return erro(422, "COTACAO_EXPIRADA", "Cotação expirada.")

    opcao = None
    for o in cotacao.get("opcoes") or []:
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

    dados_evento = {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "transportadora_id": envio["transportadora_id"],
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": envio["destinatario"]["email"],
        "data_hora": data_hora,
    }
    try:
        publicar("envio.criado", data_hora, dados_evento)
    except ServicoIndisponivel:
        return erro(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")

    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        return invalido("Parâmetro 'pedido_id' é obrigatório.")
    itens = [
        copy.deepcopy(_estado["envios"][k])
        for k in sorted(_estado["envios"])
        if _estado["envios"][k]["pedido_id"] == pedido_id
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
    ok, corpo = await ler_json(request)
    if not ok or not isinstance(corpo, dict):
        return invalido("Corpo inválido.")
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        return invalido("Campo 'status' inválido.")
    data_hora = corpo.get("data_hora")
    if not data_hora_valida(data_hora):
        return invalido("Campo 'data_hora' inválido.")
    local = corpo.get("local")
    if not isinstance(local, str) or local == "":
        return invalido("Campo 'local' inválido.")

    atual = _estado["envios"].get(eid)
    if atual is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if (atual["status"], status) not in TRANSICOES:
        return erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
    if data_hora < atual["historico"][-1]["data_hora"]:
        return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novo = copy.deepcopy(atual)
    novos_itens = []
    item = {"status": status, "data_hora": data_hora, "local": local}
    novo["historico"].append(item)
    novos_itens.append(item)
    novo["status"] = status
    if status == "FALHA_ENTREGA":
        novo["tentativas_falhas"] += 1
        if novo["tentativas_falhas"] >= 3:
            item2 = {"status": "DEVOLVIDO", "data_hora": data_hora, "local": local}
            novo["historico"].append(item2)
            novos_itens.append(item2)
            novo["status"] = "DEVOLVIDO"

    try:
        for it in novos_itens:
            publicar("envio.status_atualizado", it["data_hora"], evento_status(novo, it))
    except ServicoIndisponivel:
        return erro(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    ok, corpo = await ler_json(request)
    if not ok or not isinstance(corpo, dict):
        return invalido("Corpo inválido.")
    data_hora = corpo.get("data_hora")
    if not data_hora_valida(data_hora):
        return invalido("Campo 'data_hora' inválido.")

    atual = _estado["envios"].get(eid)
    if atual is None:
        return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if atual["status"] != "CRIADO":
        return erro(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")
    if data_hora < atual["historico"][-1]["data_hora"]:
        return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novo = copy.deepcopy(atual)
    item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
    novo["historico"].append(item)
    novo["status"] = "CANCELADO"

    try:
        publicar("envio.status_atualizado", data_hora, evento_status(novo, item))
    except ServicoIndisponivel:
        return erro(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["envios"] = {}
    return Response(status_code=204)
