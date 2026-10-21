# arquivo: servicos/notificacoes/main.py
import copy

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_estado = {"contador": 0, "processados": set(), "notificacoes": []}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request, exc):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request, exc):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def resposta_processado(valor):
    return JSONResponse(status_code=200, content={"processado": valor})


def registrar(tipo, dados, criada_em):
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _estado["contador"] += 1
    _estado["notificacoes"].append({
        "id": "NOT-%06d" % _estado["contador"],
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": criada_em,
    })


@app.post("/eventos")
async def eventos(request: Request):
    try:
        env = await request.json()
    except Exception:
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    if not isinstance(env, dict):
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in env:
            return erro(422, "DADOS_INVALIDOS", "Envelope sem o campo " + campo + ".")
    eid = env["id"]
    dados = env["dados"]
    if not isinstance(eid, str) or not isinstance(dados, dict):
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")

    if eid in _estado["processados"]:
        return resposta_processado(False)

    tipo = env["tipo"]
    if tipo == "envio.criado":
        registrar("CRIADO", dados, env["ocorrido_em"])
        _estado["processados"].add(eid)
        return resposta_processado(True)

    if tipo == "envio.status_atualizado":
        status = dados.get("status")
        if status in MENSAGENS and status != "CRIADO":
            registrar(status, dados, env["ocorrido_em"])
        _estado["processados"].add(eid)
        return resposta_processado(True)

    return resposta_processado(False)


@app.get("/notificacoes")
async def listar(request: Request):
    email = request.query_params.get("email")
    if email is None:
        return erro(422, "DADOS_INVALIDOS", "Parâmetro email obrigatório.")
    lista = [copy.deepcopy(n) for n in _estado["notificacoes"] if n["email"] == email]
    return JSONResponse(status_code=200, content=lista)


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["processados"].clear()
    _estado["notificacoes"].clear()
    return Response(status_code=204)
