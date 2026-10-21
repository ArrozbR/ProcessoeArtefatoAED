# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime
from typing import Any, Optional

import httpx
from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _erro_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        codigo = "NAO_ENCONTRADO"
    elif exc.status_code == 405:
        codigo = "METODO_NAO_PERMITIDO"
    elif exc.status_code == 422:
        codigo = "DADOS_INVALIDOS"
    else:
        codigo = "ERRO"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


def invalido(mensagem: str = "Dados inválidos.") -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RE_DATA_HORA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
TIMEOUT = 10.0

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}
TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}
MAX_FALHAS = 3

_lock = threading.RLock()
_envios: dict = {}
_contador = {"envio": 0}


def _texto_nao_vazio(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _parse_data_hora(v: Any) -> datetime:
    if not isinstance(v, str) or not RE_DATA_HORA.match(v):
        raise invalido("Campo 'data_hora' inválido.")
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise invalido("Campo 'data_hora' inválido.")


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        httpx.post(os.environ["URL_BARRAMENTO"] + "/publicar", json=evento, timeout=TIMEOUT)
    except Exception:
        pass


def _publicar_status(envio: dict, item: dict) -> None:
    _publicar(
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


def _obter_cotacao(cotacao_id: str) -> dict:
    url = os.environ["URL_COTACAO"] + "/cotacoes/" + cotacao_id
    try:
        resp = httpx.get(url, timeout=TIMEOUT)
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


@app.post("/envios", status_code=201)
def criar_envio(body: Any = Body(None)):
    # 1. Validação do corpo
    if not isinstance(body, dict):
        raise invalido("Corpo deve ser um objeto JSON.")
    pedido_id = body.get("pedido_id")
    if not _texto_nao_vazio(pedido_id):
        raise invalido("Campo 'pedido_id' inválido.")
    cotacao_id = body.get("cotacao_id")
    if not isinstance(cotacao_id, str) or cotacao_id == "":
        raise invalido("Campo 'cotacao_id' inválido.")
    transportadora_id = body.get("transportadora_id")
    if not isinstance(transportadora_id, str) or transportadora_id == "":
        raise invalido("Campo 'transportadora_id' inválido.")
    destinatario = body.get("destinatario")
    if not isinstance(destinatario, dict):
        raise invalido("Campo 'destinatario' inválido.")
    nome = destinatario.get("nome")
    if not _texto_nao_vazio(nome):
        raise invalido("Campo 'destinatario.nome' inválido.")
    email = destinatario.get("email")
    if not isinstance(email, str) or not RE_EMAIL.match(email):
        raise invalido("Campo 'destinatario.email' inválido.")
    data_hora = body.get("data_hora")
    dh = _parse_data_hora(data_hora)

    with _lock:
        # 2. Pedido duplicado
        for e in _envios.values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

        # 3 e 4. Cotação
        cotacao = _obter_cotacao(cotacao_id)

        # 5. Validade
        try:
            valida_ate = date.fromisoformat(cotacao["valida_ate"])
            opcoes = cotacao["opcoes"]
        except Exception:
            raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
        if dh.date() > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação expirou.")

        # 6. Transportadora oferecida
        opcao = None
        for o in opcoes:
            if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        _contador["envio"] += 1
        n = _contador["envio"]
        envio = {
            "id": f"ENV-{n:06d}",
            "codigo_rastreio": f"LG{n:09d}BR",
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

        _publicar(
            "envio.criado",
            data_hora,
            {
                "envio_id": envio["id"],
                "codigo_rastreio": envio["codigo_rastreio"],
                "pedido_id": pedido_id,
                "transportadora_id": transportadora_id,
                "data_prevista_entrega": envio["data_prevista_entrega"],
                "destinatario_email": email,
                "data_hora": data_hora,
            },
        )
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: Optional[str] = None):
    if pedido_id is None:
        raise invalido("Parâmetro 'pedido_id' obrigatório.")
    with _lock:
        itens = [copy.deepcopy(_envios[k]) for k in sorted(_envios) if _envios[k]["pedido_id"] == pedido_id]
    return itens


@app.get("/envios/{envio_id}")
def consultar_envio(envio_id: str):
    with _lock:
        e = _envios.get(envio_id)
        if e is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        return copy.deepcopy(e)


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar_envio(envio_id: str, body: Any = Body(None)):
    # 1. Validação do corpo
    if not isinstance(body, dict):
        raise invalido("Corpo deve ser um objeto JSON.")
    status = body.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise invalido("Campo 'status' inválido.")
    data_hora = body.get("data_hora")
    dh = _parse_data_hora(data_hora)
    local = body.get("local")
    if not _texto_nao_vazio(local):
        raise invalido("Campo 'local' inválido.")

    with _lock:
        # 2. Existência
        envio = _envios.get(envio_id)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        # 3. Transição
        if status not in TRANSICOES.get(envio["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
        # 4. Data e hora
        ultima = datetime.strptime(envio["historico"][-1]["data_hora"], "%Y-%m-%dT%H:%M:%S")
        if dh < ultima:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao último registro.")

        novos = [{"status": status, "data_hora": data_hora, "local": local}]
        envio["status"] = status
        if status == "FALHA_ENTREGA":
            envio["tentativas_falhas"] += 1
            if envio["tentativas_falhas"] >= MAX_FALHAS:
                novos.append({"status": "DEVOLVIDO", "data_hora": data_hora, "local": local})
                envio["status"] = "DEVOLVIDO"
        envio["historico"].extend(novos)

        for item in novos:
            _publicar_status(envio, item)
        return copy.deepcopy(envio)


@app.post("/envios/{envio_id}/cancelar")
def cancelar_envio(envio_id: str, body: Any = Body(None)):
    if not isinstance(body, dict):
        raise invalido("Corpo deve ser um objeto JSON.")
    data_hora = body.get("data_hora")
    dh = _parse_data_hora(data_hora)

    with _lock:
        envio = _envios.get(envio_id)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")
        ultima = datetime.strptime(envio["historico"][-1]["data_hora"], "%Y-%m-%dT%H:%M:%S")
        if dh < ultima:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao último registro.")

        item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
        envio["status"] = "CANCELADO"
        envio["historico"].append(item)
        _publicar_status(envio, item)
        return copy.deepcopy(envio)


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _contador["envio"] = 0
    return Response(status_code=204)
