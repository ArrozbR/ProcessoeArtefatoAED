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
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
    "ENTREGUE": set(),
    "CANCELADO": set(),
    "DEVOLVIDO": set(),
}

_lock = asyncio.Lock()
_envios: dict = {}
_contador = {"envio": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    if exc.status_code == 422:
        return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _texto_nao_branco(obj: dict, campo: str) -> str:
    if campo not in obj:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = obj[campo]
    if not isinstance(valor, str) or not valor.strip():
        raise _invalido(f"Campo '{campo}' não pode ser vazio.")
    return valor


def _texto_nao_vazio(obj: dict, campo: str) -> str:
    if campo not in obj:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = obj[campo]
    if not isinstance(valor, str) or valor == "":
        raise _invalido(f"Campo '{campo}' não pode ser vazio.")
    return valor


def _data_hora(obj: dict, campo: str) -> datetime:
    if campo not in obj:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = obj[campo]
    if not isinstance(valor, str) or not DATA_HORA_RE.match(valor):
        raise _invalido(f"Campo '{campo}' deve estar no formato AAAA-MM-DDTHH:MM:SS.")
    try:
        return datetime.strptime(valor, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise _invalido(f"Campo '{campo}' não é uma data e hora válida.")


def _parse_dh(texto: str) -> datetime:
    return datetime.strptime(texto, "%Y-%m-%dT%H:%M:%S")


def _buscar_cotacao(cotacao_id: str) -> dict:
    url = os.environ["URL_COTACAO"] + "/cotacoes/" + cotacao_id
    try:
        resp = httpx.get(url, timeout=10.0)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    if resp.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", f"Cotação '{cotacao_id}' não encontrada.")
    if resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação respondeu com erro.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    if not isinstance(dados, dict):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    return dados


def _publicar(eventos: list) -> None:
    for evento in eventos:
        try:
            url = os.environ["URL_BARRAMENTO"] + "/publicar"
            httpx.post(url, json=evento, timeout=10.0)
        except Exception:
            pass


def _evento(tipo: str, ocorrido_em: str, dados: dict) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }


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


def _pedido_tem_envio_ativo(pedido_id: str) -> bool:
    for e in _envios.values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            return True
    return False


@app.post("/envios")
async def criar_envio(request: Request):
    corpo = await _ler_corpo(request)

    pedido_id = _texto_nao_branco(corpo, "pedido_id")
    cotacao_id = _texto_nao_vazio(corpo, "cotacao_id")
    transportadora_id = _texto_nao_vazio(corpo, "transportadora_id")
    if "destinatario" not in corpo:
        raise _invalido("Campo 'destinatario' ausente.")
    dest = corpo["destinatario"]
    if not isinstance(dest, dict):
        raise _invalido("Campo 'destinatario' deve ser um objeto.")
    nome = _texto_nao_branco(dest, "nome")
    if "email" not in dest:
        raise _invalido("Campo 'destinatario.email' ausente.")
    email = dest["email"]
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    dh = _data_hora(corpo, "data_hora")
    data_hora_txt = corpo["data_hora"]

    async with _lock:
        if _pedido_tem_envio_ativo(pedido_id):
            raise ErroAPI(409, "ENVIO_DUPLICADO", f"O pedido '{pedido_id}' já tem um envio ativo.")

        cotacao = await run_in_threadpool(_buscar_cotacao, cotacao_id)

        try:
            valida_ate = date.fromisoformat(cotacao["valida_ate"])
            opcoes = cotacao["opcoes"]
        except (KeyError, TypeError, ValueError):
            raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")

        if dh.date() > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

        opcao = None
        for o in opcoes:
            if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA",
                          f"A transportadora '{transportadora_id}' não está entre as opções da cotação.")

        _contador["envio"] += 1
        numero = _contador["envio"]
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
            "historico": [{"status": "CRIADO", "data_hora": data_hora_txt, "local": None}],
        }
        _envios[envio["id"]] = envio
        resposta = copy.deepcopy(envio)

        evento = _evento("envio.criado", data_hora_txt, {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": pedido_id,
            "transportadora_id": transportadora_id,
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": email,
            "data_hora": data_hora_txt,
        })
        await run_in_threadpool(_publicar, [evento])

    return JSONResponse(status_code=201, content=resposta)


@app.get("/envios")
async def listar_envios(request: Request):
    params = request.query_params
    if "pedido_id" not in params:
        raise _invalido("Parâmetro 'pedido_id' é obrigatório.")
    pedido_id = params["pedido_id"]
    async with _lock:
        itens = [copy.deepcopy(e) for eid, e in sorted(_envios.items(), key=lambda kv: kv[0])
                 if e["pedido_id"] == pedido_id]
    return JSONResponse(status_code=200, content=itens)


@app.get("/envios/{eid}")
async def consultar_envio(eid: str):
    async with _lock:
        envio = _envios.get(eid)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", f"Envio '{eid}' não encontrado.")
        resposta = copy.deepcopy(envio)
    return JSONResponse(status_code=200, content=resposta)


@app.post("/envios/{eid}/atualizacoes")
async def registrar_atualizacao(eid: str, request: Request):
    corpo = await _ler_corpo(request)

    if "status" not in corpo:
        raise _invalido("Campo 'status' ausente.")
    status = corpo["status"]
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    dh = _data_hora(corpo, "data_hora")
    data_hora_txt = corpo["data_hora"]
    local = _texto_nao_vazio(corpo, "local")

    async with _lock:
        envio = _envios.get(eid)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", f"Envio '{eid}' não encontrado.")

        if status not in TRANSICOES.get(envio["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA",
                          f"Transição de {envio['status']} para {status} não permitida.")

        ultimo = _parse_dh(envio["historico"][-1]["data_hora"])
        if dh < ultimo:
            raise ErroAPI(422, "DATA_HORA_INVALIDA",
                          "A data e hora é anterior à do último item do histórico.")

        novos = [{"status": status, "data_hora": data_hora_txt, "local": local}]
        tentativas = envio["tentativas_falhas"]
        status_final = status
        if status == "FALHA_ENTREGA":
            tentativas += 1
            if tentativas >= 3:
                novos.append({"status": "DEVOLVIDO", "data_hora": data_hora_txt, "local": local})
                status_final = "DEVOLVIDO"

        envio["historico"].extend(novos)
        envio["status"] = status_final
        envio["tentativas_falhas"] = tentativas
        resposta = copy.deepcopy(envio)

        eventos = [_evento_status(envio, item) for item in novos]
        await run_in_threadpool(_publicar, eventos)

    return JSONResponse(status_code=200, content=resposta)


@app.post("/envios/{eid}/cancelar")
async def cancelar_envio(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    dh = _data_hora(corpo, "data_hora")
    data_hora_txt = corpo["data_hora"]

    async with _lock:
        envio = _envios.get(eid)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", f"Envio '{eid}' não encontrado.")

        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA",
                          f"Envio no status {envio['status']} não pode ser cancelado.")

        ultimo = _parse_dh(envio["historico"][-1]["data_hora"])
        if dh < ultimo:
            raise ErroAPI(422, "DATA_HORA_INVALIDA",
                          "A data e hora é anterior à do último item do histórico.")

        item = {"status": "CANCELADO", "data_hora": data_hora_txt, "local": None}
        envio["historico"].append(item)
        envio["status"] = "CANCELADO"
        resposta = copy.deepcopy(envio)

        await run_in_threadpool(_publicar, [_evento_status(envio, item)])

    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    async with _lock:
        _envios.clear()
        _contador["envio"] = 0
    return Response(status_code=204)
