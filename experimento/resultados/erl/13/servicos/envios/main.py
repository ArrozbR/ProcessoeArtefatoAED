# arquivo: servicos/envios/main.py
import copy
import json
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

_estado = {"envios": {}, "contador": 0}


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


def _indisponivel(mensagem: str) -> ErroAPI:
    return ErroAPI(503, "SERVICO_INDISPONIVEL", mensagem)


async def _ler_json(request: Request):
    try:
        corpo = await request.body()
        return json.loads(corpo)
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")


def _texto_nao_vazio(obj: dict, campo: str, permitir_espacos: bool = False) -> str:
    v = obj.get(campo)
    if not isinstance(v, str) or len(v) == 0:
        raise _invalido(f"Campo '{campo}' ausente ou vazio.")
    if not permitir_espacos and not v.strip():
        raise _invalido(f"Campo '{campo}' não pode ser só espaços.")
    return v


def _data_hora(obj: dict) -> str:
    v = obj.get("data_hora")
    if not isinstance(v, str) or not RE_DATA_HORA.fullmatch(v):
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
        url = os.environ["URL_BARRAMENTO"] + "/publicar"
        resp = httpx.post(url, json=evento, timeout=10.0)
    except Exception:
        raise _indisponivel("Barramento indisponível.")
    if resp.status_code >= 500:
        raise _indisponivel("Barramento indisponível.")


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


@app.post("/envios", status_code=201)
async def criar_envio(request: Request):
    corpo = await _ler_json(request)
    if not isinstance(corpo, dict):
        raise _invalido("Corpo deve ser um objeto JSON.")

    pedido_id = _texto_nao_vazio(corpo, "pedido_id")
    cotacao_id = _texto_nao_vazio(corpo, "cotacao_id", permitir_espacos=True)
    transportadora_id = _texto_nao_vazio(corpo, "transportadora_id", permitir_espacos=True)
    destinatario = corpo.get("destinatario")
    if not isinstance(destinatario, dict):
        raise _invalido("Campo 'destinatario' ausente ou inválido.")
    nome = _texto_nao_vazio(destinatario, "nome")
    email = destinatario.get("email")
    if not isinstance(email, str) or not RE_EMAIL.fullmatch(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    data_hora = _data_hora(corpo)

    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

    try:
        url = os.environ["URL_COTACAO"] + "/cotacoes/" + cotacao_id
        resp = httpx.get(url, timeout=10.0)
    except Exception:
        raise _indisponivel("Serviço Cotação indisponível.")
    if resp.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        raise _indisponivel("Serviço Cotação indisponível.")
    try:
        cotacao = resp.json()
    except Exception:
        raise _indisponivel("Resposta inválida do serviço Cotação.")

    if data_hora[:10] > cotacao["valida_ate"]:
        raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in cotacao.get("opcoes", []):
        if o.get("transportadora_id") == transportadora_id:
            opcao = o
            break
    if opcao is None:
        raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não está entre as opções da cotação.")

    numero = _estado["contador"] + 1
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

    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar_envios(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        raise _invalido("Parâmetro 'pedido_id' é obrigatório.")
    itens = [e for e in _estado["envios"].values() if e["pedido_id"] == pedido_id]
    itens.sort(key=lambda e: e["id"])
    return JSONResponse(status_code=200, content=copy.deepcopy(itens))


@app.get("/envios/{eid}")
async def consultar_envio(eid: str):
    if eid not in _estado["envios"]:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=copy.deepcopy(_estado["envios"][eid]))


@app.post("/envios/{eid}/atualizacoes")
async def atualizar_envio(eid: str, request: Request):
    corpo = await _ler_json(request)
    if not isinstance(corpo, dict):
        raise _invalido("Corpo deve ser um objeto JSON.")
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    data_hora = _data_hora(corpo)
    local = corpo.get("local")
    if not isinstance(local, str) or len(local) == 0:
        raise _invalido("Campo 'local' ausente ou vazio.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if (envio["status"], status) not in TRANSICOES:
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")

    if data_hora < envio["historico"][-1]["data_hora"]:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novo = copy.deepcopy(envio)
    novos_itens = [{"status": status, "data_hora": data_hora, "local": local}]
    novo["status"] = status
    if status == "FALHA_ENTREGA":
        novo["tentativas_falhas"] += 1
        if novo["tentativas_falhas"] >= 3:
            novos_itens.append({"status": "DEVOLVIDO", "data_hora": data_hora, "local": local})
            novo["status"] = "DEVOLVIDO"
    novo["historico"].extend(novos_itens)

    for item in novos_itens:
        _publicar("envio.status_atualizado", data_hora, _dados_status(novo, item))

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/envios/{eid}/cancelar")
async def cancelar_envio(eid: str, request: Request):
    corpo = await _ler_json(request)
    if not isinstance(corpo, dict):
        raise _invalido("Corpo deve ser um objeto JSON.")
    data_hora = _data_hora(corpo)

    envio = _estado["envios"].get(eid)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if envio["status"] != "CRIADO":
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")

    if data_hora < envio["historico"][-1]["data_hora"]:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novo = copy.deepcopy(envio)
    item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
    novo["status"] = "CANCELADO"
    novo["historico"].append(item)

    _publicar("envio.status_atualizado", data_hora, _dados_status(novo, item))

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/admin/reset", status_code=204)
async def reset():
    _estado["envios"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)
