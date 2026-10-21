# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

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


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


app = FastAPI()

_lock = threading.RLock()
_envios: dict = {}
_contador = {"envio": 0}


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
        return _erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo JSON inválido ou ausente.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _texto_nao_branco(obj: dict, campo: str) -> str:
    v = obj.get(campo)
    if not isinstance(v, str) or v.strip() == "":
        raise _invalido(f"Campo '{campo}' ausente ou vazio.")
    return v


def _texto_nao_vazio(obj: dict, campo: str) -> str:
    v = obj.get(campo)
    if not isinstance(v, str) or v == "":
        raise _invalido(f"Campo '{campo}' ausente ou vazio.")
    return v


def _validar_data_hora(v) -> str:
    if not isinstance(v, str) or not DATA_HORA_RE.fullmatch(v):
        raise _invalido("Campo 'data_hora' inválido.")
    try:
        datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise _invalido("Campo 'data_hora' inválido.")
    return v


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        httpx.post(os.environ["URL_BARRAMENTO"] + "/publicar", json=evento, timeout=10)
    except Exception:
        pass


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


def _buscar_cotacao(cotacao_id: str) -> dict:
    url = os.environ["URL_COTACAO"] + "/cotacoes/" + cotacao_id
    try:
        resp = httpx.get(url, timeout=10)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if resp.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de cotação.")
    if not isinstance(dados, dict):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de cotação.")
    return dados


@app.post("/envios")
async def criar_envio(request: Request):
    corpo = await _ler_corpo(request)
    pedido_id = _texto_nao_branco(corpo, "pedido_id")
    cotacao_id = _texto_nao_vazio(corpo, "cotacao_id")
    transportadora_id = _texto_nao_vazio(corpo, "transportadora_id")
    destinatario = corpo.get("destinatario")
    if not isinstance(destinatario, dict):
        raise _invalido("Campo 'destinatario' ausente ou inválido.")
    nome = _texto_nao_branco(destinatario, "nome")
    email = destinatario.get("email")
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    data_hora = _validar_data_hora(corpo.get("data_hora"))

    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

    cotacao = _buscar_cotacao(cotacao_id)

    try:
        valida_ate = date.fromisoformat(cotacao["valida_ate"])
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de cotação.")
    if date.fromisoformat(data_hora[:10]) > valida_ate:
        raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in cotacao.get("opcoes") or []:
        if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
            opcao = o
            break
    if opcao is None:
        raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "A transportadora não está entre as opções da cotação.")

    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")
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
        resposta = copy.deepcopy(envio)

        _publicar("envio.criado", data_hora, {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": pedido_id,
            "transportadora_id": transportadora_id,
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": email,
            "data_hora": data_hora,
        })
    return JSONResponse(status_code=201, content=resposta)


@app.get("/envios/{eid}")
async def consultar_envio(eid: str):
    with _lock:
        e = _envios.get(eid)
        if e is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        resposta = copy.deepcopy(e)
    return JSONResponse(status_code=200, content=resposta)


@app.get("/envios")
async def listar_envios(request: Request):
    if "pedido_id" not in request.query_params:
        raise _invalido("Parâmetro 'pedido_id' é obrigatório.")
    pedido_id = request.query_params["pedido_id"]
    with _lock:
        lista = [copy.deepcopy(_envios[k]) for k in sorted(_envios) if _envios[k]["pedido_id"] == pedido_id]
    return JSONResponse(status_code=200, content=lista)


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    data_hora = _validar_data_hora(corpo.get("data_hora"))
    local = _texto_nao_vazio(corpo, "local")

    with _lock:
        envio = _envios.get(eid)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if (envio["status"], status) not in TRANSICOES:
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
        if data_hora < envio["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        novos = [{"status": status, "data_hora": data_hora, "local": local}]
        envio["historico"].append(novos[0])
        envio["status"] = status
        if status == "FALHA_ENTREGA":
            envio["tentativas_falhas"] += 1
            if envio["tentativas_falhas"] >= 3:
                devolvido = {"status": "DEVOLVIDO", "data_hora": data_hora, "local": local}
                envio["historico"].append(devolvido)
                envio["status"] = "DEVOLVIDO"
                novos.append(devolvido)
        resposta = copy.deepcopy(envio)

        for item in novos:
            _publicar("envio.status_atualizado", item["data_hora"], _evento_status(envio, item))
    return JSONResponse(status_code=200, content=resposta)


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    data_hora = _validar_data_hora(corpo.get("data_hora"))

    with _lock:
        envio = _envios.get(eid)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar um envio no estado CRIADO.")
        if data_hora < envio["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
        envio["historico"].append(item)
        envio["status"] = "CANCELADO"
        resposta = copy.deepcopy(envio)

        _publicar("envio.status_atualizado", data_hora, _evento_status(envio, item))
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _envios.clear()
        _contador["envio"] = 0
    return Response(status_code=204)
