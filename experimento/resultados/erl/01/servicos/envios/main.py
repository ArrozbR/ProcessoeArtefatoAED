# arquivo: servicos/envios/main.py
import copy
import datetime
import os
import re
import urllib.parse
import uuid

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
        return _resposta_erro(404, "ROTA_NAO_ENCONTRADA", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


def _indisponivel(mensagem):
    return ErroAPI(503, "SERVICO_INDISPONIVEL", mensagem)


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _data_hora_valida(texto):
    if not isinstance(texto, str) or not RE_DATA_HORA.fullmatch(texto):
        return False
    try:
        datetime.datetime.strptime(texto, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return True


def _texto_nao_vazio(valor, permitir_espacos=False):
    if not isinstance(valor, str) or valor == "":
        return False
    if not permitir_espacos and not valor.strip():
        return False
    return True


def _validar_criacao(corpo):
    if not _texto_nao_vazio(corpo.get("pedido_id")):
        raise _invalido("Campo 'pedido_id' ausente ou vazio.")
    if not _texto_nao_vazio(corpo.get("cotacao_id"), permitir_espacos=True):
        raise _invalido("Campo 'cotacao_id' ausente ou vazio.")
    if not _texto_nao_vazio(corpo.get("transportadora_id"), permitir_espacos=True):
        raise _invalido("Campo 'transportadora_id' ausente ou vazio.")
    destinatario = corpo.get("destinatario")
    if not isinstance(destinatario, dict):
        raise _invalido("Campo 'destinatario' ausente ou inválido.")
    if not _texto_nao_vazio(destinatario.get("nome")):
        raise _invalido("Campo 'destinatario.nome' ausente ou vazio.")
    email = destinatario.get("email")
    if not isinstance(email, str) or not RE_EMAIL.match(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    if not _data_hora_valida(corpo.get("data_hora")):
        raise _invalido("Campo 'data_hora' deve estar no formato AAAA-MM-DDTHH:MM:SS.")


def _buscar_cotacao(cotacao_id):
    base = os.environ["URL_COTACAO"]
    url = f"{base}/cotacoes/{urllib.parse.quote(cotacao_id, safe='')}"
    try:
        resposta = httpx.get(url, timeout=10.0)
    except Exception:
        raise _indisponivel("Serviço de cotação indisponível.")
    if resposta.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resposta.status_code != 200:
        raise _indisponivel("Serviço de cotação indisponível.")
    try:
        dados = resposta.json()
    except Exception:
        raise _indisponivel("Resposta inválida do serviço de cotação.")
    if not isinstance(dados, dict):
        raise _indisponivel("Resposta inválida do serviço de cotação.")
    return dados


def _publicar(tipo, ocorrido_em, dados):
    base = os.environ["URL_BARRAMENTO"]
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    try:
        resposta = httpx.post(f"{base}/publicar", json=evento, timeout=10.0)
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


@app.post("/envios")
async def criar_envio(request: Request):
    corpo = await _ler_corpo(request)
    _validar_criacao(corpo)

    pedido_id = corpo["pedido_id"]
    cotacao_id = corpo["cotacao_id"]
    transportadora_id = corpo["transportadora_id"]
    data_hora = corpo["data_hora"]

    for envio in _estado["envios"].values():
        if envio["pedido_id"] == pedido_id and envio["status"] != "CANCELADO":
            raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

    cotacao = _buscar_cotacao(cotacao_id)

    valida_ate = cotacao.get("valida_ate")
    if isinstance(valida_ate, str) and data_hora[:10] > valida_ate:
        raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação expirou.")

    opcao = None
    for o in cotacao.get("opcoes", []) or []:
        if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
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
        "destinatario": {
            "nome": corpo["destinatario"]["nome"],
            "email": corpo["destinatario"]["email"],
        },
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
        "destinatario_email": envio["destinatario"]["email"],
        "data_hora": data_hora,
    })

    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio
    return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios/{ident}")
async def consultar_envio(ident: str):
    envio = _estado["envios"].get(ident)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.get("/envios")
async def listar_envios(request: Request):
    if "pedido_id" not in request.query_params:
        raise _invalido("Parâmetro 'pedido_id' é obrigatório.")
    pedido_id = request.query_params["pedido_id"]
    resultado = [
        copy.deepcopy(e)
        for ident, e in sorted(_estado["envios"].items())
        if e["pedido_id"] == pedido_id
    ]
    return JSONResponse(status_code=200, content=resultado)


@app.post("/envios/{ident}/atualizacoes")
async def registrar_atualizacao(ident: str, request: Request):
    corpo = await _ler_corpo(request)
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    data_hora = corpo.get("data_hora")
    if not _data_hora_valida(data_hora):
        raise _invalido("Campo 'data_hora' deve estar no formato AAAA-MM-DDTHH:MM:SS.")
    local = corpo.get("local")
    if not isinstance(local, str) or local == "":
        raise _invalido("Campo 'local' deve ser um texto não vazio.")

    envio = _estado["envios"].get(ident)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if status not in TRANSICOES.get(envio["status"], set()):
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")

    if data_hora < envio["historico"][-1]["data_hora"]:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior à do último item do histórico.")

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
        _publicar("envio.status_atualizado", item["data_hora"], _evento_status(novo, item))

    _estado["envios"][ident] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/envios/{ident}/cancelar")
async def cancelar_envio(ident: str, request: Request):
    corpo = await _ler_corpo(request)
    data_hora = corpo.get("data_hora")
    if not _data_hora_valida(data_hora):
        raise _invalido("Campo 'data_hora' deve estar no formato AAAA-MM-DDTHH:MM:SS.")

    envio = _estado["envios"].get(ident)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

    if envio["status"] != "CRIADO":
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")

    if data_hora < envio["historico"][-1]["data_hora"]:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior à do último item do histórico.")

    novo = copy.deepcopy(envio)
    item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
    novo["status"] = "CANCELADO"
    novo["historico"].append(item)

    _publicar("envio.status_atualizado", data_hora, _evento_status(novo, item))

    _estado["envios"][ident] = novo
    return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/admin/reset")
async def reset():
    _estado["envios"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)
