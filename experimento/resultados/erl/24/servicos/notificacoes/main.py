# arquivo: servicos/notificacoes/main.py
import copy
import json
import threading

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

CAMPOS_ENVELOPE = ("id", "tipo", "ocorrido_em", "dados")
MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_lock = threading.Lock()
_notificacoes = []
_processados = set()
_contador = 0


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request, exc):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _http(request, exc):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request):
    try:
        raw = await request.body()
        if not raw:
            return None, False
        return json.loads(raw), True
    except Exception:
        return None, False


def chave_evento(eid):
    return json.dumps(eid, sort_keys=True)


def registrar(tipo, dados, ocorrido_em):
    global _contador
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _contador += 1
    _notificacoes.append({
        "id": "NOT-%06d" % _contador,
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": ocorrido_em,
    })


@app.post("/eventos")
async def eventos(request: Request):
    corpo, ok = await ler_json(request)
    if not ok or not isinstance(corpo, dict):
        return dados_invalidos("Envelope inválido.")
    for campo in CAMPOS_ENVELOPE:
        if campo not in corpo:
            return dados_invalidos("Envelope sem o campo %s." % campo)
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        return dados_invalidos("Campo dados inválido.")

    chave = chave_evento(corpo["id"])
    tipo = corpo["tipo"]
    with _lock:
        if chave in _processados:
            return JSONResponse(status_code=200, content={"processado": False})
        if tipo == "envio.criado":
            registrar("CRIADO", dados, corpo["ocorrido_em"])
        elif tipo == "envio.status_atualizado":
            status = dados.get("status")
            if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
                registrar(status, dados, corpo["ocorrido_em"])
        else:
            return JSONResponse(status_code=200, content={"processado": False})
        _processados.add(chave)
        return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar(request: Request):
    if "email" not in request.query_params:
        return dados_invalidos("Parâmetro email é obrigatório.")
    email = request.query_params["email"]
    with _lock:
        itens = [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=itens)


@app.post("/admin/reset")
async def reset():
    global _contador
    with _lock:
        _notificacoes.clear()
        _processados.clear()
        _contador = 0
    return Response(status_code=204)
