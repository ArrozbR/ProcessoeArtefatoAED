# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
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

_lock = threading.RLock()
_envios: dict = {}
_contador = {"envio": 0}


class ErroApi(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados da requisição inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _invalido(mensagem: str) -> ErroApi:
    return ErroApi(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _validar_data_hora(valor) -> str:
    if not isinstance(valor, str) or not RE_DATA_HORA.fullmatch(valor):
        raise _invalido("Campo 'data_hora' inválido; use AAAA-MM-DDTHH:MM:SS.")
    try:
        datetime.strptime(valor, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise _invalido("Campo 'data_hora' inválido; use AAAA-MM-DDTHH:MM:SS.")
    return valor


def _texto_nao_vazio(valor) -> bool:
    return isinstance(valor, str) and valor.strip() != ""


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        url = os.environ["URL_BARRAMENTO"]
        resposta = httpx.post(f"{url}/publicar", json=evento, timeout=10.0)
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")
    if resposta.status_code >= 500:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Barramento de eventos respondeu com erro.")


def _dados_status(envio: dict, item: dict) -> dict:
    return {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    }


def _buscar_cotacao(cotacao_id: str) -> dict:
    try:
        url = os.environ["URL_COTACAO"]
        resposta = httpx.get(f"{url}/cotacoes/{cotacao_id}", timeout=10.0)
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    if resposta.status_code == 404:
        raise ErroApi(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resposta.status_code != 200:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço Cotação respondeu com erro.")
    try:
        dados = resposta.json()
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    if not isinstance(dados, dict):
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    return dados


@app.post("/envios")
async def criar_envio(request: Request):
    corpo = await _ler_corpo(request)

    pedido_id = corpo.get("pedido_id")
    if not _texto_nao_vazio(pedido_id):
        raise _invalido("Campo 'pedido_id' inválido.")
    cotacao_id = corpo.get("cotacao_id")
    if not isinstance(cotacao_id, str) or cotacao_id == "":
        raise _invalido("Campo 'cotacao_id' inválido.")
    transportadora_id = corpo.get("transportadora_id")
    if not isinstance(transportadora_id, str) or transportadora_id == "":
        raise _invalido("Campo 'transportadora_id' inválido.")
    destinatario = corpo.get("destinatario")
    if not isinstance(destinatario, dict):
        raise _invalido("Campo 'destinatario' ausente ou inválido.")
    nome = destinatario.get("nome")
    if not _texto_nao_vazio(nome):
        raise _invalido("Campo 'destinatario.nome' inválido.")
    email = destinatario.get("email")
    if not isinstance(email, str) or not RE_EMAIL.match(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    data_hora = _validar_data_hora(corpo.get("data_hora"))

    with _lock:
        for envio in _envios.values():
            if envio["pedido_id"] == pedido_id and envio["status"] != "CANCELADO":
                raise ErroApi(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

        cotacao = _buscar_cotacao(cotacao_id)

        valida_ate = cotacao.get("valida_ate", "")
        if data_hora[:10] > valida_ate:
            raise ErroApi(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

        opcao = None
        for o in cotacao.get("opcoes", []):
            if o.get("transportadora_id") == transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroApi(422, "TRANSPORTADORA_NAO_OFERECIDA", "A transportadora não está entre as opções da cotação.")

        numero = _contador["envio"] + 1
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

        _publicar("envio.criado", data_hora, {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": pedido_id,
            "transportadora_id": transportadora_id,
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": email,
            "data_hora": data_hora,
        })

        _contador["envio"] = numero
        _envios[envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar_envios(request: Request):
    params = request.query_params
    if "pedido_id" not in params:
        raise _invalido("Parâmetro 'pedido_id' é obrigatório.")
    pedido_id = params["pedido_id"]
    with _lock:
        lista = [
            copy.deepcopy(_envios[eid])
            for eid in sorted(_envios)
            if _envios[eid]["pedido_id"] == pedido_id
        ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/envios/{eid}")
async def consultar_envio(eid: str):
    with _lock:
        envio = _envios.get(eid)
        if envio is None:
            raise ErroApi(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{eid}/atualizacoes")
async def registrar_atualizacao(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    data_hora = _validar_data_hora(corpo.get("data_hora"))
    local = corpo.get("local")
    if not isinstance(local, str) or local == "":
        raise _invalido("Campo 'local' deve ser um texto não vazio.")

    with _lock:
        envio = _envios.get(eid)
        if envio is None:
            raise ErroApi(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if (envio["status"], status) not in TRANSICOES:
            raise ErroApi(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
        if data_hora < envio["historico"][-1]["data_hora"]:
            raise ErroApi(422, "DATA_HORA_INVALIDA", "data_hora anterior à do último item do histórico.")

        novo = copy.deepcopy(envio)
        itens = [{"status": status, "data_hora": data_hora, "local": local}]
        novo["status"] = status
        if status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                itens.append({"status": "DEVOLVIDO", "data_hora": data_hora, "local": local})
                novo["status"] = "DEVOLVIDO"
        novo["historico"].extend(itens)

        for item in itens:
            _publicar("envio.status_atualizado", data_hora, _dados_status(novo, item))

        _envios[eid] = novo
        return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/envios/{eid}/cancelar")
async def cancelar_envio(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    data_hora = _validar_data_hora(corpo.get("data_hora"))

    with _lock:
        envio = _envios.get(eid)
        if envio is None:
            raise ErroApi(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if envio["status"] != "CRIADO":
            raise ErroApi(409, "TRANSICAO_INVALIDA", "Só é possível cancelar um envio no estado CRIADO.")
        if data_hora < envio["historico"][-1]["data_hora"]:
            raise ErroApi(422, "DATA_HORA_INVALIDA", "data_hora anterior à do último item do histórico.")

        novo = copy.deepcopy(envio)
        item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
        novo["status"] = "CANCELADO"
        novo["historico"].append(item)

        _publicar("envio.status_atualizado", data_hora, _dados_status(novo, item))

        _envios[eid] = novo
        return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/admin/reset")
async def reset():
    with _lock:
        _envios.clear()
        _contador["envio"] = 0
    return Response(status_code=204)
