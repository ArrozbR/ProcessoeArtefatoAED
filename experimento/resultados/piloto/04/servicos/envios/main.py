# arquivo: servicos/envios/main.py
import asyncio
import copy
import os
import re
import uuid
from datetime import date, datetime
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
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

_estado = {"contador": 0, "envios": {}}
_trava = asyncio.Lock()


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        erro = "NAO_ENCONTRADO"
    elif exc.status_code == 405:
        erro = "METODO_NAO_PERMITIDO"
    else:
        erro = "ERRO"
    return _resposta_erro(exc.status_code, erro, str(exc.detail))


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _texto(obj, campo, rejeitar_espacos):
    if campo not in obj:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = obj[campo]
    if not isinstance(valor, str) or len(valor) == 0:
        raise _invalido(f"Campo '{campo}' deve ser um texto não vazio.")
    if rejeitar_espacos and not valor.strip():
        raise _invalido(f"Campo '{campo}' não pode conter só espaços.")
    return valor


def _data_hora(obj, campo="data_hora"):
    if campo not in obj:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = obj[campo]
    if not isinstance(valor, str) or not RE_DATA_HORA.fullmatch(valor):
        raise _invalido(f"Campo '{campo}' deve estar no formato AAAA-MM-DDTHH:MM:SS.")
    try:
        momento = datetime.strptime(valor, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise _invalido(f"Campo '{campo}' não é uma data e hora válida.")
    return valor, momento


def _momento(texto):
    return datetime.strptime(texto, "%Y-%m-%dT%H:%M:%S")


def _buscar_cotacao(cotacao_id):
    url = os.environ["URL_COTACAO"] + "/cotacoes/" + quote(cotacao_id, safe="")
    try:
        resp = httpx.get(url, timeout=10)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação indisponível.")
    if resp.status_code == 404:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    if resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Cotação respondeu com erro.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    if not isinstance(dados, dict):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Cotação.")
    return dados


def _publicar(eventos):
    url = os.environ["URL_BARRAMENTO"] + "/publicar"
    for evento in eventos:
        try:
            resp = httpx.post(url, json=evento, timeout=10)
        except Exception:
            raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")
        if resp.status_code >= 500:
            raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento de eventos respondeu com erro.")


def _evento(tipo, ocorrido_em, dados):
    return {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }


def _evento_status(envio, item):
    return _evento("envio.status_atualizado", item["data_hora"], {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    })


@app.post("/envios", status_code=201)
async def criar_envio(request: Request):
    corpo = await _ler_corpo(request)

    pedido_id = _texto(corpo, "pedido_id", True)
    cotacao_id = _texto(corpo, "cotacao_id", False)
    transportadora_id = _texto(corpo, "transportadora_id", False)
    if "destinatario" not in corpo:
        raise _invalido("Campo 'destinatario' ausente.")
    destinatario = corpo["destinatario"]
    if not isinstance(destinatario, dict):
        raise _invalido("Campo 'destinatario' deve ser um objeto.")
    nome = _texto(destinatario, "nome", True)
    if "email" not in destinatario:
        raise _invalido("Campo 'email' ausente.")
    email = destinatario["email"]
    if not isinstance(email, str) or not RE_EMAIL.fullmatch(email):
        raise _invalido("Campo 'email' inválido.")
    data_hora, momento = _data_hora(corpo)

    async with _trava:
        for envio in _estado["envios"].values():
            if envio["pedido_id"] == pedido_id and envio["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

        cotacao = await run_in_threadpool(_buscar_cotacao, cotacao_id)

        valida_ate = date.fromisoformat(cotacao["valida_ate"])
        if momento.date() > valida_ate:
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
        evento = _evento("envio.criado", data_hora, {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": pedido_id,
            "transportadora_id": transportadora_id,
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": email,
            "data_hora": data_hora,
        })
        await run_in_threadpool(_publicar, [evento])

        _estado["contador"] = numero
        _estado["envios"][envio["id"]] = envio
        return copy.deepcopy(envio)


@app.get("/envios")
async def listar_envios(pedido_id: str = Query(...)):
    resultado = [
        copy.deepcopy(e)
        for id_, e in sorted(_estado["envios"].items())
        if e["pedido_id"] == pedido_id
    ]
    return resultado


@app.get("/envios/{id_envio}")
async def consultar_envio(id_envio: str):
    envio = _estado["envios"].get(id_envio)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return copy.deepcopy(envio)


@app.post("/envios/{id_envio}/atualizacoes")
async def atualizar_envio(id_envio: str, request: Request):
    corpo = await _ler_corpo(request)

    if "status" not in corpo:
        raise _invalido("Campo 'status' ausente.")
    status = corpo["status"]
    if not isinstance(status, str) or status not in STATUS_ATUALIZACAO:
        raise _invalido("Campo 'status' inválido.")
    data_hora, momento = _data_hora(corpo)
    local = _texto(corpo, "local", False)

    async with _trava:
        atual = _estado["envios"].get(id_envio)
        if atual is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

        if status not in TRANSICOES.get(atual["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", f"Transição de {atual['status']} para {status} não permitida.")

        if momento < _momento(atual["historico"][-1]["data_hora"]):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior à do último item do histórico.")

        envio = copy.deepcopy(atual)
        novos = []
        item = {"status": status, "data_hora": data_hora, "local": local}
        envio["historico"].append(item)
        envio["status"] = status
        novos.append(item)
        if status == "FALHA_ENTREGA":
            envio["tentativas_falhas"] += 1
            if envio["tentativas_falhas"] >= 3:
                devolvido = {"status": "DEVOLVIDO", "data_hora": data_hora, "local": local}
                envio["historico"].append(devolvido)
                envio["status"] = "DEVOLVIDO"
                novos.append(devolvido)

        eventos = [_evento_status(envio, i) for i in novos]
        await run_in_threadpool(_publicar, eventos)

        _estado["envios"][id_envio] = envio
        return copy.deepcopy(envio)


@app.post("/envios/{id_envio}/cancelar")
async def cancelar_envio(id_envio: str, request: Request):
    corpo = await _ler_corpo(request)
    data_hora, momento = _data_hora(corpo)

    async with _trava:
        atual = _estado["envios"].get(id_envio)
        if atual is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")

        if atual["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no estado CRIADO.")

        if momento < _momento(atual["historico"][-1]["data_hora"]):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "data_hora anterior à do último item do histórico.")

        envio = copy.deepcopy(atual)
        item = {"status": "CANCELADO", "data_hora": data_hora, "local": None}
        envio["historico"].append(item)
        envio["status"] = "CANCELADO"

        await run_in_threadpool(_publicar, [_evento_status(envio, item)])

        _estado["envios"][id_envio] = envio
        return copy.deepcopy(envio)


@app.post("/admin/reset", status_code=204)
async def reiniciar():
    async with _trava:
        _estado["contador"] = 0
        _estado["envios"] = {}
    return Response(status_code=204)
