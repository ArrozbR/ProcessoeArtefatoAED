# arquivo: servicos/envios/main.py
import copy
import json
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

app = FastAPI()

RE_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
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


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


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


async def _ler_corpo(request: Request) -> dict:
    try:
        bruto = await request.body()
        corpo = json.loads(bruto) if bruto else None
    except (ValueError, UnicodeDecodeError):
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return corpo


def _validar_data_hora(v) -> datetime:
    if not isinstance(v, str) or not RE_DATA_HORA.fullmatch(v):
        raise _invalido("Campo 'data_hora' inválido.")
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise _invalido("Campo 'data_hora' inválido.")


def _texto_nao_branco(obj: dict, campo: str) -> str:
    v = obj.get(campo)
    if not isinstance(v, str) or not v.strip():
        raise _invalido(f"Campo '{campo}' inválido.")
    return v


def _texto_nao_vazio(obj: dict, campo: str) -> str:
    v = obj.get(campo)
    if not isinstance(v, str) or len(v) == 0:
        raise _invalido(f"Campo '{campo}' inválido.")
    return v


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    url = os.environ["URL_BARRAMENTO"] + "/publicar"
    try:
        with httpx.Client(timeout=10.0) as cliente:
            cliente.post(url, json=evento)
    except httpx.HTTPError:
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
        with httpx.Client(timeout=10.0) as cliente:
            resp = cliente.get(url)
    except httpx.HTTPError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    if resp.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação respondeu com erro.")
    try:
        dados = resp.json()
    except ValueError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    if not isinstance(dados, dict):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    return dados


def _ultimo_data_hora(envio: dict) -> datetime:
    return datetime.strptime(envio["historico"][-1]["data_hora"], "%Y-%m-%dT%H:%M:%S")


@app.post("/envios")
async def criar_envio(request: Request):
    corpo = await _ler_corpo(request)
    pedido_id = _texto_nao_branco(corpo, "pedido_id")
    cotacao_id = _texto_nao_vazio(corpo, "cotacao_id")
    transportadora_id = _texto_nao_vazio(corpo, "transportadora_id")
    dest = corpo.get("destinatario")
    if not isinstance(dest, dict):
        raise _invalido("Campo 'destinatario' inválido.")
    nome = _texto_nao_branco(dest, "nome")
    email = dest.get("email")
    if not isinstance(email, str) or not RE_EMAIL.fullmatch(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    data_hora_txt = corpo.get("data_hora")
    data_hora = _validar_data_hora(data_hora_txt)

    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

        cotacao = _buscar_cotacao(cotacao_id)

        try:
            valida_ate = date.fromisoformat(cotacao["valida_ate"])
        except (KeyError, TypeError, ValueError):
            raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
        if data_hora.date() > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
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
            "historico": [{"status": "CRIADO", "data_hora": data_hora_txt, "local": None}],
        }
        _envios[envio["id"]] = envio
        resposta = copy.deepcopy(envio)

        _publicar("envio.criado", data_hora_txt, {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": pedido_id,
            "transportadora_id": transportadora_id,
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": email,
            "data_hora": data_hora_txt,
        })
    return JSONResponse(status_code=201, content=resposta)


@app.get("/envios")
async def listar_envios(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        raise _invalido("Parâmetro 'pedido_id' é obrigatório.")
    with _lock:
        itens = [copy.deepcopy(_envios[k]) for k in sorted(_envios) if _envios[k]["pedido_id"] == pedido_id]
    return JSONResponse(status_code=200, content=itens)


@app.get("/envios/{eid}")
async def consultar_envio(eid: str):
    with _lock:
        e = _envios.get(eid)
        if e is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        return JSONResponse(status_code=200, content=copy.deepcopy(e))


@app.post("/envios/{eid}/atualizacoes")
async def atualizar_envio(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    data_hora_txt = corpo.get("data_hora")
    data_hora = _validar_data_hora(data_hora_txt)
    local = _texto_nao_vazio(corpo, "local")

    with _lock:
        envio = _envios.get(eid)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if (envio["status"], status) not in TRANSICOES:
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
        if data_hora < _ultimo_data_hora(envio):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

        novos = [{"status": status, "data_hora": data_hora_txt, "local": local}]
        envio["status"] = status
        if status == "FALHA_ENTREGA":
            envio["tentativas_falhas"] += 1
            if envio["tentativas_falhas"] >= 3:
                novos.append({"status": "DEVOLVIDO", "data_hora": data_hora_txt, "local": local})
                envio["status"] = "DEVOLVIDO"
        envio["historico"].extend(novos)
        resposta = copy.deepcopy(envio)

        for item in novos:
            _publicar("envio.status_atualizado", data_hora_txt, _evento_status(envio, item))
    return JSONResponse(status_code=200, content=resposta)


@app.post("/envios/{eid}/cancelar")
async def cancelar_envio(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    data_hora_txt = corpo.get("data_hora")
    data_hora = _validar_data_hora(data_hora_txt)

    with _lock:
        envio = _envios.get(eid)
        if envio is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")
        if data_hora < _ultimo_data_hora(envio):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

        item = {"status": "CANCELADO", "data_hora": data_hora_txt, "local": None}
        envio["status"] = "CANCELADO"
        envio["historico"].append(item)
        resposta = copy.deepcopy(envio)

        _publicar("envio.status_atualizado", data_hora_txt, _evento_status(envio, item))
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _envios.clear()
        _contador["envio"] = 0
    return Response(status_code=204)
