# arquivo: servicos/envios/main.py
import copy
import os
import re
import uuid
from datetime import date, datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RE_DATA_HORA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")

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

_estado = {"contador": 0, "envios": {}}


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
    if exc.status_code == 404:
        return _resposta_erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 422:
        return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _resposta_erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _invalido(mensagem: str = "Dados inválidos na requisição."):
    raise ErroAPI(422, "DADOS_INVALIDOS", mensagem)


def _indisponivel(mensagem: str = "Serviço indisponível."):
    raise ErroAPI(503, "SERVICO_INDISPONIVEL", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        _invalido("Corpo da requisição deve ser um objeto JSON.")
    return corpo


def _validar_data_hora(texto) -> datetime:
    if not isinstance(texto, str) or not RE_DATA_HORA.match(texto):
        _invalido("Campo 'data_hora' inválido.")
    try:
        return datetime.strptime(texto, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        _invalido("Campo 'data_hora' inválido.")


def _texto_nao_vazio(obj: dict, campo: str, permitir_espacos: bool = False) -> str:
    v = obj.get(campo)
    if not isinstance(v, str) or v == "":
        _invalido(f"Campo '{campo}' inválido.")
    if not permitir_espacos and not v.strip():
        _invalido(f"Campo '{campo}' inválido.")
    return v


def _publicar(tipo: str, ocorrido_em: str, dados: dict):
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
        _indisponivel("Barramento de eventos indisponível.")
    if resp.status_code >= 500:
        _indisponivel("Barramento de eventos indisponível.")


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
        resp = httpx.get(url, timeout=10.0)
    except Exception:
        _indisponivel("Serviço de cotação indisponível.")
    if resp.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        _indisponivel("Serviço de cotação indisponível.")
    try:
        dados = resp.json()
    except Exception:
        _indisponivel("Resposta inválida do serviço de cotação.")
    if not isinstance(dados, dict):
        _indisponivel("Resposta inválida do serviço de cotação.")
    return dados


@app.post("/envios", status_code=201)
async def criar_envio(request: Request):
    corpo = await _ler_corpo(request)
    pedido_id = _texto_nao_vazio(corpo, "pedido_id")
    cotacao_id = _texto_nao_vazio(corpo, "cotacao_id", permitir_espacos=True)
    transportadora_id = _texto_nao_vazio(corpo, "transportadora_id", permitir_espacos=True)
    destinatario = corpo.get("destinatario")
    if not isinstance(destinatario, dict):
        _invalido("Campo 'destinatario' inválido.")
    nome = _texto_nao_vazio(destinatario, "nome")
    email = destinatario.get("email")
    if not isinstance(email, str) or not RE_EMAIL.match(email):
        _invalido("Campo 'destinatario.email' inválido.")
    data_hora_txt = corpo.get("data_hora")
    data_hora = _validar_data_hora(data_hora_txt)

    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

    cotacao = _buscar_cotacao(cotacao_id)

    try:
        valida_ate = date.fromisoformat(cotacao["valida_ate"])
        opcoes = cotacao["opcoes"]
    except Exception:
        _indisponivel("Resposta inválida do serviço de cotação.")

    if data_hora.date() > valida_ate:
        raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in opcoes:
        if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
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
        "historico": [{"status": "CRIADO", "data_hora": data_hora_txt, "local": None}],
    }

    _publicar("envio.criado", data_hora_txt, {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": pedido_id,
        "transportadora_id": transportadora_id,
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": email,
        "data_hora": data_hora_txt,
    })

    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar_envios(request: Request):
    if "pedido_id" not in request.query_params:
        _invalido("Parâmetro 'pedido_id' é obrigatório.")
    pedido_id = request.query_params.get("pedido_id")
    lista = [
        copy.deepcopy(_estado["envios"][k])
        for k in sorted(_estado["envios"])
        if _estado["envios"][k]["pedido_id"] == pedido_id
    ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/envios/{eid}")
async def consultar_envio(eid: str):
    envio = _estado["envios"].get(eid)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


def _ultimo_data_hora(envio: dict) -> datetime:
    return datetime.strptime(envio["historico"][-1]["data_hora"], "%Y-%m-%dT%H:%M:%S")


@app.post("/envios/{eid}/atualizacoes")
async def atualizar_envio(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        _invalido("Campo 'status' inválido.")
    data_hora_txt = corpo.get("data_hora")
    data_hora = _validar_data_hora(data_hora_txt)
    local = corpo.get("local")
    if not isinstance(local, str) or local == "":
        _invalido("Campo 'local' inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if (envio["status"], status) not in TRANSICOES:
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
    if data_hora < _ultimo_data_hora(envio):
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novo = copy.deepcopy(envio)
    itens = [{"status": status, "data_hora": data_hora_txt, "local": local}]
    novo["status"] = status
    if status == "FALHA_ENTREGA":
        novo["tentativas_falhas"] += 1
        if novo["tentativas_falhas"] >= 3:
            itens.append({"status": "DEVOLVIDO", "data_hora": data_hora_txt, "local": local})
            novo["status"] = "DEVOLVIDO"
    novo["historico"].extend(itens)

    for item in itens:
        _publicar("envio.status_atualizado", data_hora_txt, _evento_status(novo, item))

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/envios/{eid}/cancelar")
async def cancelar_envio(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    data_hora_txt = corpo.get("data_hora")
    data_hora = _validar_data_hora(data_hora_txt)

    envio = _estado["envios"].get(eid)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    if envio["status"] != "CRIADO":
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")
    if data_hora < _ultimo_data_hora(envio):
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novo = copy.deepcopy(envio)
    item = {"status": "CANCELADO", "data_hora": data_hora_txt, "local": None}
    novo["status"] = "CANCELADO"
    novo["historico"].append(item)

    _publicar("envio.status_atualizado", data_hora_txt, _evento_status(novo, item))

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/admin/reset", status_code=204)
async def reset():
    _estado["contador"] = 0
    _estado["envios"] = {}
    return Response(status_code=204)
