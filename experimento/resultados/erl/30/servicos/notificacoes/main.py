# arquivo: servicos/notificacoes/main.py
import copy
import json

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

_estado = {"processados": set(), "contador": 0, "notificacoes": []}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request, exc):
    return invalido("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request, exc):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_corpo(request):
    try:
        raw = await request.body()
        dados = json.loads(raw)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    return dados


def chave_evento(eid):
    try:
        return json.dumps(eid, sort_keys=True)
    except Exception:
        return str(eid)


def registrar(tipo, dados, ocorrido_em):
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _estado["contador"] += 1
    _estado["notificacoes"].append(
        {
            "id": f"NOT-{_estado['contador']:06d}",
            "email": dados.get("destinatario_email"),
            "pedido_id": pedido_id,
            "codigo_rastreio": codigo,
            "tipo": tipo,
            "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
            "criada_em": ocorrido_em,
        }
    )


@app.post("/eventos")
async def eventos(request: Request):
    ev = await ler_corpo(request)
    if ev is None:
        return invalido("Corpo inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in ev:
            return invalido(f"Campo {campo} ausente.")
    dados = ev["dados"]
    if not isinstance(dados, dict):
        return invalido("Campo dados inválido.")

    chave = chave_evento(ev["id"])
    if chave in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = ev["tipo"]
    if tipo == "envio.criado":
        registrar("CRIADO", dados, ev["ocorrido_em"])
    elif tipo == "envio.status_atualizado":
        status = dados.get("status")
        if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
            registrar(status, dados, ev["ocorrido_em"])
    else:
        return JSONResponse(status_code=200, content={"processado": False})

    _estado["processados"].add(chave)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar(request: Request):
    if "email" not in request.query_params:
        return invalido("Parâmetro email obrigatório.")
    email = request.query_params["email"]
    lista = [copy.deepcopy(n) for n in _estado["notificacoes"] if n["email"] == email]
    return JSONResponse(status_code=200, content=lista)


@app.post("/admin/reset")
async def reset():
    _estado["processados"].clear()
    _estado["contador"] = 0
    _estado["notificacoes"].clear()
    return Response(status_code=204)
