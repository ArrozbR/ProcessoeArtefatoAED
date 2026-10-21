# arquivo: servicos/envios/main.py
import copy
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

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
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_estado: Dict[str, Any] = {"contador": 0, "envios": {}}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.erro, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos na requisição."},
    )


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        erro = "NAO_ENCONTRADO"
    elif exc.status_code == 405:
        erro = "METODO_NAO_PERMITIDO"
    else:
        erro = "ERRO"
    return JSONResponse(status_code=exc.status_code, content={"erro": erro, "mensagem": str(exc.detail)})


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")


def _data_hora_valida(s: Any) -> bool:
    if not isinstance(s, str) or not RE_DATA_HORA.fullmatch(s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return True


def _indisponivel(mensagem: str) -> ErroAPI:
    return ErroAPI(503, "SERVICO_INDISPONIVEL", mensagem)


def _buscar_cotacao(cotacao_id: str) -> Optional[Dict[str, Any]]:
    url = os.environ["URL_COTACAO"] + "/cotacoes/" + cotacao_id
    try:
        resp = httpx.get(url, timeout=10.0)
    except Exception:
        raise _indisponivel("Serviço Cotação indisponível.")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise _indisponivel("Serviço Cotação indisponível.")
    try:
        dados = resp.json()
    except Exception:
        raise _indisponivel("Resposta inválida do serviço Cotação.")
    if not isinstance(dados, dict):
        raise _indisponivel("Resposta inválida do serviço Cotação.")
    return dados


def _publicar(eventos: List[Dict[str, Any]]) -> None:
    url = os.environ["URL_BARRAMENTO"] + "/publicar"
    for evento in eventos:
        try:
            resp = httpx.post(url, json=evento, timeout=10.0)
        except Exception:
            raise _indisponivel("Barramento indisponível.")
        if resp.status_code >= 400:
            raise _indisponivel("Barramento indisponível.")


def _evento(tipo: str, ocorrido_em: str, dados: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def _evento_status(envio: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    return _evento(
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


def _validar_criacao(corpo: Any) -> Dict[str, Any]:
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    pedido_id = corpo.get("pedido_id")
    if not isinstance(pedido_id, str) or not pedido_id.strip():
        raise _invalido("Campo 'pedido_id' inválido.")
    cotacao_id = corpo.get("cotacao_id")
    if not isinstance(cotacao_id, str) or cotacao_id == "":
        raise _invalido("Campo 'cotacao_id' inválido.")
    transportadora_id = corpo.get("transportadora_id")
    if not isinstance(transportadora_id, str) or transportadora_id == "":
        raise _invalido("Campo 'transportadora_id' inválido.")
    dest = corpo.get("destinatario")
    if not isinstance(dest, dict):
        raise _invalido("Campo 'destinatario' inválido.")
    nome = dest.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        raise _invalido("Campo 'destinatario.nome' inválido.")
    email = dest.get("email")
    if not isinstance(email, str) or not RE_EMAIL.fullmatch(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    data_hora = corpo.get("data_hora")
    if not _data_hora_valida(data_hora):
        raise _invalido("Campo 'data_hora' inválido.")
    return {
        "pedido_id": pedido_id,
        "cotacao_id": cotacao_id,
        "transportadora_id": transportadora_id,
        "nome": nome,
        "email": email,
        "data_hora": data_hora,
    }


@app.post("/envios", status_code=201)
async def criar_envio(request: Request):
    corpo = await _ler_corpo(request)
    req = _validar_criacao(corpo)

    for e in _estado["envios"].values():
        if e["pedido_id"] == req["pedido_id"] and e["status"] != "CANCELADO":
            raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

    cotacao = _buscar_cotacao(req["cotacao_id"])
    if cotacao is None:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")

    valida_ate = cotacao.get("valida_ate")
    if not isinstance(valida_ate, str):
        raise _indisponivel("Resposta inválida do serviço Cotação.")
    if req["data_hora"][0:10] > valida_ate:
        raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in cotacao.get("opcoes") or []:
        if isinstance(o, dict) and o.get("transportadora_id") == req["transportadora_id"]:
            opcao = o
            break
    if opcao is None:
        raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "A transportadora não está entre as opções da cotação.")

    numero = _estado["contador"] + 1
    envio = {
        "id": f"ENV-{numero:06d}",
        "codigo_rastreio": f"LG{numero:09d}BR",
        "pedido_id": req["pedido_id"],
        "cotacao_id": req["cotacao_id"],
        "transportadora_id": req["transportadora_id"],
        "preco_centavos": opcao["preco_centavos"],
        "data_prevista_entrega": opcao["data_prevista_entrega"],
        "destinatario": {"nome": req["nome"], "email": req["email"]},
        "status": "CRIADO",
        "tentativas_falhas": 0,
        "historico": [{"status": "CRIADO", "data_hora": req["data_hora"], "local": None}],
    }

    evento = _evento(
        "envio.criado",
        req["data_hora"],
        {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "transportadora_id": envio["transportadora_id"],
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": req["email"],
            "data_hora": req["data_hora"],
        },
    )
    _publicar([evento])

    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar_envios(pedido_id: Optional[str] = None):
    if pedido_id is None:
        raise _invalido("Parâmetro 'pedido_id' é obrigatório.")
    itens = [e for e in _estado["envios"].values() if e["pedido_id"] == pedido_id]
    itens.sort(key=lambda e: e["id"])
    return copy.deepcopy(itens)


@app.get("/envios/{eid}")
async def consultar_envio(eid: str):
    e = _estado["envios"].get(eid)
    if e is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return copy.deepcopy(e)


@app.post("/envios/{eid}/atualizacoes")
async def registrar_atualizacao(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    data_hora = corpo.get("data_hora")
    if not _data_hora_valida(data_hora):
        raise _invalido("Campo 'data_hora' inválido.")
    local = corpo.get("local")
    if not isinstance(local, str) or local == "":
        raise _invalido("Campo 'local' inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if status not in TRANSICOES.get(envio["status"], set()):
        raise ErroAPI(409, "TRANSICAO_INVALIDA", f"Transição de {envio['status']} para {status} não permitida.")

    if data_hora < envio["historico"][-1]["data_hora"]:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "A data e hora é anterior à do último item do histórico.")

    novos_itens = [{"status": status, "data_hora": data_hora, "local": local}]
    tentativas = envio["tentativas_falhas"]
    if status == "FALHA_ENTREGA":
        tentativas += 1
        if tentativas >= 3:
            novos_itens.append({"status": "DEVOLVIDO", "data_hora": data_hora, "local": local})

    eventos = [_evento_status(envio, item) for item in novos_itens]
    _publicar(eventos)

    envio["historico"].extend(novos_itens)
    envio["status"] = novos_itens[-1]["status"]
    envio["tentativas_falhas"] = tentativas
    return copy.deepcopy(envio)


@app.post("/envios/{eid}/cancelar")
async def cancelar_envio(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    data_hora = corpo.get("data_hora")
    if not _data_hora_valida(data_hora):
        raise _invalido("Campo 'data_hora' inválido.")

    envio = _estado["envios"].get(eid)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if envio["status"] != "CRIADO":
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar um envio no estado CRIADO.")

    if data_hora < envio["historico"][-1]["data_hora"]:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "A data e hora é anterior à do último item do histórico.")

    item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
    _publicar([_evento_status(envio, item)])

    envio["historico"].append(item)
    envio["status"] = "CANCELADO"
    return copy.deepcopy(envio)


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["envios"] = {}
    return Response(status_code=204)
