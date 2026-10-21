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
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_estado = {"contador": 0, "envios": {}}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


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


def _validar_data_hora(valor) -> str:
    if not isinstance(valor, str) or not RE_DATA_HORA.fullmatch(valor):
        raise _invalido("Campo 'data_hora' deve estar no formato AAAA-MM-DDTHH:MM:SS.")
    try:
        datetime.strptime(valor, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise _invalido("Campo 'data_hora' não é uma data e hora válida.")
    return valor


def _texto_nao_branco(obj: dict, campo: str) -> str:
    valor = obj.get(campo)
    if not isinstance(valor, str) or not valor.strip():
        raise _invalido(f"Campo '{campo}' ausente ou vazio.")
    return valor


def _texto_nao_vazio(obj: dict, campo: str) -> str:
    valor = obj.get(campo)
    if not isinstance(valor, str) or valor == "":
        raise _invalido(f"Campo '{campo}' ausente ou vazio.")
    return valor


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    envelope = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    url = os.environ["URL_BARRAMENTO"]
    try:
        resposta = httpx.post(f"{url}/publicar", json=envelope, timeout=10.0)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")
    if resposta.status_code >= 500:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento de eventos respondeu com erro.")


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
    url = os.environ["URL_COTACAO"]
    try:
        resposta = httpx.get(f"{url}/cotacoes/{cotacao_id}", timeout=10.0)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    if resposta.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resposta.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação respondeu com erro.")
    try:
        dados = resposta.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    if not isinstance(dados, dict):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    return dados


@app.post("/envios", status_code=201)
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
    if not isinstance(email, str) or not RE_EMAIL.fullmatch(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    data_hora = _validar_data_hora(corpo.get("data_hora"))

    for existente in _estado["envios"].values():
        if existente["pedido_id"] == pedido_id and existente["status"] != "CANCELADO":
            raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

    cotacao = _buscar_cotacao(cotacao_id)

    valida_ate = date.fromisoformat(cotacao["valida_ate"])
    if date.fromisoformat(data_hora[:10]) > valida_ate:
        raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in cotacao.get("opcoes", []):
        if o.get("transportadora_id") == transportadora_id:
            opcao = o
            break
    if opcao is None:
        raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "A transportadora não está entre as opções da cotação.")

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
    if "pedido_id" not in request.query_params:
        raise _invalido("Parâmetro 'pedido_id' é obrigatório.")
    pedido_id = request.query_params["pedido_id"]
    resultado = [
        copy.deepcopy(e)
        for eid, e in sorted(_estado["envios"].items())
        if e["pedido_id"] == pedido_id
    ]
    return JSONResponse(status_code=200, content=resultado)


@app.get("/envios/{eid}")
async def consultar_envio(eid: str):
    envio = _estado["envios"].get(eid)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{eid}/atualizacoes")
async def atualizar_envio(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' ausente ou inválido.")
    data_hora = _validar_data_hora(corpo.get("data_hora"))
    local = _texto_nao_vazio(corpo, "local")

    envio = _estado["envios"].get(eid)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if status not in TRANSICOES.get(envio["status"], set()):
        raise ErroAPI(409, "TRANSICAO_INVALIDA", f"Transição de {envio['status']} para {status} não permitida.")

    if data_hora < envio["historico"][-1]["data_hora"]:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "A data e hora é anterior à do último item do histórico.")

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
        _publicar("envio.status_atualizado", item["data_hora"], _dados_status(novo, item))

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/envios/{eid}/cancelar")
async def cancelar_envio(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    data_hora = _validar_data_hora(corpo.get("data_hora"))

    envio = _estado["envios"].get(eid)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if envio["status"] != "CRIADO":
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar um envio no estado CRIADO.")

    if data_hora < envio["historico"][-1]["data_hora"]:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "A data e hora é anterior à do último item do histórico.")

    novo = copy.deepcopy(envio)
    item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
    novo["status"] = "CANCELADO"
    novo["historico"].append(item)

    _publicar("envio.status_atualizado", data_hora, _dados_status(novo, item))

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/admin/reset", status_code=204)
async def reset():
    _estado["contador"] = 0
    _estado["envios"] = {}
    return Response(status_code=204)
