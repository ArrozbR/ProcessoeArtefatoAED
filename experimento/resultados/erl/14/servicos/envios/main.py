# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
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

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_lock = threading.RLock()
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
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


def _indisponivel() -> ErroAPI:
    return ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço dependente indisponível.")


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return corpo


def _texto(corpo: dict, campo: str, sem_espacos: bool) -> str:
    if campo not in corpo:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = corpo[campo]
    if not isinstance(valor, str) or valor == "":
        raise _invalido(f"Campo '{campo}' inválido.")
    if sem_espacos and not valor.strip():
        raise _invalido(f"Campo '{campo}' não pode ser só espaços.")
    return valor


def _data_hora(corpo: dict, campo: str = "data_hora") -> str:
    if campo not in corpo:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = corpo[campo]
    if not isinstance(valor, str) or not DATA_HORA_RE.match(valor):
        raise _invalido(f"Campo '{campo}' deve estar no formato AAAA-MM-DDTHH:MM:SS.")
    try:
        datetime.strptime(valor, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise _invalido(f"Campo '{campo}' não é uma data e hora válida.")
    return valor


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    envelope = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    url = f"{os.environ['URL_BARRAMENTO']}/publicar"
    try:
        resp = httpx.post(url, json=envelope, timeout=10.0)
    except httpx.HTTPError:
        raise _indisponivel()
    if resp.status_code >= 500:
        raise _indisponivel()


def _evento_status(envio: dict, item: dict) -> dict:
    return {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    }


@app.post("/envios", status_code=201)
async def criar(request: Request):
    corpo = await _ler_corpo(request)

    pedido_id = _texto(corpo, "pedido_id", sem_espacos=True)
    cotacao_id = _texto(corpo, "cotacao_id", sem_espacos=False)
    transportadora_id = _texto(corpo, "transportadora_id", sem_espacos=False)
    if "destinatario" not in corpo or not isinstance(corpo["destinatario"], dict):
        raise _invalido("Campo 'destinatario' ausente ou inválido.")
    destinatario = corpo["destinatario"]
    nome = _texto(destinatario, "nome", sem_espacos=True)
    if "email" not in destinatario:
        raise _invalido("Campo 'destinatario.email' ausente.")
    email = destinatario["email"]
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    data_hora = _data_hora(corpo)

    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

        url = f"{os.environ['URL_COTACAO']}/cotacoes/{quote(cotacao_id, safe='')}"
        try:
            resp = httpx.get(url, timeout=10.0)
        except httpx.HTTPError:
            raise _indisponivel()
        if resp.status_code == 404:
            raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        if resp.status_code != 200:
            raise _indisponivel()
        try:
            cotacao = resp.json()
        except Exception:
            raise _indisponivel()
        if not isinstance(cotacao, dict):
            raise _indisponivel()

        data_envio = date.fromisoformat(data_hora[:10])
        valida_ate = date.fromisoformat(cotacao["valida_ate"])
        if data_envio > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

        opcao = None
        for o in cotacao.get("opcoes", []):
            if o.get("transportadora_id") == transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "A transportadora não está entre as opções da cotação.")

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
def listar(pedido_id: str):
    with _lock:
        itens = [copy.deepcopy(e) for k, e in sorted(_envios.items()) if e["pedido_id"] == pedido_id]
    return itens


@app.get("/envios/{id_}")
def consultar(id_: str):
    with _lock:
        e = _envios.get(id_)
        if e is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        return copy.deepcopy(e)


@app.post("/envios/{id_}/atualizacoes")
async def atualizar(id_: str, request: Request):
    corpo = await _ler_corpo(request)
    if "status" not in corpo:
        raise _invalido("Campo 'status' ausente.")
    status = corpo["status"]
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    data_hora = _data_hora(corpo)
    local = _texto(corpo, "local", sem_espacos=False)

    with _lock:
        envio = _envios.get(id_)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if status not in TRANSICOES.get(envio["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
        if data_hora < envio["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

        novo = copy.deepcopy(envio)
        itens = [{"status": status, "data_hora": data_hora, "local": local}]
        if status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                itens.append({"status": "DEVOLVIDO", "data_hora": data_hora, "local": local})
        novo["historico"].extend(itens)
        novo["status"] = itens[-1]["status"]

        for item in itens:
            _publicar("envio.status_atualizado", data_hora, _evento_status(novo, item))

        _envios[id_] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{id_}/cancelar")
async def cancelar(id_: str, request: Request):
    corpo = await _ler_corpo(request)
    data_hora = _data_hora(corpo)

    with _lock:
        envio = _envios.get(id_)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar um envio CRIADO.")
        if data_hora < envio["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

        novo = copy.deepcopy(envio)
        item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"

        _publicar("envio.status_atualizado", data_hora, _evento_status(novo, item))

        _envios[id_] = novo
        return copy.deepcopy(novo)


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _contador["envio"] = 0
    return Response(status_code=204)
