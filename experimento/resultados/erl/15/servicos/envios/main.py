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
RE_DATA_HORA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")

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

_estado = {"contador": 0, "envios": {}}


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
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return corpo


def _texto_nao_vazio(obj: dict, campo: str, permitir_espacos: bool = False) -> str:
    if campo not in obj:
        raise _invalido(f"Campo '{campo}' ausente.")
    v = obj[campo]
    if not isinstance(v, str):
        raise _invalido(f"Campo '{campo}' deve ser texto.")
    if v == "" or (not permitir_espacos and v.strip() == ""):
        raise _invalido(f"Campo '{campo}' não pode ser vazio.")
    return v


def _validar_data_hora(obj: dict, campo: str = "data_hora") -> datetime:
    if campo not in obj:
        raise _invalido(f"Campo '{campo}' ausente.")
    v = obj[campo]
    if not isinstance(v, str) or not RE_DATA_HORA.fullmatch(v):
        raise _invalido(f"Campo '{campo}' deve estar no formato AAAA-MM-DDTHH:MM:SS.")
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise _invalido(f"Campo '{campo}' não é uma data e hora válida.")


def _dt(texto: str) -> datetime:
    return datetime.strptime(texto, "%Y-%m-%dT%H:%M:%S")


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    url = os.environ["URL_BARRAMENTO"]
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        resp = httpx.post(f"{url}/publicar", json=evento, timeout=10.0)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")
    if resp.status_code >= 500:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento indisponível.")


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


def _buscar_envio(eid: str) -> dict:
    envio = _estado["envios"].get(eid)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return envio


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["envios"] = {}
    return Response(status_code=204)


@app.post("/envios")
async def criar(request: Request):
    corpo = await _ler_corpo(request)

    pedido_id = _texto_nao_vazio(corpo, "pedido_id")
    cotacao_id = _texto_nao_vazio(corpo, "cotacao_id", permitir_espacos=True)
    transportadora_id = _texto_nao_vazio(corpo, "transportadora_id", permitir_espacos=True)

    if "destinatario" not in corpo or not isinstance(corpo["destinatario"], dict):
        raise _invalido("Campo 'destinatario' ausente ou inválido.")
    dest = corpo["destinatario"]
    nome = _texto_nao_vazio(dest, "nome")
    if "email" not in dest or not isinstance(dest["email"], str) or not RE_EMAIL.match(dest["email"]):
        raise _invalido("Campo 'destinatario.email' inválido.")
    email = dest["email"]

    momento = _validar_data_hora(corpo)
    data_hora = corpo["data_hora"]

    for e in _estado["envios"].values():
        if e["pedido_id"] == pedido_id and e["status"] != "CANCELADO":
            raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

    url_cot = os.environ["URL_COTACAO"]
    try:
        resp = httpx.get(f"{url_cot}/cotacoes/{cotacao_id}", timeout=10.0)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    if resp.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    try:
        cotacao = resp.json()
        valida_ate = date.fromisoformat(cotacao["valida_ate"])
        opcoes = cotacao["opcoes"]
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")

    if momento.date() > valida_ate:
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
        "historico": [{"status": "CRIADO", "data_hora": data_hora, "local": None}],
    }

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

    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio
    return JSONResponse(status_code=201, content=envio)


@app.get("/envios")
async def listar(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        raise _invalido("Parâmetro 'pedido_id' é obrigatório.")
    lista = [
        _estado["envios"][k]
        for k in sorted(_estado["envios"].keys())
        if _estado["envios"][k]["pedido_id"] == pedido_id
    ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/envios/{eid}")
async def consultar(eid: str):
    envio = _buscar_envio(eid)
    return JSONResponse(status_code=200, content=envio)


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    corpo = await _ler_corpo(request)

    if "status" not in corpo or not isinstance(corpo["status"], str) or corpo["status"] not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    status = corpo["status"]
    momento = _validar_data_hora(corpo)
    data_hora = corpo["data_hora"]
    local = _texto_nao_vazio(corpo, "local")

    envio = _buscar_envio(eid)

    if status not in TRANSICOES.get(envio["status"], set()):
        raise ErroAPI(409, "TRANSICAO_INVALIDA", f"Transição de {envio['status']} para {status} não permitida.")

    ultimo = envio["historico"][-1]
    if momento < _dt(ultimo["data_hora"]):
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

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
        _publicar("envio.status_atualizado", data_hora, _evento_status(novo, item))

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=novo)


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    momento = _validar_data_hora(corpo)
    data_hora = corpo["data_hora"]

    envio = _buscar_envio(eid)

    if envio["status"] != "CRIADO":
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")

    ultimo = envio["historico"][-1]
    if momento < _dt(ultimo["data_hora"]):
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novo = copy.deepcopy(envio)
    item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
    novo["status"] = "CANCELADO"
    novo["historico"].append(item)

    _publicar("envio.status_atualizado", data_hora, _evento_status(novo, item))

    _estado["envios"][eid] = novo
    return JSONResponse(status_code=200, content=novo)
