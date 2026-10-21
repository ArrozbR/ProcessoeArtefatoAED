# arquivo: servicos/envios/main.py
import asyncio
import copy
import os
import re
import uuid
from datetime import date, datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
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

_lock = asyncio.Lock()
_envios: dict = {}
_contador = {"envio": 0}


class ServicoIndisponivel(Exception):
    pass


class CotacaoNaoEncontrada(Exception):
    pass


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
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def parse_data_hora(valor):
    if not isinstance(valor, str) or not DATA_HORA_RE.match(valor):
        return None
    try:
        return datetime.strptime(valor, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def texto_nao_branco(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def buscar_cotacao(cotacao_id: str):
    url = os.environ["URL_COTACAO"] + "/cotacoes/" + cotacao_id
    try:
        resp = httpx.get(url, timeout=10.0)
    except Exception:
        raise ServicoIndisponivel()
    if resp.status_code == 404:
        raise CotacaoNaoEncontrada()
    if resp.status_code != 200:
        raise ServicoIndisponivel()
    try:
        dados = resp.json()
    except Exception:
        raise ServicoIndisponivel()
    if not isinstance(dados, dict):
        raise ServicoIndisponivel()
    return dados


def publicar(tipo: str, ocorrido_em: str, dados: dict):
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
        raise ServicoIndisponivel()
    if resp.status_code >= 500:
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


@app.post("/envios")
async def criar(request: Request):
    corpo = await ler_json(request)
    if not isinstance(corpo, dict):
        return dados_invalidos("Corpo inválido.")
    pedido_id = corpo.get("pedido_id")
    cotacao_id = corpo.get("cotacao_id")
    transportadora_id = corpo.get("transportadora_id")
    destinatario = corpo.get("destinatario")
    data_hora_txt = corpo.get("data_hora")
    if not texto_nao_branco(pedido_id):
        return dados_invalidos("pedido_id inválido.")
    if not isinstance(cotacao_id, str) or cotacao_id == "":
        return dados_invalidos("cotacao_id inválido.")
    if not isinstance(transportadora_id, str) or transportadora_id == "":
        return dados_invalidos("transportadora_id inválido.")
    if not isinstance(destinatario, dict):
        return dados_invalidos("destinatario inválido.")
    nome = destinatario.get("nome")
    email = destinatario.get("email")
    if not texto_nao_branco(nome):
        return dados_invalidos("destinatario.nome inválido.")
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        return dados_invalidos("destinatario.email inválido.")
    data_hora = parse_data_hora(data_hora_txt)
    if data_hora is None:
        return dados_invalidos("data_hora inválida.")

    async with _lock:
        for e in _envios.values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                return erro(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

        try:
            cotacao = await run_in_threadpool(buscar_cotacao, cotacao_id)
        except CotacaoNaoEncontrada:
            return erro(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        except ServicoIndisponivel:
            return erro(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")

        try:
            valida_ate = date.fromisoformat(cotacao["valida_ate"])
        except Exception:
            return erro(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de cotação.")
        if data_hora.date() > valida_ate:
            return erro(422, "COTACAO_EXPIRADA", "A cotação expirou.")

        opcao = None
        for o in cotacao.get("opcoes", []) or []:
            if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
                opcao = o
                break
        if opcao is None:
            return erro(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _contador["envio"] + 1
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
        dados_evento = {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": pedido_id,
            "transportadora_id": transportadora_id,
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": email,
            "data_hora": data_hora_txt,
        }
        try:
            await run_in_threadpool(publicar, "envio.criado", data_hora_txt, dados_evento)
        except ServicoIndisponivel:
            return erro(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")

        _contador["envio"] = numero
        _envios[envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar(request: Request):
    if "pedido_id" not in request.query_params:
        return dados_invalidos("Parâmetro pedido_id é obrigatório.")
    pedido_id = request.query_params.get("pedido_id")
    async with _lock:
        itens = [copy.deepcopy(_envios[k]) for k in sorted(_envios) if _envios[k]["pedido_id"] == pedido_id]
    return JSONResponse(status_code=200, content=itens)


@app.get("/envios/{eid}")
async def consultar(eid: str):
    async with _lock:
        e = _envios.get(eid)
        if e is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        return JSONResponse(status_code=200, content=copy.deepcopy(e))


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    corpo = await ler_json(request)
    if not isinstance(corpo, dict):
        return dados_invalidos("Corpo inválido.")
    status = corpo.get("status")
    data_hora_txt = corpo.get("data_hora")
    local = corpo.get("local")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        return dados_invalidos("status inválido.")
    data_hora = parse_data_hora(data_hora_txt)
    if data_hora is None:
        return dados_invalidos("data_hora inválida.")
    if not isinstance(local, str) or local == "":
        return dados_invalidos("local inválido.")

    async with _lock:
        envio = _envios.get(eid)
        if envio is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if (envio["status"], status) not in TRANSICOES:
            return erro(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
        ultimo = parse_data_hora(envio["historico"][-1]["data_hora"])
        if data_hora < ultimo:
            return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

        novo = copy.deepcopy(envio)
        itens = [{"status": status, "data_hora": data_hora_txt, "local": local}]
        novo["status"] = status
        if status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                itens.append({"status": "DEVOLVIDO", "data_hora": data_hora_txt, "local": local})
                novo["status"] = "DEVOLVIDO"
        novo["historico"].extend(itens)

        try:
            for item in itens:
                await run_in_threadpool(
                    publicar, "envio.status_atualizado", item["data_hora"], evento_status(novo, item)
                )
        except ServicoIndisponivel:
            return erro(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")

        _envios[eid] = novo
        return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    corpo = await ler_json(request)
    if not isinstance(corpo, dict):
        return dados_invalidos("Corpo inválido.")
    data_hora_txt = corpo.get("data_hora")
    data_hora = parse_data_hora(data_hora_txt)
    if data_hora is None:
        return dados_invalidos("data_hora inválida.")

    async with _lock:
        envio = _envios.get(eid)
        if envio is None:
            return erro(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if envio["status"] != "CRIADO":
            return erro(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio CRIADO.")
        ultimo = parse_data_hora(envio["historico"][-1]["data_hora"])
        if data_hora < ultimo:
            return erro(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

        novo = copy.deepcopy(envio)
        item = {"status": "CANCELADO", "data_hora": data_hora_txt, "local": None}
        novo["status"] = "CANCELADO"
        novo["historico"].append(item)

        try:
            await run_in_threadpool(
                publicar, "envio.status_atualizado", data_hora_txt, evento_status(novo, item)
            )
        except ServicoIndisponivel:
            return erro(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")

        _envios[eid] = novo
        return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/admin/reset")
async def reset():
    async with _lock:
        _envios.clear()
        _contador["envio"] = 0
    return Response(status_code=204)
