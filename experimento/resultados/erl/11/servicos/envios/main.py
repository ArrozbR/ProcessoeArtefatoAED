# arquivo: servicos/envios/main.py
import copy
import datetime
import os
import re
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATA_HORA_REGEX = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")

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
    def __init__(self, status, codigo, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 422:
        return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


def _indisponivel(mensagem):
    return ErroAPI(503, "SERVICO_INDISPONIVEL", mensagem)


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return corpo


def _parse_data_hora(texto):
    if not isinstance(texto, str) or not DATA_HORA_REGEX.match(texto):
        return None
    try:
        return datetime.datetime.strptime(texto, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _texto_preenchido(valor):
    return isinstance(valor, str) and valor.strip() != ""


def _publicar(tipo, ocorrido_em, dados):
    envelope = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    url = os.environ["URL_BARRAMENTO"] + "/publicar"
    try:
        resposta = httpx.post(url, json=envelope, timeout=10.0)
    except Exception:
        raise _indisponivel("Barramento de eventos indisponível.")
    if resposta.status_code >= 400:
        raise _indisponivel("Barramento de eventos indisponível.")


def _evento_status(envio, item):
    return {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    }


def _buscar_cotacao(cotacao_id):
    url = os.environ["URL_COTACAO"] + "/cotacoes/" + cotacao_id
    try:
        resposta = httpx.get(url, timeout=10.0)
    except Exception:
        raise _indisponivel("Serviço Cotação indisponível.")
    if resposta.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resposta.status_code != 200:
        raise _indisponivel("Serviço Cotação indisponível.")
    try:
        dados = resposta.json()
    except Exception:
        raise _indisponivel("Resposta inválida do serviço Cotação.")
    if not isinstance(dados, dict):
        raise _indisponivel("Resposta inválida do serviço Cotação.")
    return dados


@app.post("/envios")
async def criar_envio(request: Request):
    corpo = await _ler_corpo(request)
    pedido_id = corpo.get("pedido_id")
    if not _texto_preenchido(pedido_id):
        raise _invalido("Campo 'pedido_id' inválido.")
    cotacao_id = corpo.get("cotacao_id")
    if not isinstance(cotacao_id, str) or cotacao_id == "":
        raise _invalido("Campo 'cotacao_id' inválido.")
    transportadora_id = corpo.get("transportadora_id")
    if not isinstance(transportadora_id, str) or transportadora_id == "":
        raise _invalido("Campo 'transportadora_id' inválido.")
    destinatario = corpo.get("destinatario")
    if not isinstance(destinatario, dict):
        raise _invalido("Campo 'destinatario' inválido.")
    nome = destinatario.get("nome")
    if not _texto_preenchido(nome):
        raise _invalido("Campo 'destinatario.nome' inválido.")
    email = destinatario.get("email")
    if not isinstance(email, str) or not EMAIL_REGEX.match(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    data_hora_texto = corpo.get("data_hora")
    data_hora = _parse_data_hora(data_hora_texto)
    if data_hora is None:
        raise _invalido("Campo 'data_hora' inválido.")

    for envio in _estado["envios"].values():
        if envio["pedido_id"] == pedido_id and envio["status"] != "CANCELADO":
            raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

    cotacao = _buscar_cotacao(cotacao_id)

    try:
        valida_ate = datetime.date.fromisoformat(cotacao["valida_ate"])
    except Exception:
        raise _indisponivel("Resposta inválida do serviço Cotação.")
    if data_hora.date() > valida_ate:
        raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in cotacao.get("opcoes") or []:
        if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
            opcao = o
            break
    if opcao is None:
        raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

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
        "historico": [{"status": "CRIADO", "data_hora": data_hora_texto, "local": None}],
    }

    _publicar("envio.criado", data_hora_texto, {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": pedido_id,
        "transportadora_id": transportadora_id,
        "data_prevista_entrega": envio["data_prevista_entrega"],
        "destinatario_email": email,
        "data_hora": data_hora_texto,
    })

    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar_envios(request: Request):
    if "pedido_id" not in request.query_params:
        raise _invalido("Parâmetro 'pedido_id' é obrigatório.")
    pedido_id = request.query_params["pedido_id"]
    itens = [
        copy.deepcopy(e)
        for id_ in sorted(_estado["envios"])
        for e in [_estado["envios"][id_]]
        if e["pedido_id"] == pedido_id
    ]
    return JSONResponse(status_code=200, content=itens)


@app.get("/envios/{id_envio}")
async def consultar_envio(id_envio: str):
    envio = _estado["envios"].get(id_envio)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{id_envio}/atualizacoes")
async def atualizar_envio(id_envio: str, request: Request):
    corpo = await _ler_corpo(request)
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    data_hora_texto = corpo.get("data_hora")
    data_hora = _parse_data_hora(data_hora_texto)
    if data_hora is None:
        raise _invalido("Campo 'data_hora' inválido.")
    local = corpo.get("local")
    if not isinstance(local, str) or local == "":
        raise _invalido("Campo 'local' inválido.")

    envio = _estado["envios"].get(id_envio)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if (envio["status"], status) not in TRANSICOES:
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")

    ultimo = _parse_data_hora(envio["historico"][-1]["data_hora"])
    if data_hora < ultimo:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último registro do histórico.")

    novo = copy.deepcopy(envio)
    novos_itens = [{"status": status, "data_hora": data_hora_texto, "local": local}]
    novo["status"] = status
    if status == "FALHA_ENTREGA":
        novo["tentativas_falhas"] += 1
        if novo["tentativas_falhas"] >= 3:
            novos_itens.append({"status": "DEVOLVIDO", "data_hora": data_hora_texto, "local": local})
            novo["status"] = "DEVOLVIDO"
    novo["historico"].extend(novos_itens)

    for item in novos_itens:
        _publicar("envio.status_atualizado", item["data_hora"], _evento_status(novo, item))

    _estado["envios"][id_envio] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/envios/{id_envio}/cancelar")
async def cancelar_envio(id_envio: str, request: Request):
    corpo = await _ler_corpo(request)
    data_hora_texto = corpo.get("data_hora")
    data_hora = _parse_data_hora(data_hora_texto)
    if data_hora is None:
        raise _invalido("Campo 'data_hora' inválido.")

    envio = _estado["envios"].get(id_envio)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if envio["status"] != "CRIADO":
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")

    ultimo = _parse_data_hora(envio["historico"][-1]["data_hora"])
    if data_hora < ultimo:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último registro do histórico.")

    novo = copy.deepcopy(envio)
    item = {"status": "CANCELADO", "data_hora": data_hora_texto, "local": None}
    novo["status"] = "CANCELADO"
    novo["historico"].append(item)

    _publicar("envio.status_atualizado", data_hora_texto, _evento_status(novo, item))

    _estado["envios"][id_envio] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/admin/reset")
async def reset():
    _estado["envios"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)
