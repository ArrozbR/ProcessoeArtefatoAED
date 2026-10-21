# arquivo: servicos/envios/main.py
import asyncio
import copy
import os
import re
import uuid
from datetime import date, datetime

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
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

_lock = asyncio.Lock()
_envios: dict = {}
_contador = {"valor": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resposta_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _invalido(mensagem: str = "Dados inválidos na requisição.") -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo JSON ausente ou inválido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _data_hora_valida(v) -> bool:
    if not isinstance(v, str) or not RE_DATA_HORA.fullmatch(v):
        return False
    try:
        datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return True


def _buscar_cotacao(cotacao_id: str) -> dict:
    url = os.environ["URL_COTACAO"] + "/cotacoes/" + cotacao_id
    try:
        with httpx.Client(timeout=10.0) as cliente:
            resp = cliente.get(url)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    if resp.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    if not isinstance(dados, dict):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    return dados


def _publicar(eventos: list) -> None:
    try:
        url = os.environ["URL_BARRAMENTO"] + "/publicar"
    except Exception:
        return
    try:
        with httpx.Client(timeout=10.0) as cliente:
            for evento in eventos:
                try:
                    cliente.post(url, json=evento)
                except Exception:
                    pass
    except Exception:
        pass


def _evento(tipo: str, ocorrido_em: str, dados: dict) -> dict:
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def _evento_status(envio: dict, item: dict) -> dict:
    return _evento("envio.status_atualizado", item["data_hora"], {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    })


@app.post("/envios")
async def criar_envio(request: Request):
    corpo = await _ler_corpo(request)
    pedido_id = corpo.get("pedido_id")
    if not isinstance(pedido_id, str) or not pedido_id.strip():
        raise _invalido("Campo 'pedido_id' inválido.")
    cotacao_id = corpo.get("cotacao_id")
    if not isinstance(cotacao_id, str) or not cotacao_id:
        raise _invalido("Campo 'cotacao_id' inválido.")
    transportadora_id = corpo.get("transportadora_id")
    if not isinstance(transportadora_id, str) or not transportadora_id:
        raise _invalido("Campo 'transportadora_id' inválido.")
    destinatario = corpo.get("destinatario")
    if not isinstance(destinatario, dict):
        raise _invalido("Campo 'destinatario' inválido.")
    nome = destinatario.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        raise _invalido("Campo 'destinatario.nome' inválido.")
    email = destinatario.get("email")
    if not isinstance(email, str) or not RE_EMAIL.search(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    data_hora = corpo.get("data_hora")
    if not _data_hora_valida(data_hora):
        raise _invalido("Campo 'data_hora' inválido.")

    async with _lock:
        for e in _envios.values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

        cotacao = await run_in_threadpool(_buscar_cotacao, cotacao_id)

        valida_ate = cotacao.get("valida_ate")
        try:
            limite = date.fromisoformat(valida_ate)
        except Exception:
            raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
        if date.fromisoformat(data_hora[:10]) > limite:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação expirou.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "A transportadora não está entre as opções da cotação.")

        _contador["valor"] += 1
        numero = _contador["valor"]
        envio = {
            "id": f"ENV-{numero:06d}",
            "codigo_rastreio": f"LG{numero:09d}BR",
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
        _envios[envio["id"]] = envio
        resposta = copy.deepcopy(envio)

        evento = _evento("envio.criado", data_hora, {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": pedido_id,
            "transportadora_id": transportadora_id,
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": email,
            "data_hora": data_hora,
        })
        await run_in_threadpool(_publicar, [evento])

    return JSONResponse(status_code=201, content=resposta)


@app.get("/envios")
async def listar_envios(pedido_id: str = Query(...)):
    async with _lock:
        lista = [copy.deepcopy(_envios[k]) for k in sorted(_envios) if _envios[k]["pedido_id"] == pedido_id]
    return JSONResponse(status_code=200, content=lista)


@app.get("/envios/{envio_id}")
async def consultar_envio(envio_id: str):
    async with _lock:
        e = _envios.get(envio_id)
        if e is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        resposta = copy.deepcopy(e)
    return JSONResponse(status_code=200, content=resposta)


@app.post("/envios/{envio_id}/atualizacoes")
async def atualizar_envio(envio_id: str, request: Request):
    corpo = await _ler_corpo(request)
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    data_hora = corpo.get("data_hora")
    if not _data_hora_valida(data_hora):
        raise _invalido("Campo 'data_hora' inválido.")
    local = corpo.get("local")
    if not isinstance(local, str) or not local:
        raise _invalido("Campo 'local' inválido.")

    async with _lock:
        envio = _envios.get(envio_id)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if (envio["status"], status) not in TRANSICOES:
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
        if data_hora < envio["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior à do último item do histórico.")

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

        resposta = copy.deepcopy(envio)
        eventos = [_evento_status(envio, item) for item in novos]
        await run_in_threadpool(_publicar, eventos)

    return JSONResponse(status_code=200, content=resposta)


@app.post("/envios/{envio_id}/cancelar")
async def cancelar_envio(envio_id: str, request: Request):
    corpo = await _ler_corpo(request)
    data_hora = corpo.get("data_hora")
    if not _data_hora_valida(data_hora):
        raise _invalido("Campo 'data_hora' inválido.")

    async with _lock:
        envio = _envios.get(envio_id)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")
        if data_hora < envio["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior à do último item do histórico.")

        item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
        envio["historico"].append(item)
        envio["status"] = "CANCELADO"
        resposta = copy.deepcopy(envio)
        await run_in_threadpool(_publicar, [_evento_status(envio, item)])

    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    async with _lock:
        _envios.clear()
        _contador["valor"] = 0
    return Response(status_code=204)
