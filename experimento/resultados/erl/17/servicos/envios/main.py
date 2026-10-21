# arquivo: servicos/envios/main.py
import copy
import os
import re
import uuid
from datetime import date, datetime
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
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
    def __init__(self, status, erro, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _resposta_erro(404, "RECURSO_NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _texto(corpo, campo, permitir_espacos=False):
    valor = corpo.get(campo)
    if not isinstance(valor, str):
        raise _invalido(f"Campo '{campo}' ausente ou não é texto.")
    if permitir_espacos:
        if valor == "":
            raise _invalido(f"Campo '{campo}' vazio.")
    elif not valor.strip():
        raise _invalido(f"Campo '{campo}' vazio.")
    return valor


def _data_hora(corpo, campo="data_hora"):
    valor = corpo.get(campo)
    if not isinstance(valor, str) or not RE_DATA_HORA.match(valor):
        raise _invalido(f"Campo '{campo}' deve estar no formato AAAA-MM-DDTHH:MM:SS.")
    try:
        datetime.strptime(valor, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise _invalido(f"Campo '{campo}' não é uma data e hora válida.")
    return valor


def _buscar_cotacao(cotacao_id):
    url = os.environ["URL_COTACAO"] + "/cotacoes/" + quote(cotacao_id, safe="")
    try:
        resposta = httpx.get(url, timeout=10.0)
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


def _publicar(eventos):
    try:
        url = os.environ["URL_BARRAMENTO"] + "/publicar"
    except Exception:
        return
    for evento in eventos:
        try:
            httpx.post(url, json=evento, timeout=10.0)
        except Exception:
            pass


def _envelope(tipo, ocorrido_em, dados):
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def _evento_status(envio, item):
    return _envelope(
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


def _obter_envio(eid):
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

    pedido_id = _texto(corpo, "pedido_id")
    cotacao_id = _texto(corpo, "cotacao_id", permitir_espacos=True)
    transportadora_id = _texto(corpo, "transportadora_id", permitir_espacos=True)
    destinatario = corpo.get("destinatario")
    if not isinstance(destinatario, dict):
        raise _invalido("Campo 'destinatario' ausente ou inválido.")
    nome = _texto(destinatario, "nome")
    email = destinatario.get("email")
    if not isinstance(email, str) or not RE_EMAIL.match(email):
        raise _invalido("Campo 'destinatario.email' inválido.")
    data_hora = _data_hora(corpo)

    for envio in _estado["envios"].values():
        if envio["pedido_id"] == pedido_id and envio["status"] != "CANCELADO":
            raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

    cotacao = await run_in_threadpool(_buscar_cotacao, cotacao_id)

    try:
        valida_ate = date.fromisoformat(cotacao["valida_ate"])
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    if date.fromisoformat(data_hora[:10]) > valida_ate:
        raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

    opcao = None
    for o in cotacao.get("opcoes") or []:
        if isinstance(o, dict) and o.get("transportadora_id") == transportadora_id:
            opcao = o
            break
    if opcao is None:
        raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "A transportadora não está entre as opções da cotação.")

    # Revalida o pedido duplicado após a chamada externa (estado pode ter mudado).
    for envio in _estado["envios"].values():
        if envio["pedido_id"] == pedido_id and envio["status"] != "CANCELADO":
            raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já tem um envio ativo.")

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
    _estado["contador"] = numero
    _estado["envios"][envio["id"]] = envio
    resposta = copy.deepcopy(envio)

    evento = _envelope(
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
    await run_in_threadpool(_publicar, [evento])
    return JSONResponse(status_code=201, content=resposta)


@app.get("/envios/{eid}")
async def consultar(eid: str):
    envio = _obter_envio(eid)
    return JSONResponse(status_code=200, content=envio)


@app.get("/envios")
async def listar(request: Request):
    pedido_id = request.query_params.get("pedido_id")
    if pedido_id is None:
        raise _invalido("Parâmetro 'pedido_id' é obrigatório.")
    lista = [
        _estado["envios"][k]
        for k in sorted(_estado["envios"])
        if _estado["envios"][k]["pedido_id"] == pedido_id
    ]
    return JSONResponse(status_code=200, content=lista)


@app.post("/envios/{eid}/atualizacoes")
async def atualizar(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    status = corpo.get("status")
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    data_hora = _data_hora(corpo)
    local = corpo.get("local")
    if not isinstance(local, str) or not local.strip():
        raise _invalido("Campo 'local' deve ser um texto não vazio.")

    envio = _obter_envio(eid)

    if status not in TRANSICOES.get(envio["status"], set()):
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de estado não permitida.")
    if data_hora < envio["historico"][-1]["data_hora"]:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    novos = [{"status": status, "data_hora": data_hora, "local": local}]
    tentativas = envio["tentativas_falhas"]
    status_final = status
    if status == "FALHA_ENTREGA":
        tentativas += 1
        if tentativas >= 3:
            novos.append({"status": "DEVOLVIDO", "data_hora": data_hora, "local": local})
            status_final = "DEVOLVIDO"

    envio["historico"].extend(novos)
    envio["tentativas_falhas"] = tentativas
    envio["status"] = status_final
    resposta = copy.deepcopy(envio)

    eventos = [_evento_status(envio, item) for item in novos]
    await run_in_threadpool(_publicar, eventos)
    return JSONResponse(status_code=200, content=resposta)


@app.post("/envios/{eid}/cancelar")
async def cancelar(eid: str, request: Request):
    corpo = await _ler_corpo(request)
    data_hora = _data_hora(corpo)

    envio = _obter_envio(eid)

    if envio["status"] != "CRIADO":
        raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")
    if data_hora < envio["historico"][-1]["data_hora"]:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior ao último item do histórico.")

    item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
    envio["historico"].append(item)
    envio["status"] = "CANCELADO"
    resposta = copy.deepcopy(envio)

    await run_in_threadpool(_publicar, [_evento_status(envio, item)])
    return JSONResponse(status_code=200, content=resposta)
